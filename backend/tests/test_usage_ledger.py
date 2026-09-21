"""用量账本：谁、用哪条 provider、花了多少 token、谁的钱。

规划书阶段一判据 3 的原话是"答得出昨天到今天每个人用了几次、消耗多少 token、
谁的钱——数据来自账本，不是日志 grep"。日志 grep 那条路今天走得通只是因为
`[Stream] 调用工具` 之类的 print 还在，它既没有归属也没有 token 数。
"""
import json
import os

from app.core.providers import ProviderError

import pytest

from app.core import usage

# 在 conftest 那个 autouse 桩生效之前把真函数抓在手里：它会把 app.core.streaming.stream_chat
# 整个换成 fake_stream，测试再从模块属性上取就拿不到真代码。
from app.core.streaming import stream_chat as real_stream_chat


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setenv("USAGE_DB_PATH", str(path))
    usage.restore(path=str(path))
    return path


def test_two_calls_by_the_same_person_add_up(isolated):
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=100, completion_tokens=20, total_tokens=120)
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=50, completion_tokens=5, total_tokens=55)
    rows = usage.snapshot()
    assert len(rows) == 1, rows
    assert rows[0]["calls"] == 2 and rows[0]["total_tokens"] == 175


def test_two_people_do_not_share_a_row(isolated):
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=1, completion_tokens=1, total_tokens=2)
    usage.record_call(user_id="u-2", provider_id="p-ds", paid_by="user",
                      prompt_tokens=7, completion_tokens=3, total_tokens=10)
    got = {(r["user_id"], r["paid_by"]): r["total_tokens"] for r in usage.snapshot()}
    assert got == {("u-1", "operator"): 2, ("u-2", "user"): 10}


def test_a_failed_call_is_counted_but_not_mixed_with_success(isolated):
    """失败的那一次也占额度、也要看得见——只记成功的那本账会低估用量。"""
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=9, completion_tokens=0, total_tokens=9, ok=True)
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=0, completion_tokens=0, total_tokens=0, ok=False)
    row = usage.snapshot()[0]
    assert row["calls"] == 2 and row["ok"] == 1 and row["failed"] == 1


def test_the_ledger_survives_a_restart(isolated):
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=3, completion_tokens=4, total_tokens=7)
    usage.restore(path=str(isolated))
    assert usage.snapshot()[0]["total_tokens"] == 7


def test_reasoning_and_cached_tokens_are_recorded_separately(isolated):
    """2026-09-21 实测 DeepSeek 回传的 usage 里有这两项，而它们都是要花钱的字。"""
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=32, completion_tokens=8, total_tokens=40,
                      reasoning_tokens=8, cached_tokens=12)
    usage.restore(path=str(isolated))
    row = usage.snapshot()[0]
    assert row["reasoning_tokens"] == 8 and row["cached_tokens"] == 12


def test_an_empty_ledger_does_not_create_a_file(isolated):
    assert not isolated.exists()
    usage.record_call(user_id="u-1", provider_id="p-ds", paid_by="operator",
                      prompt_tokens=1, completion_tokens=1, total_tokens=2)
    assert isolated.exists() and json.loads(isolated.read_text(encoding="utf-8"))["days"]


def test_the_caller_cannot_forget_who_it_was(isolated):
    with pytest.raises(ValueError):
        usage.record_call(user_id="", provider_id="p-ds", paid_by="operator",
                          prompt_tokens=1, completion_tokens=1, total_tokens=1)


# ---------- 两条聊天路径都得往账上落一行 ----------

class _Usage:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_the_streaming_path_records_one_line_with_the_vendors_numbers(tmp_path, monkeypatch):
    """流式那条是 PWA 真正在走的路。上游回了多少就记多少，不猜。"""
    import httpx
    import app.core.streaming as streaming
    from app.core import usage as usage_mod

    def chunks(request):
        body = ('data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":55,'
                '"total_tokens":175,"completion_tokens_details":{"reasoning_tokens":30},'
                '"prompt_tokens_details":{"cached_tokens":16}}}\n\n'
                'data: [DONE]\n\n')
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "text/event-stream"})

    real = httpx.Client
    monkeypatch.setattr(streaming, "build_client", lambda provider, **kw: _StreamClient(real, chunks))
    usage_mod.restore(path=str(tmp_path / "usage.json"))
    list(real_stream_chat("fake-model", [{"role": "user", "content": "hi"}],
                              provider_id="fake-model", user_id="u-7"))
    rows = usage_mod.snapshot()
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["user_id"], row["calls"], row["ok"], row["failed"]) == ("u-7", 1, 1, 0)
    assert row["total_tokens"] == 175 and row["reasoning_tokens"] == 30
    assert row["cached_tokens"] == 16 and row["paid_by"] == "operator"
    assert row["unknown_usage"] == 0, "上游明明回了数，不该算成未知"


