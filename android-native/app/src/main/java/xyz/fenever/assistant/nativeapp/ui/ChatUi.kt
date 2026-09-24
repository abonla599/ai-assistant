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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftKeyPressed
import androidx.compose.ui.input.key.nativeKeyEvent
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.ChatEvent
import xyz.fenever.assistant.nativeapp.ChatMessageDto
import xyz.fenever.assistant.nativeapp.ModelInfo
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary
import xyz.fenever.assistant.nativeapp.StoredMessage
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.AiGlowBackground
import xyz.fenever.assistant.nativeapp.theme.WebTokens
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush
import xyz.fenever.assistant.nativeapp.theme.glassColor
import xyz.fenever.assistant.nativeapp.theme.glassStrongColor
import xyz.fenever.assistant.nativeapp.theme.isWebLight
import xyz.fenever.assistant.nativeapp.theme.text3Color
import xyz.fenever.assistant.nativeapp.theme.userBubbleBrush

/* 聊天主屏 —— 逐块对位旧壳加载的网页（backend/app/web/static）：
 * index.html 的 .topbar/.messages/.composer 结构，style.css 移动端那档数值
 * （@media max-width:860 就是手机上的真身），行为语义逐条照 app.js 移植。
 * 侧栏 = 拉开的抽屉（SessionDrawer）；设置 = 底部弹层，二级页在同一张弹层里
 * 换 view —— "谁都不该是第二个弹窗"。 */

/* .att-chip / .msg-atts 的附件条目：网页字段 {id,name,kind,size,url}；
 * 原生没有 blob URL，图片按 id 现取字节解码，取不到退化成 📄 标签（同一降级语义）。 */
data class AttItem(val id: String, val name: String, val kind: String, val size: Long)

/* 一条已落地的消息。transient = 网页 runStream 失败那条 ⚠️ 气泡：看得见，
 * 但 replaceMessages/outbound 都把它滤掉，不发给服务端、不混进上下文。 */
data class UiMsg(val role: String, val content: String,
                 val messageId: String? = null, val model: String? = null,
                 val feedback: Int? = null,
                 val attachments: List<AttItem> = emptyList(),
                 val transient: Boolean = false)

