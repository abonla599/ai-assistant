"""记忆权重是否真的参与排序。

起因是 `memory_manager.adjust_weights` 的 docstring 写着"权重直接影响检索排序
（memory_router 按 relevance*weight 排序）"，而两处 sort 键都只有 `relevance_score`
（`memory_router.py:72,93`），真存储那条路更是压根不再排序、直接用 chroma 的距离序。
也就是说反馈调过的权重只落在数字上，没人读——被赞过的记忆并不会更容易被召回。

fake 与 real 两路各自有反例测试：这是本项目跨用户泄露之后定下的规矩，只测其中一路
会让另一路继续说谎。
"""
import pytest

from app.memory.memory_router import fake_store
from app.memory.ranking import weighted_rank


def test_weighted_rank_prefers_heavier_at_equal_relevance():
    out = weighted_rank([(1.0, 1.0, "a"), (1.0, 3.0, "b")], top_k=2)
    assert out == ["b", "a"]


def test_weighted_rank_is_stable_when_scores_tie():
    out = weighted_rank([(0.5, 2.0, "x"), (0.5, 2.0, "y")], top_k=2)
    assert out == ["x", "y"]


def test_weighted_rank_clamps_negative_relevance_and_truncates():
    # 余弦距离可以 > 1，于是 relevance = 1 - distance 为负。放任负数会让"权重越高
    # 排得越靠后"，把反馈奖励变成惩罚。
    entries = [(-0.5, 5.0, "far"), (0.2, 1.0, "near"), (0.9, 1.0, "mid")]
    assert weighted_rank(entries, top_k=2) == ["mid", "near"]


def test_fake_store_exact_matches_are_ordered_by_weight():
    """同样精确命中时，被赞过的必须排在前面。"""
    store = type(fake_store)()
    store.add("u1", "我喜欢柠檬", {"weight": 1.0})
    store.add("u1", "柠檬很酸", {"weight": 4.0})
    hits = store.search("u1", "柠檬", top_k=2)
    assert [h["content"] for h in hits] == ["柠檬很酸", "我喜欢柠檬"], \
        "权重没参与排序：fake 与 real 会给出不同答案"


def test_fake_store_top_k_picks_the_heavier_one():
    store = type(fake_store)()
    store.add("u1", "柠檬水", {"weight": 1.0})
    store.add("u1", "柠檬蛋糕", {"weight": 5.0})
    hits = store.search("u1", "柠檬", top_k=1)
    assert len(hits) == 1 and hits[0]["content"] == "柠檬蛋糕"


class _StubCollection:
    """替掉 chroma：按距离升序给固定结果，权重各不相同。"""

    def __init__(self, rows):
        self.rows = rows          # [(id, doc, distance, weight)]
        self.n_results_seen = None

    def count(self):
        return len(self.rows)

    def query(self, query_embeddings=None, n_results=1, where=None):
        self.n_results_seen = n_results
        picked = self.rows[:n_results]
        return {
            "ids": [[r[0] for r in picked]],
            "documents": [[r[1] for r in picked]],
            "distances": [[r[2] for r in picked]],
            "metadatas": [[{"user_id": where["user_id"], "weight": r[3]} for r in picked]],
        }


def _manager_with(rows):
    from app.memory.memory_manager import MemoryManager

    manager = MemoryManager.__new__(MemoryManager)
    manager.collection = _StubCollection(rows)
    manager._embed = lambda text: [0.0, 0.0]
    return manager


def test_real_store_reorders_by_weight_instead_of_distance_only():
    """chroma 给的是距离序；权重必须能在它之上重排。"""
    rows = [("m1", "近的弱", 0.10, 0.2),
            ("m2", "远的强", 0.30, 5.0)]
    out = _manager_with(rows).search_memory("u1", "任意", top_k=2)
    assert [doc for doc, _, _ in out] == ["远的强", "近的弱"], \
        "真存储仍按 chroma 的距离序返回，权重没参与"


def test_real_store_overfetches_so_a_heavy_memory_can_surface():
    """只取 top_k 条再重排 = 权重永远救不回排在窗口外的记忆。"""
    rows = [("m1", "a", 0.10, 1.0), ("m2", "b", 0.20, 1.0),
            ("m3", "c", 0.30, 1.0), ("m4", "重的该冒头", 0.40, 5.0)]
    collection = _StubCollection(rows)
    manager = _manager_with(rows)
    manager.collection = collection
    out = manager.search_memory("u1", "任意", top_k=3)
    assert collection.n_results_seen > 3, f"没有多取，只问了 {collection.n_results_seen} 条"
    assert "重的该冒头" in [doc for doc, _, _ in out]
    assert len(out) == 3, "重排之后必须截回 top_k，否则注入上下文的条数失控"
