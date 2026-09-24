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
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.ChatEvent
import xyz.fenever.assistant.nativeapp.ChatMessageDto
import xyz.fenever.assistant.nativeapp.ModelInfo
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.UploadInfo
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.WebTokens
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush
import xyz.fenever.assistant.nativeapp.theme.userBubbleBrush

private data class UiMessage(val role: String, val text: String,
                             val messageId: String? = null, val failed: Boolean = false)

/* 网页空对话里的四枚建议 chips（.sug），照抄线上文案。 */
private val SUGGESTIONS = listOf(
    "帮我看看这段代码有什么问题",
    "用一句话解释什么是向量数据库",
    "我在准备网络安全考研，帮我规划复习",
    "把这段内容改得更简洁",
)

/* 聊天页即主页 —— 与旧壳加载的网页结构一致：
 * 顶栏 ☰ + 会话标题；会话列表是左侧抽屉（ModalDrawer）；
 * 设置与长期记忆是底部弹层（ModalBottomSheet）；
 * 空对话是品牌大图标 +「开始一段对话」+ 建议 chips；
 * 附件瓦片（相机/图片/文件）平铺在输入卡下方。 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ChatScreen(onWebApp: () -> Unit, onReset: () -> Unit, onLoggedOut: () -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val drawerState = rememberDrawerState(DrawerValue.Closed)

    var sessionId by remember { mutableStateOf(Prefs.lastSessionId) }
    var title by remember { mutableStateOf("新对话") }
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
    var attachOpen by remember { mutableStateOf(false) }
    var pickMime by remember { mutableStateOf("image/*") }
    var drawerTick by remember { mutableStateOf(0) }
    var settingsOpen by remember { mutableStateOf(false) }
    var memoryOpen by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    fun logoutIf401(e: Throwable): Boolean {
        if (e is ApiException && e.status == 401) { Prefs.clearAuth(); onLoggedOut(); return true }
        return false
    }

    LaunchedEffect(Unit) {
        runCatching { Api.models() }.onSuccess { mr ->
            models = mr.models
            // 本机默认（设置弹层里选的）优先于服务端全局默认
            providerId = Prefs.defaultProviderId.takeIf { it.isNotEmpty() }
                ?: mr.default
                ?: models.firstOrNull { it.default }?.id
                ?: models.firstOrNull()?.id
        }.onFailure { logoutIf401(it) }
    }

    // sessionId 空串 = 新对话：不拉详情，直接清空消息区
    LaunchedEffect(sessionId) {
        if (sessionId.isEmpty()) {
            title = "新对话"; messages = emptyList(); return@LaunchedEffect
        }
        runCatching { Api.getSession(sessionId) }.onSuccess { d ->
            title = d.title.ifBlank { "新对话" }
            messages = d.messages.map { UiMessage(it.role, it.content, it.message_id) }
        }.onFailure { if (!logoutIf401(it)) error = it.message }
    }

    // 抽屉每次拉开重新拉列表：和网页侧栏一样，不留缓存快照
    LaunchedEffect(drawerState.currentValue) {
        if (drawerState.currentValue == DrawerValue.Open) drawerTick++
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw Exception("读取附件失败")
                var name = if (pickMime == "image/*") "image.jpg" else "file.bin"
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

    // 网页 .attach-menu 有相机一格：原生侧 TakePicturePreview 直接唤起系统相机
    val camera = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicturePreview()) { bmp ->
        if (bmp == null) return@rememberLauncherForActivityResult
        scope.launch {
            val out = java.io.ByteArrayOutputStream()
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
            runCatching { Api.upload(out.toByteArray(), "camera.jpg", "image/jpeg") }
                .onSuccess { uploads = uploads + it }
                .onFailure { error = "拍照上传失败：${it.message}" }
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
        val pending = uploads
        uploads = emptyList()
        streaming = ""
        streamJob = scope.launch {
            // 新对话在第一条消息发出时才真正建会话（与网页端行为一致）
            var sid = sessionId
            if (sid.isEmpty()) {
                try {
                    sid = Api.createSession().session_id
                    sessionId = sid
                    Prefs.lastSessionId = sid
                } catch (e: Exception) {
                    streaming = null; busy = false; streamJob = null
                    if (!logoutIf401(e)) error = e.message
                    return@launch
                }
            }
            val payload = Api.chatPayload(providerId, history + ChatMessageDto("user", text),
                pending.map { it.id }, sid)
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

    fun openDrawer() { scope.launch { drawerState.open() } }
    fun closeDrawer() { scope.launch { drawerState.close() } }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionDrawer(
                tick = drawerTick,
                currentId = sessionId,
                onNewChat = { sessionId = ""; Prefs.lastSessionId = ""; closeDrawer() },
                onOpen = { id -> sessionId = id; Prefs.lastSessionId = id; closeDrawer() },
                onMemory = { memoryOpen = true; closeDrawer() },
                onSettings = { settingsOpen = true; closeDrawer() },
                onModelService = { closeDrawer(); onWebApp() },
                onDeleted = { id -> if (id == sessionId) { sessionId = ""; Prefs.lastSessionId = "" } },
                onLoggedOut = onLoggedOut,
                onClose = { closeDrawer() },
            )
        },
    ) {
        Scaffold(
            containerColor = Color.Transparent,
            topBar = {
                // 网页 .topbar 只有两样：☰ +「我在哪段对话里」。模型芯片在输入卡里。
                TopAppBar(
                    title = { Text(title, maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold) },
                    navigationIcon = {
                        IconButton(onClick = { openDrawer() }) {
                            Text("☰", fontSize = 20.sp,
                                color = MaterialTheme.colorScheme.onSurface)
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
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    if (messages.isEmpty() && streaming == null && !busy) item {
                        ChatHero(onPick = { s -> input = s; doSend() })
                    }
                    items(messages) { m ->
                        // onFeedback 不是末位参数（typing 在后），不能用尾随 lambda 语法
                        MessageBubble(m, feedbackSent.contains(m.messageId ?: ""), { mid, rating ->
                            scope.launch {
                                runCatching { Api.feedback(mid, rating) }.onSuccess {
                                    feedbackSent = feedbackSent + mid
                                }
                            }
                        })
                    }
                    streaming?.let { s -> if (s.isNotEmpty()) item { MessageBubble(UiMessage("assistant", s), false, null, typing = true) } }
                    if (busy && streaming?.isEmpty() == true) item {
                        // 网页版首 token 前就是"空气泡 + 呼吸光标"，这里一模一样
                        MessageBubble(UiMessage("assistant", ""), false, null, typing = true)
                    }
                }
                if (uploads.isNotEmpty()) Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    uploads.forEach { u ->
                        AssistChip(onClick = { uploads = uploads.filter { it.id != u.id } },
                            label = { Text(u.name, maxLines = 1) })
                    }
                }
                Row(Modifier.fillMaxWidth().imePadding().padding(horizontal = 12.dp, vertical = 8.dp)) {
                    // 网页 .input-card：圆角 18 表面卡 + 细描边；上行 textarea，
                    // 下行 .input-foot：＋ / 提示 / 模型芯片 / 停止 / 发送
                    Surface(Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(18.dp),
                        color = MaterialTheme.colorScheme.surface,
                        border = androidx.compose.foundation.BorderStroke(
                            1.dp, MaterialTheme.colorScheme.outline)) {
                        Column(Modifier.padding(horizontal = 8.dp, vertical = 6.dp)) {
                            OutlinedTextField(
                                value = input, onValueChange = { input = it },
                                placeholder = { Text("发消息…") },
                                maxLines = 5,
                                shape = RoundedCornerShape(14.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedBorderColor = Color.Transparent,
                                    unfocusedBorderColor = Color.Transparent,
                                    focusedContainerColor = Color.Transparent,
                                    unfocusedContainerColor = Color.Transparent,
                                ),
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Row(Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                // 网页 .btn-attach：描边圆钮 ＋；点开换薄荷底 ✕
                                Box(Modifier.size(36.dp)
                                    .background(if (attachOpen)
                                            MaterialTheme.colorScheme.primary else Color.Transparent,
                                        CircleShape)
                                    .border(androidx.compose.foundation.BorderStroke(
                                        1.dp, if (attachOpen) MaterialTheme.colorScheme.primary
                                              else MaterialTheme.colorScheme.outline), CircleShape)
                                    .clickable { attachOpen = !attachOpen },
                                    contentAlignment = Alignment.Center) {
                                    Text(if (attachOpen) "✕" else "＋",
                                        style = MaterialTheme.typography.titleMedium,
                                        color = if (attachOpen) MaterialTheme.colorScheme.onPrimary
                                                else MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                // 网页 .hint：状态说人话，占位吃掉剩余宽度
                                Text(when {
                                    busy && streaming?.isNotEmpty() == true -> "生成中…"
                                    busy -> "连接中…"
                                    else -> ""
                                }, style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.weight(1f))
                                Box {
                                    val cur = models.firstOrNull { it.id == providerId }
                                    // 网页 .model-chip：描边胶囊 + ⌄，点开纵向弹单
                                    Surface(Modifier
                                        .clickable { modelsOpen = true }
                                        .border(androidx.compose.foundation.BorderStroke(
                                            1.dp, MaterialTheme.colorScheme.outline), CircleShape),
                                        shape = CircleShape,
                                        color = MaterialTheme.colorScheme.surfaceVariant) {
                                        Text((cur?.name ?: "选择模型") + "  ⌄", maxLines = 1,
                                            style = MaterialTheme.typography.labelMedium,
                                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp))
                                    }
                                    DropdownMenu(expanded = modelsOpen,
                                        onDismissRequest = { modelsOpen = false }) {
                                        models.forEach { m ->
                                            DropdownMenuItem(
                                                text = { Text(m.name + if (m.id == providerId) "  ✓"
                                                        else if (m.usable) "" else "（不可用）") },
                                                onClick = { providerId = m.id; modelsOpen = false },
                                            )
                                        }
                                    }
                                }
                                if (busy) {
                                    TextButton(onClick = { streamJob?.cancel() }) {
                                        Text("停止", color = MaterialTheme.colorScheme.error,
                                            fontWeight = FontWeight.SemiBold)
                                    }
                                } else {
                                    // 网页 .btn-send：36px 渐变圆钮，深字
                                    Button(onClick = { doSend() },
                                        enabled = input.isNotBlank() || uploads.isNotEmpty(),
                                        shape = CircleShape,
                                        contentPadding = PaddingValues(0.dp),
                                        colors = ButtonDefaults.buttonColors(
                                            containerColor = Color.Transparent,
                                            contentColor = MaterialTheme.colorScheme.onPrimary),
                                        elevation = ButtonDefaults.buttonElevation(defaultElevation = 3.dp),
                                        modifier = Modifier.size(38.dp)
                                            .background(aiPrimaryBrush(), CircleShape)) {
                                        Text("↑", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                                    }
                                }
                            }
                        }
                    }
                }
                // 网页 .attach-menu：＋ 点开、平铺在输入卡下方的瓦片卡（相机/图片/文件）
                AnimatedVisibility(visible = attachOpen,
                    enter = fadeIn(tween(140)) + slideInVertically(tween(140)) { it / 4 },
                    modifier = Modifier.padding(horizontal = 12.dp)) {
                    Surface(Modifier.fillMaxWidth().padding(top = 4.dp),
                        shape = RoundedCornerShape(16.dp),
                        color = MaterialTheme.colorScheme.surface,
                        border = androidx.compose.foundation.BorderStroke(
                            1.dp, MaterialTheme.colorScheme.outline)) {
                        Row(Modifier.padding(10.dp),
                            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            AttachTile("📷", "相机") { attachOpen = false; camera.launch(null) }
                            AttachTile("🖼", "图片") {
                                pickMime = "image/*"; attachOpen = false; picker.launch("image/*")
                            }
                            AttachTile("📄", "文件") {
                                pickMime = "*/*"; attachOpen = false; picker.launch("*/*")
                            }
                        }
                    }
                }
            }
            }
        }
    }

    // 设置与记忆：网页壳里都是底部弹层，这里用 ModalBottomSheet 对位
    if (settingsOpen) {
        ModalBottomSheet(onDismissRequest = { settingsOpen = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            SettingsSheet(
                onModelPicked = { providerId = it },
                onMemory = { settingsOpen = false; memoryOpen = true },
                onWebApp = { settingsOpen = false; onWebApp() },
                onReset = { settingsOpen = false; onReset() },
                onLoggedOut = { settingsOpen = false; onLoggedOut() },
                onClose = { settingsOpen = false },
            )
        }
    }
    if (memoryOpen) {
        ModalBottomSheet(onDismissRequest = { memoryOpen = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            MemorySheet(onClose = { memoryOpen = false })
        }
    }
}

/* 空对话首屏：网页同款——品牌大图标 +「开始一段对话」+ 左对齐建议 chips。 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChatHero(onPick: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally) {
        AiBrandMark(64)
        Spacer(Modifier.height(14.dp))
        Text("开始一段对话", style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(6.dp))
        Text("点一条建议快速开始，或直接输入", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(22.dp))
        FlowRow(Modifier.fillMaxWidth().padding(horizontal = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SUGGESTIONS.forEach { s ->
                Surface(onClick = { onPick(s) },
                    shape = CircleShape,
                    color = Color.Transparent,
                    border = androidx.compose.foundation.BorderStroke(
                        1.dp, MaterialTheme.colorScheme.outline)) {
                    Text(s, style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp))
                }
            }
        }
    }
}

@Composable
private fun AttachTile(ico: String, label: String, onClick: () -> Unit) {
    Surface(Modifier.clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        border = androidx.compose.foundation.BorderStroke(
            1.dp, MaterialTheme.colorScheme.outline)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(ico, style = MaterialTheme.typography.titleMedium)
            Text(label, style = MaterialTheme.typography.labelLarge)
        }
    }
}

@Composable
private fun MessageBubble(m: UiMessage, feedbackGiven: Boolean,
                          onFeedback: ((String, Int) -> Unit)?,
                          typing: Boolean = false) {
    val mine = m.role == "user"
    val scheme = MaterialTheme.colorScheme
    val shape = if (mine) RoundedCornerShape(20.dp, 20.dp, 6.dp, 20.dp)
                else RoundedCornerShape(6.dp, 20.dp, 20.dp, 20.dp)
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    AnimatedVisibility(visible = shown,
        enter = fadeIn(tween(220)) + slideInVertically(tween(220)) { it / 4 }) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        ) {
            // 网页 .msg-role：气泡上方一行小字标注是谁说的
            Text(if (mine) "我" else "AI", style = MaterialTheme.typography.labelSmall,
                color = scheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp))
            val bubbleMod = (if (mine) Modifier.widthIn(max = 320.dp)
                             else Modifier.fillMaxWidth()).then(
                when {
                    m.failed && !mine -> Modifier
                        .background(scheme.errorContainer.copy(alpha = 0.6f), shape)
                        .border(1.dp, scheme.error, shape)
                    mine -> Modifier.background(userBubbleBrush(), shape)
                    else -> Modifier
                        .background(scheme.surfaceVariant, shape)
                        .border(1.dp, scheme.outline, shape)
                })
            Column(bubbleMod.padding(
                horizontal = if (mine) 15.dp else 16.dp,
                vertical = if (mine) 10.dp else 12.dp)) {
                RichText(m.text,
                    if (mine || !m.failed) scheme.onBackground else scheme.onErrorContainer)
                if (typing) BlinkCursor()
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

/* 网页 .typing .msg-body::after 那颗呼吸的 ▋。 */
@Composable
private fun BlinkCursor() {
    val tr = rememberInfiniteTransition(label = "cursor")
    val a by tr.animateFloat(1f, 0f,
        infiniteRepeatable(tween(500), RepeatMode.Reverse), label = "blink")
    Text("▋", color = MaterialTheme.colorScheme.primary.copy(alpha = a))
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
            if (code) {
                // 网页 .msg-body pre：--code-bg 深底 + --line 描边 + 14px 圆角，等宽 13px/1.6
                val preShape = RoundedCornerShape(14.dp)
                Text(seg.trimEnd('\n'),
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                    fontSize = 13.sp, lineHeight = 21.sp,
                    color = WebTokens.Text,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp)
                        .background(WebTokens.CodeBg, preShape)
                        .border(1.dp, WebTokens.Line, preShape)
                        .padding(horizontal = 14.dp, vertical = 12.dp)
                        .horizontalScroll(rememberScrollState()))
            } else if (seg.isNotBlank()) Text(seg, style = MaterialTheme.typography.bodyMedium,
                color = color)
        }
    }
}
