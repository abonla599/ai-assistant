package xyz.fenever.assistant.nativeapp.theme

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/* 视觉方向：墨黑 × 暖纸 × 琥珀——"深夜写信"的纸感界面。
 * 琥珀只在主操作与用户气泡发声；标题使用重字重 + 紧字距构成识别度，
 * 背景用径向光晕替代纯色，拒绝 AI 味的白底紫渐变。 */
object AiColors {
    val Ink = Color(0xFF17140F)
    val Paper = Color(0xFFFAF6EE)
    val Amber = Color(0xFFD96A0B)
    val AmberDeep = Color(0xFF9E4C00)
    val AmberSoft = Color(0xFFF5C77F)
    val Teal = Color(0xFF0F6E5D)
    val TealSoft = Color(0xFF8FD5C4)
    val Danger = Color(0xFFB3261E)
}

private val LightScheme = lightColorScheme(
    primary = AiColors.Amber,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFE7CC),
    onPrimaryContainer = Color(0xFF4E2400),
    secondary = AiColors.Teal,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFD8EFE8),
    background = AiColors.Paper,
    onBackground = AiColors.Ink,
    surface = Color(0xFFFFFDF8),
    onSurface = AiColors.Ink,
    surfaceVariant = Color(0xFFF0E9DA),
    onSurfaceVariant = Color(0xFF5C554A),
    error = AiColors.Danger,
    onError = Color.White,
    errorContainer = Color(0xFFFDE3E0),
    outline = Color(0xFFD8CFBC),
    outlineVariant = Color(0xFFE9E1D1),
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFFFFA54C),
    onPrimary = AiColors.Ink,
    primaryContainer = Color(0xFF6B3300),
    onPrimaryContainer = Color(0xFFFFDDBF),
    secondary = AiColors.TealSoft,
    background = Color(0xFF141210),
    onBackground = Color(0xFFEDE6D8),
    surface = Color(0xFF1E1B17),
    onSurface = Color(0xFFEDE6D8),
    surfaceVariant = Color(0xFF2A2620),
    onSurfaceVariant = Color(0xFFB4AB99),
    error = Color(0xFFFF8A80),
    errorContainer = Color(0xFF5C1A15),
    outline = Color(0xFF4A443B),
    outlineVariant = Color(0xFF332E27),
)

// 圆角刻度：气泡与卡片都走"信纸折角"的大圆角，锐角只出现在代码块。
private val AiShapeTokens = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(28.dp),
    extraLarge = RoundedCornerShape(40.dp),
)

private val AiTypography = Typography(
    headlineMedium = TextStyle(
        fontWeight = FontWeight.Black, letterSpacing = (-0.5).sp, fontSize = 28.sp),
    headlineSmall = TextStyle(
        fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.3).sp, fontSize = 22.sp),
    titleLarge = TextStyle(fontWeight = FontWeight.Bold, letterSpacing = 0.sp, fontSize = 20.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
    bodyLarge = TextStyle(fontWeight = FontWeight.Normal, fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontWeight = FontWeight.Normal, fontSize = 12.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.Bold, letterSpacing = 0.5.sp, fontSize = 14.sp),
    labelSmall = TextStyle(fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp, fontSize = 11.sp),
)

@Composable
fun AiTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkScheme else LightScheme,
        shapes = AiShapeTokens,
        typography = AiTypography,
        content = content,
    )
}

/* 品牌主渐变：登录按钮、发送键、FAB 共用，保证"琥珀只在一处发声"的连续感。 */
@Composable
fun aiPrimaryBrush(): Brush = Brush.linearGradient(
    listOf(AiColors.AmberSoft, MaterialTheme.colorScheme.primary, AiColors.AmberDeep))

/* 氛围背景：两枚径向光晕（琥珀 + 青）叠在纸色上；登录/列表共用。 */
@Composable
fun AiGlowBackground(content: @Composable BoxScope.() -> Unit) {
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Canvas(Modifier.fillMaxSize()) {
            drawCircle(
                Brush.radialGradient(
                    listOf(AiColors.Amber.copy(alpha = 0.30f), Color.Transparent),
                    center = Offset(size.width * 0.88f, size.height * 0.10f),
                    radius = size.width * 0.62f),
                radius = size.width * 0.62f,
                center = Offset(size.width * 0.88f, size.height * 0.10f))
            drawCircle(
                Brush.radialGradient(
                    listOf(AiColors.Teal.copy(alpha = 0.18f), Color.Transparent),
                    center = Offset(size.width * 0.06f, size.height * 0.80f),
                    radius = size.width * 0.55f),
                radius = size.width * 0.55f,
                center = Offset(size.width * 0.06f, size.height * 0.80f))
        }
        content()
    }
}

/* 品牌记号：墨色方块里一枚琥珀圆点，签名式元素。 */
@Composable
fun AiBrandMark(sizeDp: Int = 44) {
    Box(
        Modifier
            .size(sizeDp.dp)
            .background(AiColors.Ink, RoundedCornerShape((sizeDp / 3).dp)),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size((sizeDp * 0.62f).dp)) {
            drawCircle(Brush.linearGradient(listOf(AiColors.AmberSoft, AiColors.AmberDeep)))
        }
    }
}

/* 等宽段（代码块）统一从这取，保持聊天与后续页面一致。 */
val AiMono = FontFamily.Monospace
