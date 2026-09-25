"""真假存储分叉里从来没被执行过的那几条真实分支（派单 G1–G6）。

pytest 下 `memory_router` 恒走 FakeMemoryStore（in_pytest 判定），于是
`safe_call(real, fake)` 的 real 半边在测试套件里从未被调用过——上一轮的跨用户
泄露正是活在这种"守卫只写在假存储上"的分支里的。本文件用 test_isolation 的
现成搭法（_memory_app + 注入向量），把 G1–G6 逐条挪到真 ChromaDB 后端上跑。

回退必红自查：把 memory_router 里任一 real_* 闭包换成 fake_*，本文件对应断言
当场红；把 safe_call 的 503 分支删掉，G3 红；把 _summarize 退回写死模型名，
G4 红（fake build_client 一收参数就露馅）。
"""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.memory.memory_manager as mm
import app.memory.memory_router as mr
from tests.test_isolation import BOOT, _fixed_vector, _memory_app


@pytest.fixture
def mem_real(tmp_path, monkeypatch):
    """真 ChromaDB 后端 + 连 manager 本体一起给出（G1/G2 要直接核对集合状态）。"""
    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"),
                               embedding_fn=_fixed_vector)
    client, ha, hb = _memory_app(tmp_path, monkeypatch, manager=manager)
    return (client, ha, hb), manager


@pytest.fixture(autouse=True)
def _sanitizer_terms_untouched():
    """G5 会往脱敏登记表里塞测试专用词，跑完必须原样还回去。

    登记表是进程级全局——带出去的话，后面任何用例的断言字符串里恰好含这个
    "模型名"就会被无声打码，红得查无此人。
    """
    from app.core import logsanitizer

    before = logsanitizer.registered_terms()
    yield
    logsanitizer.clear_terms_for_tests()
    for term in before:
        logsanitizer.register(term)


# ---------- G1：decay_weights 的真库路径 ----------

def test_decay_on_real_store_only_touches_caller(mem_real):
    """衰减只动调用者本人，且动的是 chroma 里那份 metadata，不是假字典。

    端点是 RequireAdmin 且身份从凭据推导：所以"别人的权重不被碰"必须在真库上
    断言——假存储里那个循环任何人跑了 pytest 都绿，真库下推错了 where 才是要命的方向。
    """
    (client, ha, _), manager = mem_real
    client.post("/v1/memory/add", json={"content": "A的记忆", "summarize": False},
                headers=ha)
    client.post("/v1/memory/add", json={"content": "管理员的记忆", "summarize": False},
                headers=BOOT)

    r = client.post("/v1/memory/decay?decay_factor=0.5", headers=BOOT)
    assert r.status_code == 200

    admin = client.get("/v1/memory/list?limit=50", headers=BOOT).json()["memories"]
    assert admin and all(m["metadata"]["weight"] == pytest.approx(0.5) for m in admin)
    a = client.get("/v1/memory/list?limit=50", headers=ha).json()["memories"]
    assert a[0]["metadata"]["weight"] == 1.0, "衰减串到别人头上了"
    # 真库侧再看一眼：路由若悄悄打回假存储，collection 里根本不会有这些记录
    uid = a[0]["metadata"]["user_id"]
    stored = manager.collection.get(where={"user_id": uid})["metadatas"]
    assert stored and all(m["weight"] == 1.0 for m in stored)


def test_decay_still_rejects_non_admin_on_real_store(mem_real):
    (client, ha, _), _ = mem_real
    assert client.post("/v1/memory/decay", headers=ha).status_code == 403


# ---------- G2：get_collection_stats 的真库路径 ----------

def test_stats_reads_the_real_collection(mem_real):
    (client, ha, hb), manager = mem_real
    for headers, content in ((ha, "a1"), (hb, "b1"), (hb, "b2")):
        client.post("/v1/memory/add", json={"content": content, "summarize": False},
                    headers=headers)
    body = client.get("/v1/memory/stats", headers=BOOT).json()
    assert body["total_memories"] == manager.collection.count() == 3
    assert body["collection_name"] == "user_memories"
    # 假存储那份会报 "fake_store"——这里断言真名，就是断言没打回假路
    assert body["collection_name"] != "fake_store"
    assert client.get("/v1/memory/stats", headers=ha).status_code == 403


