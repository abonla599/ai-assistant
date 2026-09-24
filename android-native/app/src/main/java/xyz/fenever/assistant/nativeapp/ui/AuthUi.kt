package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.Prefs

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
                         content: @Composable () -> Unit) {
    Scaffold(topBar = {
        TopAppBar(title = { Text(title) },
            navigationIcon = {
                if (onBack != null) IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                }
            })
    }) { pad ->
        Column(
            modifier = Modifier.padding(pad).fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.Center,
        ) { content() }
    }
}

@Composable
private fun Field(label: String, value: String, onValue: (String) -> Unit,
                  secret: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onValue, label = { Text(label) },
        singleLine = true, visualTransformation =
            if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
    )
}

@Composable
private fun ErrorText(msg: String?) {
    if (!msg.isNullOrBlank()) Text(msg, color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.padding(vertical = 6.dp))
}

private fun errOf(e: Throwable): String = when (e) {
    is ApiException -> if (e.status == 429) "尝试太频繁，稍后再试" else e.message.orEmpty()
    else -> "网络不可达，检查服务地址"
}

@Composable
fun LoginScreen(onAuthed: () -> Unit, onRegister: () -> Unit, onReset: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var server by remember { mutableStateOf(Prefs.baseUrl) }
    var editServer by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AuthScaffold(title = "AI 助手") {
        Card {
            Column(Modifier.padding(20.dp)) {
                Text("登录", style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("服务地址：$server", style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f))
                    TextButton(onClick = { editServer = !editServer }) {
                        Text(if (editServer) "收起" else "修改")
                    }
                }
                if (editServer) {
                    Field(label = "服务器地址 (https://...)", value = server,
                        onValue = { server = it })
                }
                Field("用户名", username, { username = it })
                Field("密码", password, { password = it }, secret = true)
                ErrorText(error)
                Button(onClick = {
                    if (busy) return@Button
                    busy = true; error = null
                    scope.launch {
                        try {
                            val url = server.trim().ifEmpty { Prefs.baseUrl }
                            // 服务器与已存 token 不是同一个世界才清；同地址重登不清别机凭据
                            if (url.trimEnd('/') != Prefs.baseUrl.trimEnd('/'))
                                Prefs.setBaseUrlAndReauth(url)
                            else Prefs.baseUrl = url
                            val r = Api.login(username.trim(), password)
                            Prefs.token = r.token
                            Prefs.username = r.username
                            Prefs.role = r.role
                            onAuthed()
                        } catch (e: Exception) { error = errOf(e) }
                        busy = false
                    }
                }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp), enabled = !busy) {
                    if (busy) CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.height(16.dp))
                    else Text("登录")
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = onRegister) { Text("注册新账号") }
                    TextButton(onClick = onReset) { Text("忘记密码") }
                }
            }
        }
    }
}

@Composable
fun RegisterScreen(onAuthed: () -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    val answers = remember { List(3) { mutableStateOf("") } }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AuthScaffold(title = "注册", onBack = onBack) {
        Card {
            Column(Modifier.padding(20.dp)) {
                Text("注册即登录；三题找回答案是唯一的自助凭证，请记牢",
                    style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(12.dp))
                Field("用户名", username, { username = it })
                Field("密码", password, { password = it }, secret = true)
                RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                    Field(q, answers[i].value, { answers[i].value = it })
                }
                ErrorText(error)
                Button(onClick = {
                    if (busy) return@Button
                    busy = true; error = null
                    scope.launch {
                        try {
                            val r = Api.register(username.trim(), password,
                                answers.map { it.value.trim() })
                            Prefs.token = r.token
                            Prefs.username = r.username
                            Prefs.role = r.role
                            onAuthed()
                        } catch (e: Exception) { error = errOf(e) }
                        busy = false
                    }
                }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp), enabled = !busy) {
                    Text(if (busy) "注册中…" else "注册并登录")
                }
            }
        }
    }
}

@Composable
fun ResetScreen(onDone: () -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    val answers = remember { List(3) { mutableStateOf("") } }
    var newPassword by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }

    AuthScaffold(title = "重置密码", onBack = onBack) {
        Card {
            Column(Modifier.padding(20.dp)) {
                Text("三题 + 新密码一次提交；成功后该账号在所有设备上掉线",
                    style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(12.dp))
                Field("用户名", username, { username = it })
                RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                    Field(q, answers[i].value, { answers[i].value = it })
                }
                Field("新密码", newPassword, { newPassword = it }, secret = true)
                ErrorText(error)
                if (done) {
                    Text("密码已重置，请用新密码登录",
                        color = MaterialTheme.colorScheme.secondary)
                    Button(onClick = onDone, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                        Text("去登录")
                    }
                } else {
                    Button(onClick = {
                        if (busy) return@Button
                        busy = true; error = null
                        scope.launch {
                            try {
                                Api.resetPassword(username.trim(),
                                    answers.map { it.value.trim() }, newPassword)
                                done = true
                            } catch (e: Exception) { error = errOf(e) }
                            busy = false
                        }
                    }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp), enabled = !busy) {
                        Text(if (busy) "提交中…" else "重置密码")
                    }
                }
            }
        }
    }
}
