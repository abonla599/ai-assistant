"""本机运维脚本（deploy/）里那些"只有出事那天才看得出来"的源码锁。

这些脚本没有可注入的接口、也不该被 import（它们一跑就往计划任务/进程表上伸手），
所以这里全是读源码的形状锁 —— 和 test_android_shell.py 同一类：它们不证明功能对，
只守住"别再让注释变成第二个事实来源"。下面每条都对应一次真实的静默失效。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "deploy" / "watchdog.ps1"


def _code(src: str) -> str:
    """去掉 <# #> 块注释、整行 # 注释和行尾 # 注释，只留代码。

    为什么要先剥注释：这个文件里解释"为什么这么写"的行数快赶上代码了。
    拿原文去数 `if`、`return`，注释里那句"上一版这里漏了这个开关"就会参与计数。
    """
    src = re.sub(r"<#.*?#>", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if stripped.strip():
            out.append(stripped)
    return "\n".join(out)


def _region(code: str, start: str, end: str, label: str) -> str:
    """取 start 之后、end 之前的那段。任一标记不存在就 ValueError 当场报错——
    这正是正向对照要的：路径写错、改名没同步时，锁不许安静地"通过"。"""
    i = code.index(start)
    j = code.index(end, i)
    assert j > i, f"{label}：结束标记在起始标记之前，取到的区间不可信"
    return code[i:j]


def test_the_tunnel_guard_asks_whether_the_tunnel_is_serving():
    """2026-09-20 那次 Error 1033：cloudflared 进程一直活着，看门狗每分钟全绿。

    「进程在不在」量不出「链路通不通」，所以隧道那一条必须带第二判据。少传任何一个
    参数都会让它退回只看进程：LivenessKey 漏了就没处记连续次数，阈值漏了虽然还有
    默认值 3 能跑，但「改一处就把守护悄悄降级」的形状必须当场红。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))
    block = _region(code, "-Key 'tunnel'", "-Launch", "隧道那条 Invoke-Guard")

    for param in ("-LivenessProbe", "-LivenessKey", "-LivenessThreshold"):
        assert param in block, f"隧道守护少了 {param}：它会退回只看进程在不在，1033 那种故障再次无人知晓"
    assert "tunnel-unready" in block, "连续不通的次数没存进状态文件，重启就清零、永远攒不到门槛"


def test_the_readiness_probe_covers_both_connectors():
    """补起来的那个 connector 用的是另一个端口。

    只问主端口的话：升级动作真的成功了，看门狗却仍然认为「不服务」，于是每小时烧掉
    三次预算去起第四、第五个进程 —— 检测对了，动作变成了新的故障。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))
    served = _region(code, "function Test-TunnelServed", "function Test-TunnelReady",
                     "Test-TunnelServed")
    assert "Test-TunnelReady" in served, "Test-TunnelServed 没去问 /ready，那它量的就不是「对外可服务」"

    block = _region(code, "-Key 'tunnel'", "-Launch", "隧道那条 Invoke-Guard")
    assert "$TunnelMetricsPort" in block and "$TunnelAltMetricsPort" in block, (
        "判据必须同时问主端口和补位端口：补的那个不能抢主端口（旧的还占着，抢了就当场退出）"
    )

    probe = _region(code, "function Test-TunnelReady", "function Get-BackendProcess",
                    "Test-TunnelReady")
    assert "/ready" in probe, "问的不是 /ready —— 只有它表示「至少一条 connector 连上了边缘」"
    assert "200" in probe, "只看请求没抛异常的话，非 200 也会被当成服务中"


def _unguarded_returns(lines):
    """返回那些【直接父层不是 if】的裸 `return` 所在行（0 起）。

    为什么不是"往前看 6 行有没有 if"：试过一版那样判的，结果上一轮那个真实 bug
    （把 `return` 加在 `} else {` 的末尾）逃得干干净净 —— 它前 6 行里全是 `if`，
    可那些 `if` 早就闭合了，这条 return 相对 else 分支仍然是无条件的。
    所以这里维护一个括号栈，只看【当前还开着的最内层那个块】是谁开的。

    字符串字面量先抹掉再数括号。仍然是源码形状的近似，不是编译器。
    """
    stack = []
    offenders = []
    for idx, line in enumerate(lines):
        code = re.sub(r"'[^']*'", "''", line)
        if re.fullmatch(r"\s*return\s*", line):
            parent = stack[-1] if stack else ""
            if "if (" not in parent:
                offenders.append(idx)
        for pos, ch in enumerate(code):
            if ch == "{":
                stack.append(code[:pos])
            elif ch == "}" and stack:
                stack.pop()
    return offenders


def test_detecting_unready_actually_reaches_the_launch_code():
    """上一版最贵的一条：WARN 打出来了，函数却照样 return。

    活着的分支里那句无条件 `return` 让升级路径根本到不了拉起代码 —— 日志写着
    「按【隧道对外不可用】处理」，进程表里一个都没多。这类「效果没了但不报错」
    正是这个文件要守的形状。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))
    block = _region(code, "$escalate = $false", "if (-not $escalate)", "存活分支的判据段")

    lines = block.splitlines()
    returns = [i for i, ln in enumerate(lines) if re.fullmatch(r"\s*return\s*", ln)]
    assert returns, "这段里一条 return 都没有 —— 要么改形状了，要么这条锁在空转"

    offenders = _unguarded_returns(lines)
    assert not offenders, (
        f"这段第 {offenders} 行的 return 直接父层不是 if（多半挂在某个分支末尾），"
        "它会把存活分支无条件收尾，于是「检测到不服务」永远不会变成「再起一个 connector」"
    )

    assert "$escalate = $true" in block, "没有置真这一步，检测与动作就是两套各跑各的"



