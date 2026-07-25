package ai.radpretation.opd.data

import ai.radpretation.opd.data.local.DoseDao
import ai.radpretation.opd.data.local.FileDao
import ai.radpretation.opd.data.local.FileEntryRow
import ai.radpretation.opd.data.local.KvDao
import ai.radpretation.opd.data.local.KvRow
import ai.radpretation.opd.data.local.OpdDatabase
import ai.radpretation.opd.data.local.PendingDoseRow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Everything the screens read, and the one place that decides what "offline"
 * means for each thing.
 *
 * The rule, per feature, is not uniform — because the honest answer isn't:
 *
 * * **Care file**: cache is the source of truth for *rendering*. The network
 *   refreshes it; a failure is silent and the patient still sees her papers.
 * * **Reminder plan / chemo calendar**: same, because an alarm that stops
 *   ringing when the signal drops is worse than one built on last week's plan.
 * * **Queue position**: never cached as truth. "You are 7th" from twenty minutes
 *   ago is a lie that sends someone walking out of the door at the wrong time,
 *   so a stale position is shown *with its age* and the screen says so.
 * * **Dose reports**: queued locally and drained later. The phone knows what
 *   happened; the server finding out five hours later is fine.
 */
class PatientRepository(
    private val api: ApiClient,
    private val db: OpdDatabase,
    private val tokens: TokenStore,
) {
    private val files: FileDao get() = db.files()
    private val kv: KvDao get() = db.kv()
    private val doses: DoseDao get() = db.doses()
    private val json get() = api.json

    // -- care file -------------------------------------------------------------

    val cachedFile: Flow<List<FileEntryOut>> = db.files().entries().map { rows ->
        rows.mapNotNull { row ->
            runCatching { json.decodeFromString(FileEntryOut.serializer(), row.payload) }.getOrNull()
        }
    }

    val cachedPatient: Flow<MeOut?> = kv.watch(OpdDatabase.KEY_FILE_PATIENT).map { raw ->
        raw?.let { runCatching { json.decodeFromString(MeOut.serializer(), it) }.getOrNull() }
    }

    /**
     * Pull the file if it changed. Returns true if anything was written.
     *
     * A 304 is the *expected* answer on a phone that already synced — the ETag
     * is what makes opening the app on 2G cost a few hundred bytes.
     */
    suspend fun refreshFile(): Boolean {
        val etag = kv.get(OpdDatabase.KEY_FILE_ETAG)
        val response = api.get("/patient/file", etag = etag)
        if (response.code == ApiClient.NOT_MODIFIED) return false

        val file = json.decodeFromString(CareFileOut.serializer(), response.body)
        files.replaceAll(
            file.entries.map { entry ->
                FileEntryRow(
                    id = "${entry.kind}:${entry.id}",
                    kind = entry.kind,
                    atIso = entry.at,
                    department = entry.department,
                    doctor = entry.doctor,
                    payload = json.encodeToString(FileEntryOut.serializer(), entry),
                )
            },
        )
        response.etag?.let { kv.put(KvRow(OpdDatabase.KEY_FILE_ETAG, it)) }
        kv.put(KvRow(OpdDatabase.KEY_FILE_PATIENT, json.encodeToString(MeOut.serializer(), file.patient)))
        tokens.rememberName(file.patient.name)
        return true
    }

    // -- queue -----------------------------------------------------------------

    suspend fun queuePosition(travelMinutes: Int): QueuePositionOut {
        val response = api.get("/patient/queue?travel_minutes=$travelMinutes")
        val position = json.decodeFromString(QueuePositionOut.serializer(), response.body)
        kv.put(
            KvRow(
                OpdDatabase.KEY_QUEUE_LAST,
                json.encodeToString(QueuePositionOut.serializer(), position),
            ),
        )
        return position
    }

    /** The last position seen, for the "as of HH:MM" line — never shown as live. */
    suspend fun lastKnownQueue(): QueuePositionOut? =
        kv.get(OpdDatabase.KEY_QUEUE_LAST)
            ?.let { runCatching { json.decodeFromString(QueuePositionOut.serializer(), it) }.getOrNull() }

    suspend fun arrive(): ArriveOut {
        val response = api.post("/patient/arrive", "{}")
        return json.decodeFromString(ArriveOut.serializer(), response.body)
    }

    suspend fun travelMinutes(): Int =
        kv.get(OpdDatabase.KEY_TRAVEL_MINUTES)?.toIntOrNull() ?: 45

    suspend fun setTravelMinutes(minutes: Int) {
        kv.put(KvRow(OpdDatabase.KEY_TRAVEL_MINUTES, minutes.toString()))
    }

    // -- reminders -------------------------------------------------------------

    suspend fun refreshReminderPlan(): ReminderPlanOut {
        val response = api.get("/patient/reminders")
        kv.put(KvRow(OpdDatabase.KEY_REMINDER_PLAN, response.body))
        return json.decodeFromString(ReminderPlanOut.serializer(), response.body)
    }

    suspend fun cachedReminderPlan(): ReminderPlanOut? =
        kv.get(OpdDatabase.KEY_REMINDER_PLAN)
            ?.let { runCatching { json.decodeFromString(ReminderPlanOut.serializer(), it) }.getOrNull() }

    /**
     * Record what happened to a dose, network or not.
     *
     * Always queued first and sent second. A "taken" tap that failed because the
     * phone was in a lift must not vanish, and a "missed" that never reaches the
     * server never pings the caregiver — which is the one thing this feature is
     * for.
     */
    suspend fun reportDose(
        prescriptionId: String,
        medIndex: Int,
        scheduledForIso: String,
        status: String,
    ) {
        doses.enqueue(
            PendingDoseRow(
                prescriptionId = prescriptionId,
                medIndex = medIndex,
                scheduledFor = scheduledForIso,
                status = status,
                reportedAt = System.currentTimeMillis(),
            ),
        )
        runCatching { drainDoses() }
    }

    /** Push every queued dose report. Returns how many landed. */
    suspend fun drainDoses(): Int {
        var sent = 0
        for (row in doses.pending()) {
            val body = buildJsonObject {
                put("prescription_id", JsonPrimitive(row.prescriptionId))
                put("med_index", JsonPrimitive(row.medIndex))
                put("scheduled_for", JsonPrimitive(row.scheduledFor))
                put("status", JsonPrimitive(row.status))
            }
            try {
                api.post("/patient/reminders/events", json.encodeToString(JsonObject.serializer(), body))
                doses.done(row.id)
                sent++
            } catch (e: ApiException) {
                // A 404 means the prescription is gone or was never hers. Retrying
                // forever would block every later report behind a row that can
                // never succeed, so it is dropped — the server is the authority
                // on what exists.
                if (e.code == 404) doses.done(row.id) else throw e
            }
        }
        return sent
    }

    suspend fun pendingDoseCount(): Int = doses.count()

    // -- chemo calendar --------------------------------------------------------

    suspend fun refreshChemoCalendar(): List<CycleOut> {
        val response = api.get("/patient/chemo-calendar")
        kv.put(KvRow(OpdDatabase.KEY_CHEMO_CALENDAR, response.body))
        return json.decodeFromString(ListSerializer(CycleOut.serializer()), response.body)
    }

    suspend fun cachedChemoCalendar(): List<CycleOut> =
        kv.get(OpdDatabase.KEY_CHEMO_CALENDAR)
            ?.let {
                runCatching { json.decodeFromString(ListSerializer(CycleOut.serializer()), it) }.getOrNull()
            }
            .orEmpty()

    // -- family ----------------------------------------------------------------

    suspend fun caregivers(): List<CaregiverOut> =
        json.decodeFromString(ListSerializer(CaregiverOut.serializer()), api.get("/patient/caregivers").body)

    suspend fun addCaregiver(phone: String, name: String?, relation: String?): CaregiverOut {
        val body = buildJsonObject {
            put("phone", JsonPrimitive(phone))
            name?.let { put("name", JsonPrimitive(it)) }
            relation?.let { put("relation", JsonPrimitive(it)) }
        }
        val response = api.post("/patient/caregivers", json.encodeToString(JsonObject.serializer(), body))
        return json.decodeFromString(CaregiverOut.serializer(), response.body)
    }

    suspend fun removeCaregiver(id: String): CaregiverOut =
        json.decodeFromString(CaregiverOut.serializer(), api.delete("/patient/caregivers/$id").body)

    // -- appointments ----------------------------------------------------------

    suspend fun appointments(): List<AppointmentOut> =
        json.decodeFromString(
            ListSerializer(AppointmentOut.serializer()),
            api.get("/patient/appointments").body,
        )
}
