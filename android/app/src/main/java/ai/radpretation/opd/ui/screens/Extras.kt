package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.AppointmentOut
import ai.radpretation.opd.data.CaregiverOut
import ai.radpretation.opd.data.CycleOut
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.rememberSpeaker
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * The chemo calendar (doc 03 §1c.5).
 *
 * "What to expect" is **read aloud by the device**, not streamed as an audio
 * clip: the same sentences the language QA harness checks, in the patient's
 * language, at a tenth of the bytes and working with the plane off. The spec
 * asks for audio clips; this delivers the audio without the clips, and the trade
 * is recorded in STATE.md.
 */
@Composable
fun CalendarScreen(container: AppContainer, onBack: () -> Unit) {
    val patient by container.patients.cachedPatient.collectAsState(initial = null)
    val speaker = rememberSpeaker(patient?.lang ?: "hi")
    var cycles by remember { mutableStateOf<List<CycleOut>>(emptyList()) }

    LaunchedEffect(Unit) {
        cycles = try {
            container.patients.refreshChemoCalendar()
        } catch (_: Exception) {
            container.patients.cachedChemoCalendar()
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
    ) {
        Text(stringResource(R.string.calendar_title), style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(16.dp))

        if (cycles.isEmpty()) {
            Muted(stringResource(R.string.calendar_empty))
        }

        cycles.forEach { cycle ->
            SectionCard(Modifier.testTag("cycle_${cycle.cycleNo}")) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(
                        stringResource(R.string.calendar_cycle, cycle.cycleNo),
                        style = MaterialTheme.typography.titleLarge,
                    )
                    Muted(cycle.at.take(10))
                }
                cycle.doctor?.let { Muted(it) }
                Spacer(Modifier.height(12.dp))
                Text(cycle.title, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                cycle.expect.forEach {
                    Text("• $it", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(6.dp))
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    QuietButton(
                        text = stringResource(R.string.calendar_listen),
                        modifier = Modifier.weight(1f).testTag("cycle_listen_${cycle.cycleNo}"),
                        onClick = { speaker.say((listOf(cycle.title) + cycle.expect).joinToString(". ")) },
                    )
                    QuietButton(
                        text = stringResource(R.string.calendar_stop),
                        modifier = Modifier.weight(1f),
                        onClick = { speaker.stop() },
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
        }

        Spacer(Modifier.height(12.dp))
        BigButton(stringResource(R.string.close), onClick = onBack)
    }
}

/**
 * Family access (doc 03 §1c.6).
 *
 * A caregiver holding this screen sees the list and no buttons: granting access
 * is the patient's own act, and the server refuses it from a caregiver token
 * (`require_patient_self`). The UI says why rather than showing a control that
 * would 403.
 */
@Composable
fun FamilyScreen(container: AppContainer, onBack: () -> Unit) {
    val via by container.tokens.via.collectAsState(initial = "self")
    val patient by container.patients.cachedPatient.collectAsState(initial = null)
    var links by remember { mutableStateOf<List<CaregiverOut>>(emptyList()) }
    var phone by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var relation by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun reload() {
        links = runCatching { container.patients.caregivers() }.getOrDefault(links)
    }

    LaunchedEffect(Unit) { reload() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
    ) {
        Text(stringResource(R.string.family_title), style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(10.dp))
        Muted(stringResource(R.string.family_help))
        Spacer(Modifier.height(20.dp))

        if (links.none { it.status == "active" }) {
            Muted(stringResource(R.string.family_empty))
            Spacer(Modifier.height(16.dp))
        }

        links.filter { it.status == "active" }.forEach { link ->
            SectionCard(Modifier.testTag("caregiver_${link.phone}")) {
                Text(link.name ?: link.phone, style = MaterialTheme.typography.titleMedium)
                Muted(listOfNotNull(link.relation, link.phone).joinToString(" · "))
                if (via == "self") {
                    Spacer(Modifier.height(12.dp))
                    QuietButton(
                        text = stringResource(R.string.family_remove),
                        enabled = !busy,
                        onClick = {
                            busy = true
                            scope.launch {
                                runCatching { container.patients.removeCaregiver(link.id) }
                                reload()
                                busy = false
                            }
                        },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
        }

        if (via == "self") {
            Spacer(Modifier.height(12.dp))
            Text(stringResource(R.string.family_add), style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = phone,
                onValueChange = { phone = it.filter { c -> c.isDigit() || c == '+' }.take(15) },
                label = { Text(stringResource(R.string.family_phone)) },
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp).testTag("family_phone"),
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text(stringResource(R.string.family_name)) },
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp),
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = relation,
                onValueChange = { relation = it },
                label = { Text(stringResource(R.string.family_relation)) },
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp),
            )
            Spacer(Modifier.height(16.dp))
            BigButton(
                text = stringResource(R.string.family_save),
                enabled = phone.length >= 8 && !busy,
                modifier = Modifier.testTag("family_save"),
                onClick = {
                    busy = true
                    scope.launch {
                        runCatching {
                            container.patients.addCaregiver(
                                phone,
                                name.ifBlank { null },
                                relation.ifBlank { null },
                            )
                        }
                        phone = ""; name = ""; relation = ""
                        reload()
                        busy = false
                    }
                },
            )
        } else {
            Muted(stringResource(R.string.family_caregiver_readonly, patient?.name ?: ""))
        }

        Spacer(Modifier.height(24.dp))
        BigButton(stringResource(R.string.close), onClick = onBack)
    }
}

/** The upcoming appointments, read-only — booking a new one stays with the
 *  receptionist and the coordinator until the app has earned it. */
@Composable
fun AppointmentsScreen(container: AppContainer, onBack: () -> Unit) {
    var appointments by remember { mutableStateOf<List<AppointmentOut>>(emptyList()) }

    LaunchedEffect(Unit) {
        appointments = runCatching { container.patients.appointments() }.getOrDefault(emptyList())
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
    ) {
        Text(stringResource(R.string.appointments_title), style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(16.dp))

        if (appointments.isEmpty()) Muted(stringResource(R.string.appointments_empty))

        appointments.forEach { appointment ->
            SectionCard {
                Text(appointment.slotAt.take(16).replace('T', ' '), style = MaterialTheme.typography.titleMedium)
                Muted(
                    listOfNotNull(appointment.doctorName, appointment.departmentName.ifBlank { null })
                        .joinToString(" · "),
                )
            }
            Spacer(Modifier.height(12.dp))
        }

        Spacer(Modifier.height(16.dp))
        BigButton(stringResource(R.string.close), onClick = onBack)
    }
}
