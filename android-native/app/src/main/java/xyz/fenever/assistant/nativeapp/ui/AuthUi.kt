package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

/* 找回三题是全站常量，与后端 app/core/auth.py 的 RECOVERY_QUESTIONS 逐字一致
 * （Web 侧有 test_web_pwa 锁这条对齐；原生侧照抄同一份，服务器不代为下发）。 */
val RECOVERY_QUESTIONS = listOf(
    "你的手机号后四位是什么？",
    "你小学在哪上？",
    "你父母姓氏的拼音首字母各一个是什么？",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AuthScaffold(title: String, onBack: (() -> Unit)? = null,
                         content: @Composable BoxScope.() -> Unit) {
    Scaffold(
        containerColor = androidx.compose.ui.graphics.Color.Transparent,
        topBar = {
            TopAppBar(
                title = { Text(title, fontWeight = FontWeight.ExtraBold) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = androidx.compose.ui.graphics.Color.Transparent),
                navigationIcon = {
                    if (onBack != null) IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                })
        }) { pad ->
        AiGlowBackground {
            Column(
                Modifier.padding(pad).fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp, vertical = 20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) { content() }
        }
    }
}

@Composable
private fun Field(label: String, value: String, onValue: (String) -> Unit,
                  secret: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onValue, label = { Text(label) },
        singleLine = true,
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = MaterialTheme.colorScheme.surface,
            unfocusedContainerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.72f),
        ),
        visualTransformation =
            if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
    )
}

@Composable
private fun ErrorText(msg: String?) {
    if (!msg.isNullOrBlank()) Text(msg, color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp))
}

@Composable
private fun BrandHeader(subtitle: String) {
    Row(Modifier.fillMaxWidth().padding(bottom = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Start) {
        AiBrandMark(48)
        Spacer(Modifier.width(12.dp))
        Column {
            Text("AI 助手", style = MaterialTheme.typography.headlineSmall)
            Text(subtitle, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
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
    Button(
        onClick = onClick, enabled = enabled,
        shape = RoundedCornerShape(16.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = androidx.compose.ui.graphics.Color.Transparent,
            contentColor = androidx.compose.ui.graphics.Color.White),
        elevation = ButtonDefaults.buttonElevation(defaultElevation = 6.dp),
        modifier = Modifier.fillMaxWidth().height(52.dp)
            .background(aiPrimaryBrush(), RoundedCornerShape(16.dp)),
        content = { content() },
    )
}

@Composable
private fun AuthCard(content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    Card(
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        modifier = Modifier.fillMaxWidth(),
    ) { Column(Modifier.padding(20.dp), content = content) }
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

    AuthScaffold(title = "") {
        BrandHeader("原生客户端 v${xyz.fenever.assistant.nativeapp.BuildConfig.VERSION_NAME}")
        AuthCard {
            if (editServer) {
                Field("服务地址", server, { server = it })
                TextButton(onClick = {
                    if (server.isNotBlank()) {
                        Prefs.setBaseUrlAndReauth(server.trim())
                        editServer = false
                        error = null
                    }
                }) { Text("保存地址") }
            } else {
                Row(Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("服务器 ${Prefs.baseUrl}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    TextButton(onClick = { server = Prefs.baseUrl; editServer = true }) {
                        Text("修改", style = MaterialTheme.typography.labelMedium)
                    }
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
                else Text("登 录", fontWeight = FontWeight.Bold)
            }
            Row(Modifier.fillMaxWidth().padding(top = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = onRegister) { Text("注册新账号") }
                TextButton(onClick = onReset) { Text("忘记密码") }
            }
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

    AuthScaffold(title = "注册", onBack = onBack) {
        BrandHeader("注册即设置你的找回三题")
        AuthCard {
            Field("用户名", username, { username = it })
            Field("密码", password, { password = it }, secret = true)
            Field("确认密码", confirm, { confirm = it }, secret = true)
            RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                Field(q, answers[i].value, { answers[i].value = it })
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
                else Text("注 册", fontWeight = FontWeight.Bold)
            }
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

    AuthScaffold(title = "重置密码", onBack = onBack) {
        BrandHeader("答对找回三题即可换新密码")
        AuthCard {
            Field("用户名", username, { username = it })
            RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                Field(q, answers[i].value, { answers[i].value = it })
            }
            Field("新密码", pwd, { pwd = it }, secret = true)
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
                else Text("重置密码", fontWeight = FontWeight.Bold)
            }
        }
    }
}