# ---------- G3：记忆初始化失败的 503 分支 ----------

def test_init_failure_says_503_instead_of_pretending_empty(tmp_path, monkeypatch):
    """memory_manager 起不来时每个端点都要 503，而不是 200+空列表。

    safe_call 里 `memory_init_error is not None → raise` 这一格在 pytest 下永远
    为假（要么 manager 在、要么 error 是 None），线上"初始化失败但接口照常回
    '你没有记忆'"的伪装就从来没人拦。这里把这两个模块级全局摆成故障现场。
    """
    client, ha, _ = _memory_app(tmp_path, monkeypatch, manager=None)
    monkeypatch.setattr(mr, "memory_manager", None)
    monkeypatch.setattr(mr, "memory_init_error", "嵌入配置缺失，MemoryManager 没起来")

    r = client.post("/v1/memory/search", json={"query": "anything"}, headers=ha)
    assert r.status_code == 503
    assert "记忆服务不可用" in r.json()["detail"]
    # 读、写、统计三条路径同一个守卫，少一路就是一路在装正常
    assert client.post("/v1/memory/add", json={"content": "x"},
                       headers=ha).status_code == 503
    assert client.get("/v1/memory/list", headers=ha).status_code == 503
    assert client.get("/v1/memory/stats", headers=BOOT).status_code == 503


# ---------- G4：summarize=True 的真摘要端到端 ----------

def test_summarize_end_to_end_stores_the_summary_not_the_raw_text(mem_real, monkeypatch):
    """从 HTTP 打到存储：summarize=True 存的必须是模型给的那句话。

    test_memory.py 里的同名判据是拿 SimpleNamespace 假 self 直接调 _summarize——
    单元层锁了"走 provider"，但 add_memory 的分支门（`summarize and not dummy`）
    和落库内容没人端到端走过。注入向量的 manager 默认 _dummy_embed=True（按
    "不做摘要"处理），这里翻回 False，模拟云端嵌入配置齐备的线上形状。
    """
    (client, ha, _), manager = mem_real
    manager._dummy_embed = False

    calls = {}

    def fake_create(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="爱猫如命"))])

    monkeypatch.setattr(mm, "build_client", lambda provider: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))))

    long_text = "我的猫喜欢吃鱼。" * 40
    r = client.post("/v1/memory/add",
                    json={"content": long_text, "summarize": True}, headers=ha)
    assert r.status_code == 200

    listed = client.get("/v1/memory/list?limit=50", headers=ha).json()["memories"]
    assert [m["content"] for m in listed] == ["爱猫如命"], \
        "存进去的还是原文（或截断）：摘要没真正参与写库"
    assert calls.get("messages"), "摘要没走 chat completions"
    assert calls.get("model") != "gpt-3.5-turbo"


def test_summarize_failure_degrades_to_truncation_not_500(mem_real, monkeypatch):
    """模型侧炸了要按文档说的退化成截断，且整条 add 仍然成功。

    这条是反例守卫：若有人把 except 删了"让错误冒出来"，端点会变 500——
    摘要失败不值得丢用户那条记忆本身。
    """
    (client, ha, _), manager = mem_real
    manager._dummy_embed = False

    def boom(provider):
        raise RuntimeError("上游 503")

    monkeypatch.setattr(mm, "build_client", boom)
    long_text = "狗喜欢啃骨头。" * 40
    r = client.post("/v1/memory/add",
                    json={"content": long_text, "summarize": True}, headers=ha)
    assert r.status_code == 200
    listed = client.get("/v1/memory/list?limit=50", headers=ha).json()["memories"]
    assert listed[0]["content"] == long_text[:100]


# ---------- G5：启动期嵌入口的三态 ----------

_CLOUD_ENV = ("api_key", "EMBEDDINGS_BASE_URL", "EMBEDDINGS_MODEL")


@pytest.fixture
def _no_cloud_env(monkeypatch):
    """把三个嵌入配置摘干净：开发机 .env 里可能真有值，摘不走三态就测不成三态。"""
    for key in _CLOUD_ENV:
        monkeypatch.delenv(key, raising=False)


