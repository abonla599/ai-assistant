package xyz.fenever.assistant.nativeapp.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import xyz.fenever.assistant.core.ApkDownloader
import xyz.fenever.assistant.core.ReleasePlan
import xyz.fenever.assistant.nativeapp.BuildConfig
import xyz.fenever.assistant.nativeapp.Prefs
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/* 「检查更新 → 立即更新 → 校验 → 系统安装页」的编排（v0.23 T1.7 + T1.8）。

 判断与字节纪律**全部住在共享核心类里**（ReleasePlan / ApkDownloader / ApkDigest / MiniJson
 与旧壳逐字节同一份，android/app 那份由 JVM 台架 136 个用例钉着，字节一致性由
 backend/tests/test_release_probe.py 数着）——这里只做 Android 侧的三件事：
 ① 问与取的地址从 Prefs.baseUrl 现拼（服务换域名只改设置，链路上没有第二处真相）；
 ② 把状态灌进 Compose（全部经主线程 post，IO 线程绝不直接写快照）；
 ③ 下载完成后把包交给系统安装页，没授权时给出能走通的路。

 AC-4 的迁移：/v1/update/info 取不到 GitHub 时回 5xx 带 detail；这里把任何非 200
 都归成 UNUSABLE 的一句理由，值槽显示「检查更新失败：<原因>」——绝不伪装"已是最新版"。 */

private const val INFO_CONNECT_MS = 8_000
private const val INFO_READ_MS = 12_000
/** 一条 release JSON 十几 KB 封顶；读满这个数还不停手就说明回来的不是它。 */
private const val INFO_MAX_BYTES = 256 * 1024
/** 与后端 releases.APK_MAX_BYTES 同一个数：壳这边同样不信"对方说多大就多大"。 */
private const val APK_MAX_BYTES = 16L * 1024 * 1024

object Updater {
    private val main = Handler(Looper.getMainLooper())
    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** 检查三态 + 上一份决定；null = 还没查过。值槽直接读它们。 */
    var checking by mutableStateOf(false)
        private set
    var decision by mutableStateOf<ReleasePlan.Decision?>(null)
        private set
    /** 下载进度：downloading 时 percent/doneBytes/totalBytes 供进度对话框读。 */
    var downloading by mutableStateOf(false)
        private set
    var percent by mutableStateOf(0)
        private set
    var doneBytes by mutableStateOf(0L)
        private set
    var totalBytes by mutableStateOf(-1L)
        private set
    /** 非 null = 弹出「装不了：vX.Y → 去设置」的授权引导对话框（T1.7 的授权引导）。 */
    var installGuideVersion by mutableStateOf<String?>(null)
        private set

    @Volatile private var activeConn: HttpURLConnection? = null

    private fun infoUrl() = Prefs.baseUrl.trimEnd('/') + "/v1/update/info"

    /** 字节出口与旧壳/官网按钮同一个：baseUrl + ReleasePlan.SELF_APK_PATH（同源纪律）。 */
    private fun apkUrl() = Prefs.baseUrl.trimEnd('/') + ReleasePlan.SELF_APK_PATH

    /** onDone(失败理由 | null) 只在【检查】结束回调；下载结果走状态（值槽/对话框自己看）。 */
    fun startCheck(onDone: ((String?) -> Unit)? = null) {
        if (checking) return
        checking = true
        io.launch {
            val d = try {
                ReleasePlan.decide(BuildConfig.VERSION_NAME, fetchInfo(), Prefs.baseUrl)
            } catch (e: Exception) {
                // 连不上/超时/被网关改了：说清是哪一种，绝不当成"已经是最新版"
                ReleasePlan.unusable("连不上更新服务（" + e.javaClass.simpleName + "）")
            }
            main.post {
                decision = d
                checking = false
                onDone?.invoke(if (d.kind == ReleasePlan.Kind.UNUSABLE) d.reason else null)
            }
        }
    }