/* 网页 .suggestions 的四枚 chips（app.js SUGGESTIONS 逐字）。点击只填输入框、不发送。 */
val CHAT_SUGGESTIONS = listOf(
    "帮我看看这段代码有什么问题",
    "用一句话解释什么是向量数据库",
    "我在准备网络安全考研，帮我规划复习",
    "把这段内容改得更简洁",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(onRequireAuth: (String) -> Unit, onLoggedOut: () -> Unit,
               onOpenUrl: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val drawerState = rememberDrawerState(DrawerValue.Closed)

    // ---------- 状态：对照 app.js 的 state / pref ----------
    var sessionId by remember { mutableStateOf(Prefs.lastSessionId) }
    var sessions by remember { mutableStateOf(listOf<SessionSummary>()) }
    var messages by remember { mutableStateOf(listOf<UiMsg>()) }
    var input by remember { mutableStateOf("") }
    var providers by remember { mutableStateOf(listOf<ModelInfo>()) }
    var serverDefault by remember { mutableStateOf<String?>(null) }
    var pending by remember { mutableStateOf(listOf<AttItem>()) }
    var status by remember { mutableStateOf("") }
    var statusErr by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var streamText by remember { mutableStateOf<String?>(null) }
    var attachOpen by remember { mutableStateOf(false) }
    var modelMenuOpen by remember { mutableStateOf(false) }
    var drawerTick by remember { mutableStateOf(0) }
    var settingsPage by remember { mutableStateOf<String?>(null) }  // null=关；""=一级列表
    var streamJob by remember { mutableStateOf<Job?>(null) }
    var stopRequested by remember { mutableStateOf(false) }
    var editingIndex by remember { mutableStateOf<Int?>(null) }
    var copyTip by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val listState = rememberLazyListState()
    val inputFocus = remember { FocusRequester() }

    fun setStatus(t: String, err: Boolean = false) { status = t; statusErr = err }

    fun logoutIf401(e: Throwable): Boolean {
        if (e is ApiException && e.status == 401) { Prefs.clearAuth(); onLoggedOut(); return true }
        return false
    }

    /* app.js 的同一组纯函数：serverDefaultProvider / currentProvider / modelCapK。
     * 选中的那条不可用（新身份 providerId 是空的）→ 退回服务端默认，不拦发送键。 */
    fun serverDefaultProvider(): ModelInfo? =
        providers.firstOrNull { it.id == serverDefault && it.usable }

    fun currentProvider(): ModelInfo? {
        val listed = providers.firstOrNull { it.id == Prefs.defaultProviderId }
        return if (listed != null && listed.usable) listed else serverDefaultProvider()
    }

    fun modelCapK(): Int = currentProvider()?.max_context_k?.takeIf { it > 0 } ?: 64

    // ---------- 冷启动：模型清单 + 会话列表（网页 boot 的 loadModels/loadSessions） ----------
    suspend fun loadModelsNow() {
        runCatching { Api.models() }.onSuccess { r ->
            providers = r.models
            serverDefault = r.default
            if (providers.none { it.id == Prefs.defaultProviderId && it.usable })
                serverDefaultProvider()?.let { Prefs.defaultProviderId = it.id }
            if (providers.any { it.usable }) setStatus("")
        }.onFailure { if (!logoutIf401(it)) setStatus(it.message ?: "", true) }
    }
    fun refreshSessions() {
        scope.launch { runCatching { Api.listSessions() }
            .onSuccess { sessions = it }.onFailure { logoutIf401(it) } }
    }
    LaunchedEffect(Unit) { loadModelsNow(); refreshSessions() }

    // switchSession：getSession 拉全量消息（含服务端留存的附件条目）
    fun openSession(id: String) {
        sessionId = id; Prefs.lastSessionId = id
        scope.launch {
            runCatching { Api.getSession(id) }.onSuccess { d ->
                messages = d.messages.map { m ->
                    UiMsg(m.role, m.content, m.message_id, m.model,
                        attachments = (m.attachments ?: emptyList())
                            .map { AttItem(it.id, it.name, it.kind, it.size) })
                }
            }.onFailure { if (!logoutIf401(it)) setStatus(it.message ?: "", true) }
        }
    }

    // 抽屉拉开就重拉列表（网页不留快照）
    LaunchedEffect(drawerState.currentValue) {
        if (drawerState.currentValue == DrawerValue.Open) {
            drawerTick++
            scope.launch { runCatching { Api.listSessions() }.onSuccess { sessions = it } }
        }
    }

    // 贴底跟随：网页 paint() 的语义 —— 本来贴底才滚，上翻阅读不被打断
    LaunchedEffect(messages.size, streamText) {
        val info = listState.layoutInfo
        val total = info.totalItemsCount
        if (total > 0) {
            val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: 0
            if (lastVisible >= total - 2) listState.scrollToItem(total - 1)
        }
    }

    // 复制提示 1.5s 后回到「复制」（网页 setTimeout 同语义）
    LaunchedEffect(copyTip) {
        val tip = copyTip ?: return@LaunchedEffect
        delay(1500)
        if (copyTip == tip) copyTip = null
    }

    // ---------- 附件（＋菜单三格：相机 / 图片 / 文件） ----------
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw Exception("读取附件失败")
                var name = "file.bin"
                ctx.contentResolver.query(uri,
                    arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
                    ?.use { c -> if (c.moveToFirst()) name = c.getString(0) ?: name }
                val mime = ctx.contentResolver.getType(uri) ?: "application/octet-stream"
                Api.upload(bytes, name, mime)
            }.onSuccess { u ->
                pending = pending + AttItem(u.id, u.name, u.kind, u.size)
            }.onFailure { setStatus("附件上传失败：" + it.message, true) }
        }
    }
    val camera = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicturePreview()) { bmp ->
        if (bmp == null) return@rememberLauncherForActivityResult
        scope.launch {
            val out = java.io.ByteArrayOutputStream()
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
            runCatching { Api.upload(out.toByteArray(), "camera.jpg", "image/jpeg") }
                .onSuccess { u -> pending = pending + AttItem(u.id, u.name, u.kind, u.size) }
                .onFailure { setStatus("拍照上传失败：" + it.message, true) }
        }
    }

    // ---------- 持久化与出站（app.js replaceMessages / outbound 的原生对位） ----------
    suspend fun persist(list: List<UiMsg>) {
        if (sessionId.isEmpty()) return
        try {
            Api.replaceMessages(sessionId, list.filter { !it.transient }
                .map { StoredMessage(it.role, it.content, it.messageId, it.model) })
        } catch (e: Exception) {
            if (!logoutIf401(e)) setStatus("同步到服务端失败：" + e.message, true)
        }
    }

    fun outboundMsgs(): List<ChatMessageDto> =
        Api.outbound(messages.filter { it.content.isNotEmpty() && !it.transient }
            .map { ChatMessageDto(it.role, it.content) },
            Prefs.persona(sessionId), Prefs.contextTokensK, modelCapK())

    /* app.js ensureSession：会话不在清单里（或压根还没有）才建一条，model 记当前 provider。 */
    suspend fun ensureSession(): Boolean {
        if (sessionId.isNotEmpty() && sessions.any { it.session_id == sessionId }) return true
        return try {
            val created = Api.createSession(currentProvider()?.id)
            sessionId = created.session_id
            Prefs.lastSessionId = created.session_id
            messages = emptyList()
            runCatching { sessions = Api.listSessions() }
            true
        } catch (e: Exception) {
            if (!logoutIf401(e)) setStatus("会话创建失败：" + e.message, true)
            false
        }
    }

    /* ---------------- runStream（app.js send/runStream 的移植） ----------------
     * content 增量 → 局部气泡重画；done → 落 message_id/model；
     * 流内 error 事件 = 不可重试（⚠️ 占位气泡，不重发，避免重复计费）；
     * 通道层失败 = 可重试 → 回退非流式 /v1/chat；
     * 停止 = 半截内容 +「（已停止生成）」照样落本地并持久化（网页 AbortError 一支如此）。 */
    fun streamInto() {
        if (busy) return
        streamJob = scope.launch {
            busy = true; stopRequested = false
            streamText = ""
            setStatus("生成中…")
            val acc = StringBuilder()
            var doneId: String? = null
            var doneModel: String? = null
            var failed = false
            var stopped = false
            val pid = currentProvider()?.id
            val atts = messages.lastOrNull { it.role == "user" }?.attachments
                ?.map { it.id } ?: emptyList()
            val payload = Api.chatPayload(pid, pid, outboundMsgs(), atts, sessionId)
            try {
                Api.streamChat(payload).collect { ev ->
                    if (stopRequested) throw CancellationException("stop")
                    when (ev) {
                        is ChatEvent.Start -> Unit
                        is ChatEvent.Content -> { acc.append(ev.text); streamText = acc.toString() }
                        is ChatEvent.Done -> { doneId = ev.messageId; doneModel = ev.model }
                        is ChatEvent.Failed -> throw ApiException(-1, ev.message)
                    }
                }
                messages = messages + UiMsg("assistant", acc.toString(), doneId,
                    doneModel?.ifBlank { null })
            } catch (e: CancellationException) {
                stopped = true
                messages = messages + UiMsg("assistant",
                    acc.toString() + "\n\n（已停止生成）", doneId, doneModel?.ifBlank { null })
                setStatus("已停止生成")
            } catch (e: Exception) {
                val retryable = !(e is ApiException && e.status == -1)
                val fallback = try {
                    if (retryable) Api.chat(payload) else null
                } catch (e2: Exception) {
                    if (!logoutIf401(e2)) setStatus(e2.message ?: "", true)
                    null
                }
                if (fallback != null) {
                    messages = messages + UiMsg("assistant", fallback.reply,
                        fallback.message_id.ifBlank { null })
                } else {
                    failed = true
                    val msg = e.message ?: "请求失败"
                    messages = messages + UiMsg("assistant",
                        (if (acc.isNotEmpty()) acc.toString() + "\n\n" else "") + "⚠️ " + msg,
                        transient = true)
                    if (!logoutIf401(e)) setStatus(msg, true)
                }
            } finally {
                streamText = null; busy = false; streamJob = null
            }
            // persistCurrent + loadSessions：标题可能因首条消息被服务端改掉（顶栏要跟着新）
            withContext(NonCancellable) {
                persist(messages)
                runCatching { sessions = Api.listSessions() }
            }
            if (!failed && !stopped) setStatus("")
        }
    }

    /* app.js send()：空输入且没有附件 → 什么都不做；清单还没回来先补拉一次；
     * 没有可用模型 → 指路文案（管理员/普通用户两句不一样）并打开「模型服务」。 */
    fun sendNow(text: String) {
        if (busy) return
        val t = text.trim()
        if (t.isEmpty() && pending.isEmpty()) return
        streamJob = null
        scope.launch {
            if (providers.isEmpty()) loadModelsNow()
            if (currentProvider() == null) {
                setStatus(if (Prefs.role == "admin")
                    "当前没有可用模型，请在「设置 → 模型服务」中配置"
                else
                    "当前没有可用模型：可在「设置 → 模型服务」用自己的 API Key 添加模型，或联系管理员配置", true)
                settingsPage = "providers"
                return@launch
            }
            if (!ensureSession()) return@launch
            val atts = pending.toList()
            pending = emptyList()
            messages = messages + UiMsg("user", t, attachments = atts)
            input = ""
            streamInto()
        }
    }

    fun sendFeedback(index: Int, rating: Int) {
        val m = messages.getOrNull(index) ?: return
        if (m.messageId == null) {
            setStatus("这条回复缺少 message_id，无法提交反馈", true); return
        }
        scope.launch {
            runCatching { Api.feedback(m.messageId, rating) }.onSuccess {
                // 网页：同值再点撤销；message_id 缺失那句错误在上方已拦
                messages = messages.mapIndexed { i, x ->
                    if (i == index) x.copy(feedback = if (x.feedback == rating) null else rating) else x
                }
            }.onFailure { if (!logoutIf401(it)) setStatus("反馈失败：" + it.message, true) }
        }
    }

    // 顶栏标题 = syncTopTitle：在会话清单里查当前 id，查不到就是「新对话」
    val topTitle = sessions.firstOrNull { it.session_id == sessionId }?.title
        ?.ifBlank { "新对话" } ?: "新对话"

    fun closeDrawer() { scope.launch { drawerState.close() } }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionDrawer(
                tick = drawerTick,
                currentId = sessionId,
                onNewChat = {
                    // 网页 newChat：先清空再 ensureSession —— 新对话立刻建出来
                    scope.launch {
                        sessionId = ""; Prefs.lastSessionId = ""
                        if (ensureSession()) closeDrawer()
                    }
                },
                onOpen = { id -> openSession(id); closeDrawer() },
                onMemory = { settingsPage = "memory"; closeDrawer() },
                onSettings = { settingsPage = ""; closeDrawer() },
                onModelService = { settingsPage = "providers"; closeDrawer() },
                onDeleted = { id ->
                    if (id == sessionId) {
                        sessionId = ""; Prefs.lastSessionId = ""; messages = emptyList()
                    }
                },
                onLoggedOut = onLoggedOut,
                onClose = { closeDrawer() },
            )
        },
    ) {
        AiGlowBackground {
            Column(Modifier.fillMaxSize()) {
                // ---------- .topbar：☰ + 标题；玻璃底 + 1px 下边框 ----------
                Row(Modifier.fillMaxWidth().background(glassColor())
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("☰", fontSize = 16.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.clickable { scope.launch { drawerState.open() } }
                            .padding(6.dp))
                    Text(topTitle, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f))
                }
                Box(Modifier.fillMaxWidth().height(1.dp)
                    .background(MaterialTheme.colorScheme.outline))

                // ---------- .messages：padding 14/12/6，条目间 gap 22 ----------
                LazyColumn(Modifier.weight(1f).fillMaxWidth().imePadding()
                    .padding(horizontal = 12.dp),
                    state = listState,
                    contentPadding = PaddingValues(top = 14.dp, bottom = 6.dp),
                    verticalArrangement = Arrangement.spacedBy(22.dp)) {
                    if (messages.isEmpty() && !busy && streamText == null) item {
                        EmptyHero()
                    }
                    itemsIndexed(messages) { index, m ->
                        MessageRow(
                            m = m,
                            isLastAssistant = m.role == "assistant" &&
                                index == messages.lastIndex && streamText == null,
                            editing = editingIndex == index,
                            copyTip = copyTip?.takeIf { it.first == index }?.second,
                            onStartEdit = { editingIndex = index },
                            onCancelEdit = { editingIndex = null },
                            onSaveEdit = { text ->
                                editingIndex = null
                                scope.launch {
                                    val next = messages.take(index).toMutableList()
                                    next.add(messages[index].copy(content = text))
                                    persist(next)
                                    messages = next
                                    // 网页：编辑用户消息后重新生成后续（重走一轮流式）
                                    if (next[index].role == "user") streamInto()
                                }
                            },
                            onCopy = { ok -> copyTip = index to (if (ok) "已复制" else "失败") },
                            onDelete = {
                                scope.launch {
                                    val next = messages.filterIndexed { i, _ -> i != index }
                                    persist(next)
                                    messages = next
                                }
                            },
                            onRegen = {
                                scope.launch {
                                    val keep = messages.take(index)
                                    persist(keep)
                                    messages = keep
                                    streamInto()
                                }
                            },
                            onFeedback = { sendFeedback(index, it) },
                        )
                    }
                    streamText?.let { s -> item { StreamingBubble(s) } }
                }

                // ---------- .composer：附件行 / 建议 / 输入卡 / 下挂两张玻璃面板 ----------
                Column(Modifier.fillMaxWidth().imePadding()
                    .padding(horizontal = 12.dp)
                    .padding(top = 4.dp, bottom = 12.dp)) {
                    if (pending.isNotEmpty())
                        AttRow(pending) { a -> pending = pending.filter { it.id != a.id } }
                    if (messages.isEmpty() && !busy && streamText == null) {
                        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                            .horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            CHAT_SUGGESTIONS.forEach { s ->
                                SuggestionPill(s) {
                                    input = s
                                    runCatching { inputFocus.requestFocus() }
                                }
                            }
                        }
                    }
                    InputCard(
                        input = input, onInput = { input = it },
                        focusRequester = inputFocus,
                        onSendKey = { sendNow(input) },
                        attachOpen = attachOpen,
                        onToggleAttach = { attachOpen = !attachOpen; modelMenuOpen = false },
                        status = status, statusErr = statusErr, busy = busy,
                        chipModel = currentProvider()?.name ?: "选择模型",
                        chipVisible = providers.any { it.usable },
                        chipOpen = modelMenuOpen,
                        onToggleChip = { modelMenuOpen = !modelMenuOpen; attachOpen = false },
                        onStop = { stopRequested = true; streamJob?.cancel() },
                        onSend = { sendNow(input) },
                        canSend = input.isNotBlank() || pending.isNotEmpty(),
                    )
                    // .attach-menu：卡片下方玻璃面板（网页 DOM 顺序就在 .input-card 之后）
                    AnimatedVisibility(visible = attachOpen,
                        enter = fadeIn(tween(140)) + slideInVertically(tween(140)) { it / 3 }) {
                        Row(Modifier.fillMaxWidth().padding(top = 8.dp)
                            .background(glassStrongColor(), RoundedCornerShape(16.dp))
                            .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                                RoundedCornerShape(16.dp))
                            .padding(10.dp),
                            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            AttachTile("📷", "相机") { attachOpen = false; camera.launch(null) }
                            AttachTile("🖼", "图片") { attachOpen = false; picker.launch("image/*") }
                            AttachTile("📄", "文件") { attachOpen = false; picker.launch("*/*") }
                        }
                    }
                    // .model-menu：只列 usable，当前项 ✓ + 强调色；副标 共享/我的模型
                    AnimatedVisibility(visible = modelMenuOpen,
                        enter = fadeIn(tween(140)) + slideInVertically(tween(140)) { it / 3 }) {
                        Column(Modifier.fillMaxWidth().padding(top = 8.dp)
                            .background(glassStrongColor(), RoundedCornerShape(16.dp))
                            .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                                RoundedCornerShape(16.dp))
                            .padding(6.dp)) {
                            providers.filter { it.usable }.forEach { p ->
                                val cur = currentProvider()
                                val isCur = cur != null && p.id == cur.id
                                Row(Modifier.fillMaxWidth()
                                    .clickable {
                                        modelMenuOpen = false
                                        // switchModel：选择即生效；服务端「我的默认」写失败不反悔
                                        if (!isCur || p.id != Prefs.defaultProviderId) {
                                            Prefs.defaultProviderId = p.id
                                            scope.launch {
                                                runCatching { Api.setMyDefaultProvider(p.id) }
                                            }
                                            setStatus("")
                                        }
                                    }
                                    .padding(horizontal = 12.dp, vertical = 10.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    Text((if (isCur) "✓ " else "") + p.name, fontSize = 14.sp,
                                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                                        modifier = Modifier.weight(1f),
                                        color = if (isCur) MaterialTheme.colorScheme.primary
                                                else MaterialTheme.colorScheme.onSurface,
                                        fontWeight = if (isCur) FontWeight.SemiBold
                                                     else FontWeight.Normal)
                                    Text(if (p.shared) "共享" else "我的模型", fontSize = 12.sp,
                                        color = MaterialTheme.colorScheme.onSurface
                                            .copy(alpha = 0.65f))
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 设置弹层（.modal 手机档：底部出、一张弹层内换 view，不开第二层）
    if (settingsPage != null) {
        ModalBottomSheet(onDismissRequest = { settingsPage = null },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = MaterialTheme.colorScheme.surface) {
            SettingsSheet(
                page = settingsPage ?: "",
                onOpenPage = { settingsPage = it },
                onRequireAuth = { mode -> settingsPage = null; onRequireAuth(mode) },
                onOpenUrl = onOpenUrl,
                onModelsChanged = { scope.launch { loadModelsNow() } },
                onLoggedOut = { settingsPage = null; onLoggedOut() },
            )
        }
    }
}

/* ---------------- 空对话首屏（.empty-state：icon.png +「开始一段对话」，就这两样） ---------------- */
@Composable
private fun EmptyHero() {
    Column(Modifier.fillMaxWidth().padding(top = 60.dp),
        horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.padding(bottom = 14.dp)
            .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                RoundedCornerShape(16.dp))) {
            AiBrandMark(54)
        }
        Text("开始一段对话", fontSize = 20.sp, fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface)
    }
}

/* ---------------- 待发送附件行（.att-row / .att-chip） ---------------- */
@Composable
private fun AttRow(items: List<AttItem>, onRemove: (AttItem) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)
        .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items.forEach { a ->
            Row(Modifier.background(MaterialTheme.colorScheme.surfaceVariant,
                RoundedCornerShape(10.dp))
                .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                    RoundedCornerShape(10.dp))
                .padding(start = 6.dp, end = 8.dp, top = 5.dp, bottom = 5.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(Modifier.size(30.dp).background(hoverBg(), RoundedCornerShape(6.dp)),
                    contentAlignment = Alignment.Center) {
                    Text(if (a.kind == "image") "🖼" else "📄", fontSize = 14.sp)
                }
                Text(a.name, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 110.dp))
                Text(fmtSize(a.size), fontSize = 11.sp, color = text3Color())
                Text("×", fontSize = 16.sp, color = text3Color(),
                    modifier = Modifier.clickable { onRemove(a) }.padding(horizontal = 4.dp))
            }
        }
    }
}