class _StreamClient:
    """真 httpx 客户端 + 假 transport，外面套一层 OpenAI 形状的 chat.completions。"""

    def __init__(self, real, handler):
        import httpx
        self._openai = None
        self._real = real
        self._handler = handler

    def __getattr__(self, name):
        if name != "chat":
            raise AttributeError(name)
        import httpx
        from openai import OpenAI
        client = OpenAI(api_key="sk-test-ledger", base_url="https://upstream.invalid/v1",
                        max_retries=0, http_client=self._real(transport=httpx.MockTransport(self._handler)))
        return client.chat


def test_a_stream_that_never_gets_usage_says_so_instead_of_recording_zero(tmp_path, monkeypatch):
    """有些兼容网关不回 usage。记 0 与"不知道"必须是两件事。"""
    import httpx
    import app.core.streaming as streaming
    from app.core import usage as usage_mod

    def chunks(request):
        body = 'data: {"choices":[{"delta":{"content":"喂"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    real = httpx.Client
    monkeypatch.setattr(streaming, "build_client", lambda provider, **kw: _StreamClient(real, chunks))
    usage_mod.restore(path=str(tmp_path / "usage.json"))
    list(real_stream_chat("fake-model", [{"role": "user", "content": "hi"}],
                              provider_id="fake-model", user_id="u-7"))
    row = usage_mod.snapshot()[0]
    assert row["total_tokens"] == 0 and row["unknown_usage"] == 1


def test_a_stream_that_dies_midway_still_leaves_a_line(tmp_path, monkeypatch):
    """失败的调用也占额度：只记成功的那本账会系统性低估，而且看不见故障率。"""
    import httpx
    import app.core.streaming as streaming
    from app.core import usage as usage_mod

    def boom(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    real = httpx.Client
    monkeypatch.setattr(streaming, "build_client", lambda provider, **kw: _StreamClient(real, boom))
    usage_mod.restore(path=str(tmp_path / "usage.json"))
    with pytest.raises(Exception):
        list(real_stream_chat("fake-model", [{"role": "user", "content": "hi"}],
                                   provider_id="fake-model", user_id="u-7"))
    row = usage_mod.snapshot()[0]
    assert row["calls"] == 1 and row["ok"] == 0 and row["failed"] == 1


def test_the_non_streaming_path_records_too(tmp_path, monkeypatch):
    """/v1/chat 那条（非流式）以前什么也不留：两条路都得落账，否则账只对一半人。"""
    import app.pipeline as pipeline_mod
    from app.core import usage as usage_mod
    from app.core.providers import store as providers

    provider = providers.resolve("fake-model")

    class Resp:
        choices = [type("C", (), {"message": type("M", (), {
            "content": "好", "tool_calls": None, "model_dump": lambda self: {}})()})()]
        usage = _Usage(prompt_tokens=11, completion_tokens=2, total_tokens=13)

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return Resp()

    monkeypatch.setattr(pipeline_mod, "build_client", lambda provider, **kw: Client())
    usage_mod.restore(path=str(tmp_path / "usage.json"))
    pipe = pipeline_mod.ChatPipeline(user_id="u-3")
    out = pipe._call_model_with_tool_loop("fake-model", [{"role": "user", "content": "hi"}],
                                          provider_id=provider["id"])
    assert out == "好"
    row = usage_mod.snapshot()[0]
    assert (row["user_id"], row["total_tokens"], row["unknown_usage"]) == ("u-3", 13, 0)


def test_a_provider_says_whose_money_it_is(tmp_path):
    """规划书代拍 #3：内置与自带长期并存，账上必须分得清"这口钱谁出"。"""
    from app.core.providers import ProviderStore
    store = ProviderStore(path=str(tmp_path / "providers.json"))
    saved = store.upsert({"label": "内置", "base_url": "https://a.invalid/v1",
                          "api_key": "sk-some-real-looking-key", "model": "m"})  # secret-scan:allow 测试里造的假密钥，这台机器上没有这颗 key
    def paid_of(ident):
        # 按下标取会读错行：一个空目录的 store 会从 .env 先播种出一条，
        # 那条排在最前，而我这条在后面。
        return next(r["paid_by"] for r in store.public_list() if r["id"] == ident)

    assert saved["paid_by"] == "operator", "缺省必须是「我们垫钱」，老记录不该被当成用户自带"
    assert paid_of(saved["id"]) == "operator"
    mine = store.upsert({**saved, "paid_by": "user"})
    assert mine["paid_by"] == "user" and paid_of(mine["id"]) == "user"
    with pytest.raises(ProviderError):
        store.upsert({**saved, "paid_by": "somebody-else"})


def test_both_endpoints_hand_the_caller_down_to_the_ledger():
    """记账的最后一环是"谁在打这个接口"，两条路都得把身份递下去。

    这一条只能钉接线，不能测行为：conftest 把真 stream_chat 换成了桩，账在桩那边
    根本记不起来。写成都测不到的"行为测试"才是自欺——这里如实说明它是形状锁。
    """
    import inspect

    import app.main as m

    stream_src = inspect.getsource(m.stream_chat_endpoint)
    assert "user_id=principal.user_id" in stream_src, "流式端点没把当前这个人递给账本"
    assert "ChatPipeline(user_id=principal.user_id)" in inspect.getsource(m),         "非流式那条也没递：两条路一起漏，账本就只剩 unattributed 一行"
