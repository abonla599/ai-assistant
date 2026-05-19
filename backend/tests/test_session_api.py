"""
测试会话管理 API
"""
import requests
import json

BASE_URL = "http://localhost:8000"


def test_create_session():
    """测试创建会话"""
    response = requests.post(f"{BASE_URL}/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] == True
    assert "session_id" in data["data"]
    print(f"✅ 创建会话成功: {data['data']['session_id']}")
    return data["data"]["session_id"]


def test_list_sessions():
    """测试获取会话列表"""
    response = requests.get(f"{BASE_URL}/v1/sessions")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] == True
    assert isinstance(data["data"], list)
    print(f"✅ 会话列表: {len(data['data'])} 个会话")
    return data["data"]


def test_get_session(session_id):
    """测试获取会话详情"""
    response = requests.get(f"{BASE_URL}/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] == True
    assert data["data"]["session_id"] == session_id
    print(f"✅ 获取会话详情成功: {data['data']['title']}")
    return data["data"]


def test_chat_with_session(session_id):
    """测试使用 session_id 发送消息"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好，请做个自我介绍"}
        ],
        "session_id": session_id
    }
    response = requests.post(f"{BASE_URL}/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 聊天回复: {data['reply'][:50]}...")
    return data


def test_get_session_after_chat(session_id):
    """测试聊天后会话消息是否正确保存"""
    response = requests.get(f"{BASE_URL}/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    messages = data["data"]["messages"]
    assert len(messages) >= 2  # 至少有一问一答
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
    response = requests.post(f"{BASE_URL}/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 无会话聊天成功: {data['reply'][:50]}...")


def test_delete_session(session_id):
    """测试删除会话"""
    response = requests.delete(f"{BASE_URL}/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] == True
    print(f"✅ 会话已删除: {session_id}")


if __name__ == "__main__":
    print("=" * 60)
    print("开始测试会话管理 API")
    print("=" * 60)

    # 1. 创建会话
    session_id = test_create_session()

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