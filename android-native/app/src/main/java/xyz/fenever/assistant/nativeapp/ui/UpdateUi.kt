package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import xyz.fenever.assistant.nativeapp.update.Updater

/* 应用内更新的两个对话框（v0.23 T1.7）：下载进度 + 「安装未知应用」授权引导。
 * 状态全部住在 Updater（对象级快照），所以设置弹层被划掉、二级页换来换去，
 * 下载中的进度都不会丢——重开设置页时对话框会原样再挂上来。 */

@Composable
fun UpdateProgressDialog(onCancel: () -> Unit) {
    val version = Updater.decision?.version ?: ""
    AlertDialog(onDismissRequest = { /* 下载中不给"点外面"关掉的路，取消只有一个按钮 */ },
        title = { Text("下载 v$version", fontSize = 17.sp) },
        text = {
            Column(Modifier.padding(vertical = 6.dp)) {
                if (Updater.totalBytes > 0) {
                    LinearProgressIndicator(progress = { Updater.percent / 100f },
                        modifier = Modifier.padding(vertical = 8.dp))
                } else {
                    LinearProgressIndicator(modifier = Modifier.padding(vertical = 8.dp))
                }
                Text(if (Updater.totalBytes > 0)
                        "已收 ${fmtBytes(Updater.doneBytes)} / ${fmtBytes(Updater.totalBytes)}"
                    else "已收 ${fmtBytes(Updater.doneBytes)}",
                    fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onCancel) { Text("取消") } })
}

/** Updater.installGuideVersion 非空时挂出来：装不了不是死路，给出那两页能去的路。 */
@Composable
fun InstallGuideDialog(onGoSettings: () -> Unit, onDismiss: () -> Unit) {
    val v = Updater.installGuideVersion ?: return
    val noPage = v == "NO-PAGE"
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(if (noPage) "没能打开系统设置" else "装不了：v$v", fontSize = 17.sp) },
        text = { Text(if (noPage)
                "这台手机的系统没有给出可以改这个开关的页面。手动到「设置 → 应用 → AI 助手」里" +
                "允许\"安装未知应用\"，再回来点一次立即更新就行。"
            else
                "这台手机还没允许「AI 助手」安装应用。打开那个开关后再回来点一次立即更新就行。",
            fontSize = 14.sp) },
        confirmButton = {
            if (noPage) TextButton(onClick = onDismiss) { Text("好") }
            else TextButton(onClick = onGoSettings) { Text("去设置") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("稍后") } })
}

private fun fmtBytes(b: Long): String = when {
    b >= 1_048_576 -> String.format("%.1f MB", b / 1_048_576.0)
    else -> (b / 1024).toString() + " KB"
}
