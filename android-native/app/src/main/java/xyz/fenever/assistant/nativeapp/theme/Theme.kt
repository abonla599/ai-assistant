package xyz.fenever.assistant.nativeapp.theme

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
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

/* 视觉不另起炉灶：逐 token 照抄网页版 style.css 的设计语言——
 * 深蓝黑底、薄荷绿(--accent) × 紫罗兰蓝(--accent-2) 双强调色主渐变、
 * 用户气泡是深青→靛紫渐变、助手气泡是弱表面 + 细描边。
 * 默认走深主题（网页 :root 即深色）；浅色映射 [data-theme="light"] 一并带全。 */
object WebTokens {
    // ---- dark（网页 :root）----
    val Bg = Color(0xFF0B0E15)
    val BgSoft = Color(0xFF10141D)
    val BgHover = Color(0xFF191E2A)
    val Surface = Color(0xFF141824)
    val Text = Color(0xFFE6E9ED)
    val Text2 = Color(0xFF98A1AD)
    val Text3 = Color(0xFF6A7481)
    val Line = Color(0xFF242A3A)
    val Accent = Color(0xFF2FBF8F)
    val AccentSoft = Color(0x242FBF8F)      // rgba(47,191,143,.14)
    val Accent2 = Color(0xFF6C7BFF)
    val Accent2Soft = Color(0x296C7BFF)     // rgba(108,123,255,.16)
    val UserBubble = Color(0xFF1D3A49)
    val UserBubble2 = Color(0xFF2B2F58)
    val Danger = Color(0xFFF87171)
    val CodeBg = Color(0xFF080A11)
    val AuthBand = Color(0xFF06080F)
    val AuthPanel = Color(0xFF151A2B)
    val BtnPrimaryInk = Color(0xFF062518)   // 渐变主按钮上的深字

    // ---- light（网页 [data-theme="light"]）----
    val LAccent = Color(0xFF0A7D5E)
    val LAccentSoft = Color(0xFFE6F6F0)
    val LAccent2 = Color(0xFF5566F0)
    val LAccent2Soft = Color(0xFFE9ECFD)
    val LBg = Color(0xFFFFFFFF)
    val LBgSoft = Color(0xFFF6F7FB)
    val LBgHover = Color(0xFFECEEF4)
    val LSurface = Color(0xFFFFFFFF)
    val LText = Color(0xFF17191F)
    val LText2 = Color(0xFF5B6472)
    val LLine = Color(0xFFE4E7EE)
    val LUserBubble = Color(0xFFE0F5EE)
    val LUserBubble2 = Color(0xFFE6E9FC)
    val LDanger = Color(0xFFDC2626)
    val LAuthBand = Color(0xFFDFE3F2)
    val LAuthPanel = Color(0xFFFFFFFF)
}

private val DarkScheme = darkColorScheme(
    primary = WebTokens.Accent,
    onPrimary = WebTokens.BtnPrimaryInk,
    primaryContainer = Color(0xFF173B31),
    onPrimaryContainer = WebTokens.Accent,
    secondary = WebTokens.Accent2,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFF232A4D),
    background = WebTokens.Bg,
    onBackground = WebTokens.Text,
    surface = WebTokens.Surface,
    onSurface = WebTokens.Text,
    surfaceVariant = WebTokens.BgSoft,
    onSurfaceVariant = WebTokens.Text2,
    error = WebTokens.Danger,
    onError = Color(0xFF2B0F0F),
    errorContainer = Color(0xFF3A1D20),
    onErrorContainer = WebTokens.Text,
    outline = WebTokens.Line,
    outlineVariant = Color(0xFF1C2230),
)

private val LightScheme = lightColorScheme(
    primary = WebTokens.LAccent,
    onPrimary = Color.White,        // 浅主题主按钮是白字（网页同款规则）
    primaryContainer = WebTokens.LAccentSoft,
    onPrimaryContainer = WebTokens.LAccent,
    secondary = WebTokens.LAccent2,
    background = WebTokens.LBg,
    onBackground = WebTokens.LText,
    surface = WebTokens.LSurface,
    onSurface = WebTokens.LText,
    surfaceVariant = WebTokens.LBgSoft,
    onSurfaceVariant = WebTokens.LText2,
    error = WebTokens.LDanger,
    outline = WebTokens.LLine,
    outlineVariant = WebTokens.LLine,
)

