package xyz.fenever.assistant.nativeapp

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class ApiException(val status: Int, message: String) : Exception(message)

@Serializable data class AuthResult(val token: String = "", val user_id: String = "",
                                    val username: String = "", val role: String = "user")
@Serializable data class MeResult(val user_id: String = "", val username: String = "",
                                  val role: String = "user")
@Serializable data class SessionSummary(val session_id: String = "", val title: String = "新对话",
                                        val created_at: String = "", val model: String = "")
@Serializable data class SessionsList(val sessions: List<SessionSummary> = emptyList())
@Serializable data class ChatMessageDto(val role: String, val content: String)
@Serializable data class AttachmentDto(val id: String = "", val name: String = "",
                                       val kind: String = "", val size: Long = 0)
@Serializable data class StoredMessage(val role: String, val content: String,
                                       val message_id: String? = null,
                                       val model: String? = null,
                                       val attachments: List<AttachmentDto>? = null)
@Serializable data class SessionDetail(val session_id: String = "", val title: String = "新对话",
                                       val created_at: String = "", val model: String = "",
                                       val messages: List<StoredMessage> = emptyList())
@Serializable data class ModelInfo(val id: String = "", val name: String = "",
                                   val model: String = "", val supports_vision: Boolean = false,
                                   val usable: Boolean = true, val default: Boolean = false,
                                   val shared: Boolean = true, val reason: String = "",
                                   val max_context_k: Int = 0)
@Serializable data class ModelsResult(val models: List<ModelInfo> = emptyList(),
                                      val default: String? = null)
@Serializable data class UploadInfo(val id: String = "", val name: String = "",
                                    val kind: String = "", val mime: String = "",
                                    val size: Long = 0)
@Serializable data class ChatReply(val reply: String = "", val message_id: String = "")
@Serializable data class ExportTicket(val path: String = "")

/* /v1/chat/stream 的帧只有四种有效事件（start/content/done/error），
   与服务端 generate() 的产出逐字对齐。 */
sealed class ChatEvent {
    data class Start(val messageId: String, val model: String) : ChatEvent()
    data class Content(val text: String) : ChatEvent()
    data class Done(val fullText: String, val messageId: String, val model: String) : ChatEvent()
    data class Failed(val message: String) : ChatEvent()
}

/* 后端接口的原生封装。契约面与 backend/app/web/static/api.js 逐条对齐：
 * - 凭据只走 Authorization: Bearer（authz._header_credential 认可的头）。
 * - 所有路径相对 Prefs.baseUrl，服务换域名只改一处。
 */
