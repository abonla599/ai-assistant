package xyz.fenever.assistant.nativeapp.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.ChatEvent
import xyz.fenever.assistant.nativeapp.ChatMessageDto
import xyz.fenever.assistant.nativeapp.ModelInfo
import xyz.fenever.assistant.nativeapp.UploadInfo
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush
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
            } catch (e: kotlinx.coroutines.CancellationException) {
                // 用户主动"停止"或退屏：只收尾，不落失败气泡，也不触发非流式回退
                throw e
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

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
        TopAppBar(
            title = {
                Box {
                    val cur = models.firstOrNull { it.id == providerId }
                    TextButton(onClick = { modelsOpen = true }) {
                        Box(Modifier.size(8.dp)
                            .background(MaterialTheme.colorScheme.primary, CircleShape))
                        Spacer(Modifier.width(8.dp))
                        Text(cur?.name ?: "选择模型", fontWeight = FontWeight.SemiBold)
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
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.86f)),
        )
    }) { pad ->
        AiGlowBackground {
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
                items(messages) { m ->
                    MessageBubble(m, feedbackSent.contains(m.messageId ?: "")) { mid, rating ->
                        scope.launch {
                            runCatching { Api.feedback(mid, rating) }.onSuccess {
                                feedbackSent = feedbackSent + mid
                            }
                        }
                    }
                }
                streaming?.let { s -> if (s.isNotEmpty()) item { MessageBubble(UiMessage("assistant", s), false, null) } }
                if (busy && streaming?.isEmpty() == true) item { TypingBubble() }
            }
            if (uploads.isNotEmpty()) Row(
                Modifier.fillMaxWidth().padding(horizontal = 14.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                uploads.forEach { u ->
                    AssistChip(onClick = { uploads = uploads.filter { it.id != u.id } },
                        label = { Text(u.name, maxLines = 1) })
                }
            }
            Row(Modifier.fillMaxWidth().imePadding().padding(horizontal = 10.dp, vertical = 8.dp)
                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.96f),
                    RoundedCornerShape(26.dp))
                .border(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f),
                    RoundedCornerShape(26.dp))
                .padding(start = 4.dp, end = 8.dp, top = 2.dp, bottom = 2.dp),
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                IconButton(onClick = { picker.launch("image/*") }) {
                    // core 图标集没有 AttachFile，用文本符号避免为单个图标引入 icons-extended
                    Text("📎", style = MaterialTheme.typography.titleMedium)
                }
                OutlinedTextField(
                    value = input, onValueChange = { input = it },
                    placeholder = { Text("发消息…") },
                    maxLines = 5,
                    shape = RoundedCornerShape(20.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = Color.Transparent,
                        unfocusedBorderColor = Color.Transparent,
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                    ),
                    modifier = Modifier.weight(1f),
                )
                if (busy) {
                    TextButton(onClick = { streamJob?.cancel() }) {
                        Text("停止", color = MaterialTheme.colorScheme.error,
                            fontWeight = FontWeight.SemiBold)
                    }
                } else {
                    Button(onClick = { doSend() },
                        enabled = input.isNotBlank() || uploads.isNotEmpty(),
                        shape = CircleShape,
                        contentPadding = PaddingValues(0.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Color.Transparent,
                            contentColor = Color.White),
                        elevation = ButtonDefaults.buttonElevation(defaultElevation = 3.dp),
                        modifier = Modifier.size(48.dp)
                            .background(aiPrimaryBrush(), CircleShape)) {
                        Text("发送", style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
        }
    }
}

@Composable
private fun MessageBubble(m: UiMessage, feedbackGiven: Boolean,
                          onFeedback: ((String, Int) -> Unit)?) {
    val mine = m.role == "user"
    val shape = RoundedCornerShape(if (mine) 20.dp else 8.dp,
        if (mine) 8.dp else 20.dp, 20.dp, 20.dp)
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    AnimatedVisibility(visible = shown,
        enter = fadeIn(tween(220)) + slideInVertically(tween(220)) { it / 4 }) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        ) {
            val bubbleMod = Modifier.widthIn(max = 310.dp).then(
                when {
                    mine -> Modifier.background(aiPrimaryBrush(), shape)
                    m.failed -> Modifier.background(
                        MaterialTheme.colorScheme.errorContainer, shape)
                    else -> Modifier
                        .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.95f), shape)
                        .border(1.dp, MaterialTheme.colorScheme.outlineVariant
                            .copy(alpha = 0.5f), shape)
                })
            Column(bubbleMod.padding(12.dp)) {
                RichText(m.text,
                    when {
                        mine -> Color.White
                        m.failed -> MaterialTheme.colorScheme.onErrorContainer
                        else -> MaterialTheme.colorScheme.onSurface
                    })
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
}

/* 等首 token 的占位气泡：三个起伏的琥珀小点，代替旧版转圈+文字。 */
@Composable
private fun TypingBubble() {
    val tr = rememberInfiniteTransition(label = "typing")
    Row(Modifier.widthIn(max = 310.dp)
        .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.95f),
            RoundedCornerShape(8.dp, 20.dp, 20.dp, 20.dp))
        .padding(horizontal = 14.dp, vertical = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(5.dp),
        verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            val a by tr.animateFloat(0.25f, 1f,
                infiniteRepeatable(tween(520, delayMillis = i * 170), RepeatMode.Reverse),
                label = "dot$i")
            Box(Modifier.size(7.dp)
                .background(MaterialTheme.colorScheme.primary.copy(alpha = a), CircleShape))
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
