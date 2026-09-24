package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.FloatingActionButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun SessionListScreen(onOpenChat: (String) -> Unit, onMemory: () -> Unit,
                      onWebApp: () -> Unit, onLoggedOut: () -> Unit) {
    val scope = rememberCoroutineScope()
    var sessions by remember { mutableStateOf<List<SessionSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var pendingDelete by remember { mutableStateOf<SessionSummary?>(null) }
    var creating by remember { mutableStateOf(false) }

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

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("对话", fontWeight = FontWeight.ExtraBold)
                        Text(Prefs.username.ifBlank { "AI 助手" },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                },
                actions = {
                    TextButton(onClick = onMemory) { Text("记忆") }
                    TextButton(onClick = onWebApp) { Text("网页") }
                    TextButton(onClick = {
                        Prefs.clearAuth(); onLoggedOut()
                    }) { Text("退出", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.86f)),
            )
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
                shape = RoundedCornerShape(18.dp),
                containerColor = Color.Transparent,
                contentColor = Color.White,
                elevation = FloatingActionButtonDefaults.elevation(8.dp),
                modifier = Modifier.size(60.dp)
                    .background(aiPrimaryBrush(), RoundedCornerShape(18.dp)),
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
                    contentPadding = PaddingValues(top = 8.dp, bottom = 96.dp)) {
                    if (error != null) item {
                        Text(error!!, color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                            style = MaterialTheme.typography.bodySmall)
                    }
                    items(sessions, key = { it.session_id }) { s ->
                        SessionRow(s,
                            onOpen = { onOpenChat(s.session_id) },
                            onDelete = { pendingDelete = s })
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SessionRow(s: SessionSummary, onOpen: () -> Unit, onDelete: () -> Unit) {
    val title = s.title.ifBlank { "新对话" }
    Card(
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f)),
        elevation = CardDefaults.cardElevated(defaultElevation = 1.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 5.dp)
            .combinedClickable(onClick = onOpen, onLongClick = onDelete),
    ) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(Modifier.size(42.dp)
                .background(aiPrimaryBrush(), RoundedCornerShape(13.dp)),
                contentAlignment = Alignment.Center) {
                Text(title.take(1), color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Black)
            }
            Column(Modifier.weight(1f)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold)
                Text(fmtTime(s.created_at) +
                    if (s.model.isNotBlank()) " · ${s.model}" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/* created_at 是 ISO 串：截到分钟，年份只在跨年时出现，列表行短一点。
 * 用 SimpleDateFormat 而不是 java.time——后者要 API 26，minSdk 24 会挂。 */
private fun fmtTime(iso: String): String {
    if (iso.length < 16) return iso
    val thisYear = java.text.SimpleDateFormat("yyyy", java.util.Locale.US)
        .format(java.util.Date())
    val month = iso.substring(5, 10).replace('-', '/')
    return if (iso.startsWith(thisYear))
        "$month ${iso.substring(11, 16)}"
    else "${iso.take(10).replace('-', '/')} ${iso.substring(11, 16)}"
}
