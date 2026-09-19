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


def test_summarize_goes_through_the_provider_store(monkeypatch):
    """摘要不许再硬编码 gpt-3.5-turbo：那是绕过 provider 单源的第二份事实来源。

    MemoryManager 的 self.client 是给**嵌入**用的（api_key + apiyi 代理），摘要一直
    借用它并写死一个 OpenAI 模型名，等于"界面配的是 DeepSeek，记账记到别人家"，
    而且那个模型名在代理侧多半根本不存在——失败只打印一行警告就退化成截断。
    """
    from types import SimpleNamespace

    from app.core.providers import store as provider_store
    from app.memory import memory_manager as mm

    built = {}
    provider_call = {}
    embed_client_call = {}

    def _fake_client(bucket):
        def create(**kwargs):
            bucket.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="一句话摘要"))])
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(mm, "build_client",
                        lambda provider: (built.update(provider), _fake_client(provider_call))[1])
    fake_self = SimpleNamespace(use_local_embed=False, _dummy_embed=False,
                                client=_fake_client(embed_client_call))

    out = mm.MemoryManager._summarize(fake_self, "我的猫喜欢吃鱼。" * 40)

    assert out == "一句话摘要", f"没走通 provider，退化成截断了：{out[:30]}…"
    assert built, "摘要仍在使用嵌入客户端，没走 provider 解析"
    assert embed_client_call == {}, f"摘要还打在嵌入客户端上：{embed_client_call}"
    expected = provider_store.resolve()
    assert provider_call.get("model") == expected["model"] != "gpt-3.5-turbo", \
        f"用的模型是 {provider_call.get('model')}，配置里是 {expected['model']}"
    assert built["base_url"] == expected["base_url"]


def test_decay_factor_is_bounded():
    """衰减因子只许落在 (0, 1]，越界的必须在门口挡掉。

    起因是管理页这一轮把 `decay_factor` 做成了可点输入。那个 `<input>` 确实写了
    `min="0.1" max="1"`，但数字框的 validity 只在**表单提交**时才拦，而这一页是
    click 处理器直接发请求；更要紧的是接口本身谁都能直接打——所以前端那道只是
    即时反馈，承重的一直缺着：路由原先是 `Query(0.95)` 无约束，而 `decay_weights`
    又是乘完直接写回不夹逼，于是：

    - 填 `0` → 整池权重变成 0；填负数 → 负权重，而 `weighted_rank` 把负数截成 0。
      这两种都不是"这次衰减过头"，是**把这个人所有记忆的排序信号一次性抹平**；
    - 没有任何端点能按回去。`adjust_weights`（用户点赞点踩）虽然夹在 [0.1, 5.0]
      能慢慢拉回，但那要一条一条攒，而"清零"只需要一次输入框里的 0。

    `1.0` 必须放过：管理员拿它确认"这个按钮到底改了多少"，挡死它等于不许试错。
    上限取 1 是因为这个端点叫 decay——大于 1 是整池通胀，那是另一个动作。
    """
    for bad in ("0", "-1", "0.0", "1.5", "100"):
        res = client.post(f"/v1/memory/decay?decay_factor={bad}")
        assert res.status_code == 422, f"因子 {bad} 被收了（{res.status_code}）：整池权重会被这一次输入改坏"

    ok = client.post("/v1/memory/decay?decay_factor=1.0")
    assert ok.status_code == 200, "1.0（无操作）被误挡，管理员没法拿它对照"


def test_decay_at_one_changes_nothing_and_negative_never_lands():
    """1.0 真的什么都不改；而任何一路写进去的权重都不许是负数或 0。

    前一条测的是门口，这条测的是门后：万一将来有人图省事在 `decay_weights` 里自己
    乘一遍（绕过路由校验），排序层拿到的仍然是可比较的正数。
    """
    client.post("/v1/memory/add", json={"content": "边界记忆"})
    before = [(r["content"], r["weight"]) for r in
              client.post("/v1/memory/search", json={"query": "边界记忆", "top_k": 20}).json()["results"]]
    client.post("/v1/memory/decay?decay_factor=1.0")
    after = [(r["content"], r["weight"]) for r in
             client.post("/v1/memory/search", json={"query": "边界记忆", "top_k": 20}).json()["results"]]

    assert before, "这条断言在空转：一条都没搜到，比较就成了两句废话"
    assert before == after, f"因子 1.0 改了权重：{before} → {after}"
    assert all(w > 0 for _, w in after), f"排序层拿到了非正权重：{after}"


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