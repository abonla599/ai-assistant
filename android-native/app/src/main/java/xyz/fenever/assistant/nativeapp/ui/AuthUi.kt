package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.withStyle
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.shape.GenericShape
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.WebTokens
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

/* 找回三题是全站常量，与后端 app/core/auth.py 的 RECOVERY_QUESTIONS 逐字一致
 * （Web 侧有 test_web_pwa 锁这条对齐；原生侧照抄同一份，服务器不代为下发）。 */
val RECOVERY_QUESTIONS = listOf(
    "你的手机号后四位是什么？",
    "你小学在哪上？",
    "你父母姓氏的拼音首字母各一个是什么？",
)

/* 网页 .auth-sheet 那道拱形顶边：border-radius: 50% 50% 0 0 / 30px 30px 0 0。
 * 椭圆拱用一段二次贝塞尔近似（控制点 (w/2, -b) 时曲线恰好过拱顶 (w/2, 0)）。 */
private val ArchTopShape = GenericShape { s, _ ->
    val b = 30.dp.toPx().let { if (it * 2 > s.height) s.height / 2 else it }
    moveTo(0f, b)
    quadraticBezierTo(s.width / 2f, -b, s.width, b)
    lineTo(s.width, s.height)
    lineTo(0f, s.height)
    close()
}

@Composable
private fun isWebLight(): Boolean =
    MaterialTheme.colorScheme.background == WebTokens.LBg

@Composable
private fun AuthScaffold(onBack: (() -> Unit)? = null,
                         content: @Composable ColumnScope.() -> Unit) {
    val band = if (isWebLight()) WebTokens.LAuthBand else WebTokens.AuthBand
    Box(Modifier.fillMaxSize().background(band)) {
        if (onBack != null) {
            TextButton(onClick = onBack,
                modifier = Modifier.align(Alignment.TopStart).padding(8.dp)) {
                Text("‹ 返回", color = WebTokens.Text2, fontSize = 15.sp)
            }
        }
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                .imePadding().padding(horizontal = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(if (onBack != null) 56.dp else 48.dp))
            content()
            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
private fun AuthSheet(content: @Composable ColumnScope.() -> Unit) {
    val panel = if (isWebLight()) WebTokens.LAuthPanel else WebTokens.AuthPanel
    Column(
        Modifier.fillMaxWidth().shadow(14.dp, ArchTopShape, clip = false)
            .background(panel, ArchTopShape)
            .padding(horizontal = 24.dp),
        content = content,
    )
}

@Composable
private fun BrandHeader(subtitle: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            AiBrandMark(30)
            Text(buildAnnotatedString {
                withStyle(SpanStyle(brush = aiPrimaryBrush(), fontWeight = FontWeight.SemiBold)) {
                    append("AI 助手")
                }
            }, fontSize = 24.sp)
        }
        Spacer(Modifier.height(6.dp))
        Text(subtitle, style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(22.dp))
    }
}

@Composable
private fun Field(label: String, value: String, onValue: (String) -> Unit,
                  secret: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onValue, label = { Text(label) },
        singleLine = true,
        shape = CircleShape,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = MaterialTheme.colorScheme.primary,
            unfocusedBorderColor = MaterialTheme.colorScheme.outline,
            focusedContainerColor = MaterialTheme.colorScheme.surface,
            unfocusedContainerColor = MaterialTheme.colorScheme.surface,
        ),
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        visualTransformation =
            if (secret) PasswordVisualTransformation() else VisualTransformation.None,
    )
}

@Composable
private fun ErrorText(msg: String?) {
    if (!msg.isNullOrBlank()) Text(msg, color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.fillMaxWidth().padding(start = 18.dp, bottom = 4.dp))
}

private fun errOf(e: Throwable): String = when (e) {
    is ApiException -> when (e.status) {
        429 -> "尝试太频繁，稍后再试"
        401 -> "用户名或密码不对"
        else -> e.message.orEmpty()
    }
    else -> "网络不可达，检查服务地址"
}

@Composable
private fun GradientButton(onClick: () -> Unit, enabled: Boolean = true,
                           content: @Composable () -> Unit) {
    Surface(
        onClick = onClick, enabled = enabled,
        shape = CircleShape,
        color = Color.Transparent,
        contentColor = MaterialTheme.colorScheme.onPrimary,
        tonalElevation = 0.dp,
        modifier = Modifier.fillMaxWidth().height(48.dp)
            .background(aiPrimaryBrush(), CircleShape),
    ) {
        Box(contentAlignment = Alignment.Center) { content() }
    }
}

