package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import org.junit.Test;

/**
 * 闹钟档位的决策。它必须在 core 里：台架只编 {@code core/*.java}，
 * {@link android.app.AlarmManager} 那一层在本地一行都跑不到——
 * "到底排成精确还是不精确"这个决定如果是错的，只有真机会知道。
 */
public class AlarmPolicyTest {

    /** Android 12（API 31）才有 canScheduleExactAlarms() 这个问法。 */
    private static final int S = 31;

    @Test
    public void exactWhenTheSystemSaysWeMayScheduleExact() {
        assertEquals(AlarmPolicy.Mode.EXACT, AlarmPolicy.pick(true, S));
        assertEquals(AlarmPolicy.Mode.EXACT, AlarmPolicy.pick(true, 34));
    }

    @Test
    public void fallsBackInsteadOfThrowingWhenPermissionIsMissing() {
        // 拿不到精确闹钟授权时排 setExactAndAllowWhileIdle 会抛 SecurityException。
        // 症状不是报错而是"提醒设上了却永远不会响"，所以这里只能退档，不能选精确。
        assertEquals(AlarmPolicy.Mode.INEXACT, AlarmPolicy.pick(false, S));
        assertEquals(AlarmPolicy.Mode.INEXACT, AlarmPolicy.pick(false, 34));
    }

    @Test
    public void exactOnReleasesOlderThanTheApiThatCanAsk() {
        // 31 以下根本没有 canScheduleExactAlarms()，那一代排精确闹钟不需要特权。
        // 此时如果照 canSchedule=false 去退档，就把 23–30 的机器一律降成"可能晚几分钟"。
        // minSdk 是 23，所以这一支覆盖的是真实存在的最低端，不是假想值。
        assertEquals(AlarmPolicy.Mode.EXACT, AlarmPolicy.pick(false, 30));
        assertEquals(AlarmPolicy.Mode.EXACT, AlarmPolicy.pick(false, 23));
    }
}
