package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.DeptOut
import ai.radpretation.opd.data.NodeOut
import ai.radpretation.opd.data.OfflineException
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.DharaAvatar
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OptionCard
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.TouchTarget
import ai.radpretation.opd.ui.WarningStamp
import ai.radpretation.opd.ui.rememberListener
import ai.radpretation.opd.ui.rememberSpeaker
import ai.radpretation.opd.ui.theme.Accent
import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * Talk to Dhara, from home (doc 03 §1c.2).
 *
 * One question per screen (doc 04 law 2), each spoken aloud on arrival, each
 * with taps *and* a microphone, and never a spoken-only path — because
 * on-device recognition of medical Marathi is the weakest link in this app and
 * doc 04 law 8 says an error must never blame the patient. A failed listen
 * reveals the taps and says "I could not hear that properly".
 *
 * Nothing about the tree lives here. The server hands back the next node; the
 * phone renders it. See `IntakeRepository` for why there is no Kotlin walker.
 */
@Composable
fun IntakeScreen(container: AppContainer, onDone: () -> Unit) {
    val patient by container.patients.cachedPatient.collectAsState(initial = null)
    val lang = patient?.lang ?: "hi"
    val speaker = rememberSpeaker(lang)
    val listener = rememberListener(lang)
    val scope = rememberCoroutineScope()

    var complaint by remember { mutableStateOf("") }
    var sessionId by remember { mutableStateOf<String?>(null) }
    var node by remember { mutableStateOf<NodeOut?>(null) }
    var departments by remember { mutableStateOf<List<DeptOut>>(emptyList()) }
    var readback by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var listening by remember { mutableStateOf(false) }
    var couldNotHear by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var answered by remember { mutableIntStateOf(0) }

    val offlineText = stringResource(R.string.intake_needs_network)
    val genericText = stringResource(R.string.error_generic)

    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (!granted) couldNotHear = true }
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    // Every question is read aloud the moment it appears — the app's version of
    // the kiosk's auto-play (doc 04 law 1).
    LaunchedEffect(node?.id) { node?.let { speaker.say(it.text) } }
    LaunchedEffect(readback) { readback?.let { speaker.say(it) } }

    fun run(block: suspend () -> Unit) {
        busy = true
        error = null
        scope.launch {
            try {
                block()
            } catch (_: OfflineException) {
                error = offlineText
            } catch (_: Exception) {
                error = genericText
            } finally {
                busy = false
            }
        }
    }

    fun answer(value: JsonElement?, rawText: String?) {
        val id = sessionId ?: return
        val currentNode = node ?: return
        run {
            val result = container.intake.answer(id, currentNode.id, value, rawText)
            when {
                result.clarify != null -> speaker.say(result.clarify!!)
                !result.ok -> couldNotHear = true
                result.complete -> {
                    node = null
                    readback = container.intake.finish(id).readback
                }
                else -> {
                    answered++
                    couldNotHear = false
                    node = result.node
                }
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            DharaAvatar(size = 56.dp, thinking = busy || listening)
            Text(
                stringResource(R.string.intake_title),
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(start = 14.dp),
            )
        }
        Spacer(Modifier.height(20.dp))

        when {
            // -- the token screen ---------------------------------------------
            done != null -> {
                SectionCard {
                    Text(
                        stringResource(R.string.intake_done),
                        style = MaterialTheme.typography.headlineMedium,
                        modifier = Modifier.testTag("intake_done"),
                    )
                    Spacer(Modifier.height(8.dp))
                    Muted(done!!)
                }
                Spacer(Modifier.height(20.dp))
                BigButton(stringResource(R.string.close), onClick = onDone)
            }

            // -- the read-back ------------------------------------------------
            readback != null -> {
                Text(stringResource(R.string.intake_readback), style = MaterialTheme.typography.headlineMedium)
                Spacer(Modifier.height(14.dp))
                SectionCard { Text(readback!!, style = MaterialTheme.typography.bodyLarge) }
                Spacer(Modifier.height(20.dp))
                BigButton(
                    text = stringResource(R.string.intake_confirm),
                    enabled = !busy,
                    modifier = Modifier.testTag("intake_confirm"),
                    onClick = {
                        val id = sessionId ?: return@BigButton
                        run { done = container.intake.confirm(id).message }
                    },
                )
                Spacer(Modifier.height(12.dp))
                QuietButton(
                    text = stringResource(R.string.intake_change),
                    onClick = { readback = null; run { node = container.intake.finish(sessionId!!).let { node } } },
                )
            }

            // -- the department chooser ---------------------------------------
            departments.isNotEmpty() -> {
                Text(stringResource(R.string.intake_choose_dept), style = MaterialTheme.typography.headlineMedium)
                Spacer(Modifier.height(16.dp))
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    departments.forEach { dept ->
                        OptionCard(dept.name, onClick = {
                            run {
                                val started = container.intake.start(lang, complaint, dept.key)
                                sessionId = started.sessionId
                                node = started.node
                                departments = emptyList()
                            }
                        })
                    }
                }
            }

            // -- a tree question ----------------------------------------------
            node != null -> {
                val current = node!!
                Muted(stringResource(R.string.intake_progress, answered + 1, answered + 4))
                Spacer(Modifier.height(10.dp))
                Text(
                    current.text,
                    style = MaterialTheme.typography.headlineMedium,
                    modifier = Modifier.testTag("intake_question"),
                )
                Spacer(Modifier.height(20.dp))

                if (couldNotHear) {
                    WarningStamp(stringResource(R.string.intake_no_hear))
                    Spacer(Modifier.height(16.dp))
                }

                when (current.type) {
                    "single" -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        current.options.forEach { option ->
                            OptionCard(
                                option.label,
                                onClick = { answer(JsonPrimitive(option.id), null) },
                                modifier = Modifier.testTag("option_${option.id}"),
                            )
                        }
                    }

                    "multi", "body_map" -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        current.options.forEach { option ->
                            OptionCard(
                                option.label,
                                onClick = { answer(JsonArray(listOf(JsonPrimitive(option.id))), null) },
                                modifier = Modifier.testTag("option_${option.id}"),
                            )
                        }
                    }

                    // doc 04 law 6: numbers get big steppers, never a slider.
                    "scale", "number" -> Stepper(
                        min = current.min?.toInt() ?: 0,
                        max = current.max?.toInt() ?: 10,
                        onPick = { answer(JsonPrimitive(it), null) },
                    )

                    else -> VoiceOrType(
                        listening = listening,
                        onListen = {
                            listening = true
                            couldNotHear = false
                            listener.start(
                                onResult = { text ->
                                    listening = false
                                    answer(null, text)
                                },
                                onError = { listening = false; couldNotHear = true },
                            )
                        },
                        onType = { answer(JsonPrimitive(it), it) },
                    )
                }
            }

            // -- Q1: the chief complaint, by voice ----------------------------
            else -> {
                Text(stringResource(R.string.intake_complaint), style = MaterialTheme.typography.headlineMedium)
                Spacer(Modifier.height(20.dp))
                VoiceOrType(
                    listening = listening,
                    initial = complaint,
                    onListen = {
                        listening = true
                        couldNotHear = false
                        listener.start(
                            onResult = { text ->
                                listening = false
                                complaint = text
                            },
                            onError = { listening = false; couldNotHear = true },
                        )
                    },
                    onType = { complaint = it },
                )
                if (couldNotHear) {
                    Spacer(Modifier.height(12.dp))
                    WarningStamp(stringResource(R.string.intake_no_hear))
                }
                Spacer(Modifier.height(20.dp))
                BigButton(
                    text = stringResource(R.string.onboard_next),
                    enabled = complaint.isNotBlank() && !busy,
                    modifier = Modifier.testTag("intake_begin"),
                    onClick = {
                        run {
                            val started = container.intake.start(lang, complaint, null)
                            if (started.status == "needs_department") {
                                departments = started.departments
                            } else {
                                sessionId = started.sessionId
                                node = started.node
                            }
                        }
                    },
                )
            }
        }

        error?.let {
            Spacer(Modifier.height(20.dp))
            WarningStamp(it, Modifier.testTag("intake_error"))
        }
    }
}

