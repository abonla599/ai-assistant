package xyz.fenever.assistant;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import java.util.List;
import xyz.fenever.assistant.core.Reminder;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShellEvents;

/**
 * 闹钟到点。{@code AlarmManager} 把广播送到这里，于是：
 * 发通知 → 记一笔 firedAt → 按 repeat 推进（{@code once} 消失、daily/weekly 到下一个【未来】时刻）→ 重排 →
 * 若网页还开着就推一条只含 id 的事件让它回查。
 *
 * <p>通知权限没给时走另一支：给这批到点的提醒各记一笔 missed 后返回。两支都会留下痕迹，
 * 因为"到点了但没响"这件事在屏幕上读得出来，才不用再靠人在手机上翻系统设置。
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

        ReminderStore store = new ReminderStore(PrefsIo.reminders(context));
        long now = System.currentTimeMillis();
        List<Reminder> due = store.dueAt(now);
        if (due.isEmpty()) return;

        if (!PermissionStatus.notificationsGranted(context)) {
            // Android 13+ 没授权时发通知会被系统整批静默丢掉。以前这里直接 return，
            // 连一笔都不记——于是"提醒从来没响过"在屏幕上不留任何痕迹，用户只能猜。
            // 现在只记一笔 missed，**不推进排期**：吞掉这条是系统替我们做的决定，
            // 把表也一起改掉就是我们的决定了。daily/weekly 之后还会再来；once 这一条
            // 确实已经错过、补不回来（本版不做补发，见计划 §6），但它留下了证据。
            for (Reminder r : due) store.markMissed(r);
            return;
        }

        for (Reminder r : due) {
            notify(manager, context, r);
            store.markFired(r, now);            // 先记账再推进：advance 对 once 就是删除
            store.advance(r, now);
            Reminder next = store.byId(r.id);        // once 已被 advance 删掉，这里就是 null
            if (next != null) ReminderScheduler.schedule(context, next);
            ShellBridge.publish(ShellEvents.reminder(r.id));
        }
        // 计划 Step 4 的三处之三：到点推进之后"今日"那一列也变了（once 少一条、
        // daily 挪到明天），最后一次刷一遍，不在循环里刷 N 次。
        AssistantWidget.refresh(context);
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
