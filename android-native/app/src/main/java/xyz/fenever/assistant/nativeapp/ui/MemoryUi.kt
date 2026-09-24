package xyz.fenever.assistant.nativeapp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark

private data class MemRow(val id: String?, val text: String, val note: String = "")

/* 从一条记忆 JSON 里尽力取 id 与正文。真实存储（chroma）与 fake_store 的
 * 字段略有差异，统一在这里吸收，界面层只见 MemRow。 */
private fun JsonElement.asRow(): MemRow {
    val o = runCatching { jsonObject }.getOrNull() ?: return MemRow(null, toString())
    fun str(vararg keys: String): String? {
        for (k in keys) o[k]?.jsonPrimitive?.contentOrNull?.let { return it }
        return null
    }
    val meta = runCatching { o["metadata"]?.jsonObject }.getOrNull()
    val id = str("id", "memory_id") ?: meta?.let { m ->
        runCatching { m["id"]?.jsonPrimitive?.contentOrNull }.getOrNull()
    }
    val content = str("content", "text").orEmpty()
    val rel = o["relevance_score"]?.jsonPrimitive?.doubleOrNullSafe()
    val note = listOfNotNull(
        rel?.let { "相关度 ${String.format("%.2f", it)}" },
        o["weight"]?.jsonPrimitive?.intOrNull?.let { "权重 $it" },
    ).joinToString(" · ")
    return MemRow(id, content, note)
}

private fun kotlinx.serialization.json.JsonPrimitive?.doubleOrNullSafe(): Double? =
    this?.content?.toDoubleOrNull()

/* 长期记忆弹层：网页 .mem-sheet 的对位（ModalBottomSheet 内容）——
 * 「‹ 长期记忆」头部 + ✕；添加行在前（"让助手记住一件事…" + 添加），
 * 搜索行在后（"语义搜索记忆…" + 搜索）；列表限高滚动。 */
@Composable
fun MemorySheet(onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf(listOf<MemRow>()) }
    var query by remember { mutableStateOf("") }
    var draft by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun loadList() {
        scope.launch {
            busy = true; error = null
            runCatching { Api.listMemory(50) }.onSuccess { root ->
                val arr = runCatching { root.jsonObject["memories"]?.jsonArray ?: JsonArray(emptyList()) }
                    .getOrDefault(JsonArray(emptyList()))
                rows = arr.map { it.asRow() }
            }.onFailure { error = it.message }
            busy = false
        }
    }

    LaunchedEffect(Unit) { loadList() }

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        // 网页 .mem-head：标题居左，✕ 居右
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("长期记忆", style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            TextButton(onClick = onClose) {
                Text("✕", fontSize = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        // 添加行在前（网页 .mem-add）
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(draft, { draft = it },
                placeholder = { Text("让助手记住一件事，例如：我叫张三") },
                singleLine = true, shape = CircleShape,
                modifier = Modifier.weight(1f))
            TextButton(onClick = {
                if (draft.isBlank()) return@TextButton
                scope.launch {
                    busy = true; error = null
                    runCatching { Api.addMemory(draft.trim()) }.onSuccess {
                        draft = ""
                        query = ""
                        loadList()
                    }.onFailure { error = it.message }
                    busy = false
                }
            }) { Text("添加") }
        }
        // 搜索行在后（网页 .mem-search，语义搜索）
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(query, { query = it },
                placeholder = { Text("语义搜索记忆…") },
                singleLine = true, shape = CircleShape,
                modifier = Modifier.weight(1f))
            TextButton(onClick = {
                if (query.isBlank()) return@TextButton
                scope.launch {
                    busy = true; error = null
                    runCatching { Api.searchMemory(query.trim()) }.onSuccess { root ->
                        val arr = runCatching {
                            root.jsonObject["results"]?.jsonArray ?: JsonArray(emptyList())
                        }.getOrDefault(JsonArray(emptyList()))
                        rows = arr.map { it.asRow() }
                    }.onFailure { error = it.message }
                    busy = false
                }
            }) { Text("搜索") }
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall) }
        if (busy && rows.isEmpty()) CircularProgressIndicator(strokeWidth = 2.dp,
            modifier = Modifier.padding(16.dp))
        if (rows.isEmpty() && !busy) Column(Modifier.fillMaxWidth().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            AiBrandMark(48)
            Text(if (query.isNotBlank()) "没有符合条件的记忆" else "还没有记忆",
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        LazyColumn(Modifier.fillMaxWidth().heightIn(max = 400.dp),
            contentPadding = PaddingValues(vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(rows.size) { i ->
                val r = rows[i]
                // 网页记忆面板的 .mem-row：--bg-soft 底 + --line 描边 + 10px 圆角，
                // 元信息是一颗胶囊小标签，不放左侧色条。
                Column(Modifier.fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(10.dp))
                    .border(1.dp, MaterialTheme.colorScheme.outline,
                        RoundedCornerShape(10.dp))
                    .padding(horizontal = 12.dp, vertical = 10.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.Top) {
                        Text(r.text, style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.weight(1f))
                        if (r.id != null) IconButton(onClick = {
                            scope.launch {
                                runCatching { Api.deleteMemory(listOf(r.id)) }
                                    .onSuccess { rows = rows - r }
                                    .onFailure { error = it.message }
                            }
                        }) { Icon(Icons.Default.Delete, contentDescription = "删除",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                    if (r.note.isNotBlank()) {
                        Text(r.note, style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 4.dp)
                                .border(1.dp, MaterialTheme.colorScheme.outline,
                                    CircleShape)
                                .padding(horizontal = 10.dp, vertical = 2.dp))
                    }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}
