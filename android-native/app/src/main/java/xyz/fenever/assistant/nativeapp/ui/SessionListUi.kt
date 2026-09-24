package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

/* 左侧会话抽屉 —— 对位网页侧栏 .sidebar（旧壳拉开 ☰ 看到的那块）：
 * 品牌行 + 导航（新对话/模型服务/长期记忆）+ 搜索 + 日期分组列表 + 底部"我"行。
 * 当前会话行是渐变底 + 亮色左竖条（网页 .sb-item.active）；非当前行纯平文字。 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SessionDrawer(tick: Int, currentId: String,
                  onNewChat: () -> Unit, onOpen: (String) -> Unit,
                  onMemory: () -> Unit, onSettings: () -> Unit,
                  onModelService: () -> Unit, onDeleted: (String) -> Unit,
                  onLoggedOut: () -> Unit, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var sessions by remember { mutableStateOf<List<SessionSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var search by remember { mutableStateOf("") }
    var pendingDelete by remember { mutableStateOf<SessionSummary?>(null) }

    // tick=0 是首次组合加载；抽屉每次拉开 tick++ 再拉一遍，不留缓存快照
    LaunchedEffect(tick) {
        loading = true; error = null
        try { sessions = Api.listSessions() }
        catch (e: Exception) {
            if (e is ApiException && e.status == 401) { Prefs.clearAuth(); onLoggedOut() }
            else error = e.message
        }
        loading = false
    }

    val visible = sessions.filter {
        search.isBlank() || it.title.contains(search.trim(), ignoreCase = true)
    }
    val groupedList = LinkedHashMap<String, MutableList<SessionSummary>>()
    visible.forEach { s ->
        groupedList.getOrPut(groupOf(s.created_at)) { mutableListOf() }.add(s)
    }

    Column(Modifier.fillMaxSize()
        .background(MaterialTheme.colorScheme.surface)
        .padding(top = 8.dp)) {
        // 品牌行：图标 + 渐变字标 + ✕ 关闭
        Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            AiBrandMark(24)
            Text(buildAnnotatedString {
                withStyle(SpanStyle(brush = aiPrimaryBrush(), fontWeight = FontWeight.SemiBold)) {
                    append("AI 智能助手")
                }
            }, fontSize = 16.sp)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onClose) {
                Text("✕", fontSize = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        DrawerNav("＋", "新对话", onNewChat)
        DrawerNav("⌗", "模型服务", onModelService)
        DrawerNav("◈", "长期记忆", onMemory)

        Spacer(Modifier.height(6.dp))
        // 网页 .search-box：胶囊搜索框
        OutlinedTextField(search, { search = it },
            placeholder = { Text("搜索会话", style = MaterialTheme.typography.bodyMedium) },
            singleLine = true,
            shape = RoundedCornerShape(999.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp))
        // 网页 .sb-label：「会话」小标题
        Text("会话", style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 18.dp, top = 10.dp, bottom = 2.dp))

        if (loading && sessions.isEmpty()) {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(strokeWidth = 2.dp)
            }
        } else {
            LazyColumn(Modifier.fillMaxWidth().weight(1f),
                contentPadding = PaddingValues(bottom = 8.dp)) {
                if (error != null) item {
                    Text(error!!, color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.bodySmall)
                }
                if (visible.isEmpty()) item {
                    Text(if (sessions.isEmpty()) "还没有对话" else "没有匹配的会话",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 18.dp))
                }
                groupedList.forEach { (label, list) ->
                    stickyHeader {
                        // 网页 .sb-group：弱化的小日期头
                        Text(label, style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth()
                                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.94f))
                                .padding(horizontal = 16.dp, vertical = 6.dp))
                    }
                    items(list, key = { it.session_id }) { s ->
                        SessionDrawerRow(s, active = s.session_id == currentId,
                            onOpen = { onOpen(s.session_id) },
                            onDelete = { pendingDelete = s })
                    }
                }
            }
        }

        // 底部 .who-row：头像首字 + 用户名 + 「设置 ⚙」，整行进设置弹层
        Row(Modifier.fillMaxWidth()
            .clickable(onClick = onSettings)
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
            .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            Box(Modifier.size(28.dp)
                .background(aiPrimaryBrush(), CircleShape),
                contentAlignment = Alignment.Center) {
                Text(Prefs.username.take(1).ifBlank { "·" },
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onPrimary,
                    fontWeight = FontWeight.SemiBold)
            }
            Text(Prefs.username.ifBlank { "未登录" },
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("设置", style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("⚙", style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    pendingDelete?.let { target ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("删除对话？") },
            text = { Text("「${target.title.ifBlank { "新对话" }}」的历史将永久移除。") },
            confirmButton = {
                TextButton(onClick = {
                    pendingDelete = null
                    scope.launch {
                        runCatching { Api.deleteSession(target.session_id) }
                            .onSuccess {
                                sessions = sessions.filter { it.session_id != target.session_id }
                                onDeleted(target.session_id)
                            }
                            .onFailure { error = it.message }
                    }
                }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { pendingDelete = null }) { Text("取消") } },
        )
    }
}

@Composable
private fun DrawerNav(ico: String, label: String, onClick: () -> Unit) {
    // 网页 .sb-nav-item：图标强调色 + 标题，纯平一行
    Row(Modifier.fillMaxWidth()
        .clickable(onClick = onClick)
        .padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(ico, style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.primary)
        Text(label, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun SessionDrawerRow(s: SessionSummary, active: Boolean,
                             onOpen: () -> Unit, onDelete: () -> Unit) {
    val title = s.title.ifBlank { "新对话" }
    // 当前行渐变底、其余透明 —— Brush 与 Color 不能混在一个 if 表达式里，分支各自组合 Modifier
    val bgMod = if (active) Modifier.background(aiPrimaryBrush(), RoundedCornerShape(12.dp))
                else Modifier
    Row(Modifier.fillMaxWidth()
        .padding(horizontal = 8.dp, vertical = 2.dp)
        .then(bgMod)
        .clickable(onClick = onOpen),
        verticalAlignment = Alignment.CenterVertically) {
        // 当前会话的左竖条（网页 .sb-item.active::before）
        if (active) Box(Modifier.width(3.dp).height(26.dp)
            .background(MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.9f),
                RoundedCornerShape(2.dp)))
        Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
            color = if (active) MaterialTheme.colorScheme.onPrimary
                    else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f).padding(horizontal = 12.dp, vertical = 11.dp))
        // 网页端 ✕ 是 hover 才现；移动端常显，非当前行淡一点
        Text("✕", fontSize = 13.sp,
            color = if (active) MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.75f)
                    else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f),
            modifier = Modifier
                .clickable(onClick = onDelete)
                .padding(horizontal = 12.dp, vertical = 9.dp))
    }
}

/* 会话按日分组，和网页侧栏 .sb-group 同一组标签。created_at 是 ISO 串，
 * 直接截日期段比较；java.time 要 API 26，minSdk 24 用 SimpleDateFormat。 */
private fun groupOf(iso: String): String {
    if (iso.length < 10) return "更早"
    val today = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
        .format(java.util.Date())
    if (iso.take(10) == today) return "今天"
    val ms = try {
        java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
            .parse(iso.take(10))?.time ?: return "更早"
    } catch (e: Exception) { return "更早" }
    val days = ((java.util.Date().time - ms) / (24 * 3600 * 1000L)).toInt()
    return when {
        days <= 1 -> "昨天"
        days < 7 -> "7 天内"
        days < 30 -> "30 天内"
        else -> "更早"
    }
}
