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
}
