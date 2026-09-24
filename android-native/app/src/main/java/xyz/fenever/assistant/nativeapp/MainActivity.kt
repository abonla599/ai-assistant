package xyz.fenever.assistant.nativeapp

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import xyz.fenever.assistant.nativeapp.theme.AiTheme
import xyz.fenever.assistant.nativeapp.theme.ThemeMode
import xyz.fenever.assistant.nativeapp.ui.AuthScreen
import xyz.fenever.assistant.nativeapp.ui.ChatScreen
import xyz.fenever.assistant.nativeapp.ui.reenterRequested

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.init(applicationContext)
        ReminderStore.init(applicationContext)
        ReminderChannels.ensure(applicationContext)
        // 冷启动第一帧就把已存的外观偏好灌进 Compose state，不闪一下深色
        ThemeMode.value = Prefs.themeMode
        setContent {
            AiTheme { AppRoot() }
        }
    }
}

/* 导航结构与旧壳/网页一致：登录后只有"聊天页"一块主屏。
 * 会话列表是聊天页左侧抽屉，设置是底部弹层、五张二级页在弹层里换 view——
 * 不再有独立的 sessions/memory/settings 路由。认证页是同一层的另一张屏：
 * 未登录时它就是主屏；壳内要重新认证（注册新身份/改密码）时它盖上来，
 * 从盖上来的路径可以 × 退回聊天页（旧会话还在），登录成功则整壳重建。 */
@Composable
private fun AppRoot() {
    val ctx = LocalContext.current
    var authed by remember { mutableStateOf(Prefs.isAuthed) }
    var bootTick by remember { mutableIntStateOf(0) }        // ++ = 等价网页 location.reload
    var authMode by remember { mutableStateOf("login") }
    var showAuth by remember { mutableStateOf(!authed) }

    if (showAuth) {
        AuthScreen(initialMode = authMode,
            onBack = if (authed) {
                { showAuth = false; authMode = "login"; bootTick++ }
            } else null,
            onAuthed = {
                showAuth = false; authMode = "login"
                authed = true
                ReminderStore.setOwner(Prefs.currentEntry()?.userId ?: "")
                bootTick++
            })
    } else {
        key(bootTick) {
            LaunchedEffect(bootTick) {
                Prefs.currentEntry()?.let { ReminderStore.setOwner(it.userId) }
            }
            ChatScreen(
                onRequireAuth = { mode -> authMode = mode; showAuth = true },
                onLoggedOut = {
                    // 切换账号/收编令牌：静默换人重建聊天壳；真退出才落回登录页
                    if (reenterRequested) { reenterRequested = false; bootTick++ }
                    else { authed = false; showAuth = true; authMode = "login" }
                },
                onOpenUrl = { u ->
                    runCatching {
                        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u))
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }
                },
            )
        }
    }
}
