package xyz.fenever.assistant.core;

import java.text.ParsePosition;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Locale;
import java.util.TimeZone;

/**
 * 日程看板的日期算式与文案（v0.23 T2.6/T2.7，PRD R3）。
 *
 * <p>一天一份清单，界面上跟日期有关的东西一共就五件：往前往后挪一天、这一天的星期名、
 * 顶栏的「2026年9月26日 · 今天」、组头的「今日安排 / 某天安排」、计数「3 项 · 已完成 1」。
 * 每件都短，但每件都有一次算错的机会（尤其"按 UTC 解析本地日"这种——差一天，整条日期条
 * 就错位，而且只有跨零点或跨时区的人才看得见）。所以算式收在这一份纯 JVM 的类里，
 * 台架（tools/shell_jvm_tests.py）直接跑，不靠 CI 猜。
 *
 * <p><b>刻意的取舍</b>：
 * <ul>
 *   <li>解析按<b>本地时区</b>。这里没有 UTC 也不该有：`YYYY-MM-DD` 是"哪一天"，
 *       不是某个时刻；{@link #parse} 用默认时区，格式化也用默认时区，两边同一个钟。</li>
 *   <li>「今天」由调用方传进来，值来自服务端 GET 响应的 {@code day} 字段（R3-AC-3）。
 *       这一份不知道"今天是几号"，也不去问设备时钟——手机改了系统日期不该让它问出
 *       别人的一天。</li>
 *   <li>星期表与后端 {@code app/core/schedule.py} 的 {@code _WEEKDAYS} 同序同字
 *       （0=周一），两处各有钉死测试；换算入口只有 {@link #weekdayIndex} 一处。</li>
 * </ul>
 *
 * <p>字符串文案（「今天」「 · 今天」「今日安排」「 项 · 已完成 」）与网页
 * {@code backend/app/web/static/app.js} 的同名算式逐字同值，由
 * {@code backend/tests/test_schedule_ui_contract.py} 两头钉住。
 */
public final class ScheduleBoard {

    private ScheduleBoard() {
    }

    /** 与后端 _WEEKDAYS 同序：下标 0 = 周一。 */
    public static final String[] WEEKDAYS = {"周一", "周二", "周三", "周四", "周五", "周六", "周日"};

    /** 日期条副标题里"今天"那一格的字样（R3-AC-3 的可见形态）。 */
    public static final String TODAY_LABEL = "今天";

    /** 顶栏日期后面的那截锚点：全角间隔号 + 前后各一个空格，与网页同一条串。 */
    public static final String TODAY_SUFFIX = " · 今天";

    /** 组头：看的就是今天时用它，否则用 {@link #groupTitle} 的另一支。 */
    public static final String GROUP_TODAY = "今日安排";

    /** 组头另一支的后缀：「2026年9月26日」+ 这个 = 「2026年9月26日安排」。 */
    public static final String GROUP_SUFFIX = "安排";

    /** 计数串的分隔：{@code 3 + ITEM + 3 + DONE + 1}。 */
    public static final String COUNT_ITEM = " 项 · 已完成 ";

    /** 时间角标留空时占位的破折号：靠 CSS/布局隐掉，但仍在那一格上。 */
    public static final String NO_TIME = "—";

    /** 时间格空白时的提示字样：只提示形状，不做校验（校验的口径在服务端 {@code clean_at}）。 */
    public static final String TIME_PLACEHOLDER = "HH:MM";

    private static final String DAY_PATTERN = "yyyy-MM-dd";

    /**
     * `YYYY-MM-DD` → 本地零点的 Calendar。形状不对返回 null（不抛）：
     * 界面上"拿不到那一天"和"那天没有安排"是两件事，调用方要能各自表态。
     */
    public static Calendar parse(String iso) {
        if (iso == null) {
            return null;
        }
        String text = iso.trim();
        if (text.length() != DAY_PATTERN.length()) {
            return null;
        }
        SimpleDateFormat fmt = new SimpleDateFormat(DAY_PATTERN, Locale.US);
        fmt.setTimeZone(TimeZone.getDefault());
        fmt.setLenient(false);
        ParsePosition pos = new ParsePosition(0);
        java.util.Date date = fmt.parse(text, pos);
        if (date == null || pos.getIndex() != text.length()) {
            return null;
        }
        Calendar cal = Calendar.getInstance();
        cal.setTime(date);
        cal.set(Calendar.HOUR_OF_DAY, 0);
        cal.set(Calendar.MINUTE, 0);
        cal.set(Calendar.SECOND, 0);
        cal.set(Calendar.MILLISECOND, 0);
        return cal;
    }

