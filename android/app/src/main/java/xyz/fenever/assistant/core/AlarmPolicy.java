package xyz.fenever.assistant.core;

/**
 * 排一条提醒用哪个闹钟档位。
 *
 * <p>这个判断为什么不直接写在 {@code ReminderScheduler} 里：台架只编译 {@code core/}，
 * {@code AlarmManager} 那一层在本地一行都跑不到。而这一档选错的代价恰好是**没有任何报错**——
 * 31 以上没有精确闹钟特权时调 {@code setExactAndAllowWhileIdle} 抛 SecurityException，
 * 提醒设进去了、列表里看得见、小部件里也看得见，但它永远不响。所以决策本身要能在
 * JVM 里被钉住，执行层只负责照它做。
 */
public final class AlarmPolicy {

    /** 31 = Android 12，{@code canScheduleExactAlarms()} 从这个版本起才存在。 */
    public static final int API_EXACT_ALARM_GATE = 31;

    public enum Mode {
        /** 到点就发，误差按秒计。 */
        EXACT,
        /** 系统省电窗口说了算：可能晚几分钟，深睡下更晚。 */
        INEXACT
    }

    private AlarmPolicy() {}

    /**
     * @param canScheduleExact {@code AlarmManager.canScheduleExactAlarms()} 的回答；
     *                         在 31 以下调用方不必去问，随便传什么都不影响结论
     * @param sdkInt           {@code Build.VERSION.SDK_INT}
     */
    public static Mode pick(boolean canScheduleExact, int sdkInt) {
        // 31 以下排精确闹钟不需要特权。此时若因为"没特权"去退档，等于把 minSdk 23
        // 到 30 这一大片机器一律降成"可能晚几分钟"——那是本次要修的症状，不是要引入的。
        if (sdkInt < API_EXACT_ALARM_GATE) return Mode.EXACT;
        return canScheduleExact ? Mode.EXACT : Mode.INEXACT;
    }
}
