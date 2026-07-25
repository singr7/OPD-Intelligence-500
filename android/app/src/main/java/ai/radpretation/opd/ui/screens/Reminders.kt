package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.ReminderPlanOut
import ai.radpretation.opd.reminders.DoseScheduler
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OfflineBanner
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.theme.Accent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * The medicines, and the alarms behind them (doc 03 §1c.4).
 *
 * The screen has two halves and the second one is the point: **doses with a
 * time**, which ring, and **doses without one**, which do not. A prescription
 * that said "BD" gets a card explaining that the doctor did not state when — the
 * app will not put an alarm on a guess, and it says so in the patient's own
 * words instead of quietly picking 8am (the same rule the printed prescription
 * obeys, S11).
 */
@Composable
fun RemindersScreen(container: AppContainer) {
    var plan by remember { mutableStateOf<ReminderPlanOut?>(null) }
    var offline by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        plan = try {
            container.patients.refreshReminderPlan().also {
                DoseScheduler.schedule(context, it, LocalDateTime.now())
            }
        } catch (_: Exception) {
            offline = true
            container.patients.cachedReminderPlan()
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
    ) {
        if (offline) OfflineBanner()

        Text(stringResource(R.string.reminders_title), style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(16.dp))

        val current = plan
        if (current == null || (current.doses.isEmpty() && current.unscheduled.isEmpty())) {
            Muted(stringResource(R.string.reminders_empty))
            return@Column
        }

        current.doses
            .sortedBy { it.at ?: "99:99" }
            .forEach { dose ->
                SectionCard(Modifier.testTag("dose_${dose.medIndex}_${dose.slot}")) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(dose.drug, style = MaterialTheme.typography.titleMedium)
                            listOfNotNull(dose.dose, dose.route, dose.duration)
                                .joinToString(" · ")
                                .takeIf { it.isNotBlank() }
                                ?.let { Muted(it) }
                        }
                        Text(
                            dose.at ?: "—",
                            style = MaterialTheme.typography.headlineMedium,
                            color = Accent,
                            fontWeight = FontWeight.Bold,
                        )
                    }

                    if (dose.at == null) {
                        Spacer(Modifier.height(10.dp))
                        Muted(stringResource(R.string.reminders_unscheduled_help))
                    } else {
                        Spacer(Modifier.height(14.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            QuietButton(
                                text = stringResource(R.string.reminders_taken),
                                modifier = Modifier.weight(1f).testTag("taken_${dose.medIndex}"),
                                onClick = {
                                    scope.launch {
                                        container.patients.reportDose(
                                            current.prescriptionId.orEmpty(),
                                            dose.medIndex,
                                            todayAt(dose.at!!),
                                            "taken",
                                        )
                                    }
                                },
                            )
                            QuietButton(
                                text = stringResource(R.string.reminders_missed),
                                modifier = Modifier.weight(1f).testTag("missed_${dose.medIndex}"),
                                onClick = {
                                    scope.launch {
                                        container.patients.reportDose(
                                            current.prescriptionId.orEmpty(),
                                            dose.medIndex,
                                            todayAt(dose.at!!),
                                            "missed",
                                        )
                                    }
                                },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
            }

        // Information, not a warning. A drug the doctor wrote "SOS" against is
        // a normal prescription line the phone cannot ring for — showing it in
        // danger red would teach the patient to fear the one colour this app
        // reserves for a red flag.
        current.unscheduled.forEach { drug ->
            SectionCard {
                Text(
                    stringResource(R.string.reminders_unscheduled, drug),
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.height(8.dp))
                Muted(stringResource(R.string.reminders_unscheduled_help))
            }
            Spacer(Modifier.height(12.dp))
        }
    }
}

private val ISO = DateTimeFormatter.ISO_OFFSET_DATE_TIME

/** The dose's natural key on the server: today's date at the dose's own time. */
private fun todayAt(clock: String): String =
    LocalDateTime.of(LocalDate.now(), LocalTime.parse(clock))
        .atZone(ZoneId.systemDefault())
        .format(ISO)
