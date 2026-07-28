package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.EnvironmentProbe
import ai.radpretation.opd.data.EnvironmentProfile
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OptionCard
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.WarningStamp
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
 * One job: prove and select the server before patient credentials can reach it.
 *
 * Reading order: current identity, two allow-listed endpoints, explicit switch
 * confirmation. The URL is intentionally visible; it is not a secret.
 */
@Composable
fun EnvironmentScreen(container: AppContainer, onClose: () -> Unit) {
    val current by container.environments.selected.collectAsState(
        initial = container.environments.allowList.default,
    )
    var selected by remember { mutableStateOf<EnvironmentProfile?>(null) }
    var probe by remember { mutableStateOf<EnvironmentProbe?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(stringResource(R.string.environment_title), style = MaterialTheme.typography.headlineMedium)
        Muted(stringResource(R.string.environment_help))
        SectionCard(Modifier.testTag("environment_current")) {
            Text(stringResource(R.string.environment_current), style = MaterialTheme.typography.labelLarge)
            Text(current.name, style = MaterialTheme.typography.titleLarge)
            Muted(current.apiBase)
        }

        container.environments.allowList.profiles.forEach { profile ->
            OptionCard(
                text = "${profile.name}\n${profile.apiBase}",
                selected = selected?.id == profile.id,
                onClick = {
                    selected = profile
                    probe = null
                    error = null
                },
                modifier = Modifier.testTag("environment_${profile.id}"),
            )
        }

        selected?.let { profile ->
            BigButton(
                text = if (busy) stringResource(R.string.environment_testing)
                else stringResource(R.string.environment_test),
                enabled = !busy,
                onClick = {
                    busy = true
                    error = null
                    scope.launch {
                        runCatching { container.pairing.probe(profile) }
                            .onSuccess { probe = it }
                            .onFailure { error = it.message ?: "unreachable" }
                        busy = false
                    }
                },
            )
        }

        probe?.let { checked ->
            SectionCard(Modifier.testTag("environment_probe_ok")) {
                Text(stringResource(R.string.environment_ready), color = MaterialTheme.colorScheme.primary)
                Muted("${checked.identity.humanName} · ${checked.identity.releaseSha}")
                Muted("${checked.identity.apiContractVersion} · ±${checked.clockSkewSeconds}s")
            }
            if (checked.profile.id != current.id) {
                BigButton(
                    text = stringResource(R.string.environment_switch),
                    onClick = { selected = checked.profile },
                    modifier = Modifier.testTag("environment_switch"),
                )
            }
        }
        error?.let { WarningStamp(stringResource(R.string.environment_failed), Modifier.testTag("environment_error")) }
        Spacer(Modifier.height(4.dp))
        QuietButton(stringResource(R.string.close), onClick = onClose)
    }

    val confirmed = probe?.takeIf { selected?.id == it.profile.id && it.profile.id != current.id }
    if (confirmed != null) {
        AlertDialog(
            onDismissRequest = { selected = null },
            title = { Text(stringResource(R.string.environment_confirm_title)) },
            text = { Text(stringResource(R.string.environment_confirm_body, confirmed.profile.name)) },
            confirmButton = {
                BigButton(
                    text = stringResource(R.string.environment_confirm),
                    onClick = {
                        scope.launch {
                            busy = true
                            runCatching { container.pairing.confirmSwitch(confirmed) }
                                .onSuccess { onClose() }
                                .onFailure { error = it.message }
                            busy = false
                        }
                    },
                )
            },
            dismissButton = {
                QuietButton(stringResource(R.string.cancel), onClick = { selected = null })
            },
        )
    }
}
