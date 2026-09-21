"""工具得知道"是谁在调"——而且要由服务端说，不能让模型填。

`today_plan` / `set_reminder` 这一类工具读写的都是**某个具体的人**的数据。而
`execute_tool` 今天是 `func(**arguments)`，参数全部来自模型：要么这个工具拿不到身份
（只能"全局一份"，那跨用户就是迟早的），要么它声明一个 `user_id` 参数——那就是
把"读谁的日程"交给模型编，等于跨用户读取的门开着。
本项目已经为跨用户记忆投毒付过一次账（memory_manager 里那段顺序注释）。
"""
import pytest

from app.tools import registry, executor
from app.tools.response import ToolResponse


@pytest.fixture
def temp_tool():
    """注册一条只活在这条测试里的工具，跑完从注册表里摘干净。"""
    def _make(**kw):
        name = kw.pop("name", "probe_tool")
        reg = registry.register_tool(name, "探针工具", kw.pop("parameters", {}), **kw)

        def cleanup():
            registry.tools_registry.pop(name, None)
        return reg, cleanup
    yield _make
    registry.tools_registry.pop("probe_tool", None)


def test_the_server_says_who_not_the_model(temp_tool):
    seen = {}

    def probe(user_id):
        seen["user_id"] = user_id
        return "ok"
    reg, _ = temp_tool(needs_user=True)
    registry.tools_registry["probe_tool"]["function"] = reg(probe)

    executor.execute_tool("probe_tool", {"user_id": "u-999"}, user_id="u-real")
    assert seen["user_id"] == "u-real", "模型传进来的 user_id 赢了——那这条工具就是任意人可读"


def test_a_user_scoped_tool_without_an_identity_refuses_to_run(temp_tool):
    def probe(user_id):
        raise AssertionError("跑到这儿了")
    reg, _ = temp_tool(needs_user=True)
    registry.tools_registry["probe_tool"]["function"] = reg(probe)

    out = executor.execute_tool("probe_tool", {}, user_id=None)
    # 断言执行器自己那句话，不是探针的报错文案：上一版断言"身份"两个字，
    # 而探针自己的消息里也带这两个字，于是把守卫整个摘掉它照样绿。
    assert "缺少身份" in out, out
    assert "跑到这儿了" not in out, f"守卫没挡住执行：{out}"


def _offenders(table) -> list:
    return [name for name, info in table.items() if info.get("needs_user")
            and any(k in (info["parameters"] or {}).get("properties", {})
                    for k in ("user_id", "uid", "owner"))]


def test_no_user_scoped_tool_lets_the_model_supply_the_owner():
    """注册表级锁：needs_user 的工具，参数表里不许出现 user_id。

    出现了就等于在 schema 里请模型填一个归属人，注入只是时间问题。

    这条今天没有真正的对象（还没有任何工具标 needs_user），所以它自带一次正向对照：
    先拿一张故意写坏的表验判据会亮，再验现网这张是干净的。少了前半截，
    一条永远绿的锁和一个假绿灯没区别。
    """
    from app.tools import builtin_tools  # noqa: F401  确保注册表已填充

    bad = {"plan_reader": {"needs_user": True,
                           "parameters": {"type": "object",
                                          "properties": {"user_id": {"type": "string"}}}}}
    assert _offenders(bad) == ["plan_reader"], "判据本身坏了"
    assert _offenders(registry.tools_registry) == [],         f"这些工具把归属交给了模型：{_offenders(registry.tools_registry)}"


def test_existing_tools_still_work_without_an_identity():
    """calculator / help 不涉人，不能被新参数拖坏。"""
    from app.tools import builtin_tools  # noqa: F401  确保注册表已填充
    assert "结果" in executor.execute_tool("calculator", {"expression": "2+3"}) or \
        "5" in executor.execute_tool("calculator", {"expression": "2+3"})


def test_both_chat_paths_hand_the_caller_down_to_the_tool():
    """两条路都要把身份传到底，漏一条就是那条路上的工具永远看不见"是谁"。"""
    import inspect

    import app.core.streaming as streaming
    import app.pipeline as pipeline_mod

    # 判"execute_tool 那一句带没带身份"，不判"文件里有没有 user_id 字样"：
    # 后者账本那几行就能把它喂绿。
    def calls_with_identity(src):
        return [ln for ln in src.splitlines() if "execute_tool(" in ln]

    for label, src in (("非流式", pipe_src := inspect.getsource(pipeline_mod)),
                       ("流式", inspect.getsource(streaming))):
        calls = calls_with_identity(src)
        assert calls, f"{label}那条找不到 execute_tool 调用：这条锁在空转"
        assert all("user_id=" in c for c in calls), f"{label}那条有调用没带身份：{calls}"
