"""
测试流式聊天 API（SSE）
运行方式：cd backend && python tests/test_stream_api.py
"""
import requests
import json

BASE_URL = "http://localhost:8000"


def test_stream_chat():
    """测试流式聊天基本功能"""
    print("=" * 60)
    print("       流式 API 测试")
    print("=" * 60)

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "用一句话介绍人工智能"}
        ]
    }

    print(f"\n请求: {payload['messages'][0]['content']}")
    print("\n流式响应:")
    print("-" * 40)

    full_text = ""
    chunk_count = 0

    try:
        response = requests.post(
            f"{BASE_URL}/v1/chat/stream",
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True
        )

        assert response.status_code == 200, f"状态码错误: {response.status_code}"

        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith("data: "):
                    data_str = line_str[6:]  # 去掉 "data: " 前缀
                    try:
                        data = json.loads(data_str)
                        msg_type = data.get("type")

                        if msg_type == "start":
                            print("[开始] 流式传输启动")

                        elif msg_type == "content":
                            text = data.get("text", "")
                            print(text, end="", flush=True)
                            full_text += text
                            chunk_count += 1

                        elif msg_type == "done":
                            print("\n\n[完成] 流式传输结束")

                        elif msg_type == "error":
                            print(f"\n[错误] {data.get('message', '未知错误')}")
                            return False

                    except json.JSONDecodeError:
                        print(f"\n[解析错误] 无法解析: {data_str}")

    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接到服务器，请确保后端已启动")
        return False
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        return False

    print("-" * 40)
    print(f"\n结果摘要:")
    print(f"  总字符数: {len(full_text)}")
    print(f"  分块数: {chunk_count}")
    print(f"  完整回复: {full_text}")

    # 验证
    if len(full_text) > 0:
        print("\n✅ 流式 API 测试通过")
        return True
    else:
        print("\n❌ 流式 API 测试失败：回复为空")
        return False


def test_stream_with_session():
    """测试流式聊天 + 会话保存"""
    print("\n" + "=" * 60)
    print("       流式 + 会话保存测试")
    print("=" * 60)

    # 创建会话
    print("\n[1] 创建会话...")
    create_res = requests.post(f"{BASE_URL}/v1/sessions?model=deepseek-chat")
    session_id = create_res.json()["data"]["session_id"]
    print(f"   session_id = {session_id}")

    # 流式发送消息
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "session_id": session_id
    }

    print("\n[2] 流式发送消息...")
    full_text = ""

    response = requests.post(
        f"{BASE_URL}/v1/chat/stream",
        json=payload,
        headers={"Content-Type": "application/json"},
        stream=True
    )

    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            if line_str.startswith("data: "):
                data = json.loads(line_str[6:])
                if data.get("type") == "content":
                    text = data.get("text", "")
                    print(text, end="", flush=True)
                    full_text += text

    # 验证消息已保存
    print("\n\n[3] 验证消息已保存...")
    session_res = requests.get(f"{BASE_URL}/v1/sessions/{session_id}")
    messages = session_res.json()["data"]["messages"]
    print(f"   消息数: {len(messages)}")
    for msg in messages:
        role = msg["role"]
        content = msg["content"][:50] + "..." if len(msg["content"]) > 50 else msg["content"]
        print(f"   [{role}] {content}")

    if len(messages) >= 2:
        print("\n✅ 流式 + 会话保存测试通过")
    else:
        print("\n❌ 流式 + 会话保存测试失败")

    # 清理
    requests.delete(f"{BASE_URL}/v1/sessions/{session_id}")


def test_stream_error():
    """测试流式错误处理（不存在的会话）"""
    print("\n" + "=" * 60)
    print("       流式错误处理测试")
    print("=" * 60)

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "session_id": "nonexistent-id"
    }

    print(f"\n发送请求（不存在的 session_id）...")
    response = requests.post(
        f"{BASE_URL}/v1/chat/stream",
        json=payload,
        headers={"Content-Type": "application/json"},
        stream=True
    )

    if response.status_code == 404:
        print("✅ 正确返回 404 错误")
    else:
        print(f"❌ 期望 404，实际 {response.status_code}")


# ============================================================
# 主测试流程
# ============================================================
if __name__ == "__main__":
    # 测试 1: 基本流式功能
    result1 = test_stream_chat()

    # 测试 2: 流式 + 会话保存
    test_stream_with_session()

    # 测试 3: 错误处理
    test_stream_error()

    # 汇总
    print("\n" + "=" * 60)
    print("              流式 API 测试完成")
    print("=" * 60)