def test_budget_counters_never_cast_a_wrapped_array():
    """Read-State 给每个值套了 @()（那是给拉起记录数组准备的）。

    于是 `[int]$State[$Key]` 直接抛「无法将 System.Object[] 转换为 System.Int32」，
    整轮巡检被 catch 掉：后端守得好好的，隧道那条从此再没被执行过。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))
    bad = re.findall(r"\[int\]\s*\$State", code)
    assert not bad, f"这几处把状态值直接强转 int，撞上 @() 就整轮崩：{bad}"
    assert "function Get-Counter" in code and "function Set-Counter" in code, (
        "计数要走这两个函数 —— 它们替 @() 兜了一层，别拆了包装只留调用方"
    )


def test_the_replacement_connector_is_launched_with_usable_arguments():
    """三处实测踩出来的形状错误，每一条都能让「已拉起」变成一句谎：

    1) `tunnel run <名> --metrics ...` 会被判成「accepts only one argument」，进程立刻退出；
    2) 补位的 connector 若沿用主端口，会在旧进程还占着时 bind 失败退出；
    3) Start-Process -WindowStyle Hidden 丢掉子进程 stderr，于是前两种失败连一行痕迹
       都不留（第 1 条就是这么藏了一整轮：日志写着已拉起，进程表里只有原来那一个）。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))

    args_line = [ln for ln in code.splitlines() if "$tunnelArgs" in ln and "=" in ln]
    assert len(args_line) == 1, f"参数数组应当只赋值一次，读到 {len(args_line)} 行"
    pos = [args_line[0].index(t) for t in ("'tunnel'", "'--metrics'", "'run'")]
    assert pos == sorted(pos), "顺序必须是 tunnel → --metrics → run <名>；挂在 run 后面会被拒"
    assert "$TunnelAltMetricsPort" in args_line[0], "补位的用了主端口：旧进程没死它就 bind 失败退出"

    starts = [ln for ln in code.splitlines()
              if ln.strip().startswith("Start-Process") and "$TunnelExe" in ln]
    assert len(starts) == 1, f"隧道拉起应当只有一句 Start-Process，读到 {len(starts)} 行"
    assert "-RedirectStandardError" in starts[0], (
        "不接住 stderr，「用法错误」「端口被占」这类启动即失败就完全无痕"
    )


def test_the_watchdog_asks_whether_the_backend_is_complete():
    """后端那条守的是「进程在不在」，而这一轮加的东西全都能在进程活着时坏掉。

    密钥库读不动、账本写不进、嵌入降级成全零伪嵌入、新限流账没登记——四种都没有
    任何进程级症状。这和 2026-09-20 那次 Error 1033 是同一个形状：巡检每分钟全绿，
    外面早就不对了。所以这里两头都要锁：它得去问，而且问到了也不许动手拉起重启。
    """
    code = _code(WATCHDOG.read_text(encoding="utf-8"))

    probe = _region(code, "function Get-BackendSelfCheck", "function Get-Counter", "自检探测函数")
    assert "/health" in probe, "没问 /health，问的还是端口"
    assert "ConvertFrom-Json" in probe, "只看了状态码：后端刻意永远回 200，判据在 checks 里"
    assert "-TimeoutSec" in probe, "没有超时的话，一个卡住的后端会把整轮巡检一起拖住"

    verdict = _region(code, "$health = Get-BackendSelfCheck", "$SkipTunnel", "自检判决那一段")
    assert "Test-ShouldLog" in verdict, "不节流的话，一个坏掉的配置每分钟刷一条 ERROR，真告警就淹了"
    assert "'broken'" in verdict and "'ok'" in verdict, "没有按状态分级，就退化成一句「好像不对」"
    for forbidden in ("Start-Process", "Invoke-Guard"):
        assert forbidden not in verdict, (
            f"自检报 broken 就 {forbidden}：坏的是配置，重启修不好它，"
            f"只会把正在进行的对话每分钟带走一次"
        )
