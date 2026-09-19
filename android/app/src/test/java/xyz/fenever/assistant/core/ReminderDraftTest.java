package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** scheduleReminder 那段 JSON 的清洗规则。桥那边只有 parse() 返回不返回，规则全在这里。 */
public class ReminderDraftTest {

    @Test public void readsAFullPayloadAndKeepsEveryField() {
        ReminderDraft d = ReminderDraft.parse(
                "{\"id\":\"r-abc12345678\",\"at\":1800000000000,\"title\":\"背单词\","
                        + "\"body\":\"第一章\",\"repeat\":\"daily\"}");
        assertNotNull(d);
        assertEquals("r-abc12345678", d.id);
        assertEquals(1800000000000L, d.at);
        assertEquals("背单词", d.title);
        assertEquals("第一章", d.body);
        assertEquals("daily", d.repeat);
    }

    @Test public void missingTitleAndBodyBecomeEmptyStringsNotNull() {
        ReminderDraft d = ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":1}");
        assertEquals("", d.title);
        assertEquals("", d.body);
        assertEquals("once", d.repeat);       // repeat 缺省按 once
    }

    /** 时间可以是浮点：JSON 里 1.8e12 与 1800000000000 是同一个数，不能因为写法就丢掉提醒。 */
    @Test public void acceptsAFloatTimestamp() {
        ReminderDraft d = ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":1.8e12}");
        assertEquals(1800000000000L, d.at);
    }

    @Test public void rejectsBadOrMissingIdIncludingTraversal() {
        assertNull(ReminderDraft.parse("{\"id\":\"../etc/passwd\",\"at\":10}"));
        assertNull(ReminderDraft.parse("{\"id\":\"short\",\"at\":10}"));
        assertNull(ReminderDraft.parse("{\"id\":\"has space12345\",\"at\":10}"));
        assertNull(ReminderDraft.parse("{\"id\":\"r-waytoolongid123456789012\",\"at\":10}"));
        assertNull(ReminderDraft.parse("{\"id\":1234567890,\"at\":10}"));
        assertNull(ReminderDraft.parse("{\"at\":10}"));
    }

    /** 注入用的字符全在白名单之外：单引号会直接顶穿 evaluateJavascript 的那层字符串。 */
    @Test public void rejectsAnIdCarryingJsQuotes() {
        assertNull(ReminderDraft.parse(
                "{\"id\":\"r-abc');alert(1);//\",\"at\":10}"));
    }

    @Test public void rejectsTimeThatIsNotAPositiveMillisecondCount() {
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":0}"));
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":-5}"));
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":\"1800000000000\"}"));
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\"}"));
        // 大得离谱：说明进来的根本不是 epoch 毫秒，排上去等于一个永远不会响的闹钟
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":9999999999999999}"));
    }

    @Test public void rejectsGarbageInsteadOfGuessingDefaults() {
        assertNull(ReminderDraft.parse(null));
        assertNull(ReminderDraft.parse(""));
        assertNull(ReminderDraft.parse("   "));
        assertNull(ReminderDraft.parse("not json"));
        assertNull(ReminderDraft.parse("[1,2,3]"));
        assertNull(ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":10,}"));
    }

    @Test public void titleAndBodyAreClippedToTheDeclaredLengths() {
        StringBuilder title = new StringBuilder();
        StringBuilder body = new StringBuilder();
        for (int i = 0; i < 600; i++) {          // 比两个上限都长，才能看出各自被夹到哪
            title.append('标');
            body.append('x');
        }
        ReminderDraft d = ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":10,\"title\":\""
                + title + "\",\"body\":\"" + body + "\"}");
        assertEquals(ReminderDraft.MAX_TITLE, d.title.length());
        assertEquals(ReminderDraft.MAX_BODY, d.body.length());
    }

    @Test public void onlyDailyAndWeeklyRepeatAnythingElseIsOnce() {
        assertEquals("weekly", repeatOf("weekly"));
        assertEquals("daily", repeatOf("daily"));
        assertEquals("once", repeatOf("hourly"));
        assertEquals("once", repeatOf("DAILY"));         // 大小写敏感，不猜
        assertEquals("once", repeatOf(null));
        assertEquals("once", repeatOf(""));
    }

    private static String repeatOf(String repeat) {
        String tail = repeat == null ? "" : ",\"repeat\":\"" + repeat + "\"";
        ReminderDraft d = ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":10" + tail + "}");
        return d == null ? "PARSE_FAILED" : d.repeat;
    }

    @Test public void ownerIsAttachedOnlyAtTheLastStep() {
        ReminderDraft d = ReminderDraft.parse("{\"id\":\"r-abc12345678\",\"at\":10}");
        Reminder r = d.toReminder("alice");
        assertEquals("alice", r.owner);
        assertEquals("r-abc12345678", r.id);
        assertEquals(10L, r.at);
        assertTrue("repeat 必须已经洗成白名单值", "once".equals(r.repeat));
    }
}
