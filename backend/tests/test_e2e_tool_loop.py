"""真 SDK → 真 HTTP 的工具调用端到端锁（派单①-②，"5 个月不通"的那条红线）。

conftest 的 autouse 替身把 pipeline/llm_client 的 build_client 与 streaming 的
stream_chat 整个换掉——测试因此又快又不烧额度，但代价是 **openai SDK 到上游的
那一整段从来没人走过**：SDK 版本一升级改了 tool_calls 的序列化形状、或 SSE 分片
参数拼接坏了，这里的 pytest 全都不会红。历史已经付过一次账（工具链路坏了 5 个月
没人发现），这个文件就是那张账单的还法。

做法：本地起一个 OpenAI 兼容的 mock 上游（标准库 http.server，不引新依赖），
把 provider 指到它，再把 conftest 盖住的三个替身逐个掀开——跑的是装配后的
真 app、真 SDK、真 HTTP。

回退必红自查：
- 把 executor 的 needs_user 身份注入删掉 → test ③ 红（工具报"缺少身份"）；
- 把 react_agent 的 call_id 退回 f"call_{turn}" → test ④ 的"两个调用 id 互异"红；
- 把 streaming 的分片 arguments 累加删掉 → test ② 红（工具参数残缺）。
"""
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.providers import build_client as real_build_client
from app.core.providers import store as provider_store


# ------------------------------------------------------------------ mock 上游 --

class _Mode:
    """mock 上游每轮怎么答，由用例设置。"""
    def __init__(self):
        self.protocol = "native"        # native=结构化 tool_calls；text=```json``` 协议
        self.tools = [("calculator", {"expression": "987654321*123456789"})]
        self.final_prefix = "FINAL:"