private fun fmtSize(bytes: Long): String = when {
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    else -> "%.1f MB".format(bytes / 1024.0 / 1024.0)
}

/* .bg-hover 不在 MaterialTheme 色板上（网页它是独立一档），按主题直取。 */
@Composable
internal fun hoverBg(): Color =
    if (isWebLight()) WebTokens.LBgHover else WebTokens.BgHover

/* ---------------- 输入卡（.input-card：圆角 18 surface 描边；foot 一行五件套） ---------------- */
@Composable
private fun InputCard(input: String, onInput: (String) -> Unit,
                      focusRequester: FocusRequester, onSendKey: () -> Unit,
                      attachOpen: Boolean, onToggleAttach: () -> Unit,
                      status: String, statusErr: Boolean, busy: Boolean,
                      chipModel: String, chipVisible: Boolean, chipOpen: Boolean,
                      onToggleChip: () -> Unit, onStop: () -> Unit, onSend: () -> Unit,
                      canSend: Boolean) {
    val scheme = MaterialTheme.colorScheme
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp),
        color = scheme.surface, border = BorderStroke(1.dp, scheme.outline),
        shadowElevation = 6.dp) {
        Column(Modifier.padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 6.dp)) {
            BasicTextField(
                value = input, onValueChange = onInput,
                textStyle = TextStyle(fontSize = 15.sp, lineHeight = 24.sp,
                    color = scheme.onSurface),
                cursorBrush = SolidColor(scheme.primary),
                modifier = Modifier.fillMaxWidth().heightIn(min = 24.dp, max = 200.dp)
                    .padding(horizontal = 6.dp, vertical = 4.dp)
                    .focusRequester(focusRequester)
                    // 网页：Enter 发送、Shift+Enter 换行；只拦"没按 Shift 的 Enter"
                    .onPreviewKeyEvent { e ->
                        if (e.type == KeyEventType.Key &&
                            e.nativeKeyEvent.keyCode == android.view.KeyEvent.KEYCODE_ENTER &&
                            !e.isShiftKeyPressed) {
                            onSendKey(); true
                        } else false
                    },
                decorationBox = { inner ->
                    Box {
                        if (input.isEmpty())
                            Text("发消息，Shift + Enter 换行", fontSize = 15.sp,
                                color = text3Color(),
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp))
                        inner()
                    }
                },
            )
            Row(Modifier.fillMaxWidth().padding(top = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                // .icon-btn.plus-btn：30px 圆钮；open 态整枚旋转 45°、描边与字变强调色
                Box(Modifier.size(30.dp)
                    .rotate(if (attachOpen) 45f else 0f)
                    .background(scheme.surfaceVariant, CircleShape)
                    .border(BorderStroke(1.dp,
                        if (attachOpen) scheme.primary else scheme.outline), CircleShape)
                    .clickable(onClick = onToggleAttach),
                    contentAlignment = Alignment.Center) {
                    Text("＋", fontSize = 17.sp,
                        color = if (attachOpen) scheme.primary else scheme.onSurfaceVariant)
                }
                // .hint：flex:1，状态说人话；err 档换危险色
                Text(status, fontSize = 12.sp, maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = if (statusErr) scheme.error else text3Color(),
                    modifier = Modifier.weight(1f))
                // .model-chip：没有任何可用模型时整枚隐藏（renderModelChip 的那一句）
                if (chipVisible) {
                    Row(Modifier.background(
                            if (chipOpen) scheme.primaryContainer else scheme.surfaceVariant,
                            CircleShape)
                        .border(BorderStroke(1.dp,
                            if (chipOpen) scheme.primary else scheme.outline), CircleShape)
                        .clickable(onClick = onToggleChip)
                        .padding(horizontal = 10.dp, vertical = 5.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(chipModel, fontSize = 13.sp, maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color = scheme.onSurface,
                            modifier = Modifier.widthIn(max = 130.dp))
                        Text("⌄", fontSize = 12.sp,
                            color = scheme.onSurface.copy(alpha = 0.7f))
                    }
                }
                if (busy) {
                    // .btn 胶囊：描边 + surface 底，「停止」
                    Box(Modifier.border(BorderStroke(1.dp, scheme.outline), CircleShape)
                        .background(scheme.surface, CircleShape)
                        .clickable(onClick = onStop)
                        .padding(horizontal = 16.dp, vertical = 8.dp)) {
                        Text("停止", fontSize = 14.sp, color = scheme.onSurface)
                    }
                } else {
                    // .btn-send：36px 渐变圆钮，↑ 17/700；深主题深字、浅主题白字（网页同规则）
                    Box(Modifier.size(36.dp)
                        .background(aiPrimaryBrush(), CircleShape)
                        .clickable(enabled = canSend, onClick = onSend),
                        contentAlignment = Alignment.Center) {
                        Text("↑", fontSize = 17.sp, fontWeight = FontWeight.Bold,
                            color = if (!canSend) scheme.onSurface.copy(alpha = 0.45f)
                                    else if (isWebLight()) Color.White
                                    else WebTokens.BtnPrimaryInk)
                    }
                }
            }
        }
    }
}