// 网页 --radius: 18px 是主刻度；卡片 18、输入 12~14、气泡见各屏
private val WebShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

// 网页正文 15px/1.7；标题 600 字重、无花哨字距——克制本身就是这套设计的识别度
private val WebTypography = Typography(
    headlineSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 17.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
    bodyLarge = TextStyle(fontWeight = FontWeight.Normal, fontSize = 16.sp, lineHeight = 26.sp),
    bodyMedium = TextStyle(fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 25.sp),
    bodySmall = TextStyle(fontWeight = FontWeight.Normal, fontSize = 12.5.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
    labelMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 13.sp),
    labelSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 11.sp),
)

/* 外观三态（system/dark/light）：设置页「外观」⇅ 就地切换。
 * 用 Compose state 而不是只读 Prefs——改一下整棵树立刻换肤，不重启 Activity。 */
object ThemeMode {
    var value: String by androidx.compose.runtime.mutableStateOf("system")
}

@Composable
fun AiTheme(content: @Composable () -> Unit) {
    val dark = when (ThemeMode.value) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    MaterialTheme(
        colorScheme = if (dark) DarkScheme else LightScheme,
        shapes = WebShapes,
        typography = WebTypography,
        content = content,
    )
}

/* 主渐变 accent→accent-2：主按钮、发送键、品牌字标共用（网页 .btn-primary /
 * .brand clip-text 同款）。 */
@Composable
fun aiPrimaryBrush(): Brush {
    val scheme = MaterialTheme.colorScheme
    return Brush.linearGradient(
        if (scheme.background == WebTokens.LBg)
            listOf(WebTokens.LAccent, WebTokens.LAccent2)
        else listOf(WebTokens.Accent, WebTokens.Accent2))
}

/* 用户气泡渐变：深青→靛紫（网页 .msg.user .msg-body 135° 双色渐变）。 */
@Composable
fun userBubbleBrush(): Brush {
    val scheme = MaterialTheme.colorScheme
    return Brush.linearGradient(
        if (scheme.background == WebTokens.LBg)
            listOf(WebTokens.LUserBubble, WebTokens.LUserBubble2)
        else listOf(WebTokens.UserBubble, WebTokens.UserBubble2))
}

/* 氛围光与网页 body 一致：右上紫罗兰、左下薄荷，很淡，不随内容滚动。 */
@Composable
fun AiGlowBackground(content: @Composable BoxScope.() -> Unit) {
    val scheme = MaterialTheme.colorScheme
    val light = scheme.background == WebTokens.LBg
    val glow2 = if (light) WebTokens.LAccent2Soft else WebTokens.Accent2Soft
    val glowA = if (light) WebTokens.LAccentSoft else WebTokens.AccentSoft
    Box(Modifier.fillMaxSize().background(scheme.background)) {
        Canvas(Modifier.fillMaxSize()) {
            drawCircle(
                Brush.radialGradient(listOf(glow2, Color.Transparent),
                    center = Offset(size.width * 0.88f, -size.height * 0.12f),
                    radius = size.width * 0.72f),
                radius = size.width * 0.72f,
                center = Offset(size.width * 0.88f, -size.height * 0.12f))
            drawCircle(
                Brush.radialGradient(listOf(glowA, Color.Transparent),
                    center = Offset(-size.width * 0.12f, size.height * 1.08f),
                    radius = size.width * 0.66f),
                radius = size.width * 0.66f,
                center = Offset(-size.width * 0.12f, size.height * 1.08f))
        }
        content()
    }
}

/* 品牌记号：网页用的是 icon.png 那枚渐变圆角方块，原生端同形制重现。 */
@Composable
fun AiBrandMark(sizeDp: Int = 44) {
    Box(
        Modifier.size(sizeDp.dp)
            .background(aiPrimaryBrush(), RoundedCornerShape((sizeDp / 3).dp)),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size((sizeDp * 0.52f).dp)) {
            drawCircle(Color.White.copy(alpha = 0.92f))
        }
    }
}

/* 等宽段（代码块）统一从这取。 */
val AiMono = FontFamily.Monospace
