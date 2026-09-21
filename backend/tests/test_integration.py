from fastapi.testclient import TestClient
import sys
import os

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.app.main import app

client = TestClient(app)


def test_full_chat_feedback_memory_cycle(enforced):
    """
    全链路集成测试：
    创建会话 → 发送带个人信息的消息 → 提交反馈 → 搜索记忆

    身份一律来自令牌：记忆体不再收 user_id，所以这里注册一个专属用户，
    他的记忆池从空开始，断言与测试顺序无关。
    """
    me = enforced("集成测试用户")

    # 1. 创建会话
    session_res = client.post("/v1/sessions", params={"model": "deepseek-chat"}, headers=me)
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert "session_id" in session_data
    sid = session_data["session_id"]

    # 2. 发送带个人信息的消息
    chat_res = client.post("/v1/chat", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "我叫王五，今年25岁"}],
        "session_id": sid
    }, headers=me)
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert "reply" in chat_data
    assert "message_id" in chat_data
    msg_id = chat_data["message_id"]

    # 3. 提交用户反馈（好评）
    fb_res = client.post("/v1/feedback", json={
        "message_id": msg_id,
        "rating": 5,
        "comment": "回答准确，记住了我的信息"
    }, headers=me)
    assert fb_res.status_code == 200
    fb_data = fb_res.json()
    assert fb_data.get("status") in ["success", "received"]

    # 4. 验证记忆模块可用（先手动添加一条记忆用于测试）
    add_mem_res = client.post("/v1/memory/add", json={
        "content": "用户叫王五，今年25岁",
        "metadata": {"source": "chat", "importance": 5}
    }, headers=me)
    assert add_mem_res.status_code == 200

    # 5. 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "query": "王五",
        "top_k": 3
    }, headers=me)
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert "results" in search_data
    # 应该能搜到包含"王五"的结果
    assert any("王五" in r.get("content", "") for r in search_data["results"])


def test_memory_full_crud(enforced):
    """记忆模块完整CRUD测试"""
    me = enforced("CRUD 用户")
    content = "测试记忆内容：喜欢猫"

    # 添加记忆
    add_res = client.post("/v1/memory/add", json={
        "content": content,
        "metadata": {"importance": 4}
    }, headers=me)
    assert add_res.status_code == 200

    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "query": "猫",
        "top_k": 5
    }, headers=me)
    assert search_res.status_code == 200
    results = search_res.json().get("results", [])
    assert any("猫" in r.get("content", "") for r in results)


def test_feedback_only_answers_for_the_callers_own_message(client, enforced):
    """反馈端点必须存在，但它只认调用者自己消息里的 message_id。

    编造的 id 与别人的 id 得到同一个 404：原先它无条件写盘并回 200，
    任何人都能往反馈文件里加行。
    """
    stranger = enforced("反馈的旁观者")
    foreign = client.post("/v1/feedback", json={
        "message_id": "test_msg_999",
        "rating": 3,
        "comment": "一般般"
    }, headers=stranger)
    assert foreign.status_code == 404, foreign.text

    me = enforced("反馈的作者")
    sid = client.post("/v1/sessions", headers=me).json()["session_id"]
    msg_id = client.post("/v1/chat", json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "讲个笑话"}]}, headers=me).json()["message_id"]
    res = client.post("/v1/feedback", json={
        "message_id": msg_id, "rating": 3, "comment": "一般般"}, headers=me)
    assert res.status_code == 200
    assert res.json().get("status") in ["success", "received"]


def test_health_check():
    """健康检查：从"进程还在"升级成"东西齐不齐"。

    六项判据各自是什么、为什么状态码不随判决走，全在 app/core/selfcheck.py 与
    test_selfcheck.py 里；这里只认这个端点还答得出话、并且给的是那张 checks 表。
    """
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in ("ok", "degraded", "broken"), body
    assert body["checks"], "只剩一句 status，等于退回「进程活着就算好」"


def test_root():
    """根路径是官网。以前断的是 {"service": "AI 智能助手"} 那段 JSON。"""
    res = client.get("/")
    assert res.status_code == 200
    assert "AI 智能助手" in res.text