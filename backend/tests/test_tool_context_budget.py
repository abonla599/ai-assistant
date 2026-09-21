"""工具输出的上下文预算（app/tools/executor.py）。

要守的形状有两个：一是**别让一万行日志进模型**，二是**截了要看得出来**。第二条比
第一条更容易被写丢——静默截断的产物是一个自信地基于半截日志下结论的模型，而这正是
本仓反复在防的那类"效果没了但不报错"。所以除了长度断言，这里还断言开头与结尾都活着、
省略说明里带着真实字数、以及截断这件事会进服务端日志。
"""

import re
from pathlib import Path

import pytest

from app.tools import executor
from app.tools.executor import TOOL_OUTPUT_BUDGET, clip_for_model, execute_tool
from app.tools.registry import tools_registry


def _blob(n: int, head: str = "开始：查询结果如下", tail: str = "错误：最后一行才是关键") -> str:
    middle = "填充内容。" * ((n - len(head) - len(tail)) // 5 + 1)
    return (head + middle + tail)[:n]


def test_output_within_budget_passes_through_untouched():
    text = _blob(200)
    assert clip_for_model(text, budget=1000) == text
    assert clip_for_model(text, budget=200) == text, "正好等于预算的不能截，边界要能等"


@pytest.mark.parametrize("budget", [60, 200, 1000, TOOL_OUTPUT_BUDGET])
def test_long_output_is_cut_to_budget(budget):
    text = _blob(50_000)
    out = clip_for_model(text, budget=budget)
    assert len(out) <= budget, f"截完还是 {len(out)} 字，预算 {budget}：" + out[:80]
    assert "省略" in out, "截断了却没说，模型会以为自己看到的是全貌"
    assert str(len(text)) in out, "省略说明里要写出原长度，否则模型没法判断该不该再查"


def test_both_ends_survive_the_cut():
    """只留头会正好丢掉要看的那半：日志与报错的关键行总在结尾。"""
    text = _blob(50_000)
    out = clip_for_model(text, budget=2000)
    assert out.startswith(text[:20])
    assert out.endswith(text[-20:])


def test_omission_notice_is_in_the_middle_not_at_the_end():
    """说明必须在被丢掉的那段的位置上，而且不能是最后一句。

    挂在结尾的话，模型读到的是"头 + 尾 + 一句说明"，它会以为省略发生在输出的末尾
    ——而真实情况是中间少了一大段，两者给出的结论完全不同。
    """
    out = clip_for_model(_blob(50_000), budget=2000)
    at = out.index("省略")
    assert 0 < at < len(out) - 20, out[:120]


def test_execute_tool_applies_the_budget_itself(monkeypatch, capsys):
    """闸必须在执行器出口，不能靠每条聊天路径各自记得。

    本项目的工具缺口就是这么来的：非流式那条传了 tools，界面只走流式那条，于是
    "注册了"等于"不存在"。同理，预算写成调用方的自觉就等于没有预算。
    """
    def chatty():
        return _blob(100_000)

    monkeypatch.setitem(tools_registry, "chatty", {
        "function": chatty, "description": "造一大坨输出", "parameters": {"type": "object", "properties": {}},
        "available": None, "needs_user": False,
    })

    out = execute_tool("chatty", {})
    assert len(out) <= TOOL_OUTPUT_BUDGET, f"execute_tool 没有裁：{len(out)} 字"
    assert "省略" in out
    logged = capsys.readouterr().out
    assert "chatty" in logged and "截断" in logged, "线上截断了却没人知道，等于凭空少功能"


def test_failure_text_is_clipped_too(monkeypatch):
    """报错那条路也要过闸：一次异常抛出一整个响应体是常事。"""
    def explode():
        raise RuntimeError(_blob(90_000))

    monkeypatch.setitem(tools_registry, "explode", {
        "function": explode, "description": "抛一个巨型异常",
        "parameters": {"type": "object", "properties": {}},
        "available": None, "needs_user": False,
    })

    out = execute_tool("explode", {})
    assert "✗" in out, "失败还是得报成失败，不能因为超长就变成成功"
    assert len(out) <= TOOL_OUTPUT_BUDGET


def test_unknown_tool_answer_is_not_affected():
    out = execute_tool("no-such-tool", {})
    assert "未知工具" in out
    assert "省略" not in out


def test_budget_is_one_number_defined_in_one_place():
    """两处事实来源会漂移：这条锁保证截断逻辑与日志说的是同一个数。

    预算只许定义一次，而且不许有调用方自带字面量——否则改了常量，日志与说明句里
    还是旧数字，运维日志就开始说谎。
    """
    src = Path(executor.__file__).read_text(encoding="utf-8")
    assert src.count("TOOL_OUTPUT_BUDGET = ") == 1, "预算被定义了两遍，第二遍是等着漂移的那一份"
    assert not re.search(r"clip_for_model\([^)]*,\s*\d+\s*\)", src), \
        "有调用方自带字面量预算：那等于绕过单源"
