package xyz.fenever.assistant;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import java.util.List;
import xyz.fenever.assistant.core.Reminder;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShellEvents;

/**
 * 闹钟到点。{@code AlarmManager} 把广播送到这里，于是：
 * 发通知 → 按 repeat 推进（{@code once} 消失、daily/weekly 到下一个【未来】时刻）→ 重排 →
 * 若网页还开着就推一条只含 id 的事件让它回查。
 *
 * <p>归属校验（spec §3「到点」那条）由 {@link ReminderStore#dueAt} 承担：它只返回
 * {@code activeOwner} 的记录，未 setOwner 时返回空表，所以这里天然一条都不发。
 *
 * <p>不补发历史轮次：迟到的 daily 由 {@code advance} 直接对齐到下一个未来时刻。
 */
public class ReminderReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;

        if (!notificationsAllowed(context)) {
            // Android 13+ 没授权时发通知会被系统静默丢掉。这里连表都不动：
            // 记录留着，用户下次授权后到点仍能响，不至于把没看见的提醒整个吃掉。
            return;
        }

        ReminderStore store = new ReminderStore(PrefsIo.reminders(context));
        long now = System.currentTimeMillis();
        List<Reminder> due = store.dueAt(now);
        for (Reminder r : due) {
            notify(manager, context, r);
            store.advance(r, now);
            Reminder next = store.byId(r.id);        // once 已被 advance 删掉，这里就是 null
            if (next != null) ReminderScheduler.schedule(context, next);
            ShellBridge.publish(ShellEvents.reminder(r.id));
        }
        // Task 8 会在这里补 AssistantWidget.refresh(context)：今日列表变了组件得跟着换。
    }

    /**
     * POST_NOTIFICATIONS 是 Android 13 的运行时权限；13 以下它是安装期普通权限，
     * checkSelfPermission 对清单里声明过的它直接回 GRANTED，所以不用分版本。
     */
    private static boolean notificationsAllowed(Context context) {
        return context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }

    private static void notify(NotificationManager manager, Context context, Reminder r) {
        Intent open = new Intent(context, MainActivity.class)
                .putExtra("open_from", ShellEvents.OPEN_FROM_REMINDER + r.id)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent tap = PendingIntent.getActivity(context, r.id.hashCode(), open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notification = NotificationChannels.builder(context)
                .setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle(r.title)
                .setContentText(r.body)
                .setAutoCancel(true)
                .setContentIntent(tap)
                .build();
        // tag 不给、id 用提醒 id 的哈希：同一 id 重复触发会原地替换而不是攒出第二张。
        manager.notify(r.id.hashCode(), notification);
    }
}
