"""工具清单按实测可用性裁剪。

2026-09-19 把工具接进流式之后露出来的新问题：注册着四个工具，其中两个在这台机器上
必失败——execute_code 要 Docker（`docker: command not found`），web_search 要够得着
DuckDuckGo（三个端点 curl 全 000）。清单照原样传给模型，模型就会去调一个注定失败的
工具，拿回一句「工具执行错误」再硬答。

所以这里钉的是：传给模型的清单 = 现在真能用的那几个。而且是**测出来的**，不是写死的
名单——写死的那份在你装上 Docker 的第二天就变成新的谎。
"""
from pathlib import Path

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


def test_both_chat_paths_omit_the_tools_key_when_there_are_no_tools():
    """两条聊天路径对"没有工具时该发什么"必须给同一个答案。

    流式那条一直是对的（空数组不传，有些兼容网关对 tools=[] 直接 400），非流式那条
    无条件传 `tools=self.tools_schema` —— 症状只在工具全被判为不可用时出现：界面走
    流式没事，走非流式的调用方（以及以后任何直连 /v1/chat 的东西）400。
    一条路径对一条路径错，正是本项目栽过两次的那个形状。
    """
    root = Path(__file__).resolve().parent.parent
    pipeline = (root / "app" / "pipeline.py").read_text(encoding="utf-8")
    streaming = (root / "app" / "core" / "streaming.py").read_text(encoding="utf-8")
    assert "if self.tools_schema:" in pipeline, "非流式又无条件传 tools 了"
    assert "if tools:" in streaming, "流式那条的守卫被改掉了"
    assert 'tool_choice' in pipeline and 'tool_choice' in streaming


# ---------- 4. 搜索源探测问的是哪一件事（2026-09-22 换源时改的口径） ----------

def test_the_search_probe_asks_the_same_question_the_tool_answers(monkeypatch):
    """探测必须调工具用的那个函数，不能自己另算一份"通不通"。

    原来探的是 `socket.create_connection(host, 443)`——那是比"工具能用"弱得多的一条判据：
    TCP 连得上不代表源站肯给结果、更不代表我们解析得出来。换成爬搜索结果页之后这条
    分叉一定会出现（TCP 永远绿，页面结构一改就静默变空），症状正是本文件开头写的那件
    事：模型看见工具、调用它、拿回一句空话再硬答。
    """
    from app.tools import web_search

    def stale():
        availability._state["value"] = None
        availability._state["checked_at"] = 0.0

    def verdict():
        # 读 _state 而不是 search_reachable()：本文件那条 autouse 夹具把这个读者钉成了
        # 恒真（其余用例只关心注册表怎么筛），而这里要验的恰恰是探测自己算出了什么。
        # 公开读者的那条路另有 test_web_search_follows_the_probe 在钉。
        return availability._state["value"]

    stale()
    monkeypatch.setattr(web_search, "search", lambda *a, **k: [])
    availability._refresh()
    assert verdict() is False, "源解析不出结果，探测还说通"

    stale()
    monkeypatch.setattr(web_search, "search",
                        lambda *a, **k: [{"title": "t", "url": "https://e/x", "snippet": "s"}])
    availability._refresh()
    assert verdict() is True, "源明明给得出结果，探测说不通"
    assert availability.REFRESH_SECONDS >= 600, \
        f"每 {availability.REFRESH_SECONDS}s 去敲一次源站，探测本身变成了流量源"


def test_the_probe_does_not_keep_its_own_copy_of_the_source_list(monkeypatch):
    """**反向锁**：availability.py 里不许再出现自己连网络的代码，也不许留着旧源域名。

    探测口径改完之后，`SEARCH_PROBE_ENDPOINTS` 与 `socket` 就是第二份真相：它记着
    "我们用什么搜索"，而真正决定这件事的是 web_search.SEARCH_URL。留着的那天，
    换源的人只会去改 web_search，探测则继续对着一个早就不用的域名点头。
    """
    src = Path(__file__).resolve().parents[1].joinpath("app/tools/availability.py").read_text(encoding="utf-8")
    for gone in ("socket.create_connection", "duckduckgo", "SEARCH_PROBE_ENDPOINTS", "import socket"):
        assert gone not in src, f"探测里还留着自己那一套：{gone}"


def test_a_real_successful_search_postpones_the_next_probe(monkeypatch):
    """用户真搜成功一次 = 源此刻是好的，别再为这件事去敲源站。

    没有这一条，探测就是"每 15 分钟一次 + 与用量无关"的空转：闲置的服务也在替所有
    用户攒请求量，而这台机器的出口 IP 是和朋友们共用的。
    """
    from app.tools import web_search

    calls = []
    monkeypatch.setattr(web_search, "search", lambda *a, **k: calls.append(1) or [])
    availability._state["value"] = None
    availability._state["checked_at"] = 0.0
    availability.note_search_ok()
    assert availability._state["value"] is True, "记的是这次成功，不是默认值"
    availability._refresh()
    assert not calls, f"刚成功过一次还是又去探了一遍：{len(calls)} 次"