    /** 只有非 200 一种事实要处理：后端把"问不到 GitHub"回成 502 带理由，这里不解读理由。 */
    private fun fetchInfo(): String {
        val conn = (URL(infoUrl()).openConnection() as HttpURLConnection).apply {
            connectTimeout = INFO_CONNECT_MS
            readTimeout = INFO_READ_MS
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "ai-assistant-native/" + BuildConfig.VERSION_NAME)
        }
        try {
            if (conn.responseCode != HttpURLConnection.HTTP_OK) {
                throw IOException("HTTP " + conn.responseCode)
            }
            conn.inputStream.use { input ->
                val buf = ByteArrayOutputStream(8 * 1024)
                val chunk = ByteArray(8 * 1024)
                var total = 0
                while (true) {
                    val read = input.read(chunk)
                    if (read <= 0) break
                    total += read
                    if (total > INFO_MAX_BYTES) throw IOException("返回体积异常")
                    buf.write(chunk, 0, read)
                }
                return buf.toString("UTF-8")
            }
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 开始下载。onDone(失败理由 | null) 在主线程回调——成功那一刻已经顺带拉起安装页。
     * 没带可信校验值不下第一字节（R5：残包事故的原样就是"先下下来再说"）。
     */
    fun startDownload(app: Context, onDone: (String?) -> Unit) {
        val d = decision ?: return
        if (downloading || d.kind != ReleasePlan.Kind.AVAILABLE) return
        if (d.sha256 == null) {
            onDone("这一版的发布信息里没带安装包的校验值，拒绝下载来源不可核对的包")
            return
        }
        val dir = app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
        if (dir == null) {
            onDone("这台设备现在给不出下载目录（存储未就绪？）")
            return
        }
        val name = ReleasePlan.assetName(d.version)
        val target = File(dir, name)
        val temp = File(dir, "$name.part")
        if (target.isFile && !target.delete()) {
            onDone("上一次的文件删不掉，先重启手机再试一次")
            return
        }
        if (temp.isFile) temp.delete()   // 半截的 .part 只是浪费磁盘，删不掉也继续
        downloading = true
        percent = 0; doneBytes = 0; totalBytes = -1
        io.launch {
            val result = ApkDownloader.download(apkUrl(), temp, target, APK_MAX_BYTES,
                d.sha256,
                ApkDownloader.Connector { url -> openApkConnection(url) },
                ApkDownloader.Progress { p, done, total ->
                    // IO 线程只许经这里回主线程写快照，一次/百分点，量级无害
                    main.post { percent = p; doneBytes = done; totalBytes = total }
                })
            main.post {
                downloading = false
                if (result.error == null) {
                    launchInstaller(app, name, d.version, onDone)
                } else {
                    onDone(result.error)
                }
            }
        }
    }

    /** 取消 = 掐断那条连接；ApkDownloader 的失败路径会把 .part 就地清掉，不留残渣。 */
    fun cancelDownload() {
        runCatching { activeConn?.disconnect() }
    }

    private fun openApkConnection(url: String): ApkDownloader.Opened {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = INFO_CONNECT_MS
            readTimeout = INFO_READ_MS
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "ai-assistant-native/" + BuildConfig.VERSION_NAME)
        }
        activeConn = conn
        val status = conn.responseCode
        return object : ApkDownloader.Opened {
            override fun status() = status
            override fun contentLength() = conn.contentLength.toLong()
            override fun body() = conn.inputStream
            override fun closeQuietly() {
                activeConn = null
                conn.disconnect()
            }
        }
    }

    // ---------- 交给系统安装页（T1.7） ----------

    /**
     * content:// 走自己手写的 ApkFileProvider：Android 7 起跨进程给 file:// 直接抛
     * FileUriExposedException。读权限只随这一条 Intent 临时授予，装完即随进程回收。
     * 抛异常 ≈ 这台手机还没允许本应用"安装未知应用"——那是引导的入口，不是失败的终点。
     */
    private fun launchInstaller(app: Context, fileName: String, version: String,
                                onDone: (String?) -> Unit) {
        if (!File(app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), fileName).isFile) {
            onDone("下载完成了，但找不到那个文件")
            return
        }
        val install = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(ApkFileProvider.uriForFile(app, fileName), ApkFileProvider.APK_MIME)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            app.startActivity(install)
            onDone(null)
        } catch (e: Exception) {
            onDone(null)                  // 包已就位——先把"要授权"说清楚，别混成下载失败
            installGuideVersion = version
        }
    }

    /** 「去设置」：优先直达本应用的"安装未知应用"开关页，个别 ROM 没那页就退到应用详情。 */
    fun openInstallPermissionSettings(app: Context) {
        installGuideVersion = null
        val pkg = Uri.parse("package:" + app.packageName)
        val pages = listOf(
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, pkg),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg),
        )
        for (page in pages) {
            try {
                page.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                app.startActivity(page)
                return
            } catch (ignored: Exception) {
                // 下一档；两档都没有时下面兜底
            }
        }
        installGuideVersion = "NO-PAGE"   // UI 读到它只能显示"系统没给出那个页面"
    }

    fun dismissInstallGuide() {
        installGuideVersion = null
    }
}