/** Hold-to-speak with a typed fallback always visible (doc 04 law 8). */
@Composable
private fun VoiceOrType(
    listening: Boolean,
    onListen: () -> Unit,
    onType: (String) -> Unit,
    initial: String = "",
) {
    var typed by remember { mutableStateOf(initial) }
    Column {
        BigButton(
            text = stringResource(
                if (listening) R.string.intake_listening else R.string.intake_speak,
            ),
            onClick = onListen,
            modifier = Modifier.testTag("intake_mic"),
        )
        Spacer(Modifier.height(16.dp))
        Muted(stringResource(R.string.intake_type))
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = typed,
            onValueChange = { typed = it; onType(it) },
            modifier = Modifier.fillMaxWidth().heightIn(min = TouchTarget).testTag("intake_text"),
        )
    }
}

/** doc 04 law 6's stepper: whole numbers, spoken units, no dragging. */
@Composable
private fun Stepper(min: Int, max: Int, onPick: (Int) -> Unit) {
    var value by remember { mutableIntStateOf(min) }
    Column {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            QuietButton("−", onClick = { if (value > min) value-- }, modifier = Modifier.weight(1f))
            Text(
                value.toString(),
                style = MaterialTheme.typography.displayLarge,
                color = Accent,
                modifier = Modifier.weight(1f).testTag("stepper_value"),
            )
            QuietButton("+", onClick = { if (value < max) value++ }, modifier = Modifier.weight(1f))
        }
        Spacer(Modifier.height(20.dp))
        BigButton(
            text = stringResource(R.string.onboard_next),
            onClick = { onPick(value) },
            modifier = Modifier.testTag("stepper_next"),
        )
    }
}