/* .attach-tile：32px 图标方块（bg-hover 底、圆角 10）+ 标签。 */
@Composable
private fun AttachTile(ico: String, label: String, onClick: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Row(Modifier.background(scheme.surfaceVariant, RoundedCornerShape(14.dp))
        .border(BorderStroke(1.dp, scheme.outline), RoundedCornerShape(14.dp))
        .clickable(onClick = onClick)
        .padding(start = 9.dp, end = 16.dp, top = 9.dp, bottom = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.size(32.dp).background(hoverBg(), RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center) {
            Text(ico, fontSize = 16.sp)
        }
        Text(label, fontSize = 14.sp, color = scheme.onSurface)
    }
}

/* .suggestions button：胶囊、surface 底、line 描边、13sp text-2。 */
@Composable
private fun SuggestionPill(text: String, onClick: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Box(Modifier.background(scheme.surface, CircleShape)
        .border(BorderStroke(1.dp, scheme.outline), CircleShape)
        .clickable(onClick = onClick)
        .padding(horizontal = 14.dp, vertical = 6.dp)) {
        Text(text, fontSize = 13.sp, color = scheme.onSurfaceVariant, maxLines = 1)
    }
}

/* ---------------- 消息行（.msg：role 行 + 气泡 + 附件 + 工具行，条目内 gap 5） ----------------
 * 工具行手机上常显（style.css @media：.msg-tools{opacity:1}）；
 * 「重新生成」只在最后一条助手消息上出现，👍/👎 只属于助手 —— 与 messageNode 的两条
 * classList.toggle 同规则。 */
@Composable
private fun MessageRow(m: UiMsg, isLastAssistant: Boolean, editing: Boolean,
                       copyTip: String?,
                       onStartEdit: () -> Unit, onCancelEdit: () -> Unit,
                       onSaveEdit: (String) -> Unit,
                       onCopy: (Boolean) -> Unit, onDelete: () -> Unit,
                       onRegen: () -> Unit, onFeedback: (Int) -> Unit) {
    val mine = m.role == "user"
    val scheme = MaterialTheme.colorScheme
    val clipboard = LocalClipboardManager.current
    var draft by remember(editing) { mutableStateOf(m.content) }
    Column(Modifier.fillMaxWidth(),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        verticalArrangement = Arrangement.spacedBy(5.dp)) {
        // .msg-role：「我」/「助手 · 模型名」，11px text-3
        Text(if (mine) "我" else "助手" + (m.model?.let { " · $it" } ?: ""),
            fontSize = 11.sp, color = text3Color(),
            modifier = Modifier.padding(horizontal = 4.dp))
        if (editing) {
            // .edit-box：accent 描边、圆角 10、surface 底；Enter 保存 / Esc 取消
            BasicTextField(
                value = draft, onValueChange = { draft = it },
                textStyle = TextStyle(fontSize = 15.sp, lineHeight = 24.sp,
                    color = scheme.onSurface),
                cursorBrush = SolidColor(scheme.primary),
                modifier = Modifier.fillMaxWidth().widthIn(max = 320.dp)
                    .background(scheme.surface, RoundedCornerShape(10.dp))
                    .border(BorderStroke(1.dp, scheme.primary), RoundedCornerShape(10.dp))
                    .padding(horizontal = 12.dp, vertical = 9.dp)
                    .onPreviewKey { code, shift ->
                        if (code == android.view.KeyEvent.KEYCODE_ENTER && !shift) {
                            val t = draft.trim(); if (t.isNotEmpty()) onSaveEdit(t) else onCancelEdit()
                            true
                        } else if (code == android.view.KeyEvent.KEYCODE_ESCAPE) {
                            onCancelEdit(); true
                        } else false
                    },
            )
        } else {
            val shape = if (mine) RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp,
                bottomEnd = 6.dp, bottomStart = 20.dp)
            else RoundedCornerShape(topStart = 6.dp, topEnd = 20.dp,
                bottomEnd = 20.dp, bottomStart = 20.dp)
            val bubbleMod = if (mine) Modifier
                .widthIn(max = 320.dp)
                .background(userBubbleBrush(), shape)
            else Modifier
                .fillMaxWidth()
                .background(scheme.surfaceVariant, shape)
                .border(BorderStroke(1.dp, scheme.outline), shape)
            Column(bubbleMod.padding(
                horizontal = if (mine) 15.dp else 16.dp,
                vertical = if (mine) 10.dp else 12.dp)) {
                if (mine) Text(m.content, fontSize = 15.sp, lineHeight = 24.sp,
                    color = if (isWebLight()) WebTokens.LText else WebTokens.Text)
                else RichText(m.content, scheme.onSurface)
            }
        }
        // .msg-atts：图片缩略（≤240×168，圆角 10 描边）或 📄 胶囊；只挂在用户侧
        if (m.attachments.isNotEmpty()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)
                .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                m.attachments.forEach { a -> AttPreview(a) }
            }
        }
        // .msg-tools：复制 / 编辑(→保存) / 重新生成 / 👍 / 👎 / 删除
        Row(Modifier.fillMaxWidth().padding(horizontal = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            ToolBtn(copyTip ?: "复制") {
                val ok = runCatching {
                    clipboard.setText(AnnotatedString(m.content)); true
                }.getOrDefault(false)
                onCopy(ok)
            }
            if (editing) ToolBtn("保存") {
                val t = draft.trim(); if (t.isNotEmpty()) onSaveEdit(t) else onCancelEdit()
            } else ToolBtn("编辑") { onStartEdit() }
            if (!mine && isLastAssistant) ToolBtn("重新生成") { onRegen() }
            if (!mine) {
                ToolBtn("👍", on = m.feedback == 1) { onFeedback(1) }
                ToolBtn("👎", on = m.feedback == -1) { onFeedback(-1) }
            }
            ToolBtn("删除") { onDelete() }
        }
    }
}

