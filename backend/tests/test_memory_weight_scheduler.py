"""后台定时器不得再调用那个占位的"权重更新"，日志也不得再暗示存在自动衰减。

起因：`backend/app/memory_weight_updater.py` 整个模块只有一句 print
（"当前为占位实现"），却被 `app/main.py` 的 `run_scheduler` 每 300 秒调用一次。
2026-09-19 之前那句话只是没用——`weight` 当时根本不参与检索排序；从
`app/memory/ranking.py` 的 `weighted_rank` 把 weight 接进排序那天起，它开始
误导读日志的人：看起来记忆在随时间自动衰减，实际上那一轮什么都没做。

这几条用例钉的是"日志说的和代码做的一致"：
- 一轮定时任务只跑偏好分析，绝不碰权重（占位函数一旦被再调用，tripwire 当场炸）；
- 那句实话本身只讲两个真实写入者，且不再自称"占位"。
"""

import types

import pytest


class _Tripwire:
    """被调用即失败：用来证明"调度器不再调用它"，而不是靠读源码猜。"""

    def __init__(self):
        self.calls = []

    def update_memory_weights_from_feedback(self):
        self.calls.append("update_memory_weights_from_feedback")
        raise AssertionError(
            "占位的权重更新又被定时器调用了——它什么都不做，却会让日志谎称权重在更新")

    def log_weight_policy(self):
        self.calls.append("log_weight_policy")
        return "（tripwire，不是真文案）"


def test_scheduler_exposes_a_single_testable_cycle():
    """一轮任务必须是能被调用的函数，否则"它到底调没调权重"这句话没法测。"""
    import app.main as main

    assert callable(getattr(main, "run_scheduler_cycle", None)), (
        "app.main 没有 run_scheduler_cycle：整段逻辑仍埋在 while True 里，"
        "于是下面那条『定时器不碰权重』的断言永远是空话")


def test_one_scheduler_cycle_runs_preferences_and_never_touches_weights(monkeypatch,
                                                                        capsys):
    import app.main as main

    prefs = []
    tripwire = _Tripwire()
    # 桩必须打在 app.main 看到的那两个模块名上：真模块会去读反馈文件、还会 print。
    monkeypatch.setattr(main, "HAS_BG_TASKS", True, raising=False)
    monkeypatch.setattr(main, "preference_analyzer", types.SimpleNamespace(
        analyze_all_preferences=lambda: prefs.append("pref")), raising=False)
    monkeypatch.setattr(main, "memory_weight_updater", tripwire, raising=False)

    main.run_scheduler_cycle()

    # 先证明这一轮真的跑了：否则"没调用权重更新"可能只是因为什么都没执行。
    assert prefs == ["pref"], f"这一轮连偏好分析都没跑，断言就成了空转：{prefs}"
    assert tripwire.calls == [], f"定时器仍然碰了权重那一路：{tripwire.calls}"

    out = capsys.readouterr().out
    assert "权重" not in out, (
        f"每 300 秒印一次的周期日志里又出现了“权重”——这一轮并不改权重，"
        f"读日志的人会以为它在随时间自动衰减：{out!r}")


def test_weight_policy_line_tells_the_truth(capsys):
    """那句日志只能说出真实存在的写入者：用户反馈 + 管理员手动衰减。"""
    from app import memory_weight_updater

    line = memory_weight_updater.log_weight_policy()
    printed = capsys.readouterr().out
    assert printed.strip(), "log_weight_policy 什么都没打印，启动日志里看不到口径"

    text = line + printed
    assert "占位" not in text, f"启动日志仍自称占位实现：{text!r}"
    assert "反馈" in text, f"日志没说出真正的写入者之一（用户反馈）：{text!r}"
    assert "衰减" in text, (
        f"日志没提衰减是管理员手动端点而不是自动机制，读的人会以为它在自动衰减：{text!r}")


def test_placeholder_module_no_longer_prints_a_lying_update_line():
    from pathlib import Path

    import app.memory_weight_updater as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "当前为占位实现" not in src, "那句每轮都印的占位话还在模块里"
    assert not hasattr(mod, "update_memory_weights_from_feedback"), (
        "占位函数还挂着这个名字——下一个想接线的人照样能把它塞回定时器")


def test_weight_writers_are_exactly_the_documented_ones():
    """防止"权重只被显式动作改写"这句话又变成假话。

    真存储与内存替身各一份，形状必须一致。再多一个写 weight 的位置，本用例就
    要求显式承认它，并同步改启动日志那句口径——那句话说的是"谁在改权重"，
    它一旦落后于代码，就又回到本次要修的那个毛病上。
    """
    import re
    from pathlib import Path

    import app.memory.memory_manager as mm
    import app.memory.memory_router as mr

    def enclosing_def(lines, index):
        for earlier in reversed(lines[:index]):
            hit = re.match(r"\s*def (\w+)", earlier)
            if hit:
                return hit.group(1)
        return "<module>"

    writers = set()
    for module in (mm, mr):
        name = Path(module.__file__).name
        lines = Path(module.__file__).read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            if re.search(r'\[\s*["\']weight["\']\s*\]\s*=', line):
                writers.add(f"{name}:{enclosing_def(lines, lineno)}")

    assert writers == {
        "memory_manager.py:adjust_weights",     # 用户反馈，夹在 [0.1, 5.0]
        "memory_manager.py:update_memory",      # 显式改一条记忆的权重
        "memory_manager.py:decay_weights",      # 管理员手动衰减（不夹逼）
        "memory_router.py:update",              # 替身：与真存储同形状
        "memory_router.py:decay",
    }, f"权重写入者变了：{sorted(writers)}——启动日志那句口径也要跟着改"
