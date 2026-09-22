"""阻塞式上游调用不许写在 async 端点里——这条锁的是"一个请求卡住，整台服务全卡"。

起因（2026-09-17 线上）：一次对话把整个进程冻住，连 /health 都 12 秒不返回，
Cloudflare 报 524。根因不是"模型慢"，而是 `async def chat` 里直接调同步的
openai SDK：FastAPI 只把**同步** def 端点丢进线程池，async 端点是跑在事件循环上
的，所以这一个请求挂住期间，所有请求一起停摆——包括不查库、不鉴权的 /health。
在隔离实例上实测过：一个卡住的上游让 /health 从 0.08s 变成 10s 超时。

三条锁各管一层：端点的声明形式、响应体生成器的声明形式、以及症状本身（卡住的请求
进行时 /health 仍要立刻答得出来）。前两条是静态判据——要钉的就是"声明形式"，改不成
别的形式就不算修好；第三条真的把两个请求放进同一个事件循环跑，回归时它是红的那条。
"""
import inspect
import time

import pytest

from app.core import streaming
from app.main import app

# 这些端点要么朝进程外发一次网络请求（模型/嵌入），要么做真实文件 I/O
# （会话与配置整份落盘、附件写删、PDF 全文抽取、chroma 逐条更新）。它们必须是同步 def，
# 好让 FastAPI 把整个处理函数放进线程池，而不是占着事件循环。
# 判据是"有没有阻塞"，不是"是不是网络"——一次 200ms 的 sessions.json 写盘和一次
# 慢上游是同一类事故，只是量级不同。
NETWORK_BOUND_ENDPOINTS = [
    "/v1/chat",
    "/v1/chat/stream",
    "/v1/agent/run",
    "/v1/agent/orchestrate",
    "/v1/memory/add",
    "/v1/memory/search",
    "/v1/memory/update",
    # 这两个走的是 provider_store.ping()：一次 timeout=20 的同步模型调用。点一下
    # 设置里的「测试」就能把整台服务冻住二十秒，形状与上面那次 524 一模一样。
    "/v1/providers/test",
    "/v1/providers/{provider_id}/test",
    # 会话与模型服务配置：每次写都是 makedirs + 整份 JSON 落盘
    "/v1/sessions",
    "/v1/sessions/{session_id}",
    "/v1/sessions/{session_id}/messages",
    "/v1/models",
    "/v1/providers",
    "/v1/providers/{provider_id}",
    "/v1/providers/{provider_id}/default",
    # 附件：写文件、删文件，PDF 还要 fitz 全文抽取（CPU 秒级）
    "/v1/uploads",
    "/v1/uploads/{upload_id}/file",
    # 反馈：全表扫消息 + 整份 feedback.json 读写 + chroma 逐条改权重 + 再整读一遍分析
    "/v1/feedback",
]

# 上游卡住的模拟时长，与 /health 的容忍上限。上限比"循环被占住"的任何形状低一个
# 数量级，同时给本机调度留足余量（慢机器上百毫秒是常态）。
STALL_SECONDS = 3.0
HEALTH_BUDGET_SECONDS = 1.0


def _endpoint_for(path):
    for route in app.routes:
        if getattr(route, "path", None) == path and hasattr(route, "endpoint"):
            return route.endpoint
    raise AssertionError(f"路由 {path} 不在了——那这条锁就成了空断言")


@pytest.mark.parametrize("path", NETWORK_BOUND_ENDPOINTS)
def test_network_bound_endpoints_run_off_the_event_loop(path):
    endpoint = _endpoint_for(path)
    assert not inspect.iscoroutinefunction(endpoint), (
        f"{path} 是 async 端点，却直接做阻塞网络 I/O："
        f"一个慢上游就会冻住整个进程（含 /health）。改成同步 def 交给线程池。")