@Composable
private fun LinkText(text: String, onClick: () -> Unit) {
    TextButton(onClick = onClick) {
        Text(text, color = MaterialTheme.colorScheme.primary,
            style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
fun LoginScreen(onAuthed: () -> Unit, onRegister: () -> Unit, onReset: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var editServer by remember { mutableStateOf(false) }
    var server by remember { mutableStateOf(Prefs.baseUrl) }

    AuthScaffold {
        BrandHeader("v${xyz.fenever.assistant.nativeapp.BuildConfig.VERSION_NAME}")
        AuthSheet {
            Spacer(Modifier.height(6.dp))
            if (editServer) {
                Field("服务地址", server, { server = it })
                Row(Modifier.fillMaxWidth().padding(bottom = 4.dp),
                    horizontalArrangement = Arrangement.End) {
                    LinkText("保存地址") {
                        if (server.isNotBlank()) {
                            Prefs.setBaseUrlAndReauth(server.trim())
                            editServer = false
                            error = null
                        }
                    }
                }
            } else {
                Row(Modifier.fillMaxWidth().padding(bottom = 2.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("服务器 ${Prefs.baseUrl}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    LinkText("修改") { server = Prefs.baseUrl; editServer = true }
                }
            }
            Field("用户名", username, { username = it })
            Field("密码", password, { password = it }, secret = true)
            ErrorText(error)
            GradientButton(
                enabled = !busy && username.isNotBlank() && password.isNotBlank(),
                onClick = {
                    busy = true; error = null
                    scope.launch {
                        runCatching { Api.login(username.trim(), password) }
                            .onSuccess {
                                Prefs.token = it.token
                                Prefs.username = it.username
                                Prefs.role = it.role
                                onAuthed()
                            }
                            .onFailure { error = errOf(it) }
                        busy = false
                    }
                }) {
                if (busy) CircularProgressIndicator(strokeWidth = 2.dp)
                else Text("登 录", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            }
            Row(Modifier.fillMaxWidth().padding(top = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween) {
                LinkText("注册新账号", onRegister)
                LinkText("忘记密码", onReset)
            }
            Spacer(Modifier.height(26.dp))
        }
    }
}

@Composable
fun RegisterScreen(onAuthed: () -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    val answers = remember { List(3) { mutableStateOf("") } }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AuthScaffold(onBack = onBack) {
        BrandHeader("注册即设置你的找回三题")
        AuthSheet {
            Spacer(Modifier.height(6.dp))
            Field("用户名", username, { username = it })
            Field("密码（至少 6 位）", password, { password = it }, secret = true)
            Field("确认密码", confirm, { confirm = it }, secret = true)
            RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                Text(q, style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 18.dp, top = 8.dp))
                Field("你的答案", answers[i].value, { answers[i].value = it })
            }
            ErrorText(error)
            GradientButton(enabled = !busy && username.isNotBlank() &&
                password.length >= 6 && confirm == password &&
                answers.all { it.value.isNotBlank() },
                onClick = {
                    if (confirm != password) { error = "两次密码不一致"; return@GradientButton }
                    busy = true; error = null
                    scope.launch {
                        runCatching {
                            Api.register(username.trim(), password,
                                answers.map { it.value.trim() })
                        }.onSuccess {
                            Prefs.token = it.token
                            Prefs.username = it.username
                            Prefs.role = it.role
                            onAuthed()
                        }.onFailure { error = errOf(it) }
                        busy = false
                    }
                }) {
                if (busy) CircularProgressIndicator(strokeWidth = 2.dp)
                else Text("注 册", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            }
            Spacer(Modifier.height(26.dp))
        }
    }
}

@Composable
fun ResetScreen(onDone: () -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var pwd by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    val answers = remember { List(3) { mutableStateOf("") } }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AuthScaffold(onBack = onBack) {
        BrandHeader("答对找回三题即可换新密码")
        AuthSheet {
            Spacer(Modifier.height(6.dp))
            Field("用户名", username, { username = it })
            RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                Text(q, style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 18.dp, top = 8.dp))
                Field("你的答案", answers[i].value, { answers[i].value = it })
            }
            Field("新密码（至少 6 位）", pwd, { pwd = it }, secret = true)
            Field("确认新密码", confirm, { confirm = it }, secret = true)
            ErrorText(error)
            GradientButton(enabled = !busy && username.isNotBlank() &&
                pwd.length >= 6 && pwd == confirm &&
                answers.all { it.value.isNotBlank() },
                onClick = {
                    busy = true; error = null
                    scope.launch {
                        runCatching {
                            Api.resetPassword(username.trim(),
                                answers.map { it.value.trim() }, pwd)
                        }.onSuccess { onDone() }
                            .onFailure { error = errOf(it) }
                        busy = false
                    }
                }) {
                if (busy) CircularProgressIndicator(strokeWidth = 2.dp)
                else Text("重置密码", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            }
            Spacer(Modifier.height(26.dp))
        }
    }
}
