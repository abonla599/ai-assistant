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
    assert "message" in res.json()


# ========== 2. 基础对话（当前为占位接口） ==========
def test_chat():
    res = client.post("/v1/chat", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "你好"}]
    })
    assert res.status_code == 200
    assert "reply" in res.json()


# ========== 3. 记忆添加与搜索 ==========
def test_memory_add_search():
    # 添加记忆
    add_res = client.post("/v1/memory/add", json={
        "user_id": "test_user",
        "content": "我叫张三",
        "summarize": False
    })
    assert add_res.status_code == 200
    assert add_res.json()["status"] == "success"
    mem_id = add_res.json().get("memory_id")
    assert mem_id is not None

    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "user_id": "test_user",
        "query": "名字",
        "top_k": 3
    })
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    assert len(results) > 0


# ========== 4. 记忆批量删除 ==========
def test_memory_delete_batch():
    # 先加两条
    add1 = client.post("/v1/memory/add", json={"user_id": "del_tester", "content": "记忆A", "summarize": False})
    add2 = client.post("/v1/memory/add", json={"user_id": "del_tester", "content": "记忆B", "summarize": False})
    id1 = add1.json()["memory_id"]
    id2 = add2.json()["memory_id"]

    # 用 client.request 发送 DELETE 带 JSON 体（FastAPI TestClient 的坑）
    del_res = client.request(
        "DELETE",
        "/v1/memory/delete",
        json={"user_id": "del_tester", "memory_ids": [id1, id2]}
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2


# ========== 5. 记忆更新 ==========
def test_memory_update():
    add_res = client.post("/v1/memory/add", json={"user_id": "upd_tester", "content": "旧内容", "summarize": False})
    mem_id = add_res.json()["memory_id"]

    update_res = client.put("/v1/memory/update", json={
        "memory_id": mem_id,
        "new_content": "新内容"
    })
    assert update_res.status_code == 200

    search_res = client.post("/v1/memory/search", json={
        "user_id": "upd_tester",
        "query": "新内容",
        "top_k": 1
    })
    assert len(search_res.json()["results"]) > 0
    assert "新内容" in search_res.json()["results"][0]["content"]


# ========== 6. 记忆衰减 ==========
def test_memory_decay():
    client.post("/v1/memory/add", json={"user_id": "decay_tester", "content": "衰减测试", "summarize": False})
    decay_res = client.post("/v1/memory/decay?user_id=decay_tester&decay_factor=0.5")
    assert decay_res.status_code == 200
    assert decay_res.json()["status"] == "success"


# ========== 7. 获取用户记忆列表 ==========
def test_list_user_memories():
    client.post("/v1/memory/add", json={"user_id": "list_tester", "content": "列表记忆1", "summarize": False})
    client.post("/v1/memory/add", json={"user_id": "list_tester", "content": "列表记忆2", "summarize": False})
    list_res = client.get("/v1/memory/list/list_tester?limit=10")
    assert list_res.status_code == 200
    assert list_res.json()["total"] >= 2


# ========== 8. 记忆库统计 ==========
def test_stats():
    res = client.get("/v1/memory/stats")
    assert res.status_code == 200
    assert "status" in res.json()