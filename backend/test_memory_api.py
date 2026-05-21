"""
测试记忆管理 API
需要先启动后端服务：python app/main.py
"""
import requests
import json

BASE_URL = "http://127.0.0.1:8000/v1/memory"


def test_stats():
    """测试统计信息"""
    print("=" * 50)
    print("📊 获取记忆库统计信息")
    print("=" * 50)
    
    try:
        response = requests.get(f"{BASE_URL}/stats")
        result = response.json()
        print(f"状态码: {response.status_code}")
        print(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ 请求失败: {e}")


def test_add_memory():
    """测试添加记忆"""
    print("\n" + "=" * 50)
    print("📝 添加记忆")
    print("=" * 50)
    
    # 添加多条记忆
    memories = [
        {
            "user_id": "test_user",
            "content": "我喜欢在周末去咖啡馆编程",
            "metadata": {"category": "habit", "importance": "high"}
        },
        {
            "user_id": "test_user",
            "content": "我的猫叫咪咪，今年3岁了",
            "metadata": {"category": "pet", "importance": "medium"}
        },
        {
            "user_id": "test_user",
            "content": "我在学 FastAPI 和 Chroma，觉得很有意思",
            "metadata": {"category": "learning", "importance": "high"}
        }
    ]
    
    memory_ids = []
    for mem in memories:
        response = requests.post(f"{BASE_URL}/add", json=mem)
        result = response.json()
        print(f"添加: '{mem['content'][:40]}...'")
        print(f"  → memory_id: {result.get('memory_id', 'N/A')}")
        if result.get("memory_id"):
            memory_ids.append(result["memory_id"])
    
    return memory_ids


def test_search_memory():
    """测试搜索记忆"""
    print("\n" + "=" * 50)
    print("🔍 搜索记忆")
    print("=" * 50)
    
    queries = [
        "用户养了什么宠物？",
        "用户喜欢在哪里工作？",
        "用户最近在学什么？"
    ]
    
    for query in queries:
        data = {
            "user_id": "test_user",
            "query": query,
            "top_k": 3
        }
        
        response = requests.post(f"{BASE_URL}/search", json=data)
        result = response.json()
        
        print(f"\n查询: '{query}'")
        if result.get("results"):
            for i, r in enumerate(result["results"]):
                print(f"  结果{i+1}: {r['content']} (相关度: {r['relevance_score']})")
        else:
            print(f"  无结果")


def test_list_memories(user_id: str = "test_user"):
    """测试获取记忆列表"""
    print("\n" + "=" * 50)
    print("📋 获取用户记忆列表")
    print("=" * 50)
    
    response = requests.get(f"{BASE_URL}/list/{user_id}")
    result = response.json()
    
    print(f"用户: {user_id}")
    print(f"记忆总数: {result.get('total', 0)}")
    if result.get("memories"):
        for mem in result["memories"]:
            print(f"  - [{mem['metadata'].get('category', 'unknown')}] {mem['content']}")


def test_delete_memory(memory_id: str):
    """测试删除记忆"""
    print("\n" + "=" * 50)
    print("🗑️ 删除记忆")
    print("=" * 50)
    
    data = {"memory_id": memory_id}
    response = requests.post(f"{BASE_URL}/delete", json=data)
    result = response.json()
    print(f"删除 {memory_id}: {result.get('message', result)}")


if __name__ == "__main__":
    print("\n🚀 开始测试记忆管理 API")
    print("请确保后端服务已启动: python app/main.py")
    print()
    
    # 1. 查看统计
    test_stats()
    
    # 2. 添加记忆
    memory_ids = test_add_memory()
    
    # 3. 搜索记忆
    test_search_memory()
    
    # 4. 列出记忆
    test_list_memories("test_user")
    
    # 5. 再次查看统计
    test_stats()
    
    # 6. 删除最后一条记忆（可选）
    if memory_ids:
        test_delete_memory(memory_ids[-1])
    
    print("\n✅ API 测试完成")