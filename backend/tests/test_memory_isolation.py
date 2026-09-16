"""真实记忆库（chromadb）的跨用户隔离测试。

必须绕开 FakeMemoryStore：假存储本来就按 user_id 过滤，天然通过，验不到
memory_manager 里的真路径。这正是"本人无命中时返回他人记忆"那条泄露能长期
存活的原因——它在 CI 里根本没有被执行的分支。

用注入的确定性向量，因此不打付费嵌入接口，也不依赖本机有没有
sentence-transformers。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.memory.memory_manager import MemoryManager

QUERY = "密码学重点"
ALICE = "u_alice"
BOB = "u_bob"

# 维度取 4，够用且便于手算：cosine 距离 = 1 - 余弦相似度
VECTORS = {
    QUERY: [1.0, 0.0, 0.0, 0.0],
    # Bob 的记忆全部比 Alice 的更贴近查询，专门用来把 Alice 挤出 top-k 窗口
    **{f"Bob记忆{i}": [0.99, 0.01, 0.0, 0.0] for i in range(5)},
    "Alice的记忆": [0.5, 0.5, 0.0, 0.0],
}


def _embed(text: str):
    return VECTORS.get(text, [0.0, 0.0, 1.0, 0.0])


def _manager(tmp_path):
    return MemoryManager(persist_dir=str(tmp_path / "chroma"), embedding_fn=_embed)


def test_results_never_contain_another_users_memory(tmp_path):
    """任何一条返回结果的 user_id 都必须就是请求者本人。"""
    mm = _manager(tmp_path)
    mm.add_memory(user_id=ALICE, content="Alice的记忆")
    for i in range(5):
        mm.add_memory(user_id=BOB, content=f"Bob记忆{i}")

    hits = mm.search_memory(ALICE, QUERY, top_k=1)
    assert hits, "本人的记忆必须被召回"
    assert {meta.get("user_id") for _, _, meta in hits} == {ALICE}


def test_own_memory_survives_crowding(tmp_path):
    """别人存在更相近的记忆时，仍要取到自己的。

    旧写法取全局 top_k*2 再事后过滤：窗口被 Bob 占满后，Alice 的记忆根本
    进不来，于是本人反而查不到自己的东西。
    """
    mm = _manager(tmp_path)
    mm.add_memory(user_id=ALICE, content="Alice的记忆")
    for i in range(5):
        mm.add_memory(user_id=BOB, content=f"Bob记忆{i}")

    docs = [doc for doc, _, _ in mm.search_memory(ALICE, QUERY, top_k=2)]
    assert "Alice的记忆" in docs


def test_user_without_memories_gets_nothing(tmp_path):
    """没有记忆的人只能拿到空，绝不能拿到别人的。"""
    mm = _manager(tmp_path)
    for i in range(3):
        mm.add_memory(user_id=BOB, content=f"Bob记忆{i}")

    assert mm.search_memory("u_carol", QUERY, top_k=5) == []


def test_top_k_larger_than_store_size_does_not_raise(tmp_path):
    """新用户只有 1 条记忆时，top_k=10 不该把 chroma 惹崩。"""
    mm = _manager(tmp_path)
    mm.add_memory(user_id=ALICE, content="Alice的记忆")

    hits = mm.search_memory(ALICE, QUERY, top_k=10)
    assert [doc for doc, _, _ in hits] == ["Alice的记忆"]


def test_empty_store_returns_empty(tmp_path):
    mm = _manager(tmp_path)
    assert mm.search_memory(ALICE, QUERY, top_k=5) == []
