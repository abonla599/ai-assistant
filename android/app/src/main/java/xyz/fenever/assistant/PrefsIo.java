package xyz.fenever.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShareInbox;

/**
 * core 层（不 import android）与 Android 之间唯一的接缝：两边的 {@code Io} 都是
 * 「读一段字符串 / 写一段字符串」，生产实现落在 SharedPreferences 上。
 *
 * <p>用 {@code apply()} 而不是 {@code commit()}：桥的方法跑在 JS 桥线程上，
 * 同步等一次磁盘写会把网页的调用堵住；apply 自己会择机落盘，进程被杀也不会丢
 * （SharedPreferences 在终止前会保证写完）。
 */
public final class PrefsIo {

    private PrefsIo() {}

    public static ReminderStore.Io reminders(Context context) {
        final SharedPreferences prefs =
                context.getSharedPreferences("reminders", Context.MODE_PRIVATE);
        return new ReminderStore.Io() {
            public String read() { return prefs.getString("blob", ""); }
            public void write(String content) { prefs.edit().putString("blob", content).apply(); }
        };
    }

    public static ShareInbox.Io shares(Context context) {
        final SharedPreferences prefs =
                context.getSharedPreferences("shares_meta", Context.MODE_PRIVATE);
        return new ShareInbox.Io() {
            public String read() { return prefs.getString("blob", ""); }
            public void write(String content) { prefs.edit().putString("blob", content).apply(); }
        };
    }
}
