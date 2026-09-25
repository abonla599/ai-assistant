package xyz.fenever.assistant.nativeapp.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.PI
import kotlin.math.sin

/* 按住说话（对位微信语音输入条）：空输入框上长按启动系统语音识别，
 * 松手把识别文字直接发出，按住时上滑进取消区。识别走 SpeechRecognizer
 * （免弹窗的边说边出字），设备上没有识别服务时退化为状态条提示。 */

/** 一次识别会话的状态容器。回调都在主线程（SpeechRecognizer 的约定）。 */
@Stable
class VoiceRecorder(context: Context) {
    private val appCtx = context.applicationContext
    val available: Boolean = SpeechRecognizer.isRecognitionAvailable(appCtx)

    var listening by mutableStateOf(false); private set
    var heard by mutableStateOf(""); private set
    var level by mutableFloatStateOf(0.15f); private set
    var onFinal: ((String) -> Unit)? = null
    var onError: ((String) -> Unit)? = null

    private var keepSend = true
    private var rec: SpeechRecognizer? = null

    fun start() {
        if (listening || !available) return
        listening = true; heard = ""; level = 0.15f; keepSend = true
        val r = try {
            rec ?: SpeechRecognizer.createSpeechRecognizer(appCtx)
                .also { it.setRecognitionListener(listener) }
                .also { rec = it }
        } catch (_: Exception) { null }
        if (r == null) {
            listening = false; onError?.invoke("这台设备没有可用的语音识别服务"); return
        }
        try {
            r.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1))
        } catch (_: Exception) {
            listening = false; onError?.invoke("语音识别没能启动，再按住试一次")
        }
    }

    /** 松手。send=false 立刻收尾（取消）；true 等最终结果，浮层先留一步。 */
    fun finish(send: Boolean) {
        if (!listening) return
        if (send) { keepSend = true; runCatching { rec?.stopListening() } }
        else { keepSend = false; listening = false; heard = ""; runCatching { rec?.cancel() } }
    }

    fun destroy() {
        runCatching { rec?.cancel() }
        runCatching { rec?.destroy() }
        rec = null; listening = false
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onEndOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) {
            level = ((rmsdB + 2f) / 12f).coerceIn(0f, 1f)
        }
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
        override fun onPartialResults(partialResults: Bundle?) {
            firstText(partialResults)?.let { heard = it }
        }
        override fun onResults(results: Bundle?) {
            listening = false
            val t = firstText(results).orEmpty()
            heard = t
            if (keepSend && t.isNotBlank()) onFinal?.invoke(t)
        }
        override fun onError(error: Int) {
            if (!listening) return
            listening = false; heard = ""
            // 取消时部分设备也会回调一个错码，别拿它吓用户
            if (keepSend) onError?.invoke(errText(error))
        }
    }

    private fun firstText(b: Bundle?): String? =
        b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()

    private fun errText(code: Int): String = when (code) {
        SpeechRecognizer.ERROR_AUDIO -> "没收到声音，检查麦克风"
        SpeechRecognizer.ERROR_CLIENT -> "语音识别中断了，再试一次"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "没有麦克风权限，去系统设置里开一下"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
            "网络不稳，识别失败了"
        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没听清，再说一次"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "上一条还在识别，稍等一下"
        SpeechRecognizer.ERROR_SERVER -> "识别服务出了点问题"
        else -> "语音识别没成功（$code）"
    }
}

@Composable
fun rememberVoiceRecorder(): VoiceRecorder {
    val ctx = LocalContext.current
    val rec = remember(ctx) { VoiceRecorder(ctx) }
    DisposableEffect(rec) { onDispose { rec.destroy() } }
    return rec
}

/**
 * 长按手势修饰符：按住 [holdMs] 毫秒不滑动即判定「按住说话」，
 * 上滑超过 [cancelUp] 进取消区，抬手回调结果。
 * [enabled] 每次按下时现算（输入框非空/生成中就不接管触摸，行为照常编辑）。
 *
 * 两点不显然的：
 * - 不用 detectTapGestures(onLongPress)：它的 slop 不可配，判定语义也不同，
 *   索性手写状态机；
 * - 事件用 PointerEventPass.Final 收：文本框会在 Initial 段消费掉 change，
 *   Final 段拿到的事件与 pressed 状态不受消费先后影响，抬手一定看得见。
 */
