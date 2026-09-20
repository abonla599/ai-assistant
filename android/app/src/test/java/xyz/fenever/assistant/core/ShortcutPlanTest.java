package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

/**
 * 静态快捷方式的自检判据（spec §5）。{@code res/xml/shortcuts.xml} 里那条
 * {@code <intent android:data="assistant://open/camera">} 到底能不能被系统的 XML 解析器读出来，
 * 本机没有实机可证——读不出来的两种后果都要能被这条判据认出来：
 * <ul>
 *   <li>只有 data 丢了：ShortcutInfo 还在，但 {@code getIntent().getDataString()} 是 null；</li>
 *   <li>整个文件被解析器拒绝：{@code getManifestShortcuts()} 一条也没有。</li>
 * </ul>
 * 两种情况都落成"这些入口要改用 Java 建的动态快捷方式"，而判断本身不碰 android.*，能测。
 */
public class ShortcutPlanTest {

    private static final List<String> ALL = Arrays.asList(
            ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_NEW_CHAT, ShellEvents.OPEN_CHECK_UPDATE);

    @Test public void nothingMissingWhenTheDataUriRoundTripped() {
        List<String> shapes = Arrays.asList(
                "assistant://open/camera", "assistant://open/new_chat",
                "assistant://open/check_update");
        assertTrue(ShortcutPlan.missing(shapes).isEmpty());
    }

    /** 只带 open_from 的那一路同样算"送达"——两条形状共用 ShellEvents 那一个判据。 */
    @Test public void extrasOnlyCountsAsDeliveredBecauseBothShapesShareOneJudge() {
        assertTrue(ShortcutPlan.missing(Arrays.asList("camera", "new_chat", "check_update")).isEmpty());
        assertTrue(ShortcutPlan.missing(
                Arrays.asList("new_chat", "assistant://open/camera", "check_update")).isEmpty());
    }

    /**
     * 「检查更新」整条都活在原生侧，{@code fromOpenFrom} 故意不给它编事件。
     * 如果判据比的是事件 JSON，这条会永远被判成"没送到"，于是每次冷启动都补一条动态快捷方式，
     * 长按菜单里它凭空排两遍——而静态那条明明好好的。
     */
    @Test public void theNativeOnlyEntryStillCountsAsDelivered() {
        assertTrue(ShortcutPlan.missing(
                Collections.singletonList(ShellEvents.OPEN_CHECK_UPDATE)).size() == 2);
        assertTrue(ShortcutPlan.missing(Arrays.asList("camera", "new_chat"))
                .equals(Collections.singletonList(ShellEvents.OPEN_CHECK_UPDATE)));
        assertNullLike(ShellEvents.fromOpenFrom(ShellEvents.OPEN_CHECK_UPDATE));
    }

    @Test public void droppedDataOrADeadXmlFileBothFallBackToAllThreeEntries() {
        // data 没被解析：三条 ShortcutInfo 读回来的形状都是 null
        assertEquals(ALL, ShortcutPlan.missing(Arrays.asList((String) null, null, null)));
        // 整个 shortcuts.xml 作废：一条都没有
        assertEquals(ALL, ShortcutPlan.missing(null));
        assertEquals(ALL, ShortcutPlan.missing(new ArrayList<String>()));
    }

    /** 缺哪条补哪条：不要因为是兜底就把已经能用那条再注册一遍，桌面上会多出一个重复项。 */
    @Test public void onlyTheMissingEntryIsReported() {
        assertEquals(Arrays.asList(ShellEvents.OPEN_NEW_CHAT, ShellEvents.OPEN_CHECK_UPDATE),
                ShortcutPlan.missing(Arrays.asList("assistant://open/camera/", "assistant://open/")));
        assertEquals(Arrays.asList(ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_CHECK_UPDATE),
                ShortcutPlan.missing(Arrays.asList("new_chat", "bogus")));
    }

    @Test public void injectedOrWrongValuesNeverCountAsDelivered() {
        List<String> hostile = Arrays.asList(
                "assistant://open/settings",
                "assistant://open/camera'));alert(1;//",
                "javascript:assistant://open/camera",
                "camera');shell.send('hi",
                "REMINDER:r-abc12345678");
        assertEquals(3, ShortcutPlan.missing(hostile).size());
    }

    /** 兜底那一路要拿这些值当 shortcutId 与 extra，所以它们本身必须干净。 */
    @Test public void wantedValuesAreSafeToUseAsShortcutIds() {
        for (String value : ShortcutPlan.WANTED) {
            assertTrue("值里不许有分隔符: " + value, value.indexOf('/') < 0);
            // shortcutId 与 open_from 都进得去事件，引号括号一类字符一个都不能有
            assertTrue("值里不许有能顶穿字面量的字符: " + value, !value.matches(".*[\"'<>();\\s].*"));
            assertEquals(ShellEvents.fromLaunchExtras(value, null),
                    ShellEvents.fromLaunchExtras(null, ShellEvents.OPEN_URI_PREFIX + value));
        }
        assertEquals(3, ShortcutPlan.WANTED.size());
    }

    private static void assertNullLike(String value) {
        assertTrue("check_update 不该产生发给网页的事件", value == null);
    }
}