MODE = _Mode()
CALLED = []                             # 每次上游收到的完整请求


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body.get("messages", [])
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        CALLED.append({"stream": bool(body.get("stream")),
                       "tools_sent": bool(body.get("tools")),
                       "messages": msgs,
                       "tool_call_ids": [tc["id"]
                                         for m in msgs if m.get("tool_calls")
                                         for tc in m["tool_calls"]]})
        if body.get("stream"):
            self._sse(bool(tool_results))
        else:
            self._json(body, tool_results)

    def _json(self, body, tool_results):
        if tool_results:
            content = self._final_text(tool_results)
        elif MODE.protocol == "text":
            blocks = "\n".join(
                "我要用工具。\n```json\n"
                + json.dumps({"tool": name, "args": args}) + "\n```"
                for name, args in MODE.tools)
            content = blocks
        else:
            payload = {"choices": [{"index": 0, "finish_reason": "tool_calls",
                                    "message": {"role": "assistant", "content": None,
                                                "tool_calls": [
                        {"id": f"call_e2e_{i}", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}
                        for i, (name, args) in enumerate(MODE.tools)]}}]}
            self._send(payload)
            return
        self._send({"choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}]})

    @staticmethod
    def _final_text(tool_results):
        # 把每条工具回执原样拼回去：断言"哪个工具真的执行了、结果真的进了上下文"
        return "".join(f"{MODE.final_prefix}{m['content']}| " for m in tool_results)

    def _send(self, out):
        out.update({"id": "cmpl-1", "object": "chat.completion", "model": "e2e-chat",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                              "total_tokens": 15}})
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, has_result):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def ev(obj):
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()

        def chunk(delta, finish=None):
            ev({"id": "c", "object": "chat.completion.chunk", "model": "e2e-chat",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        if has_result:
            chunk({"role": "assistant"})
            chunk({"content": "FINAL:流式收工"})
            chunk({}, finish="stop")
        else:
            name, args = MODE.tools[0]
            payload = json.dumps(args)
            half = len(payload) // 2
            # 分片到达：arguments 先给空串、再劈成两段——逼出真实客户端的按 index 累加
            chunk({"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_e2e_1", "type": "function",
                 "function": {"name": name, "arguments": ""}}]})
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": payload[:half]}}]})
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": payload[half:]}}]})
            chunk({}, finish="tool_calls")
        ev({"id": "c", "object": "chat.completion.chunk", "model": "e2e-chat",
            "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                     "total_tokens": 15}})
        self.wfile.write(b"data: [DONE]\n\n")


# ------------------------------------------------------------------ 装配 --

@pytest.fixture(scope="module")
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture
def real_stack(upstream, monkeypatch):
    """把 conftest 的三个替身逐个掀开：真 stream_chat、真 build_client。

    收尾必须原样盖回去——这三枚替身是全套件的承重墙，带着真函数出门，
    下一条与它毫无关系的用例会去打网络然后超时，红到离真凶很远的地方。
    """
    import app.core.llm_client as llm_client
    import app.core.streaming as streaming
    import app.pipeline as pipeline

    fake_stream = streaming.stream_chat          # 进场时盖在上面的 conftest 替身
    prev_default = next((p["id"] for p in provider_store.all() if p.get("is_default")), None)
    provider_store.upsert({
        "id": "e2e", "label": "E2E 上游", "base_url": upstream,
        "api_key": "sk-e2e-a1b2c3d4", "model": "e2e-chat", "is_default": True,
    })
    monkeypatch.setattr(pipeline, "build_client", real_build_client)
    monkeypatch.setattr(llm_client, "build_client", real_build_client)
    importlib.reload(streaming)                  # 模块级 import 时抓的替身要一并洗掉

    CALLED.clear()
    MODE.protocol = "native"
    MODE.tools = [("calculator", {"expression": "987654321*123456789"})]
    try:
        yield
    finally:
        importlib.reload(streaming)
        streaming.stream_chat = fake_stream
        if prev_default:
            provider_store.set_default(prev_default)
        provider_store.delete("e2e")


@pytest.fixture
def client(real_stack):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _sse_content(resp):
    text = ""
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            try:
                obj = json.loads(line[6:])
            except ValueError:
                continue
            if obj.get("type") == "content":
                text += obj.get("text", "")
            elif obj.get("type") == "error":
                text += "[ERROR]" + obj.get("message", "")
    return text


# ------------------------------------------------------------------ 判据 --

def test_native_tool_loop_end_to_end(client):
    """① 非流式：原生 tool_calls → 真执行 → 回灌 → 终答，整条链一步不缺。"""
    r = client.post("/v1/chat", json={"messages": [
        {"role": "user", "content": "算 987654321*123456789"}]})
    assert r.status_code == 200
    reply = r.json()["reply"]
    assert "121932631112635269" in reply, f"工具没真跑或结果没回灌：{reply[:120]}"
    assert [c["tools_sent"] for c in CALLED] == [True, True], \
        "工具清单没随请求发给上游（文本协议之外的第二条路是 SDK 协议）"


def test_stream_tool_loop_accumulates_fragmented_arguments(client):
    """② 流式：arguments 按分片到达也要拼回完整 JSON 再执行。"""
    r = client.post("/v1/chat/stream", json={"messages": [
        {"role": "user", "content": "算 987654321*123456789"}]})
    assert r.status_code == 200
    body = _sse_content(r)
    assert "流式收工" in body and "[ERROR]" not in body, body[:200]
    # 分片拼接的硬证据在上一轮请求里：第二轮带上来的 tool 消息必须是**完整参数**
    # 执行出的结果。累加坏掉的话参数残缺、calculator 报错，这条结果串就不存在——
    # 只断言"有 tool 消息"是测不出那种坏法的。
    last_msgs = CALLED[-1]["messages"]
    tool_contents = [m["content"] for m in last_msgs if m.get("role") == "tool"]
    assert tool_contents and all("工具执行错误" not in c for c in tool_contents), \
        tool_contents
    assert any("987654321" in c or "121932631112635269" in c for c in tool_contents), \
        f"参数没拼全：{tool_contents}"


def test_needs_user_tool_gets_the_server_side_identity(client):
    """③ 身份注入：模型没带 user_id 的 needs_user 工具，服务端那份必须顶上。

    这条同时守 react_agent 的修复之外的另一半：executor 的覆盖逻辑。删掉
    _dispatch 里 kwargs["user_id"] 的覆盖，工具要么报缺身份、要么落到错误的账上。
    """
    MODE.tools = [("today_plan", {})]
    r = client.post("/v1/chat", json={"messages": [
        {"role": "user", "content": "今天干什么"}]})
    assert r.status_code == 200
    reply = r.json()["reply"]
    assert "工具执行错误" not in reply and "缺少" not in reply, reply[:200]
    assert "FINAL:" in reply, f"needs_user 工具没有真正执行：{reply[:120]}"


def test_react_multi_tool_turn_message_ids_are_unique(client):
    """④ ReAct 一轮两个工具调用：assistant.tool_calls 的 id 必须互不相同。

    原先写死 f"call_{turn}"，同一轮第二个调用的 tool 消息与第一个共 id——这份
    消息历史再发回任何 OpenAI 兼容上游都是非法形状，等于第二轮起整条链必炸。
    """
    MODE.protocol = "text"
    MODE.tools = [("calculator", {"expression": "2+3*4"}),
                  ("calculator", {"expression": "10-1"})]
    r = client.post("/v1/agent/run", json={"task": "分别算 2+3*4 和 10-1",
                                           "max_turns": 3})
    assert r.status_code == 200
    result = r.json()["result"]
    assert "14" in result and "9" in result, f"两个工具结果没都进上下文：{result[:160]}"
    ids = CALLED[-1]["tool_call_ids"]
    assert len(ids) == 2 and len(set(ids)) == 2, f"同一轮两个调用共用了 id：{ids}"
