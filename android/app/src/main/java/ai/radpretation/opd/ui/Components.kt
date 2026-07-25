package ai.radpretation.opd.ui

import ai.radpretation.opd.R
import ai.radpretation.opd.ui.theme.Accent
import ai.radpretation.opd.ui.theme.AccentSoft
import ai.radpretation.opd.ui.theme.Danger
import ai.radpretation.opd.ui.theme.DangerSoft
import ai.radpretation.opd.ui.theme.Ink
import ai.radpretation.opd.ui.theme.InkSoft
import ai.radpretation.opd.ui.theme.Primary
import ai.radpretation.opd.ui.theme.PrimaryDark
import ai.radpretation.opd.ui.theme.PrimarySoft
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The app's own small vocabulary of shapes.
 *
 * Two deliberate aesthetic risks, both carried over from the kiosk so the
 * product reads as one thing (doc 04 §5): the **breathing Dhara avatar**, and
 * the **train-board token numeral**. Everything else is quiet: generous cards
 * on a mint background, one action per screen, ≥64dp targets (doc 04 law 3).
 */

/** doc 04 law 3: nothing a patient must hit is smaller than this. */
val TouchTarget = 64.dp

/**
 * Dhara, breathing.
 *
 * The pulse is the latency mask (doc 04 law 11 — "never a spinner alone"): it
 * speeds up while she is thinking, so waiting looks like attention rather than
 * a stall. Respects the system's reduced-motion setting by way of `animated`.
 */
@Composable
fun DharaAvatar(
    modifier: Modifier = Modifier,
    size: androidx.compose.ui.unit.Dp = 96.dp,
    thinking: Boolean = false,
    animated: Boolean = true,
) {
    val transition = rememberInfiniteTransition(label = "dhara")
    val scale by transition.animateFloat(
        initialValue = 1f,
        targetValue = if (animated) 1.06f else 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(if (thinking) 700 else 2200),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "breath",
    )
    Box(
        modifier = modifier
            .size(size)
            .scale(scale)
            .background(PrimarySoft, CircleShape)
            .border(3.dp, Primary, CircleShape)
            .semantics { contentDescription = "Dhara" },
        contentAlignment = Alignment.Center,
    ) {
        // Two arcs and a dot: a face that is friendly without being a cartoon,
        // and legible at 40dp in a corner.
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Row(horizontalArrangement = Arrangement.spacedBy(size * 0.14f)) {
                Box(Modifier.size(size * 0.10f).background(PrimaryDark, CircleShape))
                Box(Modifier.size(size * 0.10f).background(PrimaryDark, CircleShape))
            }
            Spacer(Modifier.height(size * 0.12f))
            Box(
                Modifier
                    .width(size * 0.34f)
                    .height(size * 0.10f)
                    .background(Accent, RoundedCornerShape(50)),
            )
        }
    }
}

/**
 * The token, as a train-platform board.
 *
 * The product's signature visual (doc 04 §1). On a phone it is the one thing on
 * the screen that is huge, because it is the one thing a patient shows to a
 * person at a desk.
 */
@Composable
fun TokenNumeral(token: Int, modifier: Modifier = Modifier, label: String? = null) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(PrimaryDark, RoundedCornerShape(22.dp))
            .padding(vertical = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        label?.let {
            Text(
                it,
                color = PrimarySoft,
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(4.dp))
        }
        Text(
            token.toString(),
            color = Accent,
            fontSize = 84.sp,
            fontWeight = FontWeight.ExtraBold,
        )
    }
}

/** The primary action. Marigold, full width, thumb-reachable (doc 04 §3). */
@Composable
fun BigButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().heightIn(min = TouchTarget),
        shape = RoundedCornerShape(18.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = Ink),
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, fontSize = 18.sp)
    }
}

/** The quieter twin: "not now", "change something", "listen again". */
@Composable
fun QuietButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().heightIn(min = TouchTarget),
        shape = RoundedCornerShape(18.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = Primary)
    }
}

/** A whole card that is tappable — not a radio button with a label (doc 04 law 3). */
@Composable
fun OptionCard(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    selected: Boolean = false,
) {
    Card(
        onClick = onClick,
        modifier = modifier.fillMaxWidth().heightIn(min = TouchTarget + 12.dp),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (selected) PrimarySoft else Color.White,
        ),
        border = if (selected) null else CardDefaults.outlinedCardBorder(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(20.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(text, style = MaterialTheme.typography.bodyLarge, color = Ink)
        }
    }
}

@Composable
fun SectionCard(
    modifier: Modifier = Modifier,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Column(Modifier.padding(20.dp), content = content)
    }
}

/**
 * The marigold downtime banner's phone cousin (doc 04 §3).
 *
 * Marigold, never red: being offline is a normal state of a phone in Alwar, not
 * an error the patient did something to cause.
 */
@Composable
fun OfflineBanner(modifier: Modifier = Modifier, text: String? = null) {
    Row(
        modifier
            .fillMaxWidth()
            .background(AccentSoft)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text ?: stringResource(R.string.offline_banner),
            style = MaterialTheme.typography.bodyMedium,
            color = Ink,
        )
    }
}

/** A red-flag or "ask the doctor" stamp — the one place danger colour is used. */
@Composable
fun WarningStamp(text: String, modifier: Modifier = Modifier) {
    Row(
        modifier
            .fillMaxWidth()
            .background(DangerSoft, RoundedCornerShape(14.dp))
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, style = MaterialTheme.typography.bodyMedium, color = Danger)
    }
}

@Composable
fun Muted(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier = modifier, style = MaterialTheme.typography.bodyMedium, color = InkSoft)
}
