package xyz.fenever.assistant.nativeapp.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import xyz.fenever.assistant.nativeapp.Api
import xyz.fenever.assistant.nativeapp.ApiException
import xyz.fenever.assistant.nativeapp.AuthResult
import xyz.fenever.assistant.nativeapp.Prefs
import xyz.fenever.assistant.nativeapp.ReminderStore
import xyz.fenever.assistant.nativeapp.theme.AiBrandMark
import xyz.fenever.assistant.nativeapp.theme.GradientText
import xyz.fenever.assistant.nativeapp.theme.WebTokens
import xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush
import xyz.fenever.assistant.nativeapp.theme.isWebLight
import xyz.fenever.assistant.nativeapp.theme.text3Color

/* 首屏凭据层 —— 与网页 .auth 一对一：
 * 整层 --auth-band 深底，品牌行在拱形面板【外】，登录/注册/找回三块视图共用
 * 同一张 .auth-sheet（"谁都不该是第二个弹窗"是 app.js 的原话）。
 * 字段只有 placeholder 没有 label：文案逐字照抄 index.html。
 * 注册分两步、找回分两步，都在同一张表单里换字段——不发多余请求。 */

/* 找回三题是全站常量，与后端 app/core/auth.py 的 RECOVERY_QUESTIONS 逐字一致
 * （Web 侧有 test_web_pwa 锁这条对齐；原生侧照抄同一份，服务器不代为下发）。 */
val RECOVERY_QUESTIONS = listOf(
    "你的手机号后四位是什么？",
    "你小学在哪上？",
    "你父母姓氏的拼音首字母各一个是什么？",
)

/* 网页 .auth-sheet 那道拱形顶边：border-radius: 50% 50% 0 0 / 30px 30px 0 0。
 * 椭圆拱用一段二次贝塞尔近似（控制点 (w/2, -b) 时曲线恰好过拱顶 (w/2, 0)）。 */
private val ArchTopShape = object : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline {
        val b = with(density) { 30.dp.toPx() }.let { if (it * 2 > size.height) size.height / 2 else it }
        val path = Path().apply {
            moveTo(0f, b)
            quadraticBezierTo(size.width / 2f, -b, size.width, b)
            lineTo(size.width, size.height)
            lineTo(0f, size.height)
            close()
        }
        return Outline.Generic(path)
    }
}

/* initialMode 对齐网页 showAuth()/showAuthView()：设置里的「添加账户」开 register、
 * 「改密码」开 recover。onBack 非空时右上角给一颗 ×：从设置进来的人本来就登录着，
 * 网页那侧 Esc 能退层，原生没有键盘，必须给一条回头路，不能把人在登录页上关死。 */
