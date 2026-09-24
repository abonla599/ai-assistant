package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.FloatingActionButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
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
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun SessionListScreen(onOpenChat: (String) -> Unit, onSettings: () -> Unit,
                      onLoggedOut: () -> Unit) {
    val scope = rememberCoroutineScope()
    var sessions by remember { mutableStateOf<List<SessionSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var pendingDelete by remember { mutableStateOf<SessionSummary?>(null) }
    var creating by remember { mutableStateOf(false) }
    var search by remember { mutableStateOf("") }

    // 每次进入本屏重新拉列表：从聊天页返回时组合会重建，这里不缓存旧数据。
    LaunchedEffect(Unit) {
        loading = true; error = null
        try { sessions = Api.listSessions() }
        catch (e: Exception) {
            if (e is xyz.fenever.assistant.nativeapp.ApiException && e.status == 401) {
                Prefs.clearAuth(); onLoggedOut()
            } else error = e.message
        }
        loading = false
    }

    // 网页侧栏的 .search-box + 分组：先按标题过滤，再按日期归组（今天/昨天/7 天内/更早）
    val visible = sessions.filter {
        search.isBlank() || it.title.contains(search.trim(), ignoreCase = true)
    }
    val groupedList = LinkedHashMap<String, MutableList<SessionSummary>>()
    visible.forEach { s ->
        groupedList.getOrPut(groupOf(s.created_at)) { mutableListOf() }.add(s)
    }

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                        AiBrandMark(22)
                        // 网页 .brand 的 clip-text 渐变字标同款
                        Text(buildAnnotatedString {
                            withStyle(SpanStyle(brush = aiPrimaryBrush(),
                                fontWeight = FontWeight.SemiBold)) {
                                append("AI 智能助手")
                            }
                        }, fontSize = 17.sp)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.86f)),
            )
        },
        bottomBar = {
            // 网页 .sb-foot 的 .who-row：「我是谁 + 进设置」合成一行，整行可点
            Row(Modifier.fillMaxWidth()
                .clickable(onClick = onSettings)
                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.86f))
                .padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                Box(Modifier.size(28.dp)
                    .background(MaterialTheme.colorScheme.secondaryContainer, CircleShape),
                    contentAlignment = Alignment.Center) {
                    Text(Prefs.username.take(1).ifBlank { "·" },
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
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
        },
        floatingActionButton = {
            FloatingActionButton(onClick = {
                if (creating) return@FloatingActionButton
                creating = true
                scope.launch {
                    try { onOpenChat(Api.createSession().session_id) }
                    catch (e: Exception) { error = e.message }
                    creating = false
                }
            },
                shape = CircleShape,
                containerColor = Color.Transparent,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                elevation = FloatingActionButtonDefaults.elevation(8.dp),
                modifier = Modifier.size(56.dp)
                    .background(aiPrimaryBrush(), CircleShape),
            ) { Icon(Icons.Default.Add, contentDescription = "新对话") }
        },
    ) { pad ->
        AiGlowBackground {
        Box(Modifier.padding(pad).fillMaxSize()) {
            when {
                loading && sessions.isEmpty() ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                sessions.isEmpty() -> Column(Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    AiBrandMark(64)
                    Text(error ?: "还没有对话",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface)
                    if (error == null) Text("点右下角 ＋ 开启第一次对话",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (error != null) TextButton(onClick = {
                        scope.launch {
                            runCatching { Api.listSessions() }
                                .onSuccess { sessions = it; error = null }
                        }
                    }) { Text("重试") }
                }
                else -> LazyColumn(Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(top = 6.dp, bottom = 96.dp)) {
                    item {
                        // 网页 .search-box：会话搜索框挂在列表顶上
                        OutlinedTextField(search, { search = it },
                            placeholder = { Text("搜索会话", style = MaterialTheme.typography.bodyMedium) },
                            singleLine = true,
                            shape = RoundedCornerShape(999.dp),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                                unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant),
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp))
                    }
                    if (error != null) item {
                        Text(error!!, color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                            style = MaterialTheme.typography.bodySmall)
                    }
                    if (visible.isEmpty()) item {
                        Text("没有匹配的会话",
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
                                    .background(MaterialTheme.colorScheme.background.copy(alpha = 0.92f))
                                    .padding(horizontal = 16.dp, vertical = 6.dp))
                        }
                        items(list, key = { it.session_id }) { s ->
                            SessionRow(s,
                                onOpen = { onOpenChat(s.session_id) },
                                onDelete = { pendingDelete = s })
                        }
                    }
                }
            }
        }
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
                            .onSuccess { sessions = sessions.filter { it.session_id != target.session_id } }
                            .onFailure { error = it.message }
                    }
                }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { pendingDelete = null }) { Text("取消") } },
        )
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SessionRow(s: SessionSummary, onOpen: () -> Unit, onDelete: () -> Unit) {
    val title = s.title.ifBlank { "新对话" }
    // 网页 .sb-item：无卡片无头像，纯平的一行标题 + 省略号截断，圆角 12、hover 才有一层浅底。
    // 移动端没有 hover，给一层极淡的 surface 让行可辨，其余保持平铺。
    Column(Modifier.fillMaxWidth()
        .padding(horizontal = 10.dp, vertical = 4.dp)
        .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.55f),
            RoundedCornerShape(12.dp))
        .combinedClickable(onClick = onOpen, onLongClick = onDelete)
        .padding(horizontal = 14.dp, vertical = 11.dp)) {
        Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.Medium)
        Text(fmtTime(s.created_at) +
            if (s.model.isNotBlank()) " · ${s.model}" else "",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/* created_at 是 ISO 串：截到分钟，年份只在跨年时出现，列表行短一点。 */
private fun fmtTime(iso: String): String {
    if (iso.length < 16) return iso
    val thisYear = java.text.SimpleDateFormat("yyyy", java.util.Locale.US)
        .format(java.util.Date())
    val month = iso.substring(5, 10).replace('-', '/')
    return if (iso.startsWith(thisYear))
        "$month ${iso.substring(11, 16)}"
    else "${iso.take(10).replace('-', '/')} ${iso.substring(11, 16)}"
}
