package ai.radpretation.opd.reminders

import ai.radpretation.opd.data.DoseOut
import ai.radpretation.opd.data.ReminderPlanOut
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Turning a prescription into alarms (doc 03 §1c.4).
 *
 * Two rules decide everything here:
 *
 * 1. **A dose with no stated time gets no alarm.** `at == null` means the
 *    doctor said "BD" and not "1-0-1" — the server refused to invent a clock
 *    time (S11's `parse_schedule`), and the phone must refuse too. Those doses
 *    are listed on the Medicines screen for the patient to place herself; they
 *    are never quietly assigned 08:00.
 * 2. **Alarms are exact.** A dose deferred by Doze to whenever the phone next
 *    wakes is a missed dose that this app then reports to a caregiver. Exact
 *    alarms are what this permission exists for.
 *
 * The horizon is short (two days) and re-armed on every sync, on boot, and after
 * each fired alarm, because `setExactAndAllowWhileIdle` schedules one alarm at a
 * time and a phone that was off for a week must not wake up to fourteen.
 */
object DoseScheduler {

    const val EXTRA_PRESCRIPTION = "prescription_id"
    const val EXTRA_MED_INDEX = "med_index"
    const val EXTRA_SCHEDULED_FOR = "scheduled_for"
    const val EXTRA_DRUG = "drug"
    const val EXTRA_DOSE = "dose"

    /** How far ahead alarms are armed. Re-armed by [SyncWorker] daily. */
    const val HORIZON_DAYS = 2L

    private val ISO = DateTimeFormatter.ISO_OFFSET_DATE_TIME

    /**
     * The alarms a plan implies between now and the horizon, in order.
     *
     * Pure and side-effect free so the unit tests can assert the *policy* —
     * which doses ring, when, and which deliberately do not — without an
     * AlarmManager, a device, or a clock that moves.
     */
    fun occurrences(
        plan: ReminderPlanOut,
        from: LocalDateTime,
        zone: ZoneId = ZoneId.systemDefault(),
        horizonDays: Long = HORIZON_DAYS,
    ): List<Occurrence> {
        val prescriptionId = plan.prescriptionId ?: return emptyList()
        val out = mutableListOf<Occurrence>()
        var day: LocalDate = from.toLocalDate()
        val lastDay = day.plusDays(horizonDays)

        while (!day.isAfter(lastDay)) {
            for (dose in plan.doses) {
                val at = dose.at?.let { runCatching { LocalTime.parse(it) }.getOrNull() } ?: continue
                val moment = LocalDateTime.of(day, at)
                if (moment.isAfter(from)) {
                    out += Occurrence(
                        prescriptionId = prescriptionId,
                        dose = dose,
                        at = moment.atZone(zone),
                    )
                }
            }
            day = day.plusDays(1)
        }
        return out.sortedBy { it.at }
    }

    data class Occurrence(
        val prescriptionId: String,
        val dose: DoseOut,
        val at: java.time.ZonedDateTime,
    ) {
        /** Stable across re-arms, so re-scheduling replaces rather than duplicates. */
        val requestCode: Int = (prescriptionId + dose.medIndex + at.toEpochSecond()).hashCode()

        val scheduledForIso: String = at.format(ISO)
    }

    fun schedule(context: Context, plan: ReminderPlanOut, now: LocalDateTime = LocalDateTime.now()) {
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return
        for (occurrence in occurrences(plan, now)) {
            val intent = Intent(context, DoseAlarmReceiver::class.java).apply {
                putExtra(EXTRA_PRESCRIPTION, occurrence.prescriptionId)
                putExtra(EXTRA_MED_INDEX, occurrence.dose.medIndex)
                putExtra(EXTRA_SCHEDULED_FOR, occurrence.scheduledForIso)
                putExtra(EXTRA_DRUG, occurrence.dose.drug)
                putExtra(EXTRA_DOSE, occurrence.dose.dose.orEmpty())
            }
            val pending = PendingIntent.getBroadcast(
                context,
                occurrence.requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val triggerAt = occurrence.at.toInstant().toEpochMilli()
            if (canScheduleExact(alarms)) {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
            } else {
                // The patient revoked exact alarms (31–32 only). Still ring —
                // late is better than not at all — and the Medicines screen
                // explains why the time may drift.
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
            }
        }
    }

    fun canScheduleExact(alarms: AlarmManager): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) alarms.canScheduleExactAlarms() else true
}
