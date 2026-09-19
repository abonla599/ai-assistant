package xyz.fenever.assistant.core;

/**
 * 一条提醒。字段全 public final，只有 {@code at} 可变——
 * {@link ReminderStore#advance} 推进 repeat 时改的就是它，
 * 这样表里那个对象和调用方手里的引用永远是同一个。
 *
 * <p>不 import 任何 {@code android.*}，所以能在纯 JVM 里跑单测。
 */
public final class Reminder {
    public final String id;
    public final String owner;
    /** epoch 毫秒，由 JS 用设备本地时区算好传进来；壳不做任何日历运算。 */
    public long at;
    public final String title;
    public final String body;
    /** once | daily | weekly。 */
    public final String repeat;

    public Reminder(String id, String owner, long at, String title, String body, String repeat) {
        this.id = id;
        this.owner = owner;
        this.at = at;
        this.title = title;
        this.body = body;
        this.repeat = repeat;
    }

    @Override public String toString() {
        return "Reminder{" + id + " " + owner + " @" + at + " " + repeat + "}";
    }
}
