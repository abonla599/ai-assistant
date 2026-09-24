package xyz.fenever.assistant.nativeapp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import xyz.fenever.assistant.nativeapp.theme.AiTheme
import xyz.fenever.assistant.nativeapp.theme.ThemeMode
import xyz.fenever.assistant.nativeapp.ui.LoginScreen
import xyz.fenever.assistant.nativeapp.ui.RegisterScreen
import xyz.fenever.assistant.nativeapp.ui.ResetScreen
import xyz.fenever.assistant.nativeapp.ui.ChatScreen
import xyz.fenever.assistant.nativeapp.ui.WebAppScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.init(applicationContext)
        // 冷启动第一帧就把已存的外观偏好灌进 Compose state，不闪一下深色
        ThemeMode.value = Prefs.themeMode
        setContent {
            AiTheme { AppNav() }
        }
    }
}

/* 导航结构与旧壳/网页一致：登录后只有"聊天页"一块主屏。
 * 会话列表是聊天页左侧抽屉，设置和记忆是底部弹层——不再有独立的
 * sessions/memory/settings 路由，那些界面全部内嵌在 ChatScreen 里。 */
@androidx.compose.runtime.Composable
private fun AppNav() {
    val nav = rememberNavController()
    // 凭据在不在，决定落在哪一屏——不做"先进壳再弹登录"的闪屏循环。
    NavHost(navController = nav,
        startDestination = if (Prefs.isAuthed) "chat" else "login") {

        composable("login") {
            LoginScreen(
                onAuthed = { nav.navigate("chat") { popUpTo("login") { inclusive = true } } },
                onRegister = { nav.navigate("register") },
                onReset = { nav.navigate("reset") },
            )
        }
        composable("register") {
            RegisterScreen(
                onAuthed = { nav.navigate("chat") { popUpTo("login") { inclusive = true } } },
                onBack = { nav.popBackStack() },
            )
        }
        composable("reset") {
            ResetScreen(onDone = { nav.popBackStack() }, onBack = { nav.popBackStack() })
        }
        composable("chat") {
            ChatScreen(
                onWebApp = { nav.navigate("webapp") },
                // 改密码成功后旧会话凭据不再可信：与网页端一致，回到登录页重登
                onReset = { nav.navigate("reset_from_settings") },
                onLoggedOut = { nav.navigate("login") { popUpTo("chat") { inclusive = true } } },
            )
        }
        composable("webapp") { WebAppScreen(onBack = { nav.popBackStack() }) }
        composable("reset_from_settings") {
            ResetScreen(onDone = {
                Prefs.clearAuth()
                nav.navigate("login") { popUpTo("chat") { inclusive = true } }
            }, onBack = { nav.popBackStack() })
        }
    }
}
