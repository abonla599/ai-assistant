"""第五本限流账：键 `IP + 登录身份`，60 秒 20 次（规划书 A-3 ④）。

放在 auth_router 旁边而不是新起一个模块，是因为剪枝那套（`_LEDGERS` 各按自己的窗口、
`MAX_TRACKED_SOURCES` 的内存上限、`_client_ip` 只信 cf-connecting-ip）已经在那儿被
两条锁钉住了；复制一份等于留下第二个口径，而漏扫的那一本账就是 fail-open。
"""
import pytest

from app.core import auth_router as ar


@pytest.fixture(autouse=True)
def empty_ledgers(monkeypatch):
    for ledger in (ar._CHATS,):
        ledger.clear()
    yield


def test_twenty_calls_pass_and_the_twenty_first_does_not():
    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        assert ar.chat_allowed("1.2.3.4", "u-1") is True
        ar.note_chat("1.2.3.4", "u-1")
    assert ar.chat_allowed("1.2.3.4", "u-1") is False


def test_a_successful_call_never_refunds_the_budget():
    """这条是从四本旧账继承来的裁决：**任何成功都不还回预算**。

    否则"聊天成功就清账"会让一个脚本恰好卡在阈值下永远跑不完也永远不被挡。
    """
    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        ar.note_chat("1.2.3.4", "u-1")
    assert ar.chat_allowed("1.2.3.4", "u-1") is False
    ar.note_chat("1.2.3.4", "u-1")          # 又被拒之前不会还额度
    assert ar.chat_allowed("1.2.3.4", "u-1") is False


def test_two_people_on_one_machine_do_not_share_a_bucket():
    """一个人开多个号刷是另一回事；同 IP 全家共用一台出口不该互相挡。"""
    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        ar.note_chat("10.0.0.1", "u-1")
    assert ar.chat_allowed("10.0.0.1", "u-1") is False
    assert ar.chat_allowed("10.0.0.1", "u-2") is True


def test_the_same_person_on_two_ips_is_billed_separately():
    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        ar.note_chat("10.0.0.1", "u-1")
    assert ar.chat_allowed("10.0.0.2", "u-1") is True


def test_the_window_slides_and_access_comes_back(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(ar, "_now", lambda: clock["t"])
    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        ar.note_chat("1.2.3.4", "u-1")
    assert ar.chat_allowed("1.2.3.4", "u-1") is False
    clock["t"] += ar.CHAT_WINDOW_SECONDS + 1
    assert ar.chat_allowed("1.2.3.4", "u-1") is True


def test_the_fifth_ledger_is_registered_for_pruning():
    """剪枝扫的每一本都要带自己的窗口——漏一本就是只胀不收。"""
    assert (ar._CHATS, ar.CHAT_WINDOW_SECONDS) in ar._LEDGERS


@pytest.mark.parametrize("endpoint,payload", [
    ("/v1/chat", {"messages": [{"role": "user", "content": "hi"}]}),
    ("/v1/chat/stream", {"messages": [{"role": "user", "content": "hi"}]}),
])
def test_both_chat_endpoints_answer_429_before_waking_the_model(client, endpoint, payload, monkeypatch):
    """被挡下的那一次**不该已经花钱**：先判断、再记账、才调模型。"""
    import app.core.streaming as streaming
    import app.pipeline as pipeline_mod

    calls = []
    def spy(*a, **kw):
        calls.append(1)
        return iter(["x"])
    monkeypatch.setattr(streaming, "stream_chat", spy)
    monkeypatch.setattr(pipeline_mod.ChatPipeline, "_call_model_with_tool_loop",
                        lambda *a, **kw: "不该走到这儿")

    for _ in range(ar.MAX_CHAT_CALLS_PER_WINDOW):
        assert client.post(endpoint, json=payload).status_code == 200
    before = len(calls)
    res = client.post(endpoint, json=payload)
    assert res.status_code == 429, res.text
    assert "Retry-After" in res.headers
    assert len(calls) == before, "被挡之后还去叫了模型：这一下是真花钱的"
