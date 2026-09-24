package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary

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
        topBar = {
            TopAppBar(
                title = { Text("对话") },
                actions = {
                    IconButton(onClick = onMemory) { Text("记忆", color = MaterialTheme.colorScheme.onSurface) }
                    IconButton(onClick = onWebApp) { Text("网页", color = MaterialTheme.colorScheme.onSurface) }
                    TextButton(onClick = {
                        Prefs.clearAuth(); onLoggedOut()
                    }) { Text("退出") }
                },
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
            }) { Icon(Icons.Default.Add, contentDescription = "新对话") }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when {
                loading && sessions.isEmpty() ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                sessions.isEmpty() -> Column(Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(error ?: "还没有对话", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (error != null) TextButton(onClick = {
                        scope.launch {
                            runCatching { Api.listSessions() }
                                .onSuccess { sessions = it; error = null }
                        }
                    }) { Text("重试") }
                }
                else -> LazyColumn(Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(vertical = 8.dp)) {
                    if (error != null) item {
                        Text(error!!, color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                            style = MaterialTheme.typography.bodySmall)
                    }
                    items(sessions, key = { it.session_id }) { s ->
                        ListItem(
                            headlineContent = { Text(s.title.ifBlank { "新对话" }) },
                            supportingContent = {
                                Text(s.created_at.take(16).replace('T', ' ') +
                                    if (s.model.isNotBlank()) " · ${s.model}" else "")
                            },
                            modifier = Modifier.combinedClickable(
                                onClick = { onOpenChat(s.session_id) },
                                onLongClick = { pendingDelete = s },
                            ),
                        )
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
