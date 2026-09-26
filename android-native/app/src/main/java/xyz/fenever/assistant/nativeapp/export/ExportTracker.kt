package xyz.fenever.assistant.nativeapp.export

import android.Manifest
import android.app.DownloadManager
import android.content.Context
import android.os.Build
import android.os.Environment
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import xyz.fenever.assistant.core.ExportName
import xyz.fenever.assistant.core.ExportRedeem
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.ReminderChannels
import java.io.File
import java.io.FileInputStream
import java.util.concurrent.ConcurrentHashMap

/**
 * 入队之后的账房（v0.23 T2.5，PRD R2 边缘）。
 *
 * DownloadManager 把兑换权接走之后，App 对"票据 5 分钟过期"就失去了可见性——
 * 它不透出 HTTP 状态码。这里用轮询把结局问出来：成功就闭嘴（系统「已下载」
 * 通知是 T2.4 主链路的交付物，不重复贴一张）；失败则读回响应字节，认出服务端
 * 那行"导出链接无效或已过期"就地重签一次再入队；重签也救不回来、或者根本不是
 * 过期，就用 ExportRedeem 的专属文案发一条通知说清楚。轮询有尽头，不许死等。
 *
 * 重试预算按会话记在进程内存里：票据本来就一次性、不落盘（R2-AC-3"App 不缓存
 * 票据"），把"重签过没有"持久化反而多留一份跨进程状态。进程没了预算清零，
 * 用户重新点导出本来就会拿新票——语义正好。
 */
object ExportTracker {

    const val CHANNEL_ID = "ai_session_export"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val retriedSids = ConcurrentHashMap.newKeySet<String>()

    /** ≤28 写公共 Downloads 的旧存储权限是否在手。API 29+ 恒真（分区存储自带位）。 */
    fun hasLegacyStorage(ctx: Context): Boolean =
        Build.VERSION.SDK_INT >= 29 ||
            ContextCompat.checkSelfPermission(ctx,
                Manifest.permission.WRITE_EXTERNAL_STORAGE) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED

    /** 拒绝写公共目录时的兜底：票据字节直接落到 App 私有 Downloads，返回绝对路径。 */
    suspend fun savePrivateCopy(ctx: Context, ticketPath: String, fileName: String): String {
        val bytes = Api.fetchTicketBytes(ticketPath)
        return kotlinx.coroutines.withContext(Dispatchers.IO) {
            val dir = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                ?: File(ctx.filesDir, "downloads").apply { mkdirs() }
            // 私有目录没有 DownloadManager 的重名保护，自己加序号：兜底路径更要留得住每一份
            var target = File(dir, fileName)
            var n = 2
            while (target.exists()) {
                target = File(dir, fileName.removeSuffix(".md") + "-$n.md")
                n++
            }
            target.writeBytes(bytes)
            target.absolutePath
        }
    }

    /** 盯一张已入队的下载。只在失败/超时时发声；成功交给系统通知。 */
    fun watch(ctx: Context, downloadId: Long, sid: String) {
        scope.launch { poll(ctx, downloadId, sid) }
    }

