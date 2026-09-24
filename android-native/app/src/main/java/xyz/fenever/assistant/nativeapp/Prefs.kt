package xyz.fenever.assistant.nativeapp

import android.content.Context
import android.content.SharedPreferences

/* 本机配置与凭据存储。
 * token 是 /v1/auth/login 换来的 Bearer 会话令牌，只进本机私有 SharedPreferences，
 * 不进 WebView 的 localStorage——原生侧凭据通道与 Web 侧彻底分开。
 */
object Prefs {
    private const val PREF_NAME = "ai_native_prefs"
    private lateinit var sp: SharedPreferences

    fun init(ctx: Context) {
        sp = ctx.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
    }

    var baseUrl: String
        get() = sp.getString("base_url", null) ?: BuildConfig.DEFAULT_BASE_URL
        set(value) = sp.edit().putString("base_url", value.trim().trimEnd('/')).apply()

    var token: String
        get() = sp.getString("token", "").orEmpty()
        set(value) = sp.edit().putString("token", value).apply()

    var username: String
        get() = sp.getString("username", "").orEmpty()
        set(value) = sp.edit().putString("username", value).apply()

    var role: String
        get() = sp.getString("role", "user").orEmpty()
        set(value) = sp.edit().putString("role", value).apply()

    /* 外观三态：system / dark / light，对应网页端 .set-row「外观」的 ⇅ 切换。
     * 落 SharedPreferences 是为了冷启动第一帧就带上，不闪一下深色。 */
    var themeMode: String
        get() = sp.getString("theme_mode", "system").orEmpty()
        set(value) = sp.edit().putString("theme_mode", value).apply()

    /* 设置页「当前模型」的本机默认：/v1/models 的 default 是服务端全局默认，
     * 这里存的是"这台手机想多用哪个"，聊天页冷启动优先取它。 */
    var defaultProviderId: String
        get() = sp.getString("default_provider", "").orEmpty()
        set(value) = sp.edit().putString("default_provider", value).apply()

    val isAuthed: Boolean get() = token.isNotEmpty()

    /* 换服务器等于换一个身份世界：旧 token 在新地址上必然 401，留着只会把
       界面卡在"正在加载→登录"的闪屏循环里。所以改地址时一并清凭据。 */
    fun setBaseUrlAndReauth(url: String) {
        baseUrl = url
        clearAuth()
    }

    fun clearAuth() {
        sp.edit().remove("token").remove("username").remove("role").apply()
    }
}
