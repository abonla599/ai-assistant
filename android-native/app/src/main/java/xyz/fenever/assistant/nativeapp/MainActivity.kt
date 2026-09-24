package xyz.fenever.assistant.nativeapp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import xyz.fenever.assistant.nativeapp.theme.AiTheme
import xyz.fenever.assistant.nativeapp.ui.LoginScreen
import xyz.fenever.assistant.nativeapp.ui.RegisterScreen
import xyz.fenever.assistant.nativeapp.ui.ResetScreen
import xyz.fenever.assistant.nativeapp.ui.ChatScreen
import xyz.fenever.assistant.nativeapp.ui.MemoryScreen
import xyz.fenever.assistant.nativeapp.ui.SessionListScreen
import xyz.fenever.assistant.nativeapp.ui.WebAppScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.init(applicationContext)
        setContent {
            AiTheme { AppNav() }
        }
    }
}

@androidx.compose.runtime.Composable
private fun AppNav() {
    val nav = rememberNavController()
    // 凭据在不在，决定落在哪一屏——不做"先进壳再弹登录"的闪屏循环。
    NavHost(navController = nav,
        startDestination = if (Prefs.isAuthed) "sessions" else "login") {

        composable("login") {
            LoginScreen(
                onAuthed = { nav.navigate("sessions") { popUpTo("login") { inclusive = true } } },
                onRegister = { nav.navigate("register") },
                onReset = { nav.navigate("reset") },
            )
        }
        composable("register") {
            RegisterScreen(
                onAuthed = { nav.navigate("sessions") { popUpTo("login") { inclusive = true } } },
                onBack = { nav.popBackStack() },
            )
        }
        composable("reset") {
            ResetScreen(onDone = { nav.popBackStack() }, onBack = { nav.popBackStack() })
        }
        composable("sessions") {
            SessionListScreen(
                onOpenChat = { id -> nav.navigate("chat/$id") },
                onMemory = { nav.navigate("memory") },
                onWebApp = { nav.navigate("webapp") },
                onLoggedOut = { nav.navigate("login") { popUpTo("sessions") { inclusive = true } } },
            )
        }
        composable(
            route = "chat/{sessionId}",
            arguments = listOf(navArgument("sessionId") { type = NavType.StringType }),
        ) { entry ->
            ChatScreen(sessionId = entry.arguments?.getString("sessionId").orEmpty(),
                onBack = { nav.popBackStack() })
        }
        composable("memory") { MemoryScreen(onBack = { nav.popBackStack() }) }
        composable("webapp") { WebAppScreen(onBack = { nav.popBackStack() }) }
    }
}
