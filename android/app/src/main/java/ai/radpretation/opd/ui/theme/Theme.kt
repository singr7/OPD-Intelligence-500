package ai.radpretation.opd.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * doc 04 §1's tokens, verbatim. Not "inspired by" — the same hex values the
 * kiosk, the board and the consoles use, so a patient who sees the kiosk at the
 * hospital and the app at home is looking at one system.
 */
val Primary = Color(0xFF0E7C66)
val PrimaryDark = Color(0xFF0A5A4A)
val PrimarySoft = Color(0xFFE1F0EB)
val Accent = Color(0xFFE2901F)
val AccentSoft = Color(0xFFFBEBCF)
val Danger = Color(0xFFC73E3E)
val DangerSoft = Color(0xFFFBE7E7)
val Ink = Color(0xFF16302B)
val InkSoft = Color(0xFF5C6E69)
val Bg = Color(0xFFF1F5F3)
val Surface = Color(0xFFFFFFFF)
val Line = Color(0xFFD9E4DF)

/**
 * One scheme, light only.
 *
 * doc 04 §4 asks for legibility "in bright OPD light" and at 200% font scale;
 * a dark theme is a second palette to keep accessible and a second set of
 * screenshots to critique, for a surface whose users mostly hold the phone
 * outdoors. Deliberately deferred rather than half-done.
 */
private val OpdColors = lightColorScheme(
    primary = Primary,
    onPrimary = Color.White,
    primaryContainer = PrimarySoft,
    onPrimaryContainer = PrimaryDark,
    secondary = Accent,
    onSecondary = Color.White,
    secondaryContainer = AccentSoft,
    onSecondaryContainer = Ink,
    error = Danger,
    onError = Color.White,
    errorContainer = DangerSoft,
    onErrorContainer = Ink,
    background = Bg,
    onBackground = Ink,
    surface = Surface,
    onSurface = Ink,
    surfaceVariant = PrimarySoft,
    onSurfaceVariant = InkSoft,
    outline = Line,
)

/** doc 04 §1: `--radius: 22px`. Cards are round enough to read as cards, not panels. */
private val OpdShapes = Shapes(
    small = RoundedCornerShape(14.dp),
    medium = RoundedCornerShape(22.dp),
    large = RoundedCornerShape(22.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

/**
 * Sizes are generous and line heights are ≥1.5 everywhere Indic script can land
 * (doc 04 §4 asks ≥1.6 for Devanagari/Telugu; body styles here are 1.6).
 *
 * The font is the system's, not a bundled Noto: Android's own font stack already
 * ships Devanagari and Telugu on every device this app supports, and shipping
 * three more font files would cost ~4MB of the 15MB budget to render the same
 * glyphs (doc 03 §1c.7 beats doc 04 §1's self-hosting note, which is written
 * for the web surfaces that have no system font to fall back on).
 */
private val OpdTypography = Typography(
    displayLarge = TextStyle(fontSize = 56.sp, lineHeight = 64.sp, fontWeight = FontWeight.ExtraBold),
    headlineLarge = TextStyle(fontSize = 30.sp, lineHeight = 40.sp, fontWeight = FontWeight.Bold),
    headlineMedium = TextStyle(fontSize = 24.sp, lineHeight = 34.sp, fontWeight = FontWeight.Bold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 30.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 18.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 18.sp, lineHeight = 29.sp),
    bodyMedium = TextStyle(fontSize = 16.sp, lineHeight = 26.sp),
    labelLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
)

@Composable
fun OpdTheme(content: @Composable () -> Unit) {
    @Suppress("UNUSED_EXPRESSION")
    isSystemInDarkTheme() // read, then ignored on purpose — see OpdColors.
    MaterialTheme(
        colorScheme = OpdColors,
        shapes = OpdShapes,
        typography = OpdTypography,
        content = content,
    )
}
