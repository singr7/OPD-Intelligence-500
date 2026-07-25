package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.R
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.DharaAvatar
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.rememberSpeaker
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ai.radpretation.opd.ui.theme.Accent
import ai.radpretation.opd.ui.theme.Line
import java.util.Locale

/**
 * Three spoken screens (doc 04 §3), one idea each.
 *
 * Each one auto-plays its own words the moment it appears (doc 04 law 1: audio
 * is the primary channel, text is the caption) and offers "listen again". A
 * patient who cannot read finishes onboarding having heard everything.
 *
 * Order is the order of the promises in doc 03 §1c: your papers, your questions
 * from home, your medicines.
 */
@Composable
fun OnboardingScreen(onDone: () -> Unit) {
    var page by remember { mutableIntStateOf(0) }
    // Before sign-in there is no patient record to read a language from, so the
    // phone's own language decides — which for this pilot's users is the right
    // guess far more often than English would be.
    val lang = remember { Locale.getDefault().language.takeIf { it in setOf("hi", "mr", "te") } ?: "en" }
    val speaker = rememberSpeaker(lang)

    val pages = listOf(
        Triple(R.string.onboard_1_title, R.string.onboard_1_body, 0),
        Triple(R.string.onboard_2_title, R.string.onboard_2_body, 1),
        Triple(R.string.onboard_3_title, R.string.onboard_3_body, 2),
    )
    val (titleRes, bodyRes, _) = pages[page]
    val title = stringResource(titleRes)
    val body = stringResource(bodyRes)

    LaunchedEffect(page) { speaker.say("$title. $body") }

    Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp, vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        // The idea sits in the optical centre, not pinned to the top: a phone
        // held one-handed puts the middle of the screen where the eye lands,
        // and three sentences floating above a void reads as a page that failed
        // to load.
        Column(
            Modifier.weight(1f),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            DharaAvatar(size = 128.dp)
            Spacer(Modifier.height(32.dp))
            Text(
                title,
                style = MaterialTheme.typography.headlineLarge,
                textAlign = TextAlign.Center,
                modifier = Modifier.testTag("onboard_title"),
            )
            Spacer(Modifier.height(16.dp))
            Text(
                body,
                style = MaterialTheme.typography.bodyLarge,
                textAlign = TextAlign.Center,
            )
        }

        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
            Dots(page, pages.size)
            Spacer(Modifier.height(20.dp))
            QuietButton(stringResource(R.string.onboard_replay), onClick = { speaker.say("$title. $body") })
            Spacer(Modifier.height(12.dp))
            BigButton(
                text = stringResource(
                    if (page == pages.lastIndex) R.string.onboard_start else R.string.onboard_next,
                ),
                onClick = {
                    speaker.stop()
                    if (page == pages.lastIndex) onDone() else page++
                },
                modifier = Modifier.testTag("onboard_next"),
            )
            Spacer(Modifier.height(12.dp))
            Muted(stringResource(R.string.signin_trust))
        }
    }
}

/** doc 04 law 2's progress dots — the same idiom as the kiosk. */
@Composable
private fun Dots(current: Int, total: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(total) { index ->
            Box(
                Modifier
                    .size(if (index == current) 14.dp else 10.dp)
                    .background(if (index == current) Accent else Line, CircleShape),
            )
        }
    }
}
