package xyz.fenever.assistant;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import xyz.fenever.assistant.core.AlarmPolicy;
import xyz.fenever.assistant.core.Reminder;

/**
 * 往 AlarmManager 排/撤一条闹钟。
 *
 * <p>档位由 {@link AlarmPolicy} 决定——那个判断在 {@code core/} 里，因为只有那里能在
 * 本地被 JVM 跑到，而它选错的代价恰好是**不报错**：31 以上没有精确闹钟特权时调
 * {@code setExactAndAllowWhileIdle} 抛 SecurityException，提醒设进去了、列表里也看得见，
 * 但它永远不响。这一层只负责照着结论执行，并且**任何情况下都至少排上一档**：
 * 拿不到精确特权就退回 {@code setAndAllowWhileIdle}（可能晚几分钟，Doze 下更晚），
 * 绝不因为"没授权"就把这条提醒整个丢掉。
 */
public final class ReminderScheduler {

    private ReminderScheduler() {}

    public static void schedule(Context context, Reminder reminder) {
        if (reminder == null) return;
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        if (manager == null) return;
        PendingIntent pending = pendingFor(context, reminder.id);
        if (AlarmPolicy.pick(canScheduleExact(manager), Build.VERSION.SDK_INT)
                == AlarmPolicy.Mode.EXACT) {
            manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, reminder.at, pending);
        } else {
            manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, reminder.at, pending);
        }
    }

    /** 网页那一行要显示"当前排得出准点还是只能晚几分钟"，判据与排期共用这一个。 */
    public static boolean exactAllowed(Context context) {
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        return manager != null && canScheduleExact(manager);
    }

    /** 31 以下没这个问法，也不需要问：见 {@link AlarmPolicy}。短路顺序就是版本判定。 */
    private static boolean canScheduleExact(AlarmManager manager) {
        return Build.VERSION.SDK_INT < AlarmPolicy.API_EXACT_ALARM_GATE
                || manager.canScheduleExactAlarms();
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
