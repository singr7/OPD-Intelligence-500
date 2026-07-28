package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.ArriveOut
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.DharaAvatar
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OptionCard
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.TokenNumeral
import ai.radpretation.opd.ui.theme.AccentSoft
import ai.radpretation.opd.ui.theme.Ink
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * Home is a hub, not a dashboard.
 *
 * Its single job: get the patient to the one thing she opened the app for. That
 * is why the voice action is the largest element on the screen and the rest are
 * plain rows — and why "I have arrived" appears at all, since on the morning of
 * a visit it is the only thing that matters (doc 04 §5: three elements, in
 * order — Dhara + greeting, talk, arrive).
 */
@Composable
fun HomeScreen(
    container: AppContainer,
    onTalk: () -> Unit,
    onCalendar: () -> Unit,
    onFamily: () -> Unit,
    onAppointments: () -> Unit,
    onQueue: () -> Unit,
    onEnvironment: () -> Unit,
) {
    val name by container.tokens.name.collectAsState(initial = null)
    val via by container.tokens.via.collectAsState(initial = "self")
    val patient by container.patients.cachedPatient.collectAsState(initial = null)
    var arrival by remember { mutableStateOf<ArriveOut?>(null) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val errorText = stringResource(R.string.error_generic)

    LaunchedEffect(Unit) {
        // Best effort — an empty or stale file is still a file. Home never
        // blocks on the network.
        runCatching { container.patients.refreshFile() }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            DharaAvatar(size = 64.dp)
            Spacer(Modifier.height(0.dp))
            Column(Modifier.padding(start = 16.dp)) {
                Text(
                    stringResource(R.string.home_greeting, name ?: patient?.name ?: ""),
                    style = MaterialTheme.typography.headlineMedium,
                )
                patient?.hospital?.let { Muted(it) }
            }
        }

        if (via == "caregiver") {
            Spacer(Modifier.height(16.dp))
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(AccentSoft, RoundedCornerShape(14.dp))
                    .padding(16.dp),
            ) {
                Text(
                    stringResource(R.string.home_caregiver_banner, patient?.name ?: ""),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Ink,
                )
            }
        }

        Spacer(Modifier.height(24.dp))

        arrival?.let {
            TokenNumeral(it.tokenNo, label = it.department)
            Spacer(Modifier.height(8.dp))
            Muted(stringResource(R.string.queue_arrived_done, it.tokenNo))
            Spacer(Modifier.height(20.dp))
        }

        // The voice action, deliberately the biggest thing here — doc 04 §3
        // wants the voice button persistent, and on a phone the honest way to
        // make it persistent is to make it unmissable on the first screen.
        SectionCard(Modifier.testTag("home_talk_card")) {
            Text(stringResource(R.string.home_talk), style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(6.dp))
            Muted(stringResource(R.string.home_talk_sub))
            Spacer(Modifier.height(16.dp))
            BigButton(
                text = stringResource(R.string.intake_speak),
                onClick = onTalk,
                modifier = Modifier.testTag("home_talk"),
            )
        }

        Spacer(Modifier.height(16.dp))

        // Quiet, not marigold. Two full-width marigold buttons stacked read as
        // two primary actions, and doc 04 §3 gives a screen one — the voice
        // action, which is why anyone opens this app the evening before. Arrival
        // is the *next* morning's tap and can afford to be the calmer one.
        QuietButton(
            text = stringResource(R.string.home_arrived),
            enabled = !busy,
            onClick = {
                busy = true
                scope.launch {
                    try {
                        arrival = container.patients.arrive()
                        message = null
                    } catch (e: Exception) {
                        message = errorText
                    } finally {
                        busy = false
                    }
                }
            },
            modifier = Modifier.testTag("home_arrive"),
        )

        message?.let {
            Spacer(Modifier.height(12.dp))
            Muted(it)
        }

        Spacer(Modifier.height(24.dp))
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OptionCard(stringResource(R.string.home_calendar), onClick = onCalendar)
            OptionCard(stringResource(R.string.home_appointments), onClick = onAppointments)
            OptionCard(stringResource(R.string.home_family), onClick = onFamily)
            OptionCard(stringResource(R.string.tab_queue), onClick = onQueue)
        }

        Spacer(Modifier.height(28.dp))
        QuietButton(
            text = stringResource(R.string.home_signout),
            onClick = { scope.launch { container.auth.signOut() } },
        )
        Spacer(Modifier.height(12.dp))
        QuietButton(
            text = stringResource(R.string.environment_open),
            onClick = onEnvironment,
            modifier = Modifier.testTag("environment_open_signed_in"),
        )
        Spacer(Modifier.height(24.dp))
    }
}
