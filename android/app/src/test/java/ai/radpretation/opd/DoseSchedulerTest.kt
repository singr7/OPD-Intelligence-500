package ai.radpretation.opd

import ai.radpretation.opd.data.DoseOut
import ai.radpretation.opd.data.ReminderPlanOut
import ai.radpretation.opd.reminders.DoseScheduler
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * The reminder *policy*, with no device in the room.
 *
 * `DoseScheduler.occurrences` is pure so this can assert the one rule that
 * matters clinically — a dose whose time the doctor never stated does not get an
 * alarm invented for it — without an AlarmManager or a clock that moves.
 */
class DoseSchedulerTest {

    private val zone: ZoneId = ZoneId.of("Asia/Kolkata")

    private fun plan(vararg doses: DoseOut) = ReminderPlanOut(
        prescriptionId = "rx-1",
        prescribedOn = "2026-07-25",
        doses = doses.toList(),
        unscheduled = emptyList(),
    )

    private fun dose(index: Int, at: String?, slot: String = "morning") = DoseOut(
        medIndex = index,
        drug = "Tab Ondansetron",
        dose = "4mg",
        route = null,
        duration = null,
        slot = slot,
        at = at,
    )

    @Test
    fun `a dose with a stated time rings on every day of the horizon`() {
        val from = LocalDateTime.of(2026, 7, 25, 6, 0)
        val occurrences = DoseScheduler.occurrences(plan(dose(0, "08:00")), from, zone, horizonDays = 2)

        assertEquals(3, occurrences.size)
        assertTrue(occurrences.all { it.at.hour == 8 })
        assertEquals(listOf(25, 26, 27), occurrences.map { it.at.dayOfMonth })
    }

    @Test
    fun `a dose the doctor gave no time for never rings`() {
        // "BD" — the server said how many, not when (S11's parse_schedule), and
        // the phone must not fill the gap in. This is the whole reason the field
        // is nullable.
        val from = LocalDateTime.of(2026, 7, 25, 6, 0)
        val occurrences = DoseScheduler.occurrences(
            plan(dose(0, null, slot = "unscheduled"), dose(1, "20:00", slot = "night")),
            from,
            zone,
            horizonDays = 0,
        )

        assertEquals(1, occurrences.size)
        assertEquals(1, occurrences.first().dose.medIndex)
    }

    @Test
    fun `a time that has already passed today is not scheduled for today`() {
        val from = LocalDateTime.of(2026, 7, 25, 9, 30)
        val occurrences = DoseScheduler.occurrences(plan(dose(0, "08:00")), from, zone, horizonDays = 1)

        assertEquals(listOf(26), occurrences.map { it.at.dayOfMonth })
    }

    @Test
    fun `an empty plan schedules nothing`() {
        val empty = ReminderPlanOut(prescriptionId = null, prescribedOn = null)
        assertTrue(DoseScheduler.occurrences(empty, LocalDateTime.now(), zone).isEmpty())
    }

    @Test
    fun `the same dose keeps one request code across re-arms`() {
        // Alarms are re-armed on boot, after every firing and on every sync. A
        // request code that drifted would leave a duplicate alarm behind each
        // time, and the patient would be reminded three times by Thursday.
        val from = LocalDateTime.of(2026, 7, 25, 6, 0)
        val first = DoseScheduler.occurrences(plan(dose(0, "08:00")), from, zone)
        val second = DoseScheduler.occurrences(plan(dose(0, "08:00")), from.plusMinutes(5), zone)

        assertEquals(
            first.map { it.requestCode },
            second.map { it.requestCode },
        )
    }

    @Test
    fun `doses come back in clock order`() {
        val from = LocalDateTime.of(2026, 7, 25, 6, 0)
        val occurrences = DoseScheduler.occurrences(
            plan(dose(0, "20:00", "night"), dose(0, "08:00", "morning")),
            from,
            zone,
            horizonDays = 0,
        )

        assertEquals(listOf(8, 20), occurrences.map { it.at.hour })
    }
}
