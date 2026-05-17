import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_add_and_search():
    # 添加一条记忆
    add_res = client.post("/v1/memory/add", json={
        "user_id": "test_user",
        "content": "我的猫叫小白，它很喜欢吃鱼",
        "summarize": False
    })
    assert add_res.status_code == 200
    data = add_res.json()
    # ✅ 你的 API 返回的是 "success"，不是 "ok"
    assert data["status"] == "success"
    mem_id = data["memory_id"]

    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "user_id": "test_user",
        "query": "宠物",
        "top_k": 3
    })
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    assert len(results) > 0
    contents = [r["content"] for r in results]
    assert any("小白" in c for c in contents)


def test_delete_batch():
    # 添加两条记忆
    add1 = client.post("/v1/memory/add", json={"user_id": "tester", "content": "记忆A"})
    add2 = client.post("/v1/memory/add", json={"user_id": "tester", "content": "记忆B"})
    id1 = add1.json()["memory_id"]
    id2 = add2.json()["memory_id"]

    # ✅ DELETE 带 JSON 体需用 client.request("DELETE", url, json=...)
    del_res = client.request(
        "DELETE",
        "/v1/memory/delete",
        json={
            "user_id": "tester",
            "memory_ids": [id1, id2]
        }
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2


def test_update_memory():
    # 添加一条记忆
    add_res = client.post("/v1/memory/add", json={"user_id": "updater", "content": "原始内容"})
    mem_id = add_res.json()["memory_id"]

    # 更新内容
    update_res = client.put("/v1/memory/update", json={
        "memory_id": mem_id,
        "new_content": "修改后的内容"
    })
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "success"

    # 搜索验证
    search_res = client.post("/v1/memory/search", json={
        "user_id": "updater",
        "query": "修改后",
        "top_k": 1
    })
    results = search_res.json()["results"]
    assert len(results) > 0
    assert "修改后" in results[0]["content"]


def test_decay():
    # 添加记忆
    client.post("/v1/memory/add", json={"user_id": "decay_user", "content": "记忆X"})
    client.post("/v1/memory/add", json={"user_id": "decay_user", "content": "记忆Y"})

    # 衰减
    decay_res = client.post("/v1/memory/decay?user_id=decay_user&decay_factor=0.5")
    assert decay_res.status_code == 200
    assert decay_res.json()["status"] == "success"

    # 搜索并检查权重 < 1.0
    search_res = client.post("/v1/memory/search", json={
        "user_id": "decay_user",
        "query": "记忆",
        "top_k": 5
    })
    weights = [r["weight"] for r in search_res.json()["results"]]
    assert all(w < 1.0 for w in weights), f"权重应全部小于1.0，实际: {weights}"