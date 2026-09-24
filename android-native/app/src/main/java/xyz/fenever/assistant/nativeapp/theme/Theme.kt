package xyz.fenever.assistant.nativeapp.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/* 视觉方向：墨黑 × 暖纸 × 琥珀强调——延续网页版"信纸"气质，但不照抄。
 * 主色只用琥珀一处发声，语义色（成功/错误）保持克制。 */
private val Ink = Color(0xFF17140F)
private val Paper = Color(0xFFFAF6EE)
private val Card = Color(0xFFFFFDF8)
private val Amber = Color(0xFFD96A0B)
private val AmberDeep = Color(0xFF9E4C00)
private val Teal = Color(0xFF0F6E5D)
private val Danger = Color(0xFFB3261E)

private val LightScheme = lightColorScheme(
    primary = Amber,
    onPrimary = Color.White,
    secondary = Teal,
    onSecondary = Color.White,
    background = Paper,
    onBackground = Ink,
    surface = Card,
    onSurface = Ink,
    surfaceVariant = Color(0xFFF0E9DA),
    onSurfaceVariant = Color(0xFF5C554A),
    error = Danger,
    onError = Color.White,
    outline = Color(0xFFD8CFBC),
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFFFFA54C),
    onPrimary = Ink,
    secondary = Color(0xFF6FC7B4),
    background = Color(0xFF141210),
    onBackground = Color(0xFFEDE6D8),
    surface = Color(0xFF1E1B17),
    onSurface = Color(0xFFEDE6D8),
    surfaceVariant = Color(0xFF2A2620),
    onSurfaceVariant = Color(0xFFB4AB99),
    error = Color(0xFFFF8A80),
    outline = Color(0xFF4A443B),
)

@Composable
fun AiTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkScheme else LightScheme,
        typography = Typography(),
        content = content,
    )
}