object Api {
    private val json = Json { ignoreUnknownKeys = true }
    private val JSON_MT = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)   // 流式回答与工具循环都从这条线路上走
        .writeTimeout(2, TimeUnit.MINUTES)
        .build()

    private fun request(path: String, tokenOverride: String? = null): Request.Builder =
        Request.Builder().url(Prefs.baseUrl.trimEnd('/') + path)
            .apply {
                val t = tokenOverride ?: Prefs.token
                if (t.isNotEmpty()) header("Authorization", "Bearer " + t)
            }

    private fun failDetail(body: String, status: Int): String =
        runCatching {
            json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.contentOrNull
        }.getOrNull() ?: "HTTP $status"

    private suspend fun call(path: String, method: String = "GET", body: String? = null,
                             tokenOverride: String? = null): String =
        withContext(Dispatchers.IO) {
            val rb = request(path, tokenOverride)
            when (method) {
                "GET" -> rb.get()
                "DELETE" -> rb.delete(body?.toRequestBody(JSON_MT))
                else -> rb.method(method, body?.toRequestBody(JSON_MT))
            }
            client.newCall(rb.build()).execute().use { res ->
                val text = res.body?.string() ?: ""
                if (!res.isSuccessful) throw ApiException(res.code, failDetail(text, res.code))
                text
            }
        }

    private suspend inline fun <reified T> callJson(path: String, method: String = "GET",
                                                    body: String? = null): T =
        json.decodeFromString<T>(call(path, method, body))

    private fun obj(pairs: List<Pair<String, JsonElement?>>): String = buildJsonObject {
        pairs.forEach { (k, v) -> if (v != null) put(k, v) }
    }.toString()

    private fun s(v: String?): JsonElement? = v?.let { JsonPrimitive(it) }
    private fun arr(v: List<String>?): JsonElement? =
        v?.let { JsonArray(it.map { x -> JsonPrimitive(x) }) }

    // ---------- 身份 ----------
    suspend fun login(username: String, password: String): AuthResult =
        callJson("/v1/auth/login", "POST",
            obj(listOf("username" to s(username), "password" to s(password))))

    suspend fun register(username: String, password: String, answers: List<String>): AuthResult =
        callJson("/v1/auth/register", "POST",
            obj(listOf("username" to s(username), "password" to s(password),
                       "security_answers" to arr(answers))))

    suspend fun resetPassword(username: String, answers: List<String>, newPassword: String) {
        call("/v1/auth/reset", "POST",
            obj(listOf("username" to s(username), "answers" to arr(answers),
                       "new_password" to s(newPassword))))
    }

    suspend fun me(): MeResult = callJson("/v1/auth/me")

    /** 手工令牌收编：POST /v1/auth/adopt 用头凭据换回真实身份（响应与 /v1/auth/me 同形）。 */
    suspend fun adopt(token: String): MeResult =
        json.decodeFromString<MeResult>(call("/v1/auth/adopt", "POST", null, token))

    /** 撤销指定那一枚令牌（删掉清单里某个人时用它自己的令牌，不当场切换身份）。 */
    suspend fun logout(tokenOverride: String) {
        call("/v1/auth/logout", "POST", "{}", tokenOverride)
    }

    // ---------- 会话 ----------
    suspend fun listSessions(): List<SessionSummary> = callJson<SessionsList>("/v1/sessions").sessions
    suspend fun createSession(model: String? = null): SessionDetail =
        callJson("/v1/sessions" + (if (model.isNullOrEmpty()) ""
            else "?model=" + java.net.URLEncoder.encode(model, "UTF-8")), "POST")
    suspend fun getSession(id: String): SessionDetail =
        callJson("/v1/sessions/" + enc(id))
    suspend fun deleteSession(id: String) { call("/v1/sessions/" + enc(id), "DELETE") }
    suspend fun replaceMessages(id: String, messages: List<StoredMessage>) {
        val arr = JsonArray(messages.map { m ->
            buildJsonObject {
                put("role", JsonPrimitive(m.role))
                put("content", JsonPrimitive(m.content))
                m.message_id?.let { put("message_id", JsonPrimitive(it)) }
                m.model?.let { put("model", JsonPrimitive(it)) }
            }
        })
        call("/v1/sessions/" + enc(id) + "/messages", "PUT",
            buildJsonObject { put("messages", arr) }.toString())
    }
    /* 导出：换一张一次性下载票据，原生侧把浏览器指到那个真实链接
       （响应带 Content-Disposition，与网页 location.href = t.path 同一语义）。 */
    suspend fun exportTicket(id: String): ExportTicket =
        callJson("/v1/sessions/" + enc(id) + "/export-ticket", "POST")

    private fun enc(v: String) = java.net.URLEncoder.encode(v, "UTF-8")

    // ---------- 模型清单 ----------
    suspend fun models(): ModelsResult = callJson("/v1/models")

    // ---------- 模型服务（供应商）：与 api.js 同一组路径 ----------
    /* /v1/providers 仅管理员；自助面 /v1/me/providers 仅本人。
       原生侧只消费 /v1/me/providers 的 {shared, mine, default, presets} 与
       管理员的共享增删改（网页 paneProviders 的按钮集合一字不差搬过来）。 */
    suspend fun myProviders(): JsonObject =
        json.parseToJsonElement(call("/v1/me/providers")).jsonObject
    suspend fun providers(): JsonObject =
        json.parseToJsonElement(call("/v1/providers")).jsonObject
    suspend fun addMyProvider(rec: JsonObject) { call("/v1/me/providers", "POST", rec.toString()) }
    suspend fun updateMyProvider(id: String, rec: JsonObject) {
        call("/v1/me/providers/" + enc(id), "PUT", rec.toString())
    }
    suspend fun deleteMyProvider(id: String) { call("/v1/me/providers/" + enc(id), "DELETE") }
    suspend fun setMyDefaultProvider(id: String) {
        call("/v1/me/providers/default", "POST",
            obj(listOf("provider_id" to s(id))))
    }
    suspend fun testMyProvider(id: String): JsonObject =
        json.parseToJsonElement(call("/v1/me/providers/" + enc(id) + "/test", "POST")).jsonObject
    suspend fun setDefaultProvider(id: String) {
        call("/v1/providers/" + enc(id) + "/default", "POST")
    }
    suspend fun testProvider(id: String): JsonObject =
        json.parseToJsonElement(call("/v1/providers/" + enc(id) + "/test", "POST")).jsonObject
    suspend fun testProviderDraft(rec: JsonObject): JsonObject =
        json.parseToJsonElement(call("/v1/providers/test", "POST", rec.toString())).jsonObject

    /** /health 免鉴权；build 字段 = 服务端构建戳（关于页「服务端 …」那一截）。 */
    suspend fun health(): JsonObject =
        json.parseToJsonElement(call("/health")).jsonObject
    suspend fun testMyProviderDraft(rec: JsonObject): JsonObject =
        json.parseToJsonElement(call("/v1/me/providers/test", "POST", rec.toString())).jsonObject
    suspend fun addProvider(rec: JsonObject) { call("/v1/providers", "POST", rec.toString()) }
    suspend fun updateProvider(id: String, rec: JsonObject) {
        call("/v1/providers/" + enc(id), "PUT", rec.toString())
    }
    suspend fun deleteProvider(id: String) { call("/v1/providers/" + enc(id), "DELETE") }

    // ---------- 附件 ----------
    suspend fun upload(bytes: ByteArray, filename: String, mime: String): UploadInfo =
        withContext(Dispatchers.IO) {
            val part = MultipartBody.Part.createFormData("file", filename,
                bytes.toRequestBody(mime.ifBlank { "application/octet-stream" }.toMediaType()))
            val body = MultipartBody.Builder().setType(MultipartBody.FORM).addPart(part).build()
            val rb = request("/v1/uploads").post(body)
            client.newCall(rb.build()).execute().use { res ->
                val text = res.body?.string() ?: ""
                if (!res.isSuccessful) throw ApiException(res.code, failDetail(text, res.code))
                json.decodeFromString<UploadInfo>(text)
            }
        }

    /* 消息气泡里的图片缩略图：网页端是 fetch(...)/file → blob URL；
       原生侧取同一端点的字节自己解码，取不到就退化成 📄 文件名（同一降级语义）。 */
    suspend fun uploadBytes(id: String): ByteArray = withContext(Dispatchers.IO) {
        val rb = request("/v1/uploads/" + enc(id) + "/file")
        client.newCall(rb.build()).execute().use { res ->
            val text = if (res.isSuccessful) null else res.body?.string().orEmpty()
            if (!res.isSuccessful) throw ApiException(res.code, failDetail(text.orEmpty(), res.code))
            res.body?.bytes() ?: ByteArray(0)
        }
    }

    // ---------- 对话 ----------
    /* token 估算与截断逐行照抄 app.js 的 estimateTokens/truncateWithin：
     * CJK 一字一 token，其余四个字符一 token；每条 +4 包装开销；
     * 最后一条永远带上，往前塞不下就整条收手——不回头丢中间。 */
    fun estimateTokens(text: String): Int {
        var cjk = 0
        for (ch in text) {
            val c = ch.code
            if ((c in 0x2E80..0x9FFF) || (c in 0xF900..0xFAFF) || (c in 0xFF01..0xFF60)) cjk++
        }
        return cjk + kotlin.math.ceil((text.length - cjk) / 4.0).toInt()
    }

    fun truncateWithin(list: List<ChatMessageDto>, budgetTokens: Int): List<ChatMessageDto> {
        val out = ArrayDeque<ChatMessageDto>()
        var used = 0
        for (i in list.lastIndex downTo 0) {
            val cost = estimateTokens(list[i].content) + 4
            if (out.isNotEmpty() && used + cost > budgetTokens) break
            out.addFirst(list[i])
            used += cost
        }
        return out.toList()
    }

    /** 组装出站消息：本机历史（去掉空/瞬时条）按预算截断，角色设定置顶为 system。
     *  budgetK 读数时对模型上限再取一次 min（与网页 outbound() 同一条防线）。 */
    fun outbound(history: List<ChatMessageDto>, persona: String, contextTokensK: Int,
                 modelCapK: Int): List<ChatMessageDto> {
        val reserve = if (persona.isNotEmpty()) estimateTokens(persona) + 4 else 0
        val budgetK = minOf(contextTokensK, modelCapK)
        val msgs = truncateWithin(history.filter { it.content.isNotEmpty() },
            maxOf(500, budgetK * 1000 - reserve))
        return if (persona.isNotEmpty()) listOf(ChatMessageDto("system", persona)) + msgs else msgs
    }

    /* payload 形状与网页一致：{model, provider, messages, attachments, session_id}。
       providerId 传 null 时整个键省略（服务端用它自己的默认）。 */
    fun chatPayload(providerId: String?, model: String?, messages: List<ChatMessageDto>,
                    attachments: List<String>, sessionId: String?): String {
        val msgs = JsonArray(messages.map { m ->
            buildJsonObject {
                put("role", JsonPrimitive(m.role))
                put("content", JsonPrimitive(m.content))
            }
        })
        return obj(listOf(
            "model" to s(model),
            "provider" to s(providerId),
            "messages" to msgs,
            "attachments" to arr(attachments),
            "session_id" to s(sessionId),
        ))
    }

    suspend fun chat(payload: String): ChatReply = callJson("/v1/chat", "POST", payload)

    suspend fun feedback(messageId: String, rating: Int) {
        call("/v1/feedback", "POST",
            obj(listOf("message_id" to s(messageId), "rating" to JsonPrimitive(rating))))
    }

    /* 流式对话。服务端帧形状固定：`data: {json}\n\n`，JSON 内不含裸换行，
       所以按行读、空行封帧即可，无需引入 SSE 库。 */
    fun streamChat(payload: String): Flow<ChatEvent> = flow {
        val rb = request("/v1/chat/stream").post(payload.toRequestBody(JSON_MT))
        client.newCall(rb.build()).execute().use { res ->
            if (!res.isSuccessful) {
                val text = res.body?.string() ?: ""
                throw ApiException(res.code, failDetail(text, res.code))
            }
            val src = res.body!!.source()
            var dataLine: String? = null
            while (true) {
                val line = src.readUtf8Line() ?: break
                if (line.isEmpty()) {
                    dataLine?.let { parseFrame(it)?.let { ev -> emit(ev) } }
                    dataLine = null
                    continue
                }
                if (line.startsWith("data:")) dataLine = line.substring(5).trim()
            }
            dataLine?.let { parseFrame(it)?.let { ev -> emit(ev) } }
        }
    }.flowOn(Dispatchers.IO)

    private fun parseFrame(data: String): ChatEvent? = runCatching {
        val o = json.parseToJsonElement(data).jsonObject
        when (o["type"]?.jsonPrimitive?.contentOrNull) {
            "start" -> ChatEvent.Start(
                o["message_id"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                o["model"]?.jsonPrimitive?.contentOrNull.orEmpty())
            "content" -> ChatEvent.Content(o["text"]?.jsonPrimitive?.contentOrNull.orEmpty())
            "done" -> ChatEvent.Done(
                o["full_text"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                o["message_id"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                o["model"]?.jsonPrimitive?.contentOrNull.orEmpty())
            "error" -> ChatEvent.Failed(
                o["message"]?.jsonPrimitive?.contentOrNull ?: "模型返回错误")
            else -> null
        }
    }.getOrNull()

    // ---------- 记忆 ----------
    suspend fun addMemory(content: String): JsonElement =
        json.parseToJsonElement(call("/v1/memory/add", "POST",
            obj(listOf("content" to s(content), "summarize" to JsonPrimitive(false)))))

    suspend fun listMemory(limit: Int = 50): JsonElement =
        json.parseToJsonElement(call("/v1/memory/list?limit=$limit"))

    suspend fun searchMemory(query: String, topK: Int = 10): JsonElement =
        callJson<JsonElement>("/v1/memory/search", "POST",
            obj(listOf("query" to s(query), "top_k" to JsonPrimitive(topK.coerceIn(1, 20)))))

    suspend fun deleteMemory(ids: List<String>) {
        call("/v1/memory/delete", "DELETE",
            obj(listOf("memory_ids" to arr(ids))))
    }
}
