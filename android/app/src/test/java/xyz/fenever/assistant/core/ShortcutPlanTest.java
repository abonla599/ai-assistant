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
 * 两种情况都落成"这两条入口要改用 Java 建的动态快捷方式"，而判断本身不碰 android.*，能测。
 */
public class ShortcutPlanTest {

    @Test public void nothingMissingWhenTheDataUriRoundTripped() {
        List<String> shapes = Arrays.asList(
                "assistant://open/camera", "assistant://open/new_chat");
        assertTrue(ShortcutPlan.missing(shapes).isEmpty());
    }

    /** 只带 open_from 的那一路同样算"送达"——两条形状共用 ShellEvents 那一个判据。 */
    @Test public void extrasOnlyCountsAsDeliveredBecauseBothShapesShareOneJudge() {
        assertTrue(ShortcutPlan.missing(Arrays.asList("camera", "new_chat")).isEmpty());
        assertTrue(ShortcutPlan.missing(
                Arrays.asList("new_chat", "assistant://open/camera")).isEmpty());
    }

    @Test public void droppedDataOrADeadXmlFileBothFallBackToBothEntries() {
        // data 没被解析：两条 ShortcutInfo 读回来的形状都是 null
        assertEquals(Arrays.asList(ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_NEW_CHAT),
                ShortcutPlan.missing(Arrays.asList((String) null, null)));
        // 整个 shortcuts.xml 作废：一条都没有
        assertEquals(Arrays.asList(ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_NEW_CHAT),
                ShortcutPlan.missing(null));
        assertEquals(Arrays.asList(ShellEvents.OPEN_CAMERA, ShellEvents.OPEN_NEW_CHAT),
                ShortcutPlan.missing(new ArrayList<String>()));
    }

    /** 缺哪条补哪条：不要因为是兜底就把已经能用那条再注册一遍，桌面上会多出一个重复项。 */
    @Test public void onlyTheMissingEntryIsReported() {
        assertEquals(Collections.singletonList(ShellEvents.OPEN_NEW_CHAT),
                ShortcutPlan.missing(Arrays.asList("assistant://open/camera/", "assistant://open/")));
        assertEquals(Collections.singletonList(ShellEvents.OPEN_CAMERA),
                ShortcutPlan.missing(Arrays.asList("new_chat", "bogus")));
    }

    @Test public void injectedOrWrongValuesNeverCountAsDelivered() {
        List<String> hostile = Arrays.asList(
                "assistant://open/settings",
                "assistant://open/camera'));alert(1;//",
                "javascript:assistant://open/camera",
                "camera');shell.send('hi",
                "REMINDER:r-abc12345678");
        assertEquals(2, ShortcutPlan.missing(hostile).size());
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
        assertEquals(2, ShortcutPlan.WANTED.size());
    }
}
