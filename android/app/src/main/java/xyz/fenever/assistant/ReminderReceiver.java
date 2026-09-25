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
 * <p>通知权限没给时走另一支：跟正常支走同一套骨架——记一笔 missed → 推进排期 → 重排下一轮，
 * 只是跳过「发通知」那一步。两支都会留下痕迹（"到点了但没响"在屏幕上读得出来），也都不会
 * 因为这一响没响成，就把这条提醒连带排期的后续轮次一起吃掉。
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
            // Android 13+ 没授权时发通知会被系统整批静默丢掉。以前这里只记一笔 missed 就
            // return，不推进排期——本意是「吞掉这条是系统的决定，不该由我们改表」，但漏算了
            // 一件事：AlarmManager 的闹钟是一次性的，这一响已经消费掉了；不 advance 就不
            // 重排，daily/weekly 从此变成列表里看得见、BootReceiver 又不管（它只重排
            // at > now 的那条路）、之后就算授权恢复了也不会再响的孤儿——恰好是 v0.18 立项
            // 时那个「设过却从来没响」换了个形态复发。once 更糟：at 一直停在过去的时刻，
            // 之后别的提醒每一次到点广播，dueAt 都会把它捞出来当成"又错过一次"重复记一笔
            // missed，屏幕上那句"到点没发出 N 次"会凭空一直涨。
            //
            // 现在两支共用同一套骨架：记一笔（missed 而不是 firedAt）→ advance → 给
            // daily/weekly 重排下一轮。唯一跳过的就是 notify() 那一步——系统替我们决定了
            // 这一响不发，但"下一轮该继续排"从来不该是这一决定的连带后果。
            for (Reminder r : due) {
                store.markMissed(r);
                store.advance(r, now);
                Reminder next = store.byId(r.id);        // once 已被 advance 删掉，这里就是 null
                if (next != null) ReminderScheduler.schedule(context, next);
                ShellBridge.publish(ShellEvents.reminder(r.id));
            }
            AssistantWidget.refresh(context);
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