private fun UiMsg.textOrEmpty(): String = content


/* .msg-tools button：无边、透明底、12px text-3、padding 3/8、圆角 7；.on 吃强调色对。 */
@Composable
private fun ToolBtn(label: String, on: Boolean = false, onClick: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Box(Modifier.background(
            if (on) scheme.primaryContainer else Color.Transparent,
            RoundedCornerShape(7.dp))
        .clickable(onClick = onClick)
        .padding(horizontal = 8.dp, vertical = 3.dp)) {
        Text(label, fontSize = 12.sp,
            color = if (on) scheme.primary else text3Color())
    }
}

/* 流式中的那条助手气泡：网页 = .msg.assistant.typing，body 里实时长字 + ▋ 呼吸光标。 */
@Composable
private fun StreamingBubble(text: String) {
    val scheme = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(topStart = 6.dp, topEnd = 20.dp,
        bottomEnd = 20.dp, bottomStart = 20.dp)
    Column(Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.Start,
        verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("助手", fontSize = 11.sp, color = text3Color(),
            modifier = Modifier.padding(horizontal = 4.dp))
        Column(Modifier.fillMaxWidth()
            .background(scheme.surfaceVariant, shape)
            .border(BorderStroke(1.dp, scheme.outline), shape)
            .padding(horizontal = 16.dp, vertical = 12.dp)) {
            if (text.isEmpty()) BlinkCursor()
            else { RichText(text, scheme.onSurface); BlinkCursor() }
        }
    }
}

