package xyz.fenever.assistant.nativeapp.export

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment

/**
 * 一次性票据 → 系统 Downloads（v0.23 T2.4，PRD 7.2-2 / R2）。
 *
 * 为什么选 DownloadManager 而不是自己流式落盘：PRD 要"完成后系统『已下载』
 * 通知"，这条通知是系统下载队列自带的——自管下载就得自己养通知渠道、点击
 * 行为、进度样式，那是把系统已经做好的东西重做一遍还做不像。断点续传和
 * Wi-Fi 大文件调度也顺手白拿。
 *
 * 纪律（调用方必须已满足，这里不复核但注释钉死）：
 * - URL 必须是 baseUrl + 过完 ExportName.isTicketPath 门的相对票据路径：
 *   DownloadManager 会把这条 URL 存进系统下载队列（跨进程、可被设置里的
 *   "下载管理"看到），所以入队前的形状门就是"不产生第二个可访问 URL"的
 *   客户端半边——另一半是后端的一次性兑换。
 * - 文件名来自 ExportName.exportFileName（服务端清洗规则的同源镜像），
 *   不是用户输入直拼——禁用字符在那边就已经换成空格了。
 * - API ≤ 28 写公共 Downloads 要旧存储权限（manifest 里 maxSdkVersion="28"
 *   那条）；没给时 enqueue 抛 SecurityException，调用方 runCatching 兜成
 *   "导出失败：…"。权限拒绝的私有目录兜底在 T2.5。
 */
object SessionDownloads {

    /** 入队并返回系统下载 id（>0）。异常一律上抛，由 UI 层兜文案。 */
    fun enqueue(context: Context, absoluteUrl: String, fileName: String): Long {
        val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val request = DownloadManager.Request(Uri.parse(absoluteUrl)).apply {
            setTitle(fileName)
            setMimeType("text/markdown")
            // 完成时保留通知——这就是 PRD 里那颗系统「已下载」通知的来源
            setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
        }
        return dm.enqueue(request)
    }
}