    private suspend fun poll(ctx: Context, downloadId: Long, sid: String) {
        for (waitMs in ExportRedeem.watchDelaysMs()) {
            delay(waitMs.toLong())
            val status = statusOf(ctx, downloadId) ?: return   // 记录被人删了，不追
            if (status == DownloadManager.STATUS_SUCCESSFUL) return
            if (status == DownloadManager.STATUS_FAILED) {
                val body = runCatching { errorBody(ctx, downloadId) }.getOrDefault(ByteArray(0))
                val expired = ExportRedeem.looksExpired(body)
                deleteGarbage(ctx, downloadId)   // 失败留下的错误体不是导出文件，别让它躺在 Downloads
                // HTTP 层错误但判不出那行字（有些 ROM 不透出失败体）：过期嫌疑成立，
                // 预算只花一次——重签再败就报通用文案，不会套娃。
                val httpish = expired || reasonOf(ctx, downloadId) == DownloadManager.ERROR_HTTP
                if (httpish && retriedSids.add(sid)) {
                    val again = runCatching {
                        val title = Api.getSession(sid).title
                        val ticket = Api.exportTicket(sid)
                        if (!ExportName.isTicketPath(ticket.path)) {
                            error("服务端给的票据形状不对")
                        }
                        SessionDownloads.enqueue(ctx,
                            Prefs.baseUrl.trimEnd('/') + ticket.path,
                            ExportName.exportFileName(title))
                    }
                    if (again.isSuccess) {
                        watch(ctx, again.getOrThrow(), sid)   // 新票据新下载，重新盯
                        return
                    }
                }
                notify(ctx, sid, ExportRedeem.failureNotice(expired))
                return
            }
        }
        notify(ctx, sid, ExportRedeem.timeoutNotice())
    }

    private fun manager(ctx: Context): DownloadManager =
        ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager

    private fun cursorInt(ctx: Context, id: Long, column: String): Int? {
        val q = DownloadManager.Query().setFilterById(id)
        manager(ctx).query(q).use { c ->
            if (!c.moveToFirst()) return null
            val i = c.getColumnIndex(column)
            return if (i < 0 || c.isNull(i)) null else c.getInt(i)
        }
    }

    private fun statusOf(ctx: Context, id: Long): Int? =
        cursorInt(ctx, id, DownloadManager.COLUMN_STATUS)

    private fun reasonOf(ctx: Context, id: Long): Int? =
        cursorInt(ctx, id, DownloadManager.COLUMN_REASON)

    /** 失败下载的响应体，最多 8KB——过期那行 detail 用不了一整个缓冲区。 */
    private fun errorBody(ctx: Context, id: Long): ByteArray {
        manager(ctx).openDownloadedFile(id).use { pfd ->
            FileInputStream(pfd.fileDescriptor).use { ins ->
                val buf = ByteArray(8192)
                val n = ins.read(buf)
                return if (n <= 0) ByteArray(0) else buf.copyOf(n)
            }
        }
    }

    /** 尽力删掉错误体留下的假文件：29+ 走 content uri，28- 走本地路径。 */
    private fun deleteGarbage(ctx: Context, id: Long) {
        runCatching {
            manager(ctx).query(DownloadManager.Query().setFilterById(id)).use { c ->
                if (!c.moveToFirst()) return
                val pathIdx = c.getColumnIndex(DownloadManager.COLUMN_LOCAL_FILENAME)
                if (pathIdx >= 0 && !c.isNull(pathIdx)) {
                    File(c.getString(pathIdx)).delete()
                }
                val uriIdx = c.getColumnIndex(DownloadManager.COLUMN_URI)
                if (uriIdx >= 0 && !c.isNull(uriIdx)) {
                    val uri = android.net.Uri.parse(c.getString(uriIdx))
                    ctx.contentResolver.delete(uri, null, null)
                }
            }
        }
    }

    private fun notify(ctx: Context, tag: String, text: String) {
        // 系统通知被拒时不硬闯（PRD：拒绝通知权限的兜底=页内文案与下一次点击）；
        // 复用提醒那一处 POST_NOTIFICATIONS 判据，不另数一遍。
        if (!ReminderChannels.notificationsGranted(ctx)) return
        runCatching {
            val nm = ctx.getSystemService(android.app.NotificationManager::class.java) ?: return
            if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(android.app.NotificationChannel(
                    CHANNEL_ID, "导出", android.app.NotificationManager.IMPORTANCE_DEFAULT))
            }
            nm.notify(tag.hashCode(),
                androidx.core.app.NotificationCompat.Builder(ctx, CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.stat_sys_download_done)
                    .setContentTitle("导出本次对话")
                    .setContentText(text)
                    .setAutoCancel(true)
                    .build())
        }
    }
}
