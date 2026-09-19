"""流式路径的工具调用。

起因（2026-09-19 线上实测）：界面上让助手"别用心算，调用计算器工具算
987654321×123456789"，它回的是「我无法调用外部计算器工具」——那是实话。
非流式的 ChatPipeline 一直把 tools 传给模型（pipeline.py:130-134），而前端只走
`/v1/chat/stream`（api.js:70），那条路上的 `stream_chat` 连 `tools` 形参都没有。

这个文件存在的意义还包括：conftest 的 autouse 夹具把 `streaming.stream_chat`
整个换成假函数，所以真函数在别的用例里从没执行过——这里每个用例都先 reload 拿回
真身，否则测的还是那个假的。
"""
import importlib
import inspect
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.core.streaming as streaming
from app.main import app

client = TestClient(app)


def _chunk(content=None, tool_calls=None):
    """拼一个 OpenAI 流式 chunk 的形状。"""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)])


def _tool_call_delta(index=0, call_id=None, name=None, arguments=None):
    """id/name 只在第一个增量里出现，arguments 是分片流回来的——真实 API 就是这样。"""
    return SimpleNamespace(index=index, id=call_id,
                           function=SimpleNamespace(name=name, arguments=arguments))


class _RecordingStream:
    """可迭代的假 stream：按预先排好的 chunk 序列往外发。"""

    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        return iter(self._chunks)


class FakeCompletions:
    def __init__(self, responses):
        """responses: 每次 create() 调用消费一个 [chunk...] 列表。"""
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self.responses.pop(0) if self.responses else []
        return _RecordingStream(chunks)


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)


class FakeClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


@pytest.fixture
def real_stream_chat(monkeypatch):
    """拿回真的 stream_chat，并把它的 build_client 换成假客户端。

    必须先 reload：autouse 的 _stub_llm_calls 已经把模块属性换成假函数了。
    """
    importlib.reload(streaming)
    holder = {}

    def build(provider):
        fake = holder["client"]
        return fake

    monkeypatch.setattr(streaming, "build_client", build)

    def _install(responses):
        holder["client"] = FakeClient(responses)
        return holder["client"].chat.completions

    yield _install
    importlib.reload(streaming)


def _tools():
    from app.tools.registry import get_all_tools_schema
    return get_all_tools_schema()


# ---------- 1. tools 必须真的进到模型请求里 ----------

def test_stream_chat_forwards_tools_and_tool_choice(real_stream_chat):
    """这条钉的就是那个 bug 本身：以前 create() 的 kwargs 里根本没有 tools。"""
    completions = real_stream_chat([[_chunk(content="你好")]])

    out = "".join(real_result := list(
        streaming.stream_chat("fake-chat", [{"role": "user", "content": "hi"}],
                              provider_id="fake-model", tools=_tools())))

    sent = completions.calls[0]
    assert sent.get("tools"), f"没把工具清单传给模型，模型当然说它不会用工具：{sorted(sent)}"
    assert [t["function"]["name"] for t in sent["tools"] if t.get("type") == "function"] \
        == sorted([t["function"]["name"] for t in _tools() if t.get("type") == "function"]) \
        or set(t["function"]["name"] for t in sent["tools"]) == \
        set(t["function"]["name"] for t in _tools())
    assert sent.get("tool_choice") == "auto", "传了清单但没让模型自己决定，等于没传"
    assert out == "你好", f"正文要原样流出来，实得 {out!r}"


def test_stream_chat_without_tools_sends_no_tools_key(real_stream_chat):
    """不带工具时不许凭空塞一个空 tools——有些网关对空数组直接 400。"""
    completions = real_stream_chat([[_chunk(content="嗯")]])

    "".join(streaming.stream_chat("fake-chat", [{"role": "user", "content": "hi"}],
                                  provider_id="fake-model"))

    assert "tools" not in completions.calls[0], \
        f"不该带 tools：{sorted(completions.calls[0])}"


# ---------- 2. 模型要求调用工具时，真的执行并回灌 ----------

