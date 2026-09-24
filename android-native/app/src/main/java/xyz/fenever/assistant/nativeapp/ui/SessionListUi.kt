package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.SessionSummary

/* 左侧会话抽屉 = 网页 .sidebar 的原生化（旧壳拉开 ☰ 看到的那一块）。
 * 逐处对位（style.css .sidebar/.sb-* 原文数值）：
 * - 底色 --glass（移动端侧栏就是这层毛玻璃的实底近似 #10141D）+ 右描边 --line，
 *   宽 min(84vw, 300px)；
 * - 品牌行 22px 图标 + 15px 渐变字「AI 智能助手」；
 * - nav-item 9/10 内距、12 圆角、图标走 --text-2（不是强调色）；
 * - 搜索框 10 圆角、surface 底 + line 描边（不是胶囊）；
 * - 「会话」sb-label 11px --text-3；日期组头 sb-group 同字号；
 * - 活动会话行 = 弱渐变(120°, accent-soft→accent-2-soft)底 + 左缘 3px 渐变竖条
 *   （::before，180° accent→accent-2，上下各收 9px），正文颜色不变粗不染白；
 * - 「×」只落在活动行（网页 hover 才现 ≈ 触屏"选中那行才给删"），其余行长按兜底；
 * - who-row：28px 弱渐变头像 + ACCENT 色首字、「设置」+ 齿轮，整行进设置。 */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun SessionDrawer(tick: Int, currentId: String,
                  onNewChat: () -> Unit, onOpen: (String) -> Unit,
                  onMemory: () -> Unit, onSettings: () -> Unit,
                  onModelService: () -> Unit, onDeleted: (String) -> Unit,
                  onLoggedOut: () -> Unit, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var sessions by remember { mutableStateOf<List<xyz.fenever.assistant.nativeapp.SessionSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var search by remember { mutableStateOf("") }
    var pendingDelete by remember { mutableStateOf<xyz.fenever.assistant.nativeapp.SessionSummary?>(null) }

    // 抽屉每次拉开 tick++ 再拉一遍：和网页侧栏一样，不留缓存快照
    LaunchedEffect(tick) {
        loading = true; error = null
        try { sessions = Api.listSessions() }
        catch (e: Exception) {
            if (e is xyz.fenever.assistant.nativeapp.ApiException && e.status == 401) {
                xyz.fenever.assistant.nativeapp.Prefs.clearAuth(); onLoggedOut()
            } else error = e.message
        }
        loading = false
    }

    val kw = search.trim().lowercase()
    val visible = sessions.filter {
        kw.isEmpty() || it.title.lowercase().contains(kw) || it.session_id.lowercase().contains(kw)
    }
    val groupedList = LinkedHashMap<String, MutableList<SessionSummary>>()
    visible.forEach { s -> groupedList.getOrPut(groupOf(s.created_at)) { mutableListOf() }.add(s) }

    val screenW = LocalConfiguration.current.screenWidthDp.dp
    val drawerW = minOf(screenW * 0.84f, 300.dp)

    Column(Modifier.width(drawerW).fillMaxSize()
        .background(xyz.fenever.assistant.nativeapp.theme.glassColor())
        .border(1.dp, MaterialTheme.colorScheme.outline,
            RoundedCornerShape(0.dp)),
        contentPadding = PaddingValues(top = 10.dp, bottom = 10.dp)) {

        // .sb-head：品牌行 + × 关闭（× 是 only-mobile 那颗）
        Row(Modifier.fillMaxWidth().padding(start = 14.dp, end = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            xyz.fenever.assistant.nativeapp.theme.AiBrandMark(22)
            xyz.fenever.assistant.nativeapp.theme.GradientText("AI 智能助手", 15)
            Spacer(Modifier.weight(1f))
            Text("×", fontSize = 20.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(onClick = onClose)
                    .padding(horizontal = 10.dp, vertical = 4.dp))
        }

        // .sb-nav：三条导航。图标是 --text-2，不是强调色——这一排不抢会话列表的戏
        DrawerNav("＋", "新对话", onNewChat)
        DrawerNav("⌗", "模型服务", onModelService)
        DrawerNav("◈", "长期记忆", onMemory)

        // .search-box：10 圆角 surface 卡 + line 描边，placeholder「搜索会话」
        Box(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 6.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(MaterialTheme.colorScheme.surface)
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(10.dp))) {
            BasicTextField(search, { search = it }, singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                textStyle = TextStyle(fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.onSurface),
                cursorBrush = xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush(),
                decorationBox = { inner ->
                    Row(Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f)) {
                            if (search.isEmpty()) Text("搜索会话", fontSize = 14.sp,
                                color = xyz.fenever.assistant.nativeapp.theme.text3Color())
                            inner()
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth())
        }

        // .sb-label「会话」
        Text("会话", fontSize = 11.sp,
            color = xyz.fenever.assistant.nativeapp.theme.text3Color(),
            modifier = Modifier.padding(start = 10.dp, top = 12.dp, bottom = 2.dp))

        LazyColumn(Modifier.fillMaxWidth().weight(1f),
            contentPadding = PaddingValues(bottom = 8.dp)) {
            if (error != null) item {
                Text(error!!, fontSize = 12.5.sp, color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
            }
            if (visible.isEmpty()) item {
                // 网页 renderSessions 的两句空态，逐字
                Text(if (kw.isNotEmpty()) "没有匹配的会话" else "还没有会话",
                    fontSize = 11.sp,
                    color = xyz.fenever.assistant.nativeapp.theme.text3Color(),
                    modifier = Modifier.padding(horizontal = 8.dp,
                        vertical = 10.dp))
            }
            groupedList.forEach { (label, list) ->
                item {
                    // .sb-group：11px --text-3 的日期头，10/8/3 内距
                    Text(label, fontSize = 11.sp,
                        color = xyz.fenever.assistant.nativeapp.theme.text3Color(),
                        modifier = Modifier.fillMaxWidth()
                            .padding(horizontal = 8.dp, vertical = 6.dp))
                }
                items(list, key = { it.session_id }) { s ->
                    SessionDrawerRow(s, active = s.session_id == currentId,
                        onOpen = { onOpen(s.session_id); onClose() },
                        onDelete = { pendingDelete = s })
                }
            }
        }

        // .sb-foot + .who-row：整行是"进设置"的入口（网页原话：这台机器上是谁在用）
        Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp)) {
            Spacer(Modifier.height(10.dp))
            Box(Modifier.fillMaxWidth().height(1.dp)
                .background(MaterialTheme.colorScheme.outline))
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth().heightIn(min = 44.dp)
                .clip(RoundedCornerShape(12.dp))
                .clickable(onClick = { onSettings(); onClose() })
                .padding(horizontal = 6.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                // .avatar：28px 圆、弱渐变底、accent 色首字（不是实心渐变白字）
                Box(Modifier.size(28.dp)
                    .background(xyz.fenever.assistant.nativeapp.theme.aiSoftBrush(), CircleShape),
                    contentAlignment = Alignment.Center) {
                    Text(xyz.fenever.assistant.nativeapp.Prefs.username.take(1).ifBlank { "·" },
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold)
                }
                Text(Prefs.username.ifBlank { "未登录" }, fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                Text("设置", fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("⚙", fontSize = 15.sp,
                    color = xyz.fenever.assistant.nativeapp.theme.text3Color())
            }
        }
    }

    pendingDelete?.let { target ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("删除这个会话？") },
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
    // .nav-item：9/10 内距、12 圆角、14.5 字号、图标 18px --text-2
    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 2.dp)
        .clip(RoundedCornerShape(12.dp))
        .clickable(onClick = onClick)
        .background(Color.Transparent)
        .padding(horizontal = 10.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp)) {
        Text(ico, fontSize = 18.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(label, fontSize = 14.5.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
private fun SessionDrawerRow(s: xyz.fenever.assistant.nativeapp.SessionSummary,
                             active: Boolean, onOpen: () -> Unit, onDelete: () -> Unit) {
    val title = s.title.ifBlank { "新对话" }
    // .sb-item：12 圆角整行；活动行弱渐变底 + 左缘 3px 180° 渐变竖条（上下各收 9px）。
    // 文字不染白不加粗——网页选中态只是"淡了一层底"，识别度全在那根小竖条上。
    Row(Modifier.fillMaxWidth().padding(horizontal = 2.dp)
        .clip(RoundedCornerShape(12.dp))
        .then(if (active) Modifier.background(
            xyz.fenever.assistant.nativeapp.theme.aiSoftBrush(), RoundedCornerShape(12.dp))
        else Modifier)
        .combinedClickable(onClick = onOpen, onLongClick = onDelete),
        verticalAlignment = Alignment.CenterVertically) {
        if (active) Box(Modifier.width(3.dp).height(24.dp)
            .background(xyz.fenever.assistant.nativeapp.theme.aiVerticalBrush(),
                RoundedCornerShape(999.dp)))
        Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis,
            fontSize = 14.sp,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f).padding(horizontal = 10.dp, vertical = 9.dp))
        if (active) {
            // 网页 .ops 只在 hover/active 露出：这里给当前行，× 同字
            Text("×", fontSize = 15.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(onClick = onDelete)
                    .padding(horizontal = 10.dp, vertical = 6.dp))
        }
    }
}

/* 会话按日分组，标签与网页 groupLabel 逐字：今天/昨天/近 7 天/近 30 天/更早
 * （判据同一条：days<=0 今天，==1 昨天，<=7、<=30，其余更早）。
 * created_at 是 ISO 串，minSdk 24 用 SimpleDateFormat 算日差。 */
private fun groupOf(iso: String): String {
    if (iso.length < 10) return "更早"
    val fmt = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
    val then = try { fmt.parse(iso.take(10)) } catch (e: Exception) { null } ?: return "更早"
    val today = try { fmt.parse(fmt.format(java.util.Date())) } catch (e: Exception) { null }
        ?: return "更早"
    val days = ((today.time - then.time) / (24 * 3600 * 1000L)).toInt()
    return when {
        days <= 0 -> "今天"
        days == 1 -> "昨天"
        days <= 7 -> "近 7 天"
        days <= 30 -> "近 30 天"
        else -> "更早"
    }
}
