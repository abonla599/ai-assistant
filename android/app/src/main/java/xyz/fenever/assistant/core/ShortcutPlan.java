package xyz.fenever.assistant.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * 静态快捷方式到底有没有把参数送达到（spec §5 的兜底判据）。
 *
 * <p>背景：{@code res/xml/shortcuts.xml} 里那条 {@code <intent android:data="assistant://open/camera">}
 * 靠的是系统的 {@code XmlIntents} 解析器认这个【属性】。本机没有实机可证，而失败有两种形状：
 * <ul>
 *   <li>属性被忽略：快捷方式还在、点了能开助手，但参数没了 —— 弹不出该弹的面板；</li>
 *   <li>解析器整个拒绝这个文件：两条快捷方式一起消失。</li>
 * </ul>
 * 两种都能被「把读回来的意图形状折一遍、看它认不认」这一条判据认出来，于是
 * {@code ShortcutFallback} 可以给缺的那几条补一份 Java 建的动态快捷方式（那条走
 * {@code putExtra("open_from", ...)}，不经过任何 XML 解析器）。
 *
 * <p>判断本身不 import {@code android.*}：调用方负责把 {@code ShortcutInfo.getIntent()} 里的
 * {@code open_from} 与 {@code dataString} 抽成字符串交进来。
 */
public final class ShortcutPlan {

    /** 静态快捷方式应当送达的两个固定值；顺序即兜底时注册的顺序（桌面上按这个次序排）。 */
    public static final List<String> WANTED = Collections.unmodifiableList(
            Arrays.asList(ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_NEW_CHAT));

    private ShortcutPlan() {}

    /**
     * 这些读回来的形状里，哪几个固定值一个都没送到。
     *
     * <p>认的形状与冷启动那一条【完全相同】（{@link ShellEvents#fromLaunchShape}）：
     * {@code open_from} 与 {@code assistant://open/<值>} 两条路折进同一个白名单判据，
     * 这里不另立第二套规矩——否则"自检说没送到、真点进来又说送到了"这种错就没人抓得住了。
     */
    public static List<String> missing(List<String> shapes) {
        List<String> out = new ArrayList<>();
        for (String want : WANTED) {
            if (!delivers(shapes, want)) out.add(want);
        }
        return out;
    }

    private static boolean delivers(List<String> shapes, String want) {
        if (shapes == null) return false;
        String wanted = ShellEvents.fromLaunchShape(want);
        if (wanted == null) return false;              // 本类给出去的值就该永远认得
        for (String shape : shapes) {
            if (wanted.equals(ShellEvents.fromLaunchShape(shape))) return true;
        }
        return false;
    }
}
