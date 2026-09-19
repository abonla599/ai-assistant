package xyz.fenever.assistant;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import xyz.fenever.assistant.core.Reminder;
import xyz.fenever.assistant.core.ReminderStore;

/**
 * 开机重排。AlarmManager 的闹钟在设备重启后不保留，所以不补这一步，
 * 昨晚设的「明早 7:30 背词」就永远不会响。
 *
 * <p>只重排 {@code at > now} 的记录；已过点的这里直接跳过——once 那条本该在睡梦中响过，
 * 补发一条凌晨三点的通知比不补更糟，daily/weekly 的下一次由各自的规则决定，
 * 而本接收器不去改表（写表的是 ReminderReceiver，它才知道该推进谁）。
 */
public class BootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;

        ReminderStore store = new ReminderStore(PrefsIo.reminders(context));
        long now = System.currentTimeMillis();
        for (Reminder r : store.list()) {              // list() 已按 activeOwner 过滤
            if (r.at > now) ReminderScheduler.schedule(context, r);
        }
    }
}