def test_stream_body_iterator_is_sync_so_starlette_threadpools_it():
    """StreamingResponse 只对**非** AsyncIterable 的响应体走 iterate_in_threadpool。

    所以端点改成 def 还不够：里面的生成器若还是 async generator，Starlette 会把它
    当 AsyncIterable 直接在事件循环上迭代，阻塞照旧。

    这里读源码而不是 import 后的对象：conftest 会把 stream_chat 换成测试桩，
    对着桩断言等于测桩不测产品。
    """
    import ast
    from pathlib import Path

    source = Path(streaming.__file__).with_name("streaming.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    kinds = {n.name: type(n).__name__ for n in ast.walk(tree) if isinstance(
        n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "stream_chat" in kinds, f"streaming.py 里找不到 stream_chat：{sorted(kinds)}"
    assert kinds["stream_chat"] == "FunctionDef", (
        "stream_chat 是 async generator——它会跑在事件循环上，"
        "整段模型输出期间整个服务都是冻住的")


def test_health_is_answerable_while_a_stream_is_stalled(monkeypatch):
    """症状本身：一个卡在半途的对话正在进行时，/health 必须在 1 秒内答出来。

    两个请求必须跑在**同一个事件循环**上，否则这条测不到任何东西：TestClient 给
    每个请求另开一个 portal（各自一条循环、各自一个调度器），冻住一个伤不到另一个
    ——我拿它验过一次，回归形状下它照样绿。所以这里用 httpx 的 ASGITransport 直接
    驱动 app，再用 asyncio.gather 的语义并发两条，那才是 uvicorn 的形状。

    判据是耗时而不是状态码：卡住的那条最终也返回 200，只是所有人都得等它。
    """
    import asyncio
    import threading

    import httpx

    stalled = threading.Event()

    class StalledChunks:
        """一个既能被 `for` 也能被 `async for` 驱动的源，中间夹一次真阻塞 sleep。

        两种迭代协议都得给，是因为这条要钉的正是"谁来驱动它"：修好的端点在线程池里
        用 `for`，卡住的是那个线程；回归的端点在循环上用 `async for`，卡住的是循环。
        只支持一种的话，另一侧会直接 TypeError，测的就不是这件事了。
        """

        def __init__(self):
            self._chunks = iter(("半句话", "就被卡住了", "说完了"))
            self._pulls = 0

        def _pull(self):
            if self._pulls == 1:        # 只卡一次：卡住第二块，别的照常流
                stalled.set()
                time.sleep(STALL_SECONDS)
            self._pulls += 1
            return next(self._chunks)

        def __iter__(self):
            return self

        def __next__(self):
            return self._pull()

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return self._pull()
            except StopIteration:
                raise StopAsyncIteration

    def stalled_stream(model, messages, provider_id=None, temperature=0.7,
                       max_tokens=4096, tools=None, max_tool_turns=5, user_id=None):
        return StalledChunks()

    monkeypatch.setattr(streaming, "stream_chat", stalled_stream)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            began = time.perf_counter()
            chat = asyncio.ensure_future(client.post(
                "/v1/chat/stream",
                json={"model": "deepseek-chat",
                      "messages": [{"role": "user", "content": "你好"}]}))
            await asyncio.sleep(0.2)          # 让那条流真的跑到卡住的位置
            health = await client.get("/health")
            waited = time.perf_counter() - began
            status = (await chat).status_code
            return health, waited, status

    health, waited, chat_status = asyncio.run(scenario())
    assert stalled.wait(1), "前提没成立：那条流根本没走到卡住的地方，整段就是空测"
    assert health.status_code == 200, health.text
    assert chat_status == 200, "流式请求自己也要跑得完"
    assert waited < HEALTH_BUDGET_SECONDS, (
        f"/health 等了 {waited:.1f}s：一个卡住的模型请求把事件循环占住了，"
        f"这正是线上 524 的形状（上限 {HEALTH_BUDGET_SECONDS}s）")


def test_nobody_turns_off_certificate_verification_process_wide():
    """main.py 里不许再出现"全进程关掉证书校验"。

    这行早先是给卡巴斯基拆 TLS 救急用的，代价是 urllib/http.client 等所有走默认
    上下文的出网请求都不再验证书——方向与 app/core/tls.py 的承诺正相反。模型客户端
    现在统一用系统信任锚（core/tls.system_ssl_context），这张全局免检牌已经没有
    存在的理由，留着就是给下一个人省事的借口。
    """
    from pathlib import Path
    from app.core import paths as _paths          # 只为确认导入链没绕开 app 包

    src = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    assert "_create_unverified_context" not in src, "全局关掉证书校验的那行又回来了"


def _real_stream_chat():
    """拿回真的 `stream_chat`：conftest 的 autouse 夹具把它换成了测试桩。

    reload 用真函数覆盖那个模块属性；端点走的是 `streaming.stream_chat(...)`，在调用时
    查名字，所以覆盖之后跑的就是产品里那段生成器。别的用例各自拿自己的夹具补丁。
    """
    import importlib

    import app.core.streaming as mod

    importlib.reload(mod)
    return mod


def test_a_stalled_search_does_not_freeze_the_loop(monkeypatch):
    """搜索是本仓第一个"会自己发网络请求的工具"，所以把它放回两种驱动方式下各跑一次。

    为什么不是再加一条静态锁：`stream_chat` 必须是同步生成器那条已经由
    test_stream_body_iterator_is_sync_so_starlette_threadpools_it 钉着，改成 async 当场就红。
    这条钉的是**症状**：一次慢搜索（最坏走满源层超时）期间，事件循环还得能干活。

    同一个生成器跑两遍，只有一遍是生产里的形状：
      线程池驱动（Starlette 对同步生成器做的事）→ 循环照常跳；
      在循环上直接 next()（谁把它改成 async 生成器之后的形状）→ 循环整个冻住。
    第二遍是这条锁自己的"不许恒真"检查：要是连在循环上 next() 都跳得动，那这个计数器
    根本没在量东西，上面那条绿也说明不了任何事。
    """
    import asyncio
    import threading
    import types

    from starlette.concurrency import iterate_in_threadpool

    from app.tools import web_search as ws

    mod = _real_stream_chat()
    started = threading.Event()

    def slow_search(query, max_results=3):
        started.set()
        time.sleep(STALL_SECONDS)
        return [{"title": "标题", "url": "https://ex/x", "snippet": "摘要"}]

    monkeypatch.setattr(ws, "search", slow_search)

    def _chunk(content=None, tool_call=None):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                delta=types.SimpleNamespace(content=content, tool_calls=tool_call))],
            usage=None)

    def _tool_call():
        fn = types.SimpleNamespace(name="web_search", arguments='{"query": "西安 天气"}')
        return [types.SimpleNamespace(index=0, id="call_1", function=fn)]

    class _Completions:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:                       # 第一轮：模型要调工具
                return iter([_chunk(tool_call=_tool_call())])
            return iter([_chunk(content="答完了")])    # 第二轮：只回文本，生成器收尾

    # 每次 build_client 都给一个新的 _Completions：计数器必须是"每遍各一次"，
    # 否则第二遍驱动拿到的是 calls=2，直接回文本、根本不调工具，那条"不许恒真"
    # 的检查就成了空测（我第一次就是这么写错的）。
    monkeypatch.setattr(mod, "build_client",
                        lambda provider: types.SimpleNamespace(
                            chat=types.SimpleNamespace(completions=_Completions())))

    def _make_gen():
        return mod.stream_chat(
            "deepseek-chat", [{"role": "user", "content": "查天气"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}])

    async def drive(with_threadpool):
        """用指定方式把生成器抽干，同时数事件循环在此期间跳了多少拍。"""
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        async def on_the_loop(gen):          # 回归形状：next() 直接压在循环上
            out = []
            it = iter(gen)
            while True:
                await asyncio.sleep(0)
                try:
                    out.append(next(it))
                except StopIteration:
                    return out

        gen = _make_gen()
        # iterate_in_threadpool 在 starlette 0.40 里是 async **generator** 函数，
        # 调它得到的是异步生成器而不是协程，所以两边都包一层抽干它的协程。
        async def via_threadpool():
            async for _ in iterate_in_threadpool(gen):
                pass

        pump = asyncio.ensure_future(via_threadpool() if with_threadpool
                                     else on_the_loop(gen))
        tick = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.6)             # 此刻搜索应该正陷在 sleep 里
        seen = ticks
        await pump
        tick.cancel()
        return seen

    started.clear()
    off_loop = asyncio.run(drive(True))
    assert started.is_set(), "前提没成立：搜索根本没被调到，整段是空测"
    assert off_loop >= 5, (
        f"工具在线程池里跑，循环却只跳了 {off_loop} 拍：慢搜索把事件循环占住了，"
        f"这就是线上 524 的形状")

    started.clear()
    on_loop = asyncio.run(drive(False))
    assert started.is_set(), "前提没成立：第二种驱动方式没走到搜索"
    assert on_loop < 5, (
        f"在事件循环上直接 next() 也只跳了 {on_loop} 拍，说明这个计数器不成立，"
        "上面那条断言就成了空锁——换个量法")
