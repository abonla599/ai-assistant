from fastapi.testclient import TestClient
import sys
import os

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.app.main import app

client = TestClient(app)


def test_full_chat_feedback_memory_cycle():
    """
    全链路集成测试：
    创建会话 → 发送带个人信息的消息 → 提交反馈 → 搜索记忆
    """
    
    # 1. 创建会话
    session_res = client.post("/v1/sessions", params={"model": "deepseek-chat"})
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert "session_id" in session_data
    sid = session_data["session_id"]

    # 2. 发送带个人信息的消息
    chat_res = client.post("/v1/chat", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "我叫王五，今年25岁"}],
        "session_id": sid
    })
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
    })
    assert fb_res.status_code == 200
    fb_data = fb_res.json()
    assert fb_data.get("status") in ["success", "received"]

    # 4. 验证记忆模块可用（先手动添加一条记忆用于测试）
    add_mem_res = client.post("/v1/memory/add", json={
        "user_id": "test_user",
        "content": "用户叫王五，今年25岁",
        "metadata": {"source": "chat", "importance": 5}
    })
    assert add_mem_res.status_code == 200

    # 5. 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "user_id": "test_user",
        "query": "用户叫什么名字",
        "top_k": 3
    })
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert "results" in search_data
    # 应该能搜到包含"王五"的结果
    assert any("王五" in r.get("content", "") for r in search_data["results"])


def test_memory_full_crud():
    """记忆模块完整CRUD测试"""
    
    user_id = "integration_test_user"
    content = "测试记忆内容：喜欢猫"
    
    # 添加记忆
    add_res = client.post("/v1/memory/add", json={
        "user_id": user_id,
        "content": content,
        "metadata": {"importance": 4}
    })
    assert add_res.status_code == 200
    
    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "user_id": user_id,
        "query": "喜欢什么动物",
        "top_k": 5
    })
    assert search_res.status_code == 200
    results = search_res.json().get("results", [])
    assert any("猫" in r.get("content", "") for r in results)


def test_feedback_endpoint_exists():
    """验证反馈端点可用"""
    res = client.post("/v1/feedback", json={
        "message_id": "test_msg_999",
        "rating": 3,
        "comment": "一般般"
    })
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") in ["success", "received"]


def test_health_check():
    """健康检查"""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_root():
    """根路径检查"""
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["service"] == "AI 智能助手"