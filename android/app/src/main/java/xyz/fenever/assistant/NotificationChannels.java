package xyz.fenever.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;

/**
 * 通知渠道。渠道是 Android 8 才有的东西，而本应用 minSdk 23，
 * 所以每个入口都要先判版本：23-25 上不调 createNotificationChannel 才是对的，
 * 而 {@code new NotificationChannel(...)} 与 {@code Notification.Builder(context, channelId)}
 * 这两个构造/工厂在 26 以下根本不存在——照计划原文无条件调用，在 23-25 的机器上
 * 启动即 NoSuchMethodError（编译期看不出来，android.jar 里符号是有的）。
 */
public final class NotificationChannels {

    public static final String REMINDERS = "assistant_reminders";

    private NotificationChannels() {}

    /** 建一次就长期留着，重复建是幂等的。26 以下直接返回。 */
    public static void ensure(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                REMINDERS, "助手提醒", NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("到点的学习与任务提醒");
        manager.createNotificationChannel(channel);
    }

    /**
     * 26+ 走带渠道的那个构造（不带上 API 26 会被丢进「默认」渠道，用户在系统设置里
     * 关不掉本应用的通知声道，也调不了铃声）；23-25 只有单参构造，渠道概念不存在。
     */
    @SuppressWarnings("deprecation")
    public static Notification.Builder builder(Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return new Notification.Builder(context, REMINDERS);
        }
        return new Notification.Builder(context);
    }
}
