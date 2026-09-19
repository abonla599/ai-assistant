package xyz.fenever.assistant.core;

import java.util.Map;

/**
 * {@code scheduleReminder(json)} 的入参清洗：一段来自网页的 JSON 变成一条能入库的 {@link Reminder}。
 *
 * <p>放在 core 里是因为这是桥面上唯一「外部可控字符串进表」的入口，规则（id 白名单、
 * 标题正文截断、repeat 白名单、时间必须是正数）全是纯逻辑，值得在 JVM 里钉死。
 *
 * <p>解析不出来的东西一律 {@code null}，由桥翻成 {@code {"ok":false,"error":...}}——
 * 不猜、不修、不兜默认值。
 */
public final class ReminderDraft {

    public static final int MAX_TITLE = 120;
    public static final int MAX_BODY = 500;
    /** epoch 毫秒的合理上限（约 100 年）：超过它说明进来的不是毫秒，排上也永远不会响。 */
    private static final long MAX_REASONABLE_AT = 100L * 365 * 24 * 3600_000L;

    public final String id;
    public final long at;
    public final String title;
    public final String body;
    /** 只会是 once | daily | weekly。 */
    public final String repeat;

    private ReminderDraft(String id, long at, String title, String body, String repeat) {
        this.id = id;
        this.at = at;
        this.title = title;
        this.body = body;
        this.repeat = repeat;
    }

    /** 坏 JSON、不是对象、id 不过白名单、at 不是正数 —— 都回 null。 */
    public static ReminderDraft parse(String json) {
        if (json == null || json.trim().isEmpty()) return null;
        Object decoded;
        try {
            decoded = MiniJson.decode(json);
        } catch (RuntimeException e) {
            return null;
        }
        if (!(decoded instanceof Map)) return null;
        Map<?, ?> row = (Map<?, ?>) decoded;

        Object rawId = row.get("id");
        if (!(rawId instanceof String) || !IDs.valid((String) rawId)) return null;

        long at = asLong(row.get("at"));
        if (at <= 0 || at > MAX_REASONABLE_AT) return null;

        return new ReminderDraft((String) rawId, at,
                clip(str(row.get("title")), MAX_TITLE),
                clip(str(row.get("body")), MAX_BODY),
                repeat(row.get("repeat")));
    }

    /** owner 只在桥那一侧知道（{@code activeOwner}），所以留到最后一步才补上。 */
    public Reminder toReminder(String owner) {
        return new Reminder(id, owner, at, title, body, repeat);
    }

    private static String str(Object v) { return v instanceof String ? (String) v : ""; }

    /** JS 用 {@code new Date(v).getTime()}，正常是整数；但 JSON 里写成 1.7e12 也得认。 */
    private static long asLong(Object v) {
        if (v instanceof Number) return ((Number) v).longValue();
        return -1L;
    }

    private static String clip(String raw, int max) {
        if (raw == null) return "";
        return raw.length() <= max ? raw : raw.substring(0, max);
    }

    /** 白名单之外的值按 once 处理，不报错：repeat 拼错不该让整条提醒设不上。 */
    private static String repeat(Object v) {
        if ("daily".equals(v) || "weekly".equals(v)) return (String) v;
        return "once";
    }
}
