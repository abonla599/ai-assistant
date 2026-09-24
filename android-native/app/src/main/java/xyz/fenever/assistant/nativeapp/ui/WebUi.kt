package xyz.fenever.assistant.nativeapp.ui

import android.annotation.SuppressLint
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import xyz.fenever.assistant.nativeapp.Prefs

/* 混合形态里唯一的 WebView 页面：整套既有 Web 前端（模型服务配置、管理面等
 * 复杂表单）原样复用，不重复实现。
 *
 * 登录态交接：页面加载完成后用原生持有的 Bearer 令牌调一次 /v1/auth/adopt，
 * 服务端把同一枚凭据签成 httpOnly 会话 Cookie（WebView 自有 CookieJar 保存），
 * 然后重载页面——WebView 里就是已登录状态，原生侧令牌不经 URL 外泄。 */
@SuppressLint("SetJavaScriptEnabled")
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebAppScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    var webView by remember { mutableStateOf<WebView?>(null) }
    var adopted by remember { mutableStateOf(false) }
    val tokenJson = remember { Json.encodeToString(String.serializer(), Prefs.token) }
    val startUrl = Prefs.baseUrl.trimEnd('/') + "/app/"

    BackHandler(enabled = webView?.canGoBack() == true) { webView?.goBack() }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("网页版 · 设置与管理") },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                }
            },
        )
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            AndroidView(
                factory = {
                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        webViewClient = object : WebViewClient() {
                            override fun onPageFinished(view: WebView, url: String) {
                                if (adopted) return
                                adopted = true
                                // adopt 走头凭据（Authorization），按后端约定免 CSRF；
                                // 仍带上无害的 X-CSRF，与 Web 前端行为完全一致。
                                view.evaluateJavascript(
                                    "fetch('/v1/auth/adopt',{method:'POST'," +
                                        "headers:{Authorization:'Bearer '+$tokenJson,'X-CSRF':'1'}," +
                                        "credentials:'same-origin'}).then(function(r){" +
                                        "if(r.ok)location.reload();});",
                                    null,
                                )
                            }
                        }
                        loadUrl(startUrl)
                        webView = this
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
