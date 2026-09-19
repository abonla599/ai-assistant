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

    /* ---------------------------------------------------------------- 拒绝原因交给网页
     * Theme.NoDisplay 的 activity 全程没有窗口，Android 12 起这类进程的 Toast 可能整个不显示，
     * 而"分享 >10MB 要有明确拒绝提示"（spec §9 第 6 条）不能只靠一条可能被掐的 Toast。
     * 于是拒绝走桥：事件里只有 SharePolicy 枚举出来的那几个固定值，没有外部文本。 */

    @Test public void rejectionEventsCarryOnlyTheFixedCodes() {
        assertEquals("{\"type\":\"share_rejected\",\"id\":\"too_large\"}",
                ShellEvents.shareRejected("too_large"));
        assertEquals("{\"type\":\"share_rejected\",\"id\":\"text_too_large\"}",
                ShellEvents.shareRejected("text_too_large"));
        assertEquals("{\"type\":\"share_rejected\",\"id\":\"unsupported_mime\"}",
                ShellEvents.shareRejected("unsupported_mime"));
        assertEquals("{\"type\":\"share_rejected\",\"id\":\"no_stream\"}",
                ShellEvents.shareRejected("no_stream"));
        assertEquals("{\"type\":\"share_rejected\",\"id\":\"read_failed\"}",
                ShellEvents.shareRejected("read_failed"));
    }

    /** 白名单之外没有第二种码：文件名、正文、URI 这些外部字符串一律进不来。 */
    @Test public void refusalCarriesNothingAnOutsideAppCouldWrite() {
        assertNull(ShellEvents.shareRejected(null));
        assertNull(ShellEvents.shareRejected(""));
        assertNull(ShellEvents.shareRejected("ok"));                       // 收下不是拒绝
        assertNull(ShellEvents.shareRejected("TOO_LARGE"));                // 大小写不算同一个码
        assertNull(ShellEvents.shareRejected("'));alert(1;//xxxxxxxx"));
        assertNull(ShellEvents.shareRejected("../../etc/passwd"));
        assertNull(ShellEvents.shareRejected("我的合同.pdf"));              // 外部文本再合法也不是码
    }

    /* ---------------------------------------------------------------- 两条入口形状一个判据
     * 通知/桌面组件走 extra open_from，静态快捷方式只能走 android:data。
     * data 能不能被系统的 XML 解析器读出来尚未实机验证，所以两条都要认，
     * 而且认的还是同一套白名单——不在 URI 那侧另立规矩。 */

    @Test public void launchExtrasFoldBothShapesIntoOneJudge() {
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromLaunchExtras("camera", null));
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromLaunchExtras(null, "assistant://open/camera"));
        // 两个形状同时在场时 extra 优先（那是我们自己写的 PendingIntent，更可信）
        assertEquals("{\"type\":\"open\",\"id\":\"new_chat\"}",
                ShellEvents.fromLaunchExtras("new_chat", "assistant://open/camera"));
        assertNull(ShellEvents.fromLaunchExtras(null, null));
        assertNull(ShellEvents.fromLaunchExtras("", ""));
        assertNull(ShellEvents.fromLaunchExtras("settings", "assistant://open/settings"));
        assertNull(ShellEvents.fromLaunchExtras("'));alert(1;//", "assistant://open/'));alert(1;//"));
    }

    /** 启动器/ROM 在 URI 尾部加点东西（尾斜杠、query）不该让整条快捷方式失效。 */
    @Test public void trailingSlashOrQueryOnTheDataUriDoesNotKillTheShortcut() {
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromOpenUri("assistant://open/camera/"));
        assertEquals("{\"type\":\"open\",\"id\":\"new_chat\"}",
                ShellEvents.fromOpenUri("assistant://open/new_chat?from=icon"));
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromOpenUri("assistant://open/camera#icon"));
        assertEquals("{\"type\":\"open\",\"id\":\"camera\"}",
                ShellEvents.fromOpenUri("  assistant://open/camera  "));
        // 折完还是走同一个白名单：多一段路径也不是"认识的值"
        assertNull(ShellEvents.fromOpenUri("assistant://open/camera/extra"));
        assertNull(ShellEvents.fromOpenUri("assistant://open/"));
        assertNull(ShellEvents.fromOpenUri("assistant://open"));
    }
}
