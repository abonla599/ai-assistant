package xyz.fenever.assistant.core;

/**
 * 一条提醒。身份与内容字段 {@code public final}，可变的只有三个：
 * {@code at}（{@link ReminderStore#advance} 推进 repeat 时改的就是它）、以及
 * {@code firedAt}/{@code missed} 这两个记账用的数——它们由
 * {@link ReminderStore#markFired} / {@link ReminderStore#markMissed} 写，
 * 存在的唯一理由是把"到点到底响没响"变成屏幕上读得出的东西。
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
    /** 上一次真正走到"发通知"那一步的时刻，0 = 从来没有过。 */
    public long firedAt;
    /** 到点了但通知没发出去（通知权限没给）——累计几次。 */
    public long missed;

    public Reminder(String id, String owner, long at, String title, String body, String repeat) {
        this(id, owner, at, title, body, repeat, 0L, 0L);
    }

    public Reminder(String id, String owner, long at, String title, String body, String repeat,
                    long firedAt, long missed) {
        this.id = id;
        this.owner = owner;
        this.at = at;
        this.title = title;
        this.body = body;
        this.repeat = repeat;
        this.firedAt = firedAt;
        this.missed = missed;
    }

    @Override public String toString() {
        return "Reminder{" + id + " " + owner + " @" + at + " " + repeat
                + " fired=" + firedAt + " missed=" + missed + "}";
    }
}
