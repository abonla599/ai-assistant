"""
测试会话管理 API
"""
import pytest
import sys
from pathlib import Path

# 添加 backend 目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.fixture
def session_id():
    """创建测试会话并返回 session_id"""
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        sid = data.get("data", {}).get("session_id")
    else:
        sid = data.get("session_id")
    assert sid is not None, f"无法获取 session_id，响应数据: {data}"
    yield sid
    # 清理：删除会话
    try:
        client.delete(f"/v1/sessions/{sid}")
    except:
        pass


def test_create_session():
    """测试创建会话"""
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        assert "session_id" in data["data"]
        print(f"✅ 创建会话成功: {data['data']['session_id']}")
    else:
        assert "session_id" in data
        print(f"✅ 创建会话成功: {data['session_id']}")


def test_list_sessions():
    """测试获取会话列表"""
    response = client.get("/v1/sessions")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        assert isinstance(data["data"], list)
        print(f"✅ 会话列表: {len(data['data'])} 个会话")
    else:
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        print(f"✅ 会话列表: {len(data['sessions'])} 个会话")


def test_get_session(session_id):
    """测试获取会话详情"""
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    if "success" in data:
        assert data["success"] == True
        assert data["data"]["session_id"] == session_id
        print(f"✅ 获取会话详情成功: {data['data']['title']}")
    else:
        assert data["session_id"] == session_id
        print(f"✅ 获取会话详情成功: {data.get('title', 'N/A')}")


def test_chat_with_session(session_id):
    """测试使用 session_id 发送消息"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好，请做个自我介绍"}
        ],
        "session_id": session_id
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 聊天回复: {data['reply'][:50]}...")


def test_get_session_after_chat(session_id):
    """测试聊天后会话消息是否正确保存"""
    # 先发送一条消息
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "测试消息"}
        ],
        "session_id": session_id
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    
    # 验证消息已保存
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    
    # 兼容两种返回格式
    session_data = data if "session_id" in data else data.get("data", {})
    messages = session_data.get("messages", [])
    assert len(messages) >= 2, f"期望至少2条消息（一问一答），实际: {len(messages)}"
    print(f"✅ 会话消息数: {len(messages)}")
    for msg in messages:
        print(f"   [{msg['role']}] {msg['content'][:50]}...")


def test_chat_without_session():
    """测试不传 session_id 的情况"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "1+1等于几？"}
        ]
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 无会话聊天成功: {data['reply'][:50]}...")


def test_delete_session(session_id):
    """测试删除会话"""
    response = client.delete(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    if "success" in data:
        assert data["success"] == True
    else:
        assert data["status"] == "deleted"
    print(f"✅ 会话已删除: {session_id}")


if __name__ == "__main__":
    print("=" * 60)
    print("开始测试会话管理 API")
    print("=" * 60)

    # 1. 创建会话
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    session_id = data.get("session_id") or data.get("data", {}).get("session_id")
    print(f"✅ 创建会话成功: {session_id}")

    # 2. 获取会话列表
    test_list_sessions()

    # 3. 获取会话详情
    test_get_session(session_id)

    # 4. 发送消息（带 session_id）
    test_chat_with_session(session_id)

    # 5. 验证消息已保存
    test_get_session_after_chat(session_id)

    # 6. 发送消息（不带 session_id）
    test_chat_without_session()

    # 7. 删除会话
    test_delete_session(session_id)

    print("=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)