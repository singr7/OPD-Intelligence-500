package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.QueuePositionOut
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OfflineBanner
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.TokenNumeral
import ai.radpretation.opd.ui.theme.Accent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * "You are 7th; leave home by 10:30" (doc 03 §1c.3).
 *
 * The one screen that refuses to lie when offline. A queue position twenty
 * minutes old will send someone out of the door at the wrong time, so a stale
 * reading is shown *with the time it was taken* and never as the live answer.
 *
 * It polls while it is on screen — a websocket would be tidier, but a phone on
 * 2G holding a socket open in a waiting room costs battery the patient may need
 * for the journey home.
 */
@Composable
fun QueueScreen(container: AppContainer) {
    var position by remember { mutableStateOf<QueuePositionOut?>(null) }
    var stale by remember { mutableStateOf(false) }
    var checkedAt by remember { mutableStateOf<Long?>(null) }
    var travel by remember { mutableIntStateOf(45) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        try {
            position = container.patients.queuePosition(travel)
            checkedAt = System.currentTimeMillis()
            stale = false
        } catch (_: Exception) {
            if (position == null) position = container.patients.lastKnownQueue()
            stale = true
        }
    }

    LaunchedEffect(Unit) { travel = container.patients.travelMinutes() }

    LaunchedEffect(travel) {
        load()
        while (true) {
            delay(45_000)
            load()
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
    ) {
        if (stale) OfflineBanner()

        Text(stringResource(R.string.queue_title), style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(20.dp))

        val current = position
        if (current == null || !current.inQueue) {
            Muted(stringResource(R.string.queue_none))
            return@Column
        }

        current.tokenNo?.let {
            TokenNumeral(it, label = current.department, modifier = Modifier.testTag("queue_token"))
        }
        Spacer(Modifier.height(20.dp))

        SectionCard {
            val ahead = current.ahead ?: 0
            Text(
                if (ahead == 0) {
                    stringResource(R.string.queue_ahead_none)
                } else {
                    stringResource(R.string.queue_ahead, ahead)
                },
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.testTag("queue_ahead"),
            )
            Spacer(Modifier.height(8.dp))
            if (current.estWaitLow != null && current.estWaitHigh != null) {
                Muted(stringResource(R.string.queue_wait, current.estWaitLow, current.estWaitHigh))
            }
            current.nowServing?.let {
                Spacer(Modifier.height(8.dp))
                Muted(stringResource(R.string.queue_now_serving, it))
            }
        }

        // The leave-by line is the point of the screen: everything above it is
        // context for this one instruction.
        current.leaveBy?.let { iso ->
            Spacer(Modifier.height(16.dp))
            SectionCard {
                Text(
                    stringResource(R.string.queue_leave_by, formatClock(iso)),
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                    color = Accent,
                    modifier = Modifier.testTag("queue_leave_by"),
                )
            }
        }

        checkedAt?.let {
            Spacer(Modifier.height(12.dp))
            Muted(stringResource(R.string.queue_as_of, clockOf(it)))
        }

        Spacer(Modifier.height(24.dp))
        Text(stringResource(R.string.queue_travel, travel), style = MaterialTheme.typography.titleMedium)
        Slider(
            value = travel.toFloat(),
            onValueChange = { travel = it.toInt() },
            onValueChangeFinished = { scope.launch { container.patients.setTravelMinutes(travel) } },
            valueRange = 0f..180f,
            steps = 11,
            modifier = Modifier.testTag("queue_travel"),
        )

        Spacer(Modifier.height(16.dp))
        BigButton(
            text = stringResource(R.string.retry),
            onClick = { scope.launch { load() } },
        )
    }
}

private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")

private fun formatClock(iso: String): String = runCatching {
    Instant.parse(iso).atZone(ZoneId.systemDefault()).format(CLOCK)
}.getOrElse { iso.takeLast(8).take(5) }

private fun clockOf(epochMillis: Long): String =
    Instant.ofEpochMilli(epochMillis).atZone(ZoneId.systemDefault()).format(CLOCK)