def _fake_openai(bucket, vectors=None, ctor_bucket=None):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            if ctor_bucket is not None:
                ctor_bucket.update(kwargs)

        def with_options(self, **kwargs):
            bucket["_with_options"] = kwargs
            return self

        @property
        def embeddings(self):
            outer = self

            class _E:
                def create(self, **kwargs):
                    bucket.update({k: v for k, v in kwargs.items() if k != "_with_options"})
                    vec = (vectors or {}).get(kwargs.get("model"), [0.1, 0.2, 0.3, 0.4])
                    return SimpleNamespace(
                        data=[SimpleNamespace(embedding=list(vec))])
            return _E()
    return FakeOpenAI


def test_embed_boot_cloud_path(tmp_path, monkeypatch, capsys):
    """三态之一（云端）：配置齐 → 探一次 embeddings → 既非本地也非伪嵌入。"""
    monkeypatch.setenv("api_key", "sk-not-a-real-key")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://embed.invalid/v1")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "secret-embed-9k")
    probe = {}
    monkeypatch.setattr(mm, "OpenAI", _fake_openai(probe))

    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"))
    assert manager.use_local_embed is False
    assert manager._dummy_embed is False
    assert manager.embed_model == "secret-embed-9k"
    assert probe.get("model") == "secret-embed-9k", "启动探发没打过去（三态判错方向）"

    out = capsys.readouterr().out
    assert "secret-embed-9k" not in out, "启动日志里躺着明文嵌入模型名（派单②的验收点）"


def test_embed_cloud_client_fails_fast_but_probe_stays_lenient(tmp_path, monkeypatch):
    """云端嵌入客户端必须自带短超时且不重试——上游嵌入服务挂起不返回那晚（2026-09-23），
    SDK 默认 600s×3 让每条聊天卡 122~152 秒，手机端表现为"对话框没有回应"。

    同时启动探发要留宽限：探失败会当场降级后端，不能被一次偶发慢响应误判。
    """
    monkeypatch.setenv("api_key", "sk-not-a-real-key")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://embed.invalid/v1")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "secret-embed-9k")
    probe, ctor = {}, {}
    monkeypatch.setattr(mm, "OpenAI", _fake_openai(probe, ctor_bucket=ctor))

    mm.MemoryManager(persist_dir=str(tmp_path / "chroma"))

    timeout = ctor.get("timeout")
    assert isinstance(timeout, httpx.Timeout), \
        f"嵌入客户端没配显式超时（拿到 {timeout!r}），上游挂起时聊天会陪着卡死"
    assert timeout.read is not None and timeout.read <= 30, \
        f"聊天嵌入超时过长：{timeout.read}s"
    assert ctor.get("max_retries") == 0, \
        f"嵌入客户端仍在重试（max_retries={ctor.get('max_retries')}），最坏延迟=超时×(重试+1)"

    probe_timeout = probe.get("_with_options", {}).get("timeout")
    assert isinstance(probe_timeout, httpx.Timeout) and probe_timeout.read > timeout.read, \
        "启动探发没走更宽的超时：上游一次慢响应就会把整个后端误降级"


def test_embed_boot_local_path(_no_cloud_env, tmp_path, monkeypatch):
    """三态之二（本地）：无云端配置、sentence_transformers 可用 → 本地嵌入。"""
    mod = types.ModuleType("sentence_transformers")

    class FakeST:
        def __init__(self, name):
            self.name = name

        def encode(self, text):
            return SimpleNamespace(tolist=lambda: [0.05, 0.06, 0.07])

    mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)

    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"))
    assert manager.use_local_embed is True
    assert manager.embed_model == "local"


def test_embed_boot_dummy_path_with_warning(_no_cloud_env, tmp_path, monkeypatch,
                                            capsys):
    """三态之三（伪嵌入）：两头都不可用 → 降级 + 必须留下可读的警告行。

    降级本身是设计好的，坏的是"降级得悄无声息"：全零向量下语义检索结果不可信，
    运维必须能从日志里看出这台风筝线断了。
    """
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import 即 ImportError

    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"))
    assert manager._dummy_embed is True
    assert manager.embed_model == "dummy"

    out = capsys.readouterr().out
    assert "伪嵌入" in out, "降级没留警告——故障被伪装成了正常运行"


