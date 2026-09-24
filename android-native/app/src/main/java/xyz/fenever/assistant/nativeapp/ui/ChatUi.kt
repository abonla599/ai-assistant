package xyz.fenever.assistant.nativeapp.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.ChatEvent
import xyz.fenever.assistant.nativeapp.ChatMessageDto
import xyz.fenever.assistant.nativeapp.ModelInfo
import xyz.fenever.assistant.nativeapp.UploadInfo

private data class UiMessage(val role: String, val text: String,
                             val messageId: String? = null, val failed: Boolean = false)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(sessionId: String, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = androidx.compose.ui.platform.LocalContext.current

    var messages by remember { mutableStateOf(listOf<UiMessage>()) }
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var streaming by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var models by remember { mutableStateOf(listOf<ModelInfo>()) }
    var providerId by remember { mutableStateOf<String?>(null) }
    var modelsOpen by remember { mutableStateOf(false) }
    var uploads by remember { mutableStateOf(listOf<UploadInfo>()) }
    var feedbackSent by remember { mutableStateOf(setOf<String>()) }
    var streamJob by remember { mutableStateOf<Job?>(null) }
    val listState = rememberLazyListState()

    LaunchedEffect(sessionId) {
        runCatching { Api.getSession(sessionId) }.onSuccess { detail ->
            messages = detail.messages.map { UiMessage(it.role, it.content, it.message_id) }
        }.onFailure { error = it.message }
        runCatching { Api.models() }.onSuccess { mr ->
            models = mr.models
            providerId = mr.default
                ?: models.firstOrNull { it.default }?.id
                ?: models.firstOrNull()?.id
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw Exception("读取附件失败")
                var name = "image.jpg"
                ctx.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME),
                    null, null, null)?.use { c ->
                    if (c.moveToFirst()) name = c.getString(0) ?: name
                }
                val mime = ctx.contentResolver.getType(uri) ?: "image/jpeg"
                Api.upload(bytes, name, mime)
            }.onSuccess { uploads = uploads + it }
                .onFailure { error = "附件上传失败：${it.message}" }
        }
    }

    LaunchedEffect(messages.size, streaming) {
        val total = listState.layoutInfo.totalItemsCount
        if (total > 0) listState.animateScrollToItem(total - 1)
    }

    fun doSend() {
        if (busy) return
        val text = input.trim()
        if (text.isEmpty() && uploads.isEmpty()) return
        busy = true; error = null
        val history = messages.filter { !it.failed }.map { ChatMessageDto(it.role, it.text) }
        val shownText = if (text.isEmpty()) "[附件]" else text
        messages = messages + UiMessage("user", shownText)
        input = ""
        val payload = Api.chatPayload(providerId, history + ChatMessageDto("user", text),
            uploads.map { it.id }, sessionId)
        uploads = emptyList()
        streaming = ""
        streamJob = scope.launch {
            var acc = StringBuilder()
            var doneEv: ChatEvent.Done? = null
            try {
                Api.streamChat(payload).collect { ev ->
                    when (ev) {
                        is ChatEvent.Content -> { acc.append(ev.text); streaming = acc.toString() }
                        is ChatEvent.Done -> doneEv = ev
                        is ChatEvent.Failed -> throw ApiException(-1, ev.message)
                        is ChatEvent.Start -> Unit
                    }
                }
                val d = doneEv ?: throw ApiException(0, "流式响应未正常结束")
                messages = messages + UiMessage("assistant",
                    d.fullText.ifEmpty { acc.toString() }, d.messageId)
            } catch (e: Exception) {
                if (acc.isEmpty()) {
                    // 通道层面失败才退回非流式（与 Web 的 retryable 语义一致）；
                    // 模型中途报错（status == -1 的流内 error 事件）不重试，避免重复计费。
                    val retryable = e !is ApiException || e.status != -1
                    val fallback = try {
                        if (retryable) Api.chat(payload) else null
                    } catch (e2: Exception) { error = e2.message; null }
                    if (fallback != null) {
                        messages = messages + UiMessage("assistant", fallback.reply, fallback.message_id)
                    } else {
                        messages = messages + UiMessage("assistant", e.message ?: "请求失败", failed = true)
                    }
                } else {
                    messages = messages + UiMessage("assistant",
                        acc.toString() + "\n（回答中断：${e.message}）", failed = true)
                }
            } finally {
                streaming = null; busy = false; streamJob = null
            }
        }
    }

    Scaffold(topBar = {
        TopAppBar(
            title = {
                Box {
                    val cur = models.firstOrNull { it.id == providerId }
                    TextButton(onClick = { modelsOpen = true }) {
                        Text(cur?.name ?: "选择模型")
                    }
                    DropdownMenu(expanded = modelsOpen, onDismissRequest = { modelsOpen = false }) {
                        models.forEach { m ->
                            DropdownMenuItem(
                                text = { Text(m.name + if (m.usable) "" else "（不可用）") },
                                onClick = { providerId = m.id; modelsOpen = false },
                            )
                        }
                    }
                }
            },
            navigationIcon = {
                IconButton(onClick = { streamJob?.cancel(); onBack() }) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                }
            },
        )
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
            }
            LazyColumn(Modifier.weight(1f), state = listState,
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(messages) { m -> MessageBubble(m, feedbackSent.contains(m.messageId)) { mid, rating ->
                    scope.launch {
                        runCatching { Api.feedback(mid, rating) }.onSuccess {
                            feedbackSent = feedbackSent + mid
                        }
                    }
                } }
                streaming?.let { s -> if (s.isNotEmpty()) item { MessageBubble(UiMessage("assistant", s), false, null) } }
                if (busy && streaming?.isEmpty() == true) item {
                    Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.padding(4.dp))
                        Text("思考中…", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (uploads.isNotEmpty()) Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                uploads.forEach { u ->
                    AssistChip(onClick = { uploads = uploads.filter { it.id != u.id } },
                        label = { Text(u.name, maxLines = 1) })
                }
            }
            Row(Modifier.fillMaxWidth().imePadding().padding(8.dp),
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                IconButton(onClick = { picker.launch("image/*") }) {
                    Icon(Icons.Default.AttachFile, contentDescription = "添加图片")
                }
                OutlinedTextField(
                    value = input, onValueChange = { input = it },
                    placeholder = { Text("发消息…") },
                    maxLines = 5, modifier = Modifier.weight(1f),
                )
                if (busy) {
                    TextButton(onClick = { streamJob?.cancel() }) { Text("停止") }
                } else {
                    Button(onClick = { doSend() }) { Text("发送") }
                }
            }
        }
    }
}

