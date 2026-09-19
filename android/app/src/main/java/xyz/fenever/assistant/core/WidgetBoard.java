package xyz.fenever.assistant.core;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/**
 * 桌面组件上那「今日」一块的内容（spec §5）：哪些提醒算今天、最多几行、每行写什么、
 * 没有归属时说什么。
 *
 * <p>不 import 任何 {@code android.*}，所以跨天、跨时区这两种只能"等一天"才能验的情况
 * 能在这里被钉死——{@code AssistantWidget} 那一侧只剩 RemoteViews 的填表动作。
 *
 * <p>{@code nowMillis} 与 {@link TimeZone} 都是入参而不是就地取系统值：这样"GMT 里的昨天
 * 是 GMT+2 里的今天"这类判断有测试可跑。生产由 {@code AssistantWidget} 传
 * {@code System.currentTimeMillis()} 与 {@code TimeZone.getDefault()}。
 *
 * <p>注意与 {@code ReminderStore} 的分工：spec §3 说"壳不做日历运算"针对的是
 * 【把用户选的时间换算成 at】——那仍然由 JS 算好；这里做的是【把已排好的时刻归到某一天】，
 * 组件要说"今天有什么"就绕不开这一步。
 */
public final class WidgetBoard {

    /** 硬约束：最多 4 行，超了只能显示"还有 N 条"（RemoteViews 里没有列表控件可用）。 */
    public static final int MAX_ROWS = 4;

    /** 单行最长多少字：组件宽度是 2×2 那一档，再长就要换行，一行变两行等于挤掉别人的位置。 */
    public static final int MAX_TEXT = 44;

    public static final String HEADER_NO_OWNER = "打开助手登录后显示今日提醒";
    public static final String HEADER_EMPTY_DAY = "今天没有提醒";
    /** 标题为空时的占位（正文与标题都空的记录本来就不该有，但表里出现时别画出一行空白）。 */
    private static final String UNTITLED = "提醒";

    /** 一行：id 只用于点按（{@code open_from=reminder:<id>}），text 只用于显示。 */
    public static final class Row {
        public final String id;
        public final String text;

        Row(String id, String text) {
            this.id = id;
            this.text = text;
        }

        @Override public String toString() { return text; }
    }

    public final List<Row> rows;
    /** 今日条数里没被画上组件的那几条（"还有 N 条"）。 */
    public final int hidden;
    public final int todayCount;
    public final String header;

    private WidgetBoard(List<Row> rows, int hidden, int todayCount, String header) {
        this.rows = rows;
        this.hidden = hidden;
        this.todayCount = todayCount;
        this.header = header;
    }

    /**
     * 只画"属于今天"的记录。
     *
     * <p>已过的时刻也画：组件说的是今天有什么，不是接下来还有什么——不然傍晚看一眼桌面
     * 只剩空表，而那条每天 7:30 的记录明明还在表里。
     *
     * <p>owner 为空时直接空表（与 {@code ReminderStore.list()} 的 fail-closed 同一脾气）。
     * 生产里 list() 本来就已经滤过，这里再判一次是为了不依赖调用方记得滤。
     */
    public static WidgetBoard build(List<Reminder> items, String owner,
                                    long nowMillis, TimeZone zone) {
        if (owner == null || owner.isEmpty()) {
            return new WidgetBoard(new ArrayList<Row>(), 0, 0, HEADER_NO_OWNER);
        }
        TimeZone zoneOrDefault = zone == null ? TimeZone.getDefault() : zone;
        long[] day = dayWindow(nowMillis, zoneOrDefault);
        List<Reminder> todays = new ArrayList<>();
        if (items != null) {
            for (Reminder r : items) {
                if (r == null || !IDs.valid(r.id)) continue;   // 过不了桥的点不动，画出来就是死行
                if (r.at < day[0] || r.at >= day[1]) continue;
                todays.add(r);
            }
        }
        Collections.sort(todays, BY_AT);

        SimpleDateFormat clock = new SimpleDateFormat("HH:mm", Locale.US);
        clock.setTimeZone(zoneOrDefault);                      // 不设就是 JVM 默认时区，真机上会错一格
        List<Row> rows = new ArrayList<>();
        for (int i = 0; i < todays.size() && i < MAX_ROWS; i++) {
            Reminder r = todays.get(i);
            rows.add(new Row(r.id, rowText(clock.format(new Date(r.at)), r.title)));
        }
        int total = todays.size();
        String header = total == 0
                ? HEADER_EMPTY_DAY
                : "今日提醒 " + total + " 条";
        return new WidgetBoard(rows, Math.max(0, total - MAX_ROWS), total, header);
    }

    private static final Comparator<Reminder> BY_AT = new Comparator<Reminder>() {
        public int compare(Reminder a, Reminder b) {
            int byAt = Long.compare(a.at, b.at);
            return byAt != 0 ? byAt : a.id.compareTo(b.id);     // 同一时刻按 id 定序，免得每次刷新换位置
        }
    };

    /** 一天 = [本地零点, 次日零点)，按设备时区。DST 折叠由 Calendar 负责，这里不自己加减 24 小时。 */
    private static long[] dayWindow(long nowMillis, TimeZone zone) {
        Calendar c = Calendar.getInstance(zone);
        c.setTimeInMillis(nowMillis);
        c.set(Calendar.HOUR_OF_DAY, 0);
        c.set(Calendar.MINUTE, 0);
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        long start = c.getTimeInMillis();
        c.add(Calendar.DAY_OF_MONTH, 1);
        return new long[]{start, c.getTimeInMillis()};
    }

    private static String rowText(String clock, String title) {
        String t = title == null ? "" : title.trim();
        if (t.isEmpty()) t = UNTITLED;
        String line = clock + " " + t;
        if (line.length() <= MAX_TEXT) return line;
        return line.substring(0, MAX_TEXT - 3) + "...";
    }
}
