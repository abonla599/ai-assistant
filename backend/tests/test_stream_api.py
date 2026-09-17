"""
测试流式聊天 API（SSE）
运行方式：cd backend && python tests/test_stream_api.py
"""
import sys
from pathlib import Path

# 添加 backend 目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# enforced fixture 把 bootstrap 口令设成这个值。它是这里唯一的管理员身份，
# 也用来当"会话的属主"——旁观者要打的就是这个人拥有的会话。
BOOT = {"Authorization": "Bearer boot-token"}


def test_stream_chat():
    """测试流式聊天基本功能"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "用一句话介绍人工智能"}
        ]
    }

    response = client.post("/v1/chat/stream", json=payload)
    
    # 流式 API 应该返回 200
    assert response.status_code == 200
    
    # 检查响应内容
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
    
    # 如果返回的是 JSON（非流式环境），检查是否有回复
    if data:
        assert "reply" in data or "text" in data or len(data) > 0
    else:
        # 如果是流式响应，检查是否有内容
        assert len(response.content) > 0


def test_stream_with_session(monkeypatch):
    """测试流式聊天 + 会话保存（打桩模型输出，不依赖真实密钥）"""
    # 创建会话
    create_res = client.post("/v1/sessions?model=deepseek-chat")
    assert create_res.status_code == 200
    create_data = create_res.json()
    
    # 兼容两种返回格式
    session_id = create_data.get("session_id") or create_data.get("data", {}).get("session_id")
    assert session_id is not None

    # 打桩模型输出：本测试要验证的是"流式回复会写进会话"，
    # 不应依赖真实密钥是否有效（此前错误文本被当成回复保存，才让断言假性通过）。
    from app.core import streaming

    def fake_stream(model, messages, provider_id=None, temperature=0.7, max_tokens=4096):
        for piece in ("你", "好", "呀"):
            yield piece

    monkeypatch.setattr(streaming, "stream_chat", fake_stream)

    # 流式发送消息
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "session_id": session_id
    }

    response = client.post("/v1/chat/stream", json=payload)
    assert response.status_code == 200

    # 验证消息已保存
    session_res = client.get(f"/v1/sessions/{session_id}")
    assert session_res.status_code == 200
    session_data = session_res.json()
    
    # 兼容两种返回格式
    messages = session_data.get("data", session_data).get("messages", [])
    assert [m["role"] for m in messages] == ["user", "assistant"], f"实际: {messages}"
    assert messages[1]["content"] == "你好呀", "流式分块未正确拼接后保存"
    assert messages[1].get("message_id"), "助手消息未保存 message_id，反馈无法关联"

    # 清理
    client.delete(f"/v1/sessions/{session_id}")


def test_stream_rejects_a_foreign_session_that_really_exists(client, enforced, monkeypatch):
    """404 必须来自"这不是你的会话"，而不是"这个 id 压根不存在"。

    原先这里传的是 "nonexistent-id"，只走"查不到"这一条通路：整段删掉归属判定它
    确实会红（流开出去了，200），可归属判定一旦退化成"这个 id 在不在"——store 不再
    比 owner，或这里改成只查存在——别人的会话就换回一个 200，而那句
    "nonexistent-id" 的请求照旧 404，旧断言一个字都不会变（把这句判定临时改成
    "只查 id 在不在"即可复现：新断言当场变 200 变红，旧断言仍绿）。
    而"别人的会话 id 换回一个 200"是本项目最贵的一类洞：token 已经花掉，转录则被
    add_message 静默丢掉，界面上看起来一切正常。

    所以这里换成一个**确实存在、且属于别人**的会话，并把"存在"先证明一遍。
    """
    hdrs = enforced("旁观者")
    sid = client.post("/v1/sessions?model=deepseek-chat", headers=BOOT).json()["session_id"]

    # 前提：这个 id 真的在。没有这一步，下面那句 404 就又退化成"查不到"。
    owner_view = client.get(f"/v1/sessions/{sid}", headers=BOOT)
    assert owner_view.status_code == 200, owner_view.text
    assert owner_view.json()["messages"] == [], "前提：会话是空的，写入才看得见"

    # 归属判定必须早于模型调用：钱不该替一个 404 先花掉。
    from app.core import streaming

    entered = []

    def must_not_stream(model, messages, provider_id=None, temperature=0.7,
                      max_tokens=4096):
        entered.append(model)
        yield "不该被调用"

    monkeypatch.setattr(streaming, "stream_chat", must_not_stream)

    payload = {"model": "deepseek-chat",
               "messages": [{"role": "user", "content": "别人的会话"}],
               "session_id": sid}
    res = client.post("/v1/chat/stream", json=payload, headers=hdrs)

    # 归属校验在返回 StreamingResponse 之前：状态码必须真的表达失败。
    # 原先断的是 in [200, 404]——200 也算过，等于把"流起来了但没人知道失败"
    # 这个形状写成契约，什么都没钉住。
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "会话不存在"
    assert entered == [], "404 之前不该已经把流开出去（那是一次付费调用）"
    assert client.get(f"/v1/sessions/{sid}", headers=BOOT).json()["messages"] == [], \
        "越权请求一个字都不许落进别人的会话"

    # 反向对照：不存在与不属于你，是同一句话、同一个码。否则这个端点就成了
    # "哪些 session_id 真实存在"的探测器，而归属校验本身就会喂给它答案。
    absent = client.post("/v1/chat/stream",
                         json={**payload, "session_id": "nonexistent-id"}, headers=hdrs)
    assert (absent.status_code, absent.json()) == (404, {"detail": "会话不存在"}), absent.text
    assert absent.text == res.text, "两种失败必须逐字相同"
