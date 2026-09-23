package xyz.fenever.assistant;

import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

/**
 * 「通知与精确闹钟到底给没给」这一个问题的唯一答案。
 *
 * <p>抽出来的理由和桥里那个 {@code ALLOWED_HOST} 一样：这件事有两个读它的人——
 * {@link ReminderReceiver}（到点了能不能发出去）与 {@code ShellBridge.capabilities()}
 * （屏幕上那一行显示什么）。两处各写一遍 {@code checkSelfPermission}，改一处就会出现
 * 「接收器以为能发、界面显示没授权」这种互相矛盾的账，而那正是本项目最贵的一类缺陷。
 */
final class PermissionStatus {

    private PermissionStatus() {}

    /**
     * 「到点这一响，通知到底发得出去吗」——两问合一，只在这里问一次。
     *
     * <p>第一问是运行时权限：Android 13+ 要授权；13 以下它是清单里的普通权限，不分版本
     * 判断反而两版都对——33 以下 {@code POST_NOTIFICATIONS} 压根不是运行时权限，而清单里
     * 声明过的权限 {@code checkSelfPermission} 直接回 GRANTED。
     *
     * <p>但「权限给了」不等于「通知发得出去」：用户可以在系统设置里把本应用的通知总开关
     * 关掉，这在 13+ 上不一定反映到 {@code checkSelfPermission}（那是安装期就有的开关，
     * 运行时权限只回答"该不该问都不问就拦下"），13 以下更是压根查不到这一层。
     * {@code areNotificationsEnabled()} 是唯一一个跨所有版本都问对的接口——它同时覆盖
     * 「权限没给」和「总开关关了」两种情况。第二问从这里开始必须有，不然 Android 12
     * 及以下的设备上，总开关关了这里照样回"已授权"，到点 {@code notify()} 被系统吞掉，
     * {@code markFired} 却照样记一笔"发出"——正是这一版要消灭的那个反方向说谎。
     */
    static boolean notificationsGranted(Context context) {
        if (context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return true;                 // 23 没有 areNotificationsEnabled() 这个问法
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        return manager != null && manager.areNotificationsEnabled();
    }

    /**
     * 跳到系统那一页去开。target 只有 {@code notifications} 与 {@code alarms} 两个取值，
     * 其余回 false 什么都不做——这一个入口是给网页点的，不白名单就等于把
     * 「让系统打开任意 intent」交出了桥面。
     *
     * <p>回 true 只代表「intent 递出去了」，不代表用户会去开。所以界面上那一行下次仍然
     * 现问 {@link #notificationsGranted}，而不是点完就算数。
     */
    static boolean openSettings(Context context, String target) {
        Intent intent;
        if ("notifications".equals(target)) {
            intent = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, context.getPackageName())
                    : appDetails(context);                     // 23–25 没有那一页
        } else if ("alarms".equals(target)) {
            // 31 以下排精确闹钟不需要特权（见 core/AlarmPolicy），33+ 才有专门那一页；
            // 夹在中间的 31–32 只能落到应用详情页，那里也有「闹钟」这一项。
            intent = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                    ? new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
                        .setData(Uri.fromParts("package", context.getPackageName(), null))
                    : appDetails(context);
        } else {
            return false;
        }
        try {
            // 广播接收器/桥线程不是 Activity 上下文，少了 NEW_TASK 会直接被系统拒。
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return true;
        } catch (RuntimeException e) {
            return false;      // 厂商 ROM 精简掉那一页时不崩，让网页显示「去设置里自己找」
        }
    }

    private static Intent appDetails(Context context) {
        return new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.fromParts("package", context.getPackageName(), null));
    }
}