    /** Calendar → `YYYY-MM-DD`（与 {@link #parse} 同一个时区、同一份格式）。 */
    public static String iso(Calendar cal) {
        SimpleDateFormat fmt = new SimpleDateFormat(DAY_PATTERN, Locale.US);
        fmt.setTimeZone(TimeZone.getDefault());
        return fmt.format(cal.getTime());
    }

    /** 挪 ±N 天（跨月跨年都走 Calendar 的字段加法，不做秒数除法）。 */
    public static String shift(String isoDay, int delta) {
        Calendar cal = parse(isoDay);
        if (cal == null) {
            return "";
        }
        cal.add(Calendar.DAY_OF_MONTH, delta);
        return iso(cal);
    }

    /** `YYYY-MM-DD` → 星期表下标，0 = 周一。日历按本地时区解析，与 UTC 无关。 */
    public static int weekdayIndex(String isoDay) {
        Calendar cal = parse(isoDay);
        if (cal == null) {
            return -1;
        }
        // Calendar.DAY_OF_WEEK: 周日=1…周六=7；周一开头的表要把周日挪到最后。
        return (cal.get(Calendar.DAY_OF_WEEK) + 5) % 7;
    }

    public static String weekday(String isoDay) {
        int at = weekdayIndex(isoDay);
        return at < 0 ? "" : WEEKDAYS[at];
    }

    /** 「2026年9月26日」——月与日都不补零，与网页 {@code schedFullDate} 同形。 */
    public static String fullDate(String isoDay) {
        Calendar cal = parse(isoDay);
        if (cal == null) {
            return "";
        }
        return cal.get(Calendar.YEAR) + "年" + (cal.get(Calendar.MONTH) + 1) + "月"
                + cal.get(Calendar.DAY_OF_MONTH) + "日";
    }

    /** 顶栏那一截：今天多带「 · 今天」，其余只报日期。 */
    public static String dayLabel(String isoDay, String today) {
        String base = fullDate(isoDay);
        if (base.isEmpty()) {
            return "";
        }
        return base + (isToday(isoDay, today) ? TODAY_SUFFIX : "");
    }

    /** 日期条那一格的星期位：今天写「今天」，其余写星期名。 */
    public static String chipWeekday(String isoDay, String today) {
        return isToday(isoDay, today) ? TODAY_LABEL : weekday(isoDay);
    }

    /** 那一格下方的日子数字（不补零）。 */
    public static String chipDay(String isoDay) {
        Calendar cal = parse(isoDay);
        return cal == null ? "" : String.valueOf(cal.get(Calendar.DAY_OF_MONTH));
    }

    public static boolean isToday(String isoDay, String today) {
        return isoDay != null && today != null && isoDay.length() > 0 && isoDay.equals(today);
    }

    /**
     * 日期条那一排：今天前 {@code before} 格 + 今天 + 后 {@code after} 格。
     *
     * <p>窗口锚在"今天"而不是"当前看的那天"——人往后翻不该把脚下的那一天翻走。
     * 拿不到合法 today 时返回空数组，让界面自己决定"没有日期条"长什么样。
     */
    public static String[] window(String today, int before, int after) {
        if (parse(today) == null || before < 0 || after < 0) {
            return new String[0];
        }
        String[] out = new String[before + after + 1];
        for (int i = 0; i < out.length; i++) {
            out[i] = shift(today, i - before);
        }
        return out;
    }

    /** 组头：今天 = 「今日安排」；别的日子 = 「2026年10月1日安排」。 */
    public static String groupTitle(String isoDay, String today) {
        if (isoDay == null || isoDay.isEmpty()) {
            return GROUP_TODAY;
        }
        return isToday(isoDay, today) ? GROUP_TODAY : fullDate(isoDay) + GROUP_SUFFIX;
    }

    /** 「3 项 · 已完成 1」。空清单也给一句（「0 项 · 已完成 0」），别让计数位空着晃。 */
    public static String countText(int total, int done) {
        return total + COUNT_ITEM + done;
    }
}
