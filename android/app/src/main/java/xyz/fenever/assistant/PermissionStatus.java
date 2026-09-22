package xyz.fenever.assistant;

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
     * Android 13+ 要运行时授权；13 以下它是清单里的普通权限。不分版本判断反而两版都对：
     * 33 以下 {@code POST_NOTIFICATIONS} 压根不是运行时权限，而清单里声明过的权限
     * {@code checkSelfPermission} 直接回 GRANTED。
     */
    static boolean notificationsGranted(Context context) {
        return context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
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
