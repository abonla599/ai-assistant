package xyz.fenever.assistant.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Calendar;

import org.junit.Test;

/**
 * {@link ScheduleBoard} 的台架用例（v0.23 T2.6/T2.7）。
 *
 * <p>为什么要单独一份而不是只靠 CI：这个类里全是"错一天没人发现"的算式——按 UTC 解析、
 * 周一开头的表按下标 0 排、月不补零。这三条各自都能在编译通过、页面能打开的前提下
 * 把界面变成错的。台架（tools/shell_jvm_tests.py）三秒给判据。
 *
 * <p>星期锚点取自设计稿 {@code docs/v0.23-阶段0/T0.1-日程设计稿-双端同构.html}：
 * 那一排是 周一21 / 周二22 / 周三23 / 周四24 / 周五25 / 今天26 / 周日27 / 周一28，
 * 也就是 <b>2026-09-26 = 周六</b>。改任何一条星期算式，先被这条钉住。
 */
public class ScheduleBoardTest {

    private static final String SAT = "2026-09-26";

    @Test
    public void parsesLocalMidnightAndRejectsBadShapes() {
        Calendar cal = ScheduleBoard.parse(SAT);
        assertNotNull(cal);
        assertEquals(2026, cal.get(Calendar.YEAR));
        assertEquals(Calendar.SEPTEMBER, cal.get(Calendar.MONTH));
        assertEquals(26, cal.get(Calendar.DAY_OF_MONTH));
        assertEquals(0, cal.get(Calendar.HOUR_OF_DAY));
        assertEquals(0, cal.get(Calendar.MINUTE));

        assertNull(ScheduleBoard.parse(null));
        assertNull("空串不是某一天", ScheduleBoard.parse(""));
        assertNull("月日必须补零", ScheduleBoard.parse("2026-9-6"));
        assertNull("多出来的字符不收", ScheduleBoard.parse("2026-09-26T00:00"));
        assertNull("13 月不存在", ScheduleBoard.parse("2026-13-01"));
        assertNull("2026 不是闰年", ScheduleBoard.parse("2026-02-30"));
        assertNotNull("闰日合法", ScheduleBoard.parse("2028-02-29"));
    }

    @Test
    public void weekdayTableStartsOnMonday() {
        // 设计稿那一排：21 周一 … 26 周六。
        assertEquals("周一", ScheduleBoard.weekday("2026-09-21"));
        assertEquals("周五", ScheduleBoard.weekday("2026-09-25"));
        assertEquals("周六", ScheduleBoard.weekday(SAT));
        assertEquals("周日", ScheduleBoard.weekday("2026-09-27"));
        assertEquals("周一", ScheduleBoard.weekday("2026-09-28"));
        assertEquals(0, ScheduleBoard.weekdayIndex("2026-09-21"));
        assertEquals(6, ScheduleBoard.weekdayIndex("2026-09-27"));
        assertEquals(-1, ScheduleBoard.weekdayIndex("not-a-day"));
        assertEquals(7, ScheduleBoard.WEEKDAYS.length);
    }

    @Test
    public void shiftsAcrossMonthAndYearBoundaries() {
        assertEquals("2026-09-25", ScheduleBoard.shift(SAT, -1));
        assertEquals("2026-09-27", ScheduleBoard.shift(SAT, 1));
        assertEquals("2026-09-30", ScheduleBoard.shift(SAT, 4));
        assertEquals("2026-10-01", ScheduleBoard.shift(SAT, 5));
        assertEquals("2026-12-31", ScheduleBoard.shift("2026-12-30", 1));
        assertEquals("2027-01-01", ScheduleBoard.shift("2026-12-31", 1));
        assertEquals("2026-02-28", ScheduleBoard.shift("2026-03-01", -1));
        assertEquals("2028-02-29", ScheduleBoard.shift("2028-02-28", 1));
        assertEquals("", ScheduleBoard.shift("", 3));
    }

    @Test
    public void formatsWithoutZeroPaddingOnMonthAndDay() {
        assertEquals("2026年9月26日", ScheduleBoard.fullDate(SAT));
        assertEquals("2026年1月5日", ScheduleBoard.fullDate("2026-01-05"));
        assertEquals("", ScheduleBoard.fullDate("2026-1-5"));
    }

    @Test
    public void todayAnchorsComeFromTheServerDay() {
        assertEquals("2026年9月26日 · 今天", ScheduleBoard.dayLabel(SAT, SAT));
        assertEquals("2026年9月25日", ScheduleBoard.dayLabel("2026-09-25", SAT));
        assertEquals("今天", ScheduleBoard.chipWeekday(SAT, SAT));
        assertEquals("周五", ScheduleBoard.chipWeekday("2026-09-25", SAT));
        assertEquals("26", ScheduleBoard.chipDay(SAT));
        assertTrue(ScheduleBoard.isToday(SAT, SAT));
        // 今天为空 = 还没拿到服务端那份 day，此时谁都不该被标成今天。
        assertFalse(ScheduleBoard.isToday("", ""));
    }

    @Test
    public void windowIsFiveBeforeTwoAfterAnchoredOnToday() {
        String[] days = ScheduleBoard.window(SAT, 5, 2);
        assertEquals(8, days.length);
        assertArrayEquals(new String[]{
                "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25",
                "2026-09-26", "2026-09-27", "2026-09-28"}, days);
        // 窗口跟着"今天"走，不跟着当前看的那天走：月初也一样连得起来。
        assertArrayEquals(new String[]{
                "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01",
                "2026-10-02", "2026-10-03", "2026-10-04"}, ScheduleBoard.window("2026-10-02", 5, 2));
        assertEquals(0, ScheduleBoard.window("", 5, 2).length);
        assertEquals(0, ScheduleBoard.window("2026-09-26", -1, 2).length);
        assertEquals(1, ScheduleBoard.window(SAT, 0, 0).length);
    }

    @Test
    public void groupTitlesAndCountReadLikeTheWeb() {
        assertEquals("今日安排", ScheduleBoard.groupTitle(SAT, SAT));
        assertEquals("2026年9月25日安排", ScheduleBoard.groupTitle("2026-09-25", SAT));
        // 跨月那天单独钉一次：月不补零这条算式只在跨月时才和日期条对上。
        assertEquals("2026年10月1日安排", ScheduleBoard.groupTitle("2026-10-01", SAT));
        assertEquals("今日安排", ScheduleBoard.groupTitle("", SAT));
        assertEquals("3 项 · 已完成 1", ScheduleBoard.countText(3, 1));
        assertEquals("0 项 · 已完成 0", ScheduleBoard.countText(0, 0));
    }

    @Test
    public void sharedCopyConstantsMatchTheWeb() {
        // 时间格的提示字样：网页 app.js 的 at.placeholder 与原生 ScheduleUi 用的是同一串。
        assertEquals("HH:MM", ScheduleBoard.TIME_PLACEHOLDER);
        // 留空那格的占位破折号（网页 .at.none 靠 visibility 隐掉，两端同一个字符）。
        assertEquals("\u2014", ScheduleBoard.NO_TIME);
        assertEquals("今天", ScheduleBoard.TODAY_LABEL);
        assertEquals(" · 今天", ScheduleBoard.TODAY_SUFFIX);
        assertEquals("今日安排", ScheduleBoard.GROUP_TODAY);
        assertEquals("安排", ScheduleBoard.GROUP_SUFFIX);
        assertEquals(" 项 · 已完成 ", ScheduleBoard.COUNT_ITEM);
    }
}
