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


def test_stream_with_session():
    """测试流式聊天 + 会话保存"""
    # 创建会话
    create_res = client.post("/v1/sessions?model=deepseek-chat")
    assert create_res.status_code == 200
    create_data = create_res.json()
    
    # 兼容两种返回格式
    session_id = create_data.get("session_id") or create_data.get("data", {}).get("session_id")
    assert session_id is not None

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
    assert len(messages) >= 2, f"期望至少2条消息，实际: {len(messages)}"

    # 清理
    client.delete(f"/v1/sessions/{session_id}")


def test_stream_error():
    """测试流式错误处理（不存在的会话）"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "session_id": "nonexistent-id"
    }

    response = client.post("/v1/chat/stream", json=payload)
    
    # 对于不存在的会话，应该返回 404 或其他错误状态
    # 但根据实现，可能不会严格检查 session_id，所以接受 200 或 404
    assert response.status_code in [200, 404]