package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.BuildConfig
import xyz.fenever.assistant.nativeapp.ModelInfo
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.theme.ThemeMode
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

/* 设置弹层：网页 .set-sheet 的原生化（ModalBottomSheet 的 ColumnScope 内容）——
 * 居中标题 + ✕，分组小标题 + 卡片行；值的后缀语义照抄：
 * `⇅` 就地改，`›` 进二级弹单/页面。选中模型经 onModelPicked 实时回灌聊天页。 */

@Composable
fun SettingsSheet(onModelPicked: (String) -> Unit, onMemory: () -> Unit,
                  onWebApp: () -> Unit, onReset: () -> Unit,
                  onLoggedOut: () -> Unit, onClose: () -> Unit) {
    var models by remember { mutableStateOf(listOf<ModelInfo>()) }
    var modelPickOpen by remember { mutableStateOf(false) }
    var urlEditOpen by remember { mutableStateOf(false) }
    var urlDraft by remember { mutableStateOf(Prefs.baseUrl) }

    LaunchedEffect(Unit) {
        runCatching { Api.models() }.onSuccess { models = it.models }
    }

    val curModel = models.firstOrNull { it.id == Prefs.defaultProviderId }
        ?: models.firstOrNull { it.default }

    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
        .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {

        // 网页 .set-head：标题居中，✕ 靠右
        Box(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            Text("设置", style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.align(Alignment.Center))
            TextButton(onClick = onClose,
                modifier = Modifier.align(Alignment.CenterEnd)) {
                Text("✕", fontSize = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        // 我的卡片：头像首字 + 用户名 + 角色标签（网页 .set-me）
        SetCard {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Box(Modifier.size(36.dp)
                    .background(aiPrimaryBrush(), CircleShape),
                    contentAlignment = Alignment.Center) {
                    Text(Prefs.username.take(1).ifBlank { "·" },
                        color = MaterialTheme.colorScheme.onPrimary,
                        fontWeight = FontWeight.Bold)
                }
                Text(Prefs.username.ifBlank { "未登录" },
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(if (Prefs.role == "admin") "管理员" else "用户",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .border(androidx.compose.foundation.BorderStroke(
                            1.dp, MaterialTheme.colorScheme.outline), CircleShape)
                        .padding(horizontal = 10.dp, vertical = 3.dp))
            }
        }

        SetGroup("账户")
        SetCard {
            SetRow("🔑", "改密码", "›", onReset)
        }

        SetGroup("模型")
        SetCard {
            SetRow("⬡", "当前模型", (curModel?.name ?: "未选择") + "  ⇅",
                { if (models.isNotEmpty()) modelPickOpen = true })
            SetRow("⌗", "模型服务", "›", onWebApp)
        }

        SetGroup("记忆")
        SetCard {
            SetRow("◈", "长期记忆", "›", onMemory)
        }

        SetGroup("网页版")
        SetCard {
            SetRow("🌐", "在应用内打开网页版", "›", onWebApp)
        }

        SetGroup("关于")
        SetCard {
            SetRow("ⓘ", "版本",
                "v" + BuildConfig.VERSION_NAME, null)
            SetRow("☾", "外观", themeLabel() + "  ⇅", {
                ThemeMode.value = when (ThemeMode.value) {
                    "dark" -> "light"
                    "light" -> "system"
                    else -> "dark"
                }
                Prefs.themeMode = ThemeMode.value
            })
            SetRow("🌍", "服务地址", Prefs.baseUrl.removePrefix("https://"),
                { urlEditOpen = true })
        }

        SetCard(Modifier.padding(top = 6.dp)) {
            SetRow("⏻", "退出登录", "", {
                Prefs.clearAuth(); onLoggedOut()
            }, danger = true)
        }
        Spacer(Modifier.height(24.dp))
    }

    if (modelPickOpen) {
        AlertDialog(onDismissRequest = { modelPickOpen = false },
            title = { Text("当前模型") },
            text = {
                Column {
                    models.forEach { m ->
                        val on = m.id == (Prefs.defaultProviderId.ifBlank { curModel?.id ?: "" })
                        TextButton(onClick = {
                            Prefs.defaultProviderId = m.id
                            onModelPicked(m.id)
                            modelPickOpen = false
                        }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (on) "✓ ${m.name}" else m.name,
                                color = if (on) MaterialTheme.colorScheme.primary
                                        else MaterialTheme.colorScheme.onSurface)
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { modelPickOpen = false }) { Text("关闭") }
            },
        )
    }

    if (urlEditOpen) {
        AlertDialog(onDismissRequest = { urlEditOpen = false },
            title = { Text("服务地址") },
            text = {
                OutlinedTextField(urlDraft, { urlDraft = it }, singleLine = true,
                    placeholder = { Text("https://ai.fenever.xyz") },
                    modifier = Modifier.fillMaxWidth())
            },
            confirmButton = {
                TextButton(onClick = {
                    if (urlDraft.isNotBlank()) {
                        urlEditOpen = false
                        // 换服务器等于换身份世界：清凭据回登录页（与登录页改地址同语义）
                        Prefs.setBaseUrlAndReauth(urlDraft.trim())
                        onLoggedOut()
                    }
                }) { Text("保存") }
            },
            dismissButton = {
                TextButton(onClick = { urlEditOpen = false }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun themeLabel(): String = when (ThemeMode.value) {
    "dark" -> "深色"
    "light" -> "浅色"
    else -> "跟随系统"
}

@Composable
private fun SetGroup(title: String) {
    // 网页 .set-group：11px 弱化小标题，不画线，靠留白分组
    Text(title, style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 6.dp, top = 10.dp))
}

@Composable
private fun SetCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth()
        .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp))
        .border(androidx.compose.foundation.BorderStroke(
            1.dp, MaterialTheme.colorScheme.outline), RoundedCornerShape(14.dp)),
        content = content)
}

@Composable
private fun SetRow(ico: String, label: String, value: String, onClick: (() -> Unit)?,
                   danger: Boolean = false) {
    // 网页 .set-row：图标 + 标题居左，值 + ›/⇅ 居右；行间发丝分隔线
    Column {
        Row(Modifier.fillMaxWidth()
            .clickable(enabled = onClick != null) { onClick?.invoke() }
            .padding(horizontal = 14.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(ico, style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(label, style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
                color = if (danger) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurface)
            if (value.isNotEmpty()) Text(value,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                fontSize = 12.5.sp,
                modifier = Modifier.weight(1.4f, fill = false))
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outline,
            thickness = 0.5.dp, modifier = Modifier.padding(horizontal = 14.dp))
    }
}
