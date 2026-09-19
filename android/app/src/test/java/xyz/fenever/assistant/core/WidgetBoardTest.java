package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.List;
import java.util.TimeZone;
import org.junit.Test;

/**
 * 桌面组件的「今日」这块表（spec §5）。这是 Task 8 里唯一不碰 android.* 的逻辑：
 * 哪天算今天、最多几行、每行写什么、没有归属时说什么。
 *
 * <p>刻意把 {@code nowMillis} 与 {@link TimeZone} 都当参数传进来——组件在真机上
 * 跨天/跨时区才会换内容，而那两种情况都没法靠"等一天"来验，只能在这里钉死。
 */
public class WidgetBoardTest {

    private static final TimeZone GMT = TimeZone.getTimeZone("GMT");
    private static final TimeZone PLUS_TWO = TimeZone.getTimeZone("GMT+02:00");

    /** 2026-09-19 正午（GMT）。"今天"就是这一天。 */
    private static final long NOON = at(2026, Calendar.SEPTEMBER, 19, 12, 0, GMT);

    private static long at(int y, int month, int day, int h, int min, TimeZone zone) {
        Calendar c = Calendar.getInstance(zone);
        c.clear();
        c.set(y, month, day, h, min, 0);
        return c.getTimeInMillis();
    }

    private static Reminder r(String id, long at, String title) {
        return new Reminder(id, "alice", at, title, "正文", "once");
    }

    private static List<Reminder> list(Reminder... items) {
        return new ArrayList<>(Arrays.asList(items));
    }

    // ---------------------------------------------------------------- 今天这一格

