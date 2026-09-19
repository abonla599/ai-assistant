"""壳（android/）里那些只能在真机上暴露、但源码形状就能判定的坑。

本机没有模拟器也没有真机，所以这里全是"读源码"的锁，不是行为测试。它们的价值不在于
证明功能对，而在于**不让注释重新变成第二个事实来源**——下面这条就是为一次真实闪退写的。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_SRC = REPO_ROOT / "android" / "app" / "src" / "main" / "java"

# Android 14（targetSdk 34）起，Context.registerReceiver 只有在过滤器【全部】命中
# AOSP IntentFilter.SYSTEM_ONLY_ACTIONS 那张硬编码表时才能省掉导出标志，否则直接抛
# SecurityException。android.intent.action.DOWNLOAD_COMPLETE 不在那张表里。
EXPORT_FLAGS = ("RECEIVER_EXPORTED", "RECEIVER_NOT_EXPORTED")


def _call_args(src: str, open_paren: int) -> str:
    """从左括号起取到配对的右括号，跨行也算。"""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
    return src[open_paren:]


def test_every_runtime_receiver_registration_names_an_export_flag():
    """v0.13 崩在 MainActivity.onCreate 里的原因：registerReceiver 少给一个标志。

    那条注释当时写着"属系统广播，因此不必传 RECEIVER_EXPORTED"——这个豁免不成立，
    于是 Android 14+ 的设备上点图标闪一下回桌面，而服务端一条请求都收不到。

    带标志的重载是 API 33 才有的，minSdk 23 必须按版本分岔，所以"没带标志"本身不是罪：
    只有当它**同时**不在 SDK_INT 判据的保护下才红。这是源码形状的近似（往回看 12 行），
    不是精确的控制流分析——它守的是"别再靠注释宣称豁免"，不是编译器。
    """
    offenders = []
    sites = 0
    for path in sorted(SHELL_SRC.rglob("*.java")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<!un)\bregisterReceiver\s*\(", src):
            sites += 1
            args = _call_args(src, m.end() - 1)
            if any(flag in args for flag in EXPORT_FLAGS):
                continue
            line = src[:m.start()].count("\n") + 1
            guarded = "SDK_INT" in "\n".join(src.splitlines()[max(0, line - 13):line - 1])
            if not guarded:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")

    # 正向对照：路径写错时 rglob 一个文件都找不到，offenders 空着也"通过"——那这条锁
    # 就只是在空转，而它绿得和真的守住时一模一样。CI 里跑的正是这条命令，所以这里必须钉。
    assert sites >= 1, f"在 {SHELL_SRC} 下一个 registerReceiver 调用都没扫到：要么路径错了，要么这条锁没用了"

    assert not offenders, (
        "这些 registerReceiver 既没给导出标志、也不在 SDK_INT 保护下，"
        "Android 14+ 上会抛 SecurityException：" + "、".join(offenders)
    )
