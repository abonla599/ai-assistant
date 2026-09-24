package xyz.fenever.assistant.nativeapp

import android.content.Context
import android.content.SharedPreferences
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/* 本机配置与凭据存储。结构照抄网页端 localStorage 的那套事实来源：
 * - identities/currentId：这台机器上认识谁（多账户切换），上限 5 个人；
 *   条目字段与 app.js 的 addIdentity 逐一对应（userId/username/role/
 *   lastSessionId/providerId/addedAt）。网页方案 C 不存凭据明文（走 Cookie），
 *   原生没有 Cookie 通道，Bearer 令牌必须留在本机条目里——这是唯一一处
 *   有意不同步的实现差异，存储位置是应用私有目录。
 * - persona:{sessionId}：角色设定按会话存，全局不共享。
 * - contextTokensK：上下文预算（k token），默认 8，同网页。
 * - reminders：提醒存在设备侧（服务端没有这件事），所以原生也是本机排期。
 */
@Serializable
data class Identity(val userId: String, val username: String = "", val role: String = "user",
                    val token: String = "", val lastSessionId: String = "",
                    val providerId: String = "", val addedAt: String = "")

object Prefs {
    private const val PREF_NAME = "ai_native_prefs"
    private const val IDENTITY_CAP = 5
    private lateinit var sp: SharedPreferences
    private val json = Json { ignoreUnknownKeys = true }

    fun init(ctx: Context) {
        sp = ctx.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        // 冷启动第一帧就带上外观，不闪一下深色
        xyz.fenever.assistant.nativeapp.theme.ThemeMode.value = themeMode
    }

    var baseUrl: String
        get() = sp.getString("base_url", null) ?: BuildConfig.DEFAULT_BASE_URL
        set(value) = sp.edit().putString("base_url", value.trim().trimEnd('/')).apply()

    /* 外观两态：dark / light，与网页设置行同值（没有"跟随系统"）。 */
    var themeMode: String
        get() = sp.getString("theme_mode", "dark") ?: "dark"
        set(value) {
            sp.edit().putString("theme_mode", value).apply()
            xyz.fenever.assistant.nativeapp.theme.ThemeMode.value = value
        }

    /* 上下文预算（k token），聊天发送前按它截断历史。 */
    var contextTokensK: Int
        get() = sp.getInt("context_tokens_k", 8)
        set(value) = sp.edit().putInt("context_tokens_k", value.coerceIn(2, 10000)).apply()

    // ---------- 身份清单（多账户） ----------
    fun readIdentities(): List<Identity> {
        val raw = sp.getString("identities", null) ?: return emptyList()
        return runCatching { json.decodeFromString<List<Identity>>(raw) }
            .getOrNull()?.filter { it.userId.isNotEmpty() } ?: emptyList()
    }

    private fun saveIdentities(list: List<Identity>) {
        sp.edit().putString("identities", json.encodeToString(list)).apply()
    }

    var currentId: String
        get() = sp.getString("current_id", "").orEmpty()
        set(value) = sp.edit().putString("current_id", value).apply()

    /** 当前那一条：currentId 缺失或指向已不在清单里的人 → 取最新那条并写回
     *（与 app.js 的 currentEntry 同一条兜底规则）。 */
    fun currentEntry(): Identity? {
        val list = readIdentities()
        if (list.isEmpty()) return null
        list.find { it.userId == currentId }?.let { return it }
        val hit = list.sortedByDescending { it.addedAt }.first()
        currentId = hit.userId
        return hit
    }

    private fun patchCurrent(fields: Identity.() -> Identity) {
        val hit = currentEntry() ?: return
        saveIdentities(readIdentities().map { if (it.userId == hit.userId) it.fields() else it })
    }

    /** 登录/注册成功后记一个人：同 userId 旧条目先去掉再追加，超上限挤掉最旧的
     *（只忘本机——被挤掉那位的令牌到服务端自然过期/封顶作废，与网页同一取舍）。
     * minSdk 24 没有 java.time，addedAt 用定长毫秒串，排序语义与 ISO 串等价。 */
    fun addIdentity(res: AuthResult) {
        val list = readIdentities().filter { it.userId != res.user_id }.toMutableList()
        list.add(Identity(userId = res.user_id, username = res.username, role = res.role,
            token = res.token, addedAt = "%013d".format(System.currentTimeMillis())))
        saveIdentities(list)
        currentId = res.user_id
        while (readIdentities().size > IDENTITY_CAP) {
            val oldest = readIdentities().minByOrNull { it.addedAt }!!
            saveIdentities(readIdentities().filter { it.userId != oldest.userId })
        }
    }

    fun dropIdentity(userId: String) {
        saveIdentities(readIdentities().filter { it.userId != userId })
        if (currentId == userId) currentId = ""
    }

    fun switchTo(userId: String): Boolean {
        val hit = readIdentities().find { it.userId == userId } ?: return false
        currentId = userId
        return true
    }

    fun touchIdentity(username: String, role: String) =
        patchCurrent { copy(username = username, role = role) }

    // ---------- 当前身份的派生读写（都落在清单条目上，不再单独存键） ----------
    var token: String
        get() = currentEntry()?.token ?: ""
        set(value) = patchCurrent { copy(token = value) }

    var username: String
        get() = currentEntry()?.username ?: ""
        set(value) = patchCurrent { copy(username = value) }

    var role: String
        get() = currentEntry()?.role ?: "user"
        set(value) = patchCurrent { copy(role = value) }

    var defaultProviderId: String
        get() = currentEntry()?.providerId ?: ""
        set(value) = patchCurrent { copy(providerId = value) }

    var lastSessionId: String
        get() = currentEntry()?.lastSessionId ?: ""
        set(value) = patchCurrent { copy(lastSessionId = value) }

    val isAuthed: Boolean get() = token.isNotEmpty() && currentEntry() != null

    fun setPersona(sessionId: String, text: String) {
        val e = sp.edit()
        if (text.isEmpty()) e.remove("persona:$sessionId") else e.putString("persona:$sessionId", text)
        e.apply()
    }

    fun persona(sessionId: String): String = sp.getString("persona:$sessionId", "").orEmpty()

    /* 换服务器等于换一个身份世界：所有身份在新地址上都不作数，清凭据回登录页。 */
    fun setBaseUrlAndReauth(url: String) {
        baseUrl = url
        clearAuth()
    }

    /** 退出当前身份：调用方先向服务端撤销令牌，再走这里忘掉本机条目。 */
    fun forgetCurrent() {
        val hit = currentEntry() ?: return
        dropIdentity(hit.userId)
    }

    fun clearAuth() {
        saveIdentities(emptyList())
        currentId = ""
    }
}
