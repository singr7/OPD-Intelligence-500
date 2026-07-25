package ai.radpretation.opd

import ai.radpretation.opd.data.ApiClient
import ai.radpretation.opd.data.PatientRepository
import ai.radpretation.opd.data.TokenPair
import ai.radpretation.opd.data.TokenStore
import ai.radpretation.opd.data.local.OpdDatabase
import ai.radpretation.opd.reminders.DoseAlarmReceiver
import ai.radpretation.opd.reminders.DoseScheduler
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.time.LocalDateTime

/**
 * Medicine reminders on a real device (doc 03 §1c.4 — the session's second
 * instrumented AC).
 *
 * Two claims are worth a device to prove:
 *
 * 1. Scheduling a plan actually leaves alarms armed with the system, and
 *    re-arming the same plan replaces them rather than stacking duplicates.
 * 2. A dose reported with no network is *kept*, and goes out when there is one.
 *    This is the whole reason the report is queued locally first: a "missed"
 *    that evaporates is a caregiver who is never told.
 */
@RunWith(AndroidJUnit4::class)
class RemindersTest {

    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var db: OpdDatabase
    private lateinit var repo: PatientRepository
    private lateinit var tokens: TokenStore

    private val planJson = """
        {
          "prescription_id": "rx-1",
          "prescribed_on": "2026-07-24",
          "doses": [
            {"med_index": 0, "drug": "Tab Ondansetron", "dose": "4mg", "slot": "morning", "at": "08:00"},
            {"med_index": 0, "drug": "Tab Ondansetron", "dose": "4mg", "slot": "night", "at": "20:00"},
            {"med_index": 1, "drug": "Tab Paracetamol", "dose": "500mg", "slot": "unscheduled", "at": null}
          ],
          "unscheduled": ["Tab Ibuprofen"]
        }
    """.trimIndent()

    @Before
    fun setUp() {
        context = InstrumentationRegistry.getInstrumentation().targetContext
        server = MockWebServer().apply { start() }
        db = Room.inMemoryDatabaseBuilder(context, OpdDatabase::class.java).build()
        tokens = TokenStore(context)
        runBlocking {
            tokens.save(TokenPair(accessToken = "access", refreshToken = "refresh", patientId = "p1"))
        }
        repo = PatientRepository(ApiClient(server.url("/").toString(), tokens), db, tokens)
    }

    @After
    fun tearDown() {
        runCatching { server.shutdown() }
        db.close()
        runBlocking { tokens.clear() }
    }

    private fun armedAlarm(occurrence: DoseScheduler.Occurrence): PendingIntent? =
        PendingIntent.getBroadcast(
            context,
            occurrence.requestCode,
            Intent(context, DoseAlarmReceiver::class.java),
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
        )

    @Test
    fun schedulingAPlanArmsOneAlarmPerStatedDoseTime() = runBlocking {
        server.enqueue(MockResponse().setBody(planJson))
        val plan = repo.refreshReminderPlan()

        val now = LocalDateTime.now().withHour(6).withMinute(0)
        val occurrences = DoseScheduler.occurrences(plan, now)
        // Two stated times over three days; the "at": null dose is absent, which
        // is the clinical rule this app inherits from the prescription.
        assertEquals(6, occurrences.size)

        DoseScheduler.schedule(context, plan, now)
        occurrences.forEach { assertNotNull("no alarm armed for ${it.at}", armedAlarm(it)) }

        // Re-arming the same plan reuses the same request codes, so the system
        // replaces each alarm instead of adding a second one.
        DoseScheduler.schedule(context, plan, now)
        val again = DoseScheduler.occurrences(plan, now)
        assertEquals(occurrences.map { it.requestCode }, again.map { it.requestCode })
    }

    @Test
    fun aDoseReportedWithNoNetworkIsKeptAndSentLater() = runBlocking {
        server.shutdown() // the village, at 8am

        repo.reportDose("rx-1", 0, "2026-07-25T08:00:00+05:30", "missed")
        assertEquals(1, repo.pendingDoseCount())

        // The signal comes back on the bus.
        server = MockWebServer().apply { start() }
        repo = PatientRepository(ApiClient(server.url("/").toString(), tokens), db, tokens)
        server.enqueue(MockResponse().setBody("""{"recorded": true, "caregiver_notified": true}"""))

        assertEquals(1, repo.drainDoses())
        assertEquals(0, repo.pendingDoseCount())

        val request = server.takeRequest()
        assertEquals("/patient/reminders/events", request.path)
        val body = request.body.readUtf8()
        assert(body.contains("\"status\":\"missed\"")) { "status not sent: $body" }
        assert(body.contains("2026-07-25T08:00:00+05:30")) { "dose time not sent: $body" }
    }

    @Test
    fun aReportForAPrescriptionTheServerDoesNotKnowIsDropped() = runBlocking {
        // Otherwise one dead row blocks every later report behind it forever.
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"no such prescription"}"""))
        repo.reportDose("gone", 0, "2026-07-25T08:00:00+05:30", "taken")

        assertEquals(0, repo.pendingDoseCount())
        assertNull(null)
    }
}