@Composable
fun Modifier.voiceHold(enabled: () -> Boolean, holdMs: Long = 260L, cancelUp: Dp = 100.dp,
                       onStart: () -> Unit, onZone: (Boolean) -> Unit,
                       onFinish: (Boolean) -> Unit): Modifier {
    val slopPx = with(LocalDensity.current) { 12.dp.toPx() }
    val cancelPx = with(LocalDensity.current) { cancelUp.toPx() }
    return pointerInput(holdMs, cancelUp) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            if (!enabled()) return@awaitEachGesture
            var started = false
            var cancelling = false
            var movedOut = false
            // 抬手/手势终止时显式掐掉定时器：不能赌框架的会话取消时机，
            // 慢半拍触发就是"手指都离开了还凭空开始录音"。
            val timer = launch {
                delay(holdMs)
                if (!movedOut) { started = true; onStart() }
            }
            try {
                while (true) {
                    val event = awaitPointerEvent(PointerEventPass.Final)
                    val c = event.changes.firstOrNull { it.id == down.id } ?: break
                    val upShift = down.position.y - c.position.y
                    if (!started &&
                        (abs(upShift) > slopPx || abs(c.position.x - down.position.x) > slopPx))
                        movedOut = true
                    if (started) {
                        val nowCancel = upShift > cancelPx
                        if (nowCancel != cancelling) { cancelling = nowCancel; onZone(nowCancel) }
                    }
                    if (!c.pressed) break
                }
            } finally {
                timer.cancel()
            }
            if (started) onFinish(cancelling)
        }
    }
}

/* ---------------- 录音浮层：微信那三件套（波纹条 / 蓝穹顶 / 一行提示） ---------------- */

@Composable
fun VoiceOverlay(heard: String, level: Float, cancelling: Boolean) {
    val dome = if (cancelling)
        Brush.verticalGradient(listOf(Color(0xFFFF7A6B), Color(0xFFE5484D)))
    else Brush.verticalGradient(listOf(Color(0xFF5C8CFF), Color(0xFF2451D6)))
    val ink = Color.White
    Box(Modifier.fillMaxSize().background(Color(0x99000000))) {
        // 穹顶：整枚圆沉到屏幕外一半，露出的弧顶就是那只"碗"
        Box(Modifier.align(Alignment.BottomCenter).offset(y = 200.dp)
            .width(400.dp).height(400.dp).clip(CircleShape).background(dome))
        Column(Modifier.align(Alignment.BottomCenter).padding(bottom = 44.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            if (heard.isNotEmpty())
                Text(heard, fontSize = 16.sp, color = ink, fontWeight = FontWeight.Medium,
                    maxLines = 3, overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(horizontal = 28.dp, vertical = 6.dp))
            WaveBars(level, cancelling)
            Text(if (cancelling) "松开取消" else "松手发送，上移取消",
                fontSize = 15.sp, color = ink, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(top = 14.dp))
        }
    }
}

/* 波纹条：高度 = 实时音量(level) × 每根柱子的固定权重 × 一个呼吸脉冲。 */
@Composable
private fun WaveBars(level: Float, cancelling: Boolean) {
    val pulse by rememberInfiniteTransition(label = "voicePulse").animateFloat(
        0.85f, 1.15f, infiniteRepeatable(tween(360), RepeatMode.Reverse), label = "v")
    val barColor = if (cancelling) Color(0xFFFFD7D2) else Color.White
    val amp = 0.2f + 0.8f * level
    Row(Modifier.height(38.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(3.dp)) {
        // 权重两头低中间高（半圆 sin），再叠一个 fract(sin) 抖动打散，避免齐步走
        repeat(30) { i ->
            val t = i / 29f
            val center = sin(t * PI.toFloat()) * 0.7f + 0.3f
            val raw = sin(i * 12.9898f) * 43758.5453f
            val jitter = abs(raw - raw.toInt())
            val h = (5f + 30f * amp * center * (0.4f + 0.6f * jitter) * pulse)
                .coerceIn(4f, 36f)
            Box(Modifier.width(3.dp).height(h.dp)
                .clip(RoundedCornerShape(2.dp)).background(barColor))
        }
    }
}