@Composable
fun AuthScreen(initialMode: String = "login", onBack: (() -> Unit)? = null,
               onAuthed: () -> Unit) {
    val scope = rememberCoroutineScope()

    // authMode: login | register；recovering 是同一层里换的另一张表单
    var mode by remember { mutableStateOf(if (initialMode == "register") "register" else "login") }
    var recovering by remember { mutableStateOf(initialMode == "recover") }
    var regStep by remember { mutableStateOf(1) }
    var rcStep by remember { mutableStateOf(1) }

    var user by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var pass2 by remember { mutableStateOf("") }
    var showPass by remember { mutableStateOf(false) }
    val answers = remember { List(3) { mutableStateOf("") } }
    var rcName by remember { mutableStateOf("") }
    var rcNew by remember { mutableStateOf("") }
    var rcNew2 by remember { mutableStateOf("") }

    var hint by remember { mutableStateOf("") }        // authHint（err 时红色）
    var userErr by remember { mutableStateOf("") }     // 409 重名挂在用户名格下
    var rcHint by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var serverFixOpen by remember { mutableStateOf(false) }
    var serverDraft by remember { mutableStateOf(Prefs.baseUrl) }

    // afterAuth（网页同名）：令牌落进本机清单 + 告诉提醒排期器"现在是谁"。
    fun afterAuth(res: AuthResult) {
        Prefs.addIdentity(res)
        ReminderStore.setOwner(res.user_id)
        scope.launch {
            runCatching { Api.me() }.onSuccess {
                Prefs.touchIdentity(it.username, it.role)
            }
        }
        onAuthed()
    }

    fun failLike(text: String): Boolean =
        text.startsWith("登录失败") || text.startsWith("注册失败") ||
            text.startsWith("网络不可达") || text == "用户名和密码都要填" ||
            text == "用户名不能空着" || text == "密码不能空着" ||
            text == "两次输入的密码不一样" ||
            text == "三道题的答案都要填，空一格就没法自救" ||
            text == "先填用户名" || text == "三道题的答案都要填" ||
            text == "新密码不能空着" || text == "两次输入的新密码不一样"

    fun submitLogin() {
        if (busy) return
        hint = ""; userErr = ""
        if (user.isBlank() || pass.isBlank()) { hint = "用户名和密码都要填"; return }
        busy = true; hint = "登录中…"
        scope.launch {
            try {
                afterAuth(Api.login(user.trim(), pass))
            } catch (e: Exception) {
                hint = if (e is ApiException)
                    "登录失败：" + e.message
                else {
                    serverFixOpen = true; "网络不可达，检查服务地址"
                }
                pass = ""
                busy = false
            }
        }
    }

    fun regNext() {
        hint = ""; userErr = ""
        if (user.isBlank()) { hint = "用户名不能空着"; return }
        if (pass.isEmpty()) { hint = "密码不能空着"; return }
        if (pass != pass2) { hint = "两次输入的密码不一样"; return }
        regStep = 2
    }

    fun submitRegister() {
        if (busy) return
        hint = ""; userErr = ""
        if (answers.any { it.value.isBlank() }) {
            hint = "三道题的答案都要填，空一格就没法自救"; return
        }
        busy = true; hint = "注册中…"
        scope.launch {
            try {
                afterAuth(Api.register(user.trim(), pass, answers.map { it.value.trim() }))
            } catch (e: Exception) {
                if (e is ApiException && e.status == 409) {
                    // 重名是"改一下就好"的事：只挂在用户名那一格下面，表单末尾不跟着染红
                    userErr = e.message ?: "用户名已存在，换一个试试"
                } else {
                    hint = "注册失败：" + (e.message ?: "")
                }
                busy = false
            }
        }
    }

    fun rcNext() {
        rcHint = ""
        if (user.isBlank()) { rcHint = "先填用户名"; return }
        rcName = user.trim()
        rcStep = 2
    }

    fun submitRecovery() {
        if (busy) return
        rcHint = ""
        if (rcStep == 1) { rcNext(); return }
        if (answers.any { it.value.isBlank() }) { rcHint = "三道题的答案都要填"; return }
        if (rcNew.isEmpty()) { rcHint = "新密码不能空着"; return }
        if (rcNew != rcNew2) { rcHint = "两次输入的新密码不一样"; return }
        busy = true; rcHint = "改密码中…"
        scope.launch {
            try {
                Api.resetPassword(rcName, answers.map { it.value.trim() }, rcNew)
                // 服务端会把旧令牌全部作废——网页那侧只能回登录页重敲一遍；
                // 手机上新密码就在手里，直接静默登录续用，改完即回到聊天。
                // 只有自动登录真失败了才落回登录表单兜底（提示语保留那句"其他设备"）。
                runCatching { Api.login(rcName, rcNew) }
                    .onSuccess { afterAuth(it) }
                    .onFailure {
                        rcStep = 1; recovering = false; mode = "login"; regStep = 1
                        answers.forEach { a -> a.value = "" }
                        pass = ""; pass2 = ""
                        user = rcName
                        hint = "密码已重置，但自动登录没成：请用新密码再登一次。其他设备需要重新登录一次。"
                    }
            } catch (e: Exception) {
                rcHint = e.message ?: "重置失败"
            }
            busy = false
        }
    }

    val light = isWebLight()
    val band = if (light) WebTokens.LAuthBand
               else WebTokens.AuthBand
    val panel = if (light) WebTokens.LAuthPanel
                else WebTokens.AuthPanel

    // 「上一个交互页」的回退链：登录页里点「忘记密码」进来的，回上一页 = 回登录表单；
    // 从设置里直接开「改密码」进来的（initialMode=recover），回上一页 = 整层退出回聊天。
    // 返回键与 × 走同一条路（goBack），不把人在改密码页上关死。
    fun goBack() {
        if (recovering && initialMode != "recover") {
            recovering = false; rcStep = 1; rcHint = ""
        } else onBack?.invoke()
    }
    val canGoBack = onBack != null || (recovering && initialMode != "recover")
    if (canGoBack) BackHandler(enabled = true) { goBack() }

    Box(Modifier.fillMaxSize().background(band)) {
        if (canGoBack) {
            Text("×", fontSize = 24.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.align(Alignment.TopEnd)
                    .clickable(onClick = { goBack() })
                    .padding(horizontal = 18.dp, vertical = 12.dp))
        }
        Column(Modifier.widthIn(max = 460.dp)
            .verticalScroll(rememberScrollState())
            .imePadding().padding(horizontal = 20.dp)
            .align(Alignment.Center),
            horizontalAlignment = Alignment.CenterHorizontally) {

            Spacer(Modifier.height(8.dp))

            // .auth-brand：icon 30px 圆角 + 「AI 助手」24px/600 渐变字（面板外）
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                xyz.fenever.assistant.nativeapp.theme.AiBrandMark(30)
                xyz.fenever.assistant.nativeapp.theme.GradientText("AI 助手", 24)
            }
            Spacer(Modifier.height(22.dp))

            // .auth-sheet：拱顶面板，两张表单（登录/注册 与 找回）都在它里面
            Column(Modifier.fillMaxWidth()
                .shadow(14.dp, ArchTopShape, clip = false)
                .background(panel, ArchTopShape)
                .padding(horizontal = 24.dp)
                .padding(top = 34.dp, bottom = 28.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {

                if (serverFixOpen) {
                    Row(Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        PillField("服务地址", serverDraft, { serverDraft = it },
                            keyboardType = KeyboardType.Uri, modifier = Modifier.weight(1f))
                        if (serverDraft.isNotBlank()) {
                            Text("保存", color = MaterialTheme.colorScheme.primary,
                                fontSize = 13.5.sp,
                                modifier = Modifier.clickable {
                                    Prefs.setBaseUrlAndReauth(serverDraft.trim())
                                    serverFixOpen = false; hint = ""
                                })
                        }
                    }
                }

                if (!recovering) {
                    val reg = mode == "register"
                    // .auth-sub（登录/注册那张表单的第一句）
                    Text(if (reg) "注册分两步：先定用户名和密码，下一步留三道找回题的答案。"
                         else "登录后继续；还没有账号就点下面的「立即注册」。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)

                    PillField("请输入用户名", user, { user = it; userErr = "" }, error = userErr)

                    if (!(reg && regStep == 2)) {
                        PillField(if (reg) "请设置密码，至少 8 位" else "请输入密码",
                            pass, { pass = it }, secret = !showPass,
                            trailing = { EyeToggle(showPass) { showPass = !showPass } })
                    }
                    if (reg && regStep == 1) {
                        PillField("再输入一遍密码", pass2, { pass2 = it }, secret = !showPass)
                    }

                    if (reg && regStep == 2) {
                        // regStep2 块：自己的 auth-sub + 三组「题面/答案」
                        Text("最后一步：这三道题将来用于忘记密码时自助找回，答案只有你自己知道才对得上。" +
                            "它们和密码一样只存这台机器上的摘要，管理员也看不到原话。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                            Text(q, style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 6.dp))
                            PillField("你的答案", answers[i].value, { answers[i].value = it })
                        }
                    }

                    // .auth-note
                    Text("对话与记忆只存在这台机器上，不会上传到别处。",
                        fontSize = 12.5.sp,
                        color = xyz.fenever.assistant.nativeapp.theme.text3Color())

                    // .auth-links：两端对齐
                    Row(Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically) {
                        if (reg && regStep == 2) {
                            AuthLink("上一步") { regStep = 1 }
                        } else if (reg) {
                            // 注册时收起「忘记密码」：还没有账号就没有可找回的东西
                            Spacer(Modifier.width(1.dp))
                        } else {
                            AuthLink("忘记密码") { recovering = true; rcStep = 1; rcHint = ""; hint = "" }
                        }
                        AuthLink(if (reg) "已有账号？去登录" else "立即注册") {
                            mode = if (reg) "login" else "register"; regStep = 1; hint = ""
                        }
                    }

                    AuthGo(enabled = !busy,
                        text = when {
                            busy -> if (mode == "register") "注册中…" else "登录中…"
                            mode != "register" -> "登录"
                            regStep == 2 -> "注册并登录"
                            else -> "下一步"
                        }) {
                        if (busy) return@AuthGo
                        when {
                            mode != "register" -> submitLogin()
                            regStep == 1 -> regNext()
                            else -> submitRegister()
                        }
                    }
                    HintLine(hint, failLike(hint))
                } else {
                    // —— 找回视图（recoverForm）：同一张拱板里的另一块，第一步只收用户名 ——
                    Text(if (rcStep == 2) "第 2 步：三道题的答案和新密码一起提交。"
                         else "第 1 步：先输入你的用户名。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)

                    PillField("请输入用户名", if (rcStep == 2) rcName else user,
                        { if (rcStep == 1) user = it },
                        enabled = rcStep == 1)

                    if (rcStep == 2) {
                        Text("回答这三道题，再设一个新密码。三道全对才改得动：题目是全站固定的那三道，答案是注册第二步留的那三句。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        RECOVERY_QUESTIONS.forEachIndexed { i, q ->
                            Text(q, style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 6.dp))
                            PillField("你的答案", answers[i].value, { answers[i].value = it })
                        }
                        PillField("新密码，至少 8 位", rcNew, { rcNew = it }, secret = !showPass,
                            trailing = { EyeToggle(showPass) { showPass = !showPass } })
                        PillField("再输入一遍新密码", rcNew2, { rcNew2 = it }, secret = !showPass)
                    }

                    AuthGo(enabled = !busy,
                        text = if (rcStep == 2) { if (busy) "改密码中…" else "改密码并去登录" }
                               else "下一步") {
                        if (busy) return@AuthGo
                        submitRecovery()
                    }
                    HintLine(rcHint, failLike(rcHint))
                    // 「回去登录」按用户要求撤掉：改完密码就地续用；
                    // 中途想走有返回键和右上角 ×（见 goBack）。
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

/* .auth-field input：描边胶囊（radius 999、surface 底、line 描边、14/18 内边距、
 * 15px 字），placeholder 就是它唯一的标签。 */
@Composable
private fun PillField(placeholder: String, value: String, onValue: (String) -> Unit,
                      secret: Boolean = false, enabled: Boolean = true,
                      error: String = "",
                      keyboardType: KeyboardType = KeyboardType.Text,
                      trailing: (@Composable () -> Unit)? = null,
                      modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, CircleShape)
            .border(1.dp, if (error.isNotEmpty()) MaterialTheme.colorScheme.error
                          else MaterialTheme.colorScheme.outline, CircleShape)) {
            BasicTextField(
                value = value,
                onValueChange = onValue,
                enabled = enabled,
                singleLine = true,
                visualTransformation = if (secret) PasswordVisualTransformation()
                                       else VisualTransformation.None,
                keyboardOptions = KeyboardOptions(
                    keyboardType = keyboardType, imeAction = ImeAction.Next),
                textStyle = TextStyle(fontSize = 15.sp,
                    color = MaterialTheme.colorScheme.onSurface),
                decorationBox = { inner ->
                    Row(Modifier.padding(start = 18.dp, end = 12.dp, top = 14.dp, bottom = 14.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f)) {
                            if (value.isEmpty()) Text(placeholder, fontSize = 15.sp,
                                color = text3Color())
                            inner()
                        }
                        if (trailing != null) trailing()
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        if (error.isNotEmpty()) {
            // .field-err：只有用户名那一格有这种"就地报错"
            Text(error, fontSize = 12.5.sp, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(start = 18.dp, top = 4.dp))
        }
    }
}

/* .auth-eye：密码格右侧的眼睛（网页 SVG 眼形，这里同形制画出来；划斜线 = 隐藏态）。 */
@Composable
private fun EyeToggle(shown: Boolean, onToggle: () -> Unit) {
    Box(Modifier.size(36.dp).clickable(onClick = onToggle),
        contentAlignment = Alignment.Center) {
        val col = MaterialTheme.colorScheme.onSurfaceVariant
        Canvas(Modifier.size(18.dp)) {
            val w = size.width; val h = size.height
            val eye = Path().apply {
                moveTo(w * 0.06f, h / 2f)
                quadraticBezierTo(w / 2f, h * 0.02f, w * 0.94f, h / 2f)
                quadraticBezierTo(w / 2f, h * 0.98f, w * 0.06f, h / 2f)
                close()
            }
            drawPath(eye, col, style = Stroke(width = h * 0.09f))
            drawCircle(col, radius = h * 0.21f, center = Offset(w / 2f, h / 2f))
            if (!shown) drawLine(col, Offset(w * 0.12f, h * 0.9f),
                Offset(w * 0.88f, h * 0.1f), strokeWidth = h * 0.09f)
        }
    }
}

@Composable
private fun AuthLink(text: String, onClick: () -> Unit) {
    Text(text, color = MaterialTheme.colorScheme.primary, fontSize = 13.5.sp,
        modifier = Modifier.clickable(onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 8.dp))
}

/* .auth-go：48px 渐变胶囊主按钮；进行中留那句「…中」并配小转圈。 */
@Composable
private fun AuthGo(text: String, enabled: Boolean, onClick: () -> Unit) {
    Box(Modifier.fillMaxWidth().height(48.dp)
        .background(xyz.fenever.assistant.nativeapp.theme.aiPrimaryBrush(), CircleShape)
        .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            if (text.endsWith("…")) CircularProgressIndicator(strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(16.dp))
            Text(text, color = MaterialTheme.colorScheme.onPrimary,
                fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            }
    }
}

/* .auth-hint：表单末尾那句。红=失败；中性=进度与好消息（网页 .auth-hint 不加 err
 * 类就是 text-3 色，这条规则由 failLike 复刻）。 */
@Composable
private fun HintLine(text: String, bad: Boolean) {
    if (text.isEmpty()) return
    Text(text, fontSize = 13.sp,
        color = if (bad) MaterialTheme.colorScheme.error
               else text3Color(),
        modifier = Modifier.fillMaxWidth())
}