@Composable
private fun MessageBubble(m: UiMessage, feedbackGiven: Boolean,
                          onFeedback: ((String, Int) -> Unit)?) {
    val mine = m.role == "user"
    val bg = when {
        mine -> MaterialTheme.colorScheme.primary
        m.failed -> MaterialTheme.colorScheme.errorContainer
        else -> MaterialTheme.colorScheme.surfaceVariant
    }
    val fg = when {
        mine -> MaterialTheme.colorScheme.onPrimary
        m.failed -> MaterialTheme.colorScheme.onErrorContainer
        else -> MaterialTheme.colorScheme.onSurface
    }
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
    ) {
        Column(
            Modifier.widthIn(max = 300.dp)
                .background(bg, RoundedCornerShape(if (mine) 14.dp else 6.dp,
                    if (mine) 6.dp else 14.dp, 14.dp, 14.dp))
                .padding(10.dp)) {
            RichText(m.text, fg)
        }
        if (!mine && m.messageId != null && !m.failed && onFeedback != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = { onFeedback(m.messageId, 1) }, enabled = !feedbackGiven) {
                    Text(if (feedbackGiven) "已反馈" else "有用",
                        style = MaterialTheme.typography.labelSmall)
                }
                TextButton(onClick = { onFeedback(m.messageId, -1) }, enabled = !feedbackGiven) {
                    Text("没用", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

/* 轻量渲染：``` 围栏内的内容用等宽小卡片呈现，其余按段落文本。
 * 完整 Markdown 交给网页版；原生端保证代码可读即可。 */
@Composable
private fun RichText(text: String, color: androidx.compose.ui.graphics.Color) {
    val parts = ArrayList<Pair<String, Boolean>>()
    var rest = text
    while (true) {
        val open = rest.indexOf("```")
        if (open < 0) { parts.add(rest to false); break }
        if (open > 0) parts.add(rest.substring(0, open) to false)
        rest = rest.substring(open + 3)
        val nl = rest.indexOf('\n').takeIf { it >= 0 && it < 40 } ?: 0
        if (nl > 0) rest = rest.substring(nl + 1)
        val close = rest.indexOf("```")
        if (close < 0) { parts.add(rest to true); break }
        parts.add(rest.substring(0, close) to true)
        rest = rest.substring((close + 3).let {
            val after = rest.indexOf('\n', it)
            if (after >= 0 && after - it < 40) after + 1 else it
        })
    }
    Column {
        parts.forEach { (seg, code) ->
            if (code) Text(seg, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                style = MaterialTheme.typography.bodySmall, color = color,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(androidx.compose.ui.graphics.Color.Black.copy(alpha = 0.08f))
                    .padding(6.dp))
            else if (seg.isNotBlank()) Text(seg, style = MaterialTheme.typography.bodyMedium,
                color = color)
        }
    }
}