def test_stream_chat_executes_tool_and_continues(real_stream_chat):
    """arguments 分两片流回来，必须拼完整再解析；执行结果要以 tool 消息回灌，
    然后第二轮的正文才流给调用方。"""
    tool_schema = _tools()
    second = [_chunk(content="结果是 "), _chunk(content="420")]
    first = [
        _chunk(tool_calls=[_tool_call_delta(call_id="call_1", name="calculator",
                                            arguments='{"expression": ')]),
        _chunk(tool_calls=[_tool_call_delta(call_id=None, name=None,
                                            arguments='"17*24+12"}')]),
    ]
    completions = real_stream_chat([first, second])

    text = "".join(streaming.stream_chat("fake-chat",
                                         [{"role": "user", "content": "算 17×24+√144"}],
                                         provider_id="fake-model", tools=tool_schema))

    assert len(completions.calls) == 2, "工具执行后必须有第二轮，实得 " \
        f"{len(completions.calls)} 轮——模型没拿到结果就答不了"
    second_msgs = completions.calls[1]["messages"]
    assistant = [m for m in second_msgs if m.get("role") == "assistant"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert assistant and assistant[-1].get("tool_calls"), \
        "第二轮里该带上模型那条要求调用工具的 assistant 消息"
    assert tool_msgs, f"没有 tool 结果回灌：{[m.get('role') for m in second_msgs]}"
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    # 真执行：17*24+12 = 420，由 app.tools.executor 算出来的，不是假数据
    assert "420" in str(tool_msgs[0]["content"]), \
        f"计算器结果不对，实得 {tool_msgs[0]['content']!r}"
    assert text == "结果是 420", f"只该流出第二轮的正文，实得 {text!r}"


def test_stream_chat_survives_a_failing_tool(real_stream_chat):
    """工具自己炸了不能把整条流打断：错误要作为工具结果回灌，让模型自己解释。"""
    first = [_chunk(tool_calls=[_tool_call_delta(call_id="call_x", name="calculator",
                                                 arguments='{"expression": "2++3"}')])]
    second = [_chunk(content="这个表达式我算不了")]
    completions = real_stream_chat([first, second])

    text = "".join(streaming.stream_chat("fake-chat",
                                         [{"role": "user", "content": "2++3"}],
                                         provider_id="fake-model", tools=_tools()))

    tool_msgs = [m for m in completions.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["content"], "失败也要有内容回灌给模型"
    assert text == "这个表达式我算不了"


def test_stream_chat_stops_at_a_turn_limit(real_stream_chat):
    """模型反复要求调用工具时不能无限循环——每一轮都是一次真实计费调用。"""
    loop = [_chunk(tool_calls=[_tool_call_delta(call_id="c_loop", name="calculator",
                                                arguments='{"expression": "1+1"}')])]
    # 每一轮都只回工具调用，永远不给正文
    completions = real_stream_chat([loop] * 10)

    text = "".join(streaming.stream_chat("fake-chat",
                                         [{"role": "user", "content": "循环吧"}],
                                         provider_id="fake-model", tools=_tools()))

    assert len(completions.calls) <= 6, \
        f"工具循环没有上限，跑了 {len(completions.calls)} 轮"
    assert text == ""


# ---------- 3. 端点这一头也得真的把清单交下来 ----------

def test_chat_stream_endpoint_passes_the_tool_schema(real_stream_chat):
    """前两条只证明 stream_chat 收得下 tools；线上那个 bug 是**调用方没传**。

    端点里 `from app.core.streaming import stream_chat` 写在函数体内，所以它调的
    就是模块属性——夹具 reload 之后这里走的是真函数，能看见真实 kwargs。
    """
    completions = real_stream_chat([[_chunk(content="好的")]])

    res = client.post("/v1/chat/stream", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "帮我算 17×24+√144"}],
    })

    assert res.status_code == 200, f"端点直接失败：{res.status_code} {res.text[:200]}"
    assert completions.calls, "模型请求根本没发出去，这条测试是空的"
    sent = completions.calls[0]
    assert sent.get("tools"), \
        f"端点没把工具清单交给 stream_chat（这就是线上那句「我无法调用外部计算器工具」的成因）：" \
        f"{sorted(sent)}"
    names = {t["function"]["name"] for t in sent["tools"] if t.get("function")}
    assert "calculator" in names, f"清单里没有计算器：{sorted(names)}"


# ---------- 4. 打桩函数不许比真身"更能收" ----------

def test_the_autouse_stub_is_no_wider_than_the_real_signature():
    """conftest 的 autouse 夹具把 `streaming.stream_chat` 整个换成了假的，端点用例
    跑的就是那个假身。

    假签名可以比真签名窄（少个参数会在调用点当场 TypeError，响得很），但不能宽出去：
    多收一个参数、或者图省事补个 `**kwargs`，生产里必然报错的调用点在测试里就静静
    通过了——下一次给 stream_chat 加参数时，这个文件会假装自己还测得着。
    线上那个「端点没传 tools」的 bug 正是这个形状，别在测试脚手架上再犯一次。
    """
    stub = streaming.stream_chat                      # 夹具换上去的假身
    importlib.reload(streaming)                       # 只有 reload 才拿得回真身
    try:
        real = streaming.stream_chat
        stub_params = inspect.signature(stub).parameters
        real_params = inspect.signature(real).parameters
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD
                       for p in stub_params.values()), \
            "打桩函数写了 **kwargs，签名比对就此失效"
        extra = set(stub_params) - set(real_params)
        assert not extra, f"打桩函数收得下真身不认识的参数：{sorted(extra)}"
        assert inspect.isgeneratorfunction(real) and inspect.isgeneratorfunction(stub), \
            "stream_chat 必须是同步生成器（async 会让阻塞读冻住整个事件循环）"
    finally:
        importlib.reload(streaming)
