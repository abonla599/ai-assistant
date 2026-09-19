package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

/**
 * 原生到 JS 的唯一句式（spec §2 铁律①）。这里锁的是「外部字符串不可能变成一段 JS」：
 * payload 会被壳拼进 evaluateJavascript 的单引号里，所以带引号的东西必须在进门之前就被拒掉，
 * 而不是靠调用方记得先校验。
 */
public class ShellEventsTest {

    @Test public void shareAndReminderCarryOnlyTypeAndId() {
        assertEquals("{\"type\":\"share\",\"id\":\"s0123456789ab\"}",
                ShellEvents.share("s0123456789ab"));
        assertEquals("{\"type\":\"reminder\",\"id\":\"r-abc12345678\"}",
                ShellEvents.reminder("r-abc12345678"));
    }

    /** 单引号能顶穿壳那层的字符串字面量，双引号与括号能顶穿 JSON——id 不过白名单就没事件。 */
    @Test public void refusesEveryIdThatCouldBreakOutOfTheJsString() {
        assertNull(ShellEvents.share("x');alert(1);//xxxxxxxx"));
        assertNull(ShellEvents.reminder("r-abc12345678');close();//"));
        assertNull(ShellEvents.share("../../etc/passwd"));
        assertNull(ShellEvents.share("tiny"));
        assertNull(ShellEvents.share(null));
        assertNull(ShellEvents.reminder(null));
    }

    @Test public void openFromTranslatesTheThreeKnownShapes() {
        assertEquals("{\"type\":\"reminder\",\"id\":\"r-abc12345678\"}",
                ShellEvents.fromOpenFrom("reminder:r-abc12345678"));
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromOpenFrom("camera"));
        assertEquals("{\"type\":\"open\",\"id\":\"new_chat\"}",
                ShellEvents.fromOpenFrom("new_chat"));
    }

    /** open_from 是别的组件写进 Intent 的，同样按不可信输入处理。 */
    @Test public void openFromRejectsEverythingElseIncludingInjectedReminders() {
        assertNull(ShellEvents.fromOpenFrom(null));
        assertNull(ShellEvents.fromOpenFrom(""));
        assertNull(ShellEvents.fromOpenFrom("settings"));
        assertNull(ShellEvents.fromOpenFrom("javascript:alert(1)"));
        assertNull(ShellEvents.fromOpenFrom("reminder:'));alert(1;//"));
        assertNull(ShellEvents.fromOpenFrom("reminder:short"));
    }

    /*
     * 静态快捷方式（res/xml/shortcuts.xml）只能把参数藏在 android:data 里，
     * 所以那一条链路要先把 assistant://open/<值> 折回同一个 open_from 形状，
     * 再交给上面那个白名单——两条入口共用一套判据，不在 URI 那侧另立规矩。
     */
    @Test public void shortcutUrisFoldIntoTheSameOpenFromWhitelist() {
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromOpenUri("assistant://open/camera"));
        assertEquals("{\"type\":\"open\",\"id\":\"new_chat\"}",
                ShellEvents.fromOpenUri("assistant://open/new_chat"));
        // 组件与通知的 PendingIntent 用的是另一种形状（assistant-widget://…），不从这里进
        assertNull(ShellEvents.fromOpenUri("assistant-widget://camera"));
        // 前缀对了但值不认识、以及带注入企图 URI 段，全部回 null
        assertNull(ShellEvents.fromOpenUri("assistant://open/settings"));
        assertNull(ShellEvents.fromOpenUri("assistant://open/camera'));alert(1;//"));
        assertNull(ShellEvents.fromOpenUri("javascript:assistant://open/camera"));
        assertNull(ShellEvents.fromOpenUri(null));
        assertNull(ShellEvents.fromOpenUri(""));
    }

    @Test public void reminderOpenFromSurvivesTheUriFoldBecauseTheIdStillPassesIds() {
        // reminder:<id> 理论上也能走 URI，那条路径要照常校验 id
        assertEquals("{\"type\":\"reminder\",\"id\":\"r-abc12345678\"}",
                ShellEvents.fromOpenUri("assistant://open/reminder:r-abc12345678"));
        assertNull(ShellEvents.fromOpenUri("assistant://open/reminder:hacked'));x();//"));
    }
}
