package xyz.fenever.assistant;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ShortcutInfo;
import android.content.pm.ShortcutManager;
import android.os.Build;
import java.util.ArrayList;
import java.util.List;
import xyz.fenever.assistant.core.ShortcutPlan;
import xyz.fenever.assistant.core.ShellEvents;

/**
 * 静态快捷方式的兜底（spec §5 的实机风险点之三）。
 *
 * <p>{@code res/xml/shortcuts.xml} 里那条 {@code <intent android:data="assistant://open/camera">}
 * 到底能不能被系统的 XML 解析器读出来，本机无从证实。失败的两种形状：属性被忽略（快捷方式还在、
 * 参数没了），或解析器整个拒绝这个文件（两条一起消失）。收在 {@link ShortcutPlan} 里的判据
 * 能认出这两种情况，这里只负责"读回来"和"补上去"。
 *
 * <p>补的是【Java 建的动态快捷方式】：那条链路的参数走 {@code putExtra("open_from", ...)}，
 * 不经过任何 XML 解析器，所以它把 {@code android:data} 那条不确定项整个绕开了。
 * 两个通道都是平台 API（{@code android.content.pm}），零第三方依赖这条性质没被动摇。
 *
 * <p>只在【静态那条确实没送到】时才注册：静态好好的时候桌面上一条都不多，
 * 不会出现"拍照提问"排两遍。缺哪条补哪条（见 {@link ShortcutPlan#missing}）。
 *
 * <p>两个硬约束：
 * <ul>
 *   <li>{@code ShortcutManager} 是 API 25 的类，minSdk 23 上它压根不存在——
 *       所以整个方法被 {@code SDK_INT} 挡住，23-24 上这些符号连解析都不会发生
 *       （类只在方法体第一次真正用到时才被解析，那两个版本走不到）。型检抓不到这类错，
 *       这正是上一版翻过的坑。</li>
 *   <li>兜底不许把启动搞崩：所有异常吞掉，最坏结果是长按图标少两个入口，
 *       而"应用打不开"绝对不允许发生在这段代码上。</li>
 * </ul>
 */
public final class ShortcutFallback {

    private ShortcutFallback() {}

    /** 冷启动自检：静态那两条如果没把参数送到，就用动态的把缺的那几条补上。 */
    public static void verifyAndRepair(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N_MR1) return;   // API 25 以下没有 ShortcutManager
        if (context == null) return;
        Context app = context.getApplicationContext();
        try {
            Object service = app.getSystemService(Context.SHORTCUT_SERVICE);
            if (!(service instanceof ShortcutManager)) return;
            ShortcutManager manager = (ShortcutManager) service;
            List<String> shapes = new ArrayList<>();
            for (ShortcutInfo info : manager.getManifestShortcuts()) shapes.add(shapeOf(info));
            List<String> missing = ShortcutPlan.missing(shapes);
            if (missing.isEmpty()) return;               // 静态那条是好的，桌面上一条都不加
            manager.setDynamicShortcuts(replacement(missing, app));
        } catch (RuntimeException fallbackItselfFailed) {
            // 见类注释：兜底失败最多是长按菜单里没有这两个入口，不能拖慢或搞崩启动
        }
    }

    /**
     * 把一条静态快捷方式读回来的"参数形状"抽成一个字符串：优先 {@code open_from}（万一
     * 将来的 XML 支持嵌套 extra 标签），退而求其次 {@code android:data} 那条 URI。
     * 两个都没值 —— 正是"data 被解析器丢了"的样子。
     */
    private static String shapeOf(ShortcutInfo info) {
        Intent intent = info == null ? null : info.getIntent();
        if (intent == null) return null;
        String extra = intent.getStringExtra("open_from");
        return extra != null ? extra : intent.getDataString();
    }

    /** 缺哪几条就建哪几条：形状与桌面组件那两个按钮完全一致（同一个 extra、同一套 flags）。 */
    private static List<ShortcutInfo> replacement(List<String> values, Context context) {
        List<ShortcutInfo> out = new ArrayList<>();
        for (String value : values) {
            Intent open = new Intent(context, MainActivity.class)
                    .setAction(Intent.ACTION_MAIN)
                    .putExtra("open_from", value)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                            | Intent.FLAG_ACTIVITY_CLEAR_TOP
                            | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            out.add(new ShortcutInfo.Builder(context, "dyn_" + value)
                    .setShortLabel(context.getString(labelOf(value)))
                    .setIntent(open)
                    .build());
        }
        return out;
    }

    /** 标签仍从 strings.xml 读：与静态那两条用同一份文案，两条路在桌面上看着一样。 */
    private static int labelOf(String value) {
        return ShellEvents.OPEN_CAMERA.equals(value)
                ? R.string.shortcut_camera_short : R.string.shortcut_chat_short;
    }
}