    @Test public void onlyRemindersFallingInsideTodayAreShown() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-yesterday00", at(2026, Calendar.SEPTEMBER, 18, 23, 30, GMT), "昨天"),
                r("r-thismorning", at(2026, Calendar.SEPTEMBER, 19, 7, 30, GMT), "背词"),
                r("r-tomorrowpm0", at(2026, Calendar.SEPTEMBER, 20, 0, 0, GMT), "明天")),
                "alice", NOON, GMT);
        assertEquals(1, board.rows.size());
        assertEquals("r-thismorning", board.rows.get(0).id);
        assertEquals(1, board.todayCount);
    }

    @Test public void theDayWindowFollowsTheDeviceTimeZoneNotUtc() {
        // 同一个瞬间：GMT 看是 19 日 00:30，GMT+2 看是 19 日 02:30 —— 两天都可能，
        // 但更要紧的是 18 日 23:00Z：GMT 里那是昨天，GMT+2 里它已经是 19 日今天。
        long utcLateOnThe18th = at(2026, Calendar.SEPTEMBER, 18, 23, 0, GMT);
        Reminder edge = r("r-edge00000", utcLateOnThe18th, "跨时区");
        long noonPlusTwo = at(2026, Calendar.SEPTEMBER, 19, 12, 0, PLUS_TWO);

        assertEquals(0, WidgetBoard.build(list(edge), "alice", NOON, GMT).todayCount);
        assertEquals(1, WidgetBoard.build(list(edge), "alice", noonPlusTwo, PLUS_TWO).todayCount);
    }

    @Test public void bothEndsOfTheDayAreIncluded() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-firstminute", at(2026, Calendar.SEPTEMBER, 19, 0, 0, GMT), "零点"),
                r("r-lastminute", at(2026, Calendar.SEPTEMBER, 19, 23, 59, GMT), "夜里")),
                "alice", NOON, GMT);
        assertEquals(2, board.todayCount);
        // 已过点也照样显示：组件说的是"今天有什么"，不是"接下来有什么"，
        // 不然傍晚看桌面就只剩空表，而那条 daily 明明还在表里。
        assertEquals("r-firstminute", board.rows.get(0).id);
    }

    // ---------------------------------------------------------------- 行数与"还有 N 条"

    @Test public void showsAtMostFourRowsAndCountsTheRest() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-a000000000", at(2026, Calendar.SEPTEMBER, 19, 6, 0, GMT), "A"),
                r("r-b000000000", at(2026, Calendar.SEPTEMBER, 19, 8, 0, GMT), "B"),
                r("r-c000000000", at(2026, Calendar.SEPTEMBER, 19, 10, 0, GMT), "C"),
                r("r-d000000000", at(2026, Calendar.SEPTEMBER, 19, 12, 0, GMT), "D"),
                r("r-e000000000", at(2026, Calendar.SEPTEMBER, 19, 14, 0, GMT), "E"),
                r("r-f000000000", at(2026, Calendar.SEPTEMBER, 19, 16, 0, GMT), "F")),
                "alice", NOON, GMT);
        assertEquals(WidgetBoard.MAX_ROWS, board.rows.size());
        assertEquals(2, board.hidden);
        assertEquals(6, board.todayCount);
    }

    @Test public void exactlyFourRowsMeansNoOverflowLine() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-a000000000", at(2026, Calendar.SEPTEMBER, 19, 6, 0, GMT), "A"),
                r("r-b000000000", at(2026, Calendar.SEPTEMBER, 19, 8, 0, GMT), "B"),
                r("r-c000000000", at(2026, Calendar.SEPTEMBER, 19, 10, 0, GMT), "C"),
                r("r-d000000000", at(2026, Calendar.SEPTEMBER, 19, 12, 0, GMT), "D")),
                "alice", NOON, GMT);
        assertEquals(4, board.rows.size());
        assertEquals(0, board.hidden);
    }

    @Test public void rowsAreEarliestFirstEvenWhenTheTableIsNotOrdered() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-late000000", at(2026, Calendar.SEPTEMBER, 19, 20, 0, GMT), "晚"),
                r("r-early00000", at(2026, Calendar.SEPTEMBER, 19, 5, 0, GMT), "早")),
                "alice", NOON, GMT);
        assertEquals("r-early00000", board.rows.get(0).id);
        assertEquals("r-late000000", board.rows.get(1).id);
    }

    // ---------------------------------------------------------------- 每行的文字

    @Test public void eachRowIsClockPlusTitleInDeviceZone() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-730am0000", at(2026, Calendar.SEPTEMBER, 19, 7, 30, PLUS_TWO), "背词")),
                "alice", at(2026, Calendar.SEPTEMBER, 19, 12, 0, PLUS_TWO), PLUS_TWO);
        assertEquals("07:30 背词", board.rows.get(0).text);
        // 提醒的 id 单独带着，点击跳转只用它，绝不从文字里反解
        assertEquals("r-730am0000", board.rows.get(0).id);
    }

    @Test public void longTitlesAreClippedSoOneRowNeverFillsTheScreen() {
        StringBuilder big = new StringBuilder("很长的标题");
        for (int i = 0; i < 60; i++) big.append('呀');
        String text = WidgetBoard.build(list(r("r-clip000000", NOON, big.toString())),
                "alice", NOON, GMT).rows.get(0).text;
        assertTrue("裁剪没生效，长度 " + text.length(), text.length() <= WidgetBoard.MAX_TEXT);
        assertTrue(text.endsWith("..."));
    }

    @Test public void blankTitleStillGivesAReadableRow() {
        String text = WidgetBoard.build(list(r("r-blank00000", NOON, "   ")),
                "alice", NOON, GMT).rows.get(0).text;
        assertEquals("12:00 提醒", text);
    }

    // ---------------------------------------------------------------- 表头与归属

    @Test public void noOwnerSaysSoInsteadOfLyingAboutAnEmptyDay() {
        WidgetBoard board = WidgetBoard.build(list(
                r("r-someoneelse", NOON, "别人的提醒")), null, NOON, GMT);
        assertTrue(board.rows.isEmpty());
        assertEquals(0, board.hidden);
        assertEquals("打开助手登录后显示今日提醒", board.header);
    }

    @Test public void loggedInButNothingTodayIsADifferentMessageThanNoOwner() {
        WidgetBoard empty = WidgetBoard.build(list(), "alice", NOON, GMT);
        assertEquals("今天没有提醒", empty.header);
        assertEquals("今日提醒 1 条", WidgetBoard.build(
                list(r("r-x000000000", NOON, "一件事")), "alice", NOON, GMT).header);
    }

    @Test public void aNullTableIsNotACrash() {
        WidgetBoard board = WidgetBoard.build(null, "alice", NOON, GMT);
        assertEquals(0, board.rows.size());
        assertEquals("今天没有提醒", board.header);
    }

    @Test public void rowsWithAnIdTheBridgeWouldRefuseAreLeftOutEntirely() {
        // prefs 里的表可能被写坏过（load 只要求 id 非空）。留着一条过不了 IDs 白名单的记录，
        // 组件上就是一个点了没反应的行——点按要经 ShellEvents.reminder，它同样会回 null。
        WidgetBoard board = WidgetBoard.build(list(
                r("r-good000000", at(2026, Calendar.SEPTEMBER, 19, 6, 0, GMT), "能点"),
                r("../passwd", at(2026, Calendar.SEPTEMBER, 19, 7, 0, GMT), "点不动")),
                "alice", NOON, GMT);
        assertEquals(1, board.rows.size());
        assertEquals(1, board.todayCount);
        assertEquals("r-good000000", board.rows.get(0).id);
    }
}
