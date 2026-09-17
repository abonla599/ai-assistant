import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# 记忆端点的身份只来自令牌，因此每条用例都注册一个自己的用户：
# 假存储是进程级单例，换个身份就等于换了个空记忆池，断言才与测试顺序无关。


def test_add_and_search(enforced):
    me = enforced("养猫的人")
    # 添加一条记忆
    add_res = client.post("/v1/memory/add", json={
        "content": "我的猫叫小白，它很喜欢吃鱼",
        "summarize": False
    }, headers=me)
    assert add_res.status_code == 200
    data = add_res.json()
    # ✅ 你的 API 返回的是 "success"，不是 "ok"
    assert data["status"] == "success"
    mem_id = data["memory_id"]

    # 搜索记忆
    search_res = client.post("/v1/memory/search", json={
        "query": "小白",
        "top_k": 3
    }, headers=me)
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    assert len(results) > 0
    contents = [r["content"] for r in results]
    assert any("小白" in c for c in contents)
    assert client.get("/v1/memory/list?limit=50", headers=me).json()["total"] == 1


def test_delete_batch(enforced):
    me = enforced("要删记忆的人")
    # 添加两条记忆
    add1 = client.post("/v1/memory/add", json={"content": "记忆A"}, headers=me)
    add2 = client.post("/v1/memory/add", json={"content": "记忆B"}, headers=me)
    id1 = add1.json()["memory_id"]
    id2 = add2.json()["memory_id"]

    # ✅ DELETE 带 JSON 体需用 client.request("DELETE", url, json=...)
    del_res = client.request(
        "DELETE",
        "/v1/memory/delete",
        json={"memory_ids": [id1, id2]},
        headers=me
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2


def test_update_memory(enforced):
    me = enforced("要改记忆的人")
    # 添加一条记忆
    add_res = client.post("/v1/memory/add", json={"content": "原始内容"}, headers=me)
    mem_id = add_res.json()["memory_id"]

    # 更新内容
    update_res = client.put("/v1/memory/update", json={
        "memory_id": mem_id,
        "new_content": "修改后的内容"
    }, headers=me)
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "success"

    # 搜索验证
    search_res = client.post("/v1/memory/search", json={
        "query": "修改后",
        "top_k": 1
    }, headers=me)
    results = search_res.json()["results"]
    assert len(results) > 0
    assert "修改后" in results[0]["content"]


def test_decay():
    """衰减走管理员端点、只作用于调用者自己那一份记忆（此处即本机管理员）。"""
    # 添加记忆
    client.post("/v1/memory/add", json={"content": "记忆X"})
    client.post("/v1/memory/add", json={"content": "记忆Y"})

    # 衰减
    decay_res = client.post("/v1/memory/decay?decay_factor=0.5")
    assert decay_res.status_code == 200
    assert decay_res.json()["status"] == "success"

    # 搜索并检查权重 < 1.0（top_k 上限是 20，再大就是 422）
    search_res = client.post("/v1/memory/search", json={"query": "记忆", "top_k": 20})
    results = search_res.json()["results"]
    assert len(results) > 0, "应该找到至少一条记忆"
    weights = [r["weight"] for r in results]
    # 衰减后权重应该小于原始权重 1.0
    assert all(w < 1.0 for w in weights), f"权重应全部小于1.0，实际: {weights}"