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
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
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
@Serializable data class StoredMessage(val role: String, val content: String,
                                       val message_id: String? = null)
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

/* /v1/chat/stream 的帧只有四种有效事件（start/content/done/error），
   与服务端 generate() 的产出逐字对齐。 */
sealed class ChatEvent {
    data class Start(val messageId: String, val model: String) : ChatEvent()
    data class Content(val text: String) : ChatEvent()
    data class Done(val fullText: String, val messageId: String, val model: String) : ChatEvent()
    data class Failed(val message: String) : ChatEvent()
}

/* 后端接口的原生封装。契约面与 backend/app/web/static/api.js 同源：
 * - 凭据只走 Authorization: Bearer（authz._header_credential 认可的头），
 *   因此不依赖 Cookie、也不需要 X-CSRF（那头凭据在场时后端跳过 CSRF 判定）。
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

    private fun request(path: String): Request.Builder =
        Request.Builder().url(Prefs.baseUrl.trimEnd('/') + path)
            .apply { if (Prefs.token.isNotEmpty()) header("Authorization", "Bearer " + Prefs.token) }

    private fun failDetail(body: String, status: Int): String =
        runCatching {
            json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.contentOrNull
        }.getOrNull() ?: "HTTP $status"

    private suspend fun call(path: String, method: String = "GET", body: String? = null): String =
        withContext(Dispatchers.IO) {
            val rb = request(path)
            when (method) {
                "GET" -> rb.get()
                "DELETE" -> rb.delete()
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

    // ---------- 会话 ----------
    suspend fun listSessions(): List<SessionSummary> = callJson<SessionsList>("/v1/sessions").sessions
    suspend fun createSession(): SessionDetail = callJson("/v1/sessions", "POST")
    suspend fun getSession(id: String): SessionDetail =
        callJson("/v1/sessions/" + java.net.URLEncoder.encode(id, "UTF-8"))
    suspend fun deleteSession(id: String) {
        call("/v1/sessions/" + java.net.URLEncoder.encode(id, "UTF-8"), "DELETE")
    }

    // ---------- 模型清单 ----------
    suspend fun models(): ModelsResult = callJson("/v1/models")

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

    // ---------- 对话 ----------
    fun chatPayload(providerId: String?, messages: List<ChatMessageDto>,
                    attachments: List<String>, sessionId: String?): String {
        val msgs = JsonArray(messages.map { m ->
            buildJsonObject {
                put("role", JsonPrimitive(m.role))
                put("content", JsonPrimitive(m.content))
            }
        })
        return obj(listOf(
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
