# backend/tests/test_api.py
import sys
from pathlib import Path

# 添加 backend 目录到 sys.path（与你的 test_memory.py 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


# ========== 1. 根路径 ==========
def test_root():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert data["status"] == "running"


# ========== 2. 基础对话（当前为占位接口） ==========
def test_chat():
    res = client.post("/v1/chat", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "你好"}]
    })
    assert res.status_code == 200
    assert "reply" in res.json()


# ========== 3. 记忆添加与搜索 ==========
def test_memory_add_search(enforced):
    """身份取自令牌：请求体里再也没有 user_id 这个字段。

    每个用例各自注册一个用户，记忆池从空开始——共用一个身份时假存储是进程级
    单例，搜索结果会被上一个用例塞进来的记忆挤掉，断言就成了测试顺序的函数。
    """
    me = enforced("记忆主人")

    # 添加记忆
    add_res = client.post("/v1/memory/add", json={
        "content": "我叫张三",
        "summarize": False
    }, headers=me)
    assert add_res.status_code == 200
    assert add_res.json()["status"] == "success"
    mem_id = add_res.json().get("memory_id")
    assert mem_id is not None

    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "query": "张三",
        "top_k": 3
    }, headers=me)
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    assert [r["content"] for r in results] == ["我叫张三"]


# ========== 4. 记忆批量删除 ==========
def test_memory_delete_batch(enforced):
    me = enforced("删除的人")
    # 先加两条
    add1 = client.post("/v1/memory/add", json={"content": "记忆A", "summarize": False}, headers=me)
    add2 = client.post("/v1/memory/add", json={"content": "记忆B", "summarize": False}, headers=me)
    id1 = add1.json()["memory_id"]
    id2 = add2.json()["memory_id"]

    # 用 client.request 发送 DELETE 带 JSON 体（FastAPI TestClient 的坑）
    del_res = client.request(
        "DELETE",
        "/v1/memory/delete",
        json={"memory_ids": [id1, id2]},
        headers=me
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2
    assert client.get("/v1/memory/list?limit=50", headers=me).json()["total"] == 0


# ========== 5. 记忆更新 ==========
def test_memory_update(enforced):
    me = enforced("修改的人")
    add_res = client.post("/v1/memory/add", json={"content": "旧内容", "summarize": False},
                          headers=me)
    mem_id = add_res.json()["memory_id"]

    update_res = client.put("/v1/memory/update", json={
        "memory_id": mem_id,
        "new_content": "新内容"
    }, headers=me)
    assert update_res.status_code == 200
    assert update_res.json()["message"] == "记忆更新成功"

    search_res = client.post("/v1/memory/search", json={
        "query": "新内容",
        "top_k": 1
    }, headers=me)
    assert len(search_res.json()["results"]) > 0
    assert "新内容" in search_res.json()["results"][0]["content"]


# ========== 6. 记忆衰减 ==========
def test_memory_decay():
    """衰减只作用于调用者本人的记忆，端点不再收 user_id 查询参数。

    这里刻意用模块级 client：它是 disabled 模式下的本机管理员，而 decay 现在是
    管理员端点。普通用户拿到 403 由 test_isolation 钉住。
    """
    client.post("/v1/memory/add", json={"content": "衰减测试", "summarize": False})
    decay_res = client.post("/v1/memory/decay?decay_factor=0.5")
    assert decay_res.status_code == 200
    assert decay_res.json()["status"] == "success"


# ========== 7. 获取自己的记忆列表 ==========
def test_list_my_memories(enforced):
    me = enforced("列表的人")
    client.post("/v1/memory/add", json={"content": "列表记忆1", "summarize": False}, headers=me)
    client.post("/v1/memory/add", json={"content": "列表记忆2", "summarize": False}, headers=me)
    list_res = client.get("/v1/memory/list?limit=10", headers=me)
    assert list_res.status_code == 200
    body = list_res.json()
    assert body["total"] == 2, "列表里只能有自己的两条"
    assert [m["content"] for m in body["memories"]] == ["列表记忆1", "列表记忆2"]


# ========== 8. 记忆库统计 ==========
def test_stats():
    """统计是管理员端点：全局 client 处于 disabled 模式，它就是本机管理员。"""
    res = client.get("/v1/memory/stats")
    assert res.status_code == 200
    assert "status" in res.json()