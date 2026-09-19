"""工具清单按实测可用性裁剪。

2026-09-19 把工具接进流式之后露出来的新问题：注册着四个工具，其中两个在这台机器上
必失败——execute_code 要 Docker（`docker: command not found`），web_search 要够得着
DuckDuckGo（三个端点 curl 全 000）。清单照原样传给模型，模型就会去调一个注定失败的
工具，拿回一句「工具执行错误」再硬答。

所以这里钉的是：传给模型的清单 = 现在真能用的那几个。而且是**测出来的**，不是写死的
名单——写死的那份在你装上 Docker 的第二天就变成新的谎。
"""
import pytest

from app.tools import availability
from app.tools import registry
from app.tools.registry import tools_registry, get_all_tools_schema, get_available_tools_schema
from app.tools.executor import execute_tool


@pytest.fixture(autouse=True)
def _no_real_network_probe(monkeypatch):
    """默认把搜索探测钉成"通"，让每条用例只改自己关心的那一维。

    不钉住的话 get_available_tools_schema() 会去真连 DuckDuckGo：测试变成一网络
    依赖，而且在能连通的机器上会慢到几秒。
    """
    monkeypatch.setattr(availability, "search_reachable", lambda: True)


def _names(schema):
    return {t["function"]["name"] for t in schema}


def _add(name, available):
    registry.register_tool(name=name, description="探针工具", parameters={},
                           available=available)(lambda: "ok")


def _drop(*names):
    for n in names:
        tools_registry.pop(n, None)


# ---------- 1. 注册表这一层 ----------

def test_unavailable_tool_is_dropped_from_the_available_schema():
    _add("_probe_off", lambda: False)
    _add("_probe_on", lambda: True)
    try:
        assert {"_probe_off", "_probe_on"} <= _names(get_all_tools_schema()), \
            "全量清单不该被这次改动影响——它还得能给运维看"
        ok = _names(get_available_tools_schema())
        assert "_probe_on" in ok
        assert "_probe_off" not in ok, "必失败的工具还在给模型"
    finally:
        _drop("_probe_off", "_probe_on")


def test_tools_without_an_availability_check_are_kept():
    """没声明 available 的（calculator、help）默认就是能用，别把它们误杀。"""
    _add("_probe_none", None)
    try:
        assert "_probe_none" in _names(get_available_tools_schema())
    finally:
        _drop("_probe_none")


def test_a_broken_availability_check_fails_open():
    """探测代码自己炸了，不能顺手关掉一个也许好着的工具。

    方向要选对：误开只是多一次失败调用，误关是把一个能用的功能悄悄藏了，
    而后者没人会去查。
    """
    def boom():
        raise RuntimeError("探测挂了")
    _add("_probe_boom", boom)
    try:
        assert "_probe_boom" in _names(get_available_tools_schema())
    finally:
        _drop("_probe_boom")


# ---------- 2. 两个真被裁掉的工具 ----------

def test_execute_code_follows_docker(monkeypatch):
    import app.tools.builtin_tools as bt
    monkeypatch.setattr(bt.sandbox, "client", None)
    assert "execute_code" not in _names(get_available_tools_schema())
    monkeypatch.setattr(bt.sandbox, "client", object())
    assert "execute_code" in _names(get_available_tools_schema()), \
        "Docker 回来了工具也该回来，别让人再去改一遍代码"


def test_web_search_follows_the_probe(monkeypatch):
    monkeypatch.setattr(availability, "search_reachable", lambda: False)
    assert "web_search" not in _names(get_available_tools_schema())
    monkeypatch.setattr(availability, "search_reachable", lambda: True)
    assert "web_search" in _names(get_available_tools_schema())


# ---------- 3. 装配点：模型真正拿到的那份 ----------

def test_pipeline_hands_the_model_only_working_tools(monkeypatch):
    """前两条只证明注册表会筛；这条证明聊天这条路真的用了筛过的。

    少这一条就是重演 2026-09-19 那个 bug 的形状：函数能收，调用方没传。
    """
    import app.tools.builtin_tools as bt
    from app.pipeline import ChatPipeline
    monkeypatch.setattr(bt.sandbox, "client", None)
    monkeypatch.setattr(availability, "search_reachable", lambda: False)
    names = _names(ChatPipeline(user_id="u_availability_test").tools_schema)
    assert "calculator" in names
    assert "execute_code" not in names and "web_search" not in names


def test_help_does_not_advertise_what_cannot_run(monkeypatch):
    """help 是模型查"你都有什么"的地方，它推荐一个必失败的工具等于亲自挖坑。"""
    import app.tools.builtin_tools as bt
    monkeypatch.setattr(bt.sandbox, "client", None)
    out = execute_tool("help", {})
    assert "execute_code" not in out, f"help 还在推荐必失败的工具：{out}"
    assert "calculator" in out