/* .typing .msg-body::after { content:"▋"; animation: blink 1s steps(2) } —— 两帧硬切。 */
@Composable
private fun BlinkCursor() {
    val tr = rememberInfiniteTransition(label = "cursor")
    val a by tr.animateFloat(1f, 0f,
        infiniteRepeatable(tween(500), RepeatMode.Reverse), label = "blink")
    Text("▋", fontSize = 15.sp, color = MaterialTheme.colorScheme.primary.copy(alpha = a))
}

/* ---------------- 附件缩略图：按 id 现取字节（网页 fileBlobUrl 的原生对位） ----------------
 * LruCache 32 张；取不到 → 📄 文件名胶囊（hydrateImageUrls 的 catch 就是这句"只显示文件名"）。 */
private object AttCache {
    val mem = android.util.LruCache<String, android.graphics.Bitmap>(48)
}

@Composable
private fun AttPreview(a: AttItem) {
    val scheme = MaterialTheme.colorScheme
    val tag: @Composable () -> Unit = {
        Row(Modifier.background(scheme.surfaceVariant, CircleShape)
            .border(BorderStroke(1.dp, scheme.outline), CircleShape)
            .padding(horizontal = 10.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("📄 ${a.name}", fontSize = 12.sp, color = scheme.onSurfaceVariant)
        }
    }
    if (a.kind != "image") { tag(); return }
    var bmp by remember(a.id) { mutableStateOf(AttCache.mem.get(a.id)) }
    LaunchedEffect(a.id) {
        if (bmp == null) runCatching { Api.uploadBytes(a.id) }.onSuccess { bytes ->
            val opts = android.graphics.BitmapFactory.Options().apply { inSampleSize = 2 }
            val b = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
            if (b != null) { AttCache.mem.put(a.id, b); bmp = b }
        }
    }
    val b = bmp
    if (b == null) { tag(); return }
    Image(b.asImageBitmap(), contentDescription = a.name,
        contentScale = ContentScale.Fit,
        modifier = Modifier.widthIn(max = 240.dp).heightIn(max = 168.dp)
            .clip(RoundedCornerShape(10.dp))
            .border(BorderStroke(1.dp, scheme.outline), RoundedCornerShape(10.dp)))
}

private fun Modifier.onPreviewKey(handle: (keyCode: Int, shift: Boolean) -> Boolean): Modifier =
    onPreviewKeyEvent { e ->
        e.type == KeyEventType.Key &&
            handle(e.nativeKeyEvent.keyCode, e.nativeKeyEvent.isShiftKeyPressed)
    }

/* 轻量 Markdown：``` 围栏内是代码卡（--code-bg 深底、line 描边、圆角 14、等宽 13/1.6，
 * 横向可滚），其余按段落文本。完整 MD 排版留给网页版；原生保证可读、形状对得上。 */
@Composable
internal fun RichText(text: String, color: Color) {
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
                val preShape = RoundedCornerShape(14.dp)
                Text(seg.trimEnd('\n'),
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                    fontSize = 13.sp, lineHeight = 21.sp,
                    color = WebTokens.Text,
                    modifier = Modifier.fillMaxWidth()
                        .padding(vertical = 4.dp)
                        .background(WebTokens.CodeBg, preShape)
                        .border(1.dp, WebTokens.Line, preShape)
                        .padding(horizontal = 14.dp, vertical = 12.dp)
                        .horizontalScroll(rememberScrollState()))
            } else if (seg.isNotBlank()) Text(seg, fontSize = 15.sp, lineHeight = 25.sp,
                color = color)
        }
    }
}