def test_embed_boot_key_without_base_url_warns_locally(_no_cloud_env, tmp_path,
                                                       monkeypatch, capsys):
    """配了 key 没配地址：明说"按未配置云端处理"，别让人以为云端已生效。"""
    monkeypatch.setenv("api_key", "sk-orphan")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"))
    assert manager._dummy_embed is True
    out = capsys.readouterr().out
    assert "EMBEDDINGS_BASE_URL" in out


# ---------- G6：test_memory 判据在真库下的等价断言 ----------

def _volatility_stripped(body):
    """把"天生每次不一样"的字段换成占位，留下纯契约形状。"""
    if isinstance(body, dict):
        return {k: ("<volatile>" if k in {"memory_id", "id", "collection_name"}
                    else _volatility_stripped(v))
                for k, v in body.items()}
    if isinstance(body, list):
        out = []
        for item in body:
            item = _volatility_stripped(item)
            if isinstance(item, dict) and "distance" in item:
                item = {**item, "relevance_score": "<volatile>", "distance": "<volatile>"}
            out.append(item)
        return sorted(out, key=lambda x: repr(x))
    return body


def _contract_scenario(client, ha, hb):
    """test_memory.py 判过的那套动作，逐步记 (状态码, 打码后的响应形状)。"""
    steps = []
    r = client.post("/v1/memory/add", json={"content": "甲方词条", "summarize": False},
                    headers=ha)
    steps.append(("add-A", r.status_code, _volatility_stripped(r.json())))
    mid = r.json()["memory_id"]
    steps.append(("add-B", client.post(
        "/v1/memory/add", json={"content": "乙方词条", "summarize": False},
        headers=hb).status_code, None))
    r = client.get("/v1/memory/list?limit=50", headers=ha)
    body = r.json()
    contents = sorted(m["content"] for m in body["memories"])
    steps.append(("list-A", r.status_code, contents))
    r = client.put("/v1/memory/update",
                   json={"memory_id": mid, "new_content": "甲方改后"}, headers=ha)
    steps.append(("update-A", r.status_code, _volatility_stripped(r.json())))
    r = client.post("/v1/memory/search", json={"query": "甲方改后", "top_k": 5},
                    headers=ha)
    body = r.json()
    steps.append(("search-shape", r.status_code,
                  {"keys": sorted(body.keys()),
                   "item_keys": sorted(body["results"][0].keys())
                   if body["results"] else []}))
    steps.append(("delete-other-user", client.request(
        "DELETE", "/v1/memory/delete", json={"memory_ids": [mid]},
        headers=hb).status_code, None))
    r = client.request("DELETE", "/v1/memory/delete", json={"memory_ids": [mid]},
                       headers=ha)
    steps.append(("delete-A", r.status_code, _volatility_stripped(r.json())))
    return steps


def test_fake_and_real_backends_honor_the_same_contract(tmp_path, monkeypatch):
    """G6：假存储上绿的每一步，真库必须给出形状一致的答复。

    两个后端各自答出"<volatile>"以外的同一份契约——否则"pytest 全绿"与
    "线上可用"是两件事，而历史已经证明把前者当后者一次要赔一个跨用户泄露。
    一次 app 里换 manager 而不是造两个 app：safe_call 在调用期读模块全局，
    两个探针 app 会互相踩后端，那份"等价"就是比给自己看。
    """
    client, ha, hb = _memory_app(tmp_path, monkeypatch, manager=None)
    fake_steps = _contract_scenario(client, ha, hb)

    manager = mm.MemoryManager(persist_dir=str(tmp_path / "chroma"),
                               embedding_fn=_fixed_vector)
    monkeypatch.setattr(mr, "memory_manager", manager)
    real_steps = _contract_scenario(client, ha, hb)

    assert fake_steps == real_steps, (
        "假/真后端契约出现分叉：\n" + "\n".join(
            f"  {f[0]}: fake={f[1:]} real={r[1:]}"
            for f, r in zip(fake_steps, real_steps) if f != r))
