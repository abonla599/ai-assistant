package xyz.fenever.assistant;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import xyz.fenever.assistant.core.Reminder;

/**
 * 往 AlarmManager 排/撤一条闹钟。
 *
 * <p>只用 {@code setAndAllowWhileIdle}：这是不需要 {@code SCHEDULE_EXACT_ALARM}
 * （Android 12+ 的精确闹钟特权）就能用的最高档，代价是 Doze 深睡下可能晚几分钟，
 * 系统还会额外限流。spec §1 已经把这条代价写死了——产品不承诺准点，
 * 所以这里既不申请特权，也不做任何「近似闹钟降级」的分支。
 */
public final class ReminderScheduler {

    private ReminderScheduler() {}

    public static void schedule(Context context, Reminder reminder) {
        if (reminder == null) return;
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        if (manager == null) return;
        manager.setAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP, reminder.at, pendingFor(context, reminder.id));
    }

    /** 撤掉排期。没有这条闹钟时调用它是无害的空操作。 */
    public static void cancel(Context context, String id) {
        if (context == null || id == null) return;
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        if (manager == null) return;
        manager.cancel(pendingFor(context, id));
    }

    /**
     * requestCode 取 id 的哈希，所以同一台机器上每条提醒各占一个 PendingIntent；
     * FLAG_UPDATE_CURRENT 让同 id 重排时复用同一个 intent 而不是攒出第二个。
     * targetSdk 31 起 PendingIntent 必须显式声明可变性，缺了它 getBroadcast 直接抛异常，
     * 而这里全程不需要系统回写 intent，所以是 IMMUTABLE。
     */
    private static PendingIntent pendingFor(Context context, String id) {
        Intent intent = new Intent(context, ReminderReceiver.class).putExtra("id", id);
        return PendingIntent.getBroadcast(context, id.hashCode(), intent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }
}
