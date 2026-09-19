"""记忆排序的唯一一条规则。

放在独立模块里，是因为真存储（`memory_manager`，返回 chroma 的 (doc, distance, meta)）
与内存替身（`memory_router.FakeMemoryStore`，返回带 relevance_score 的 dict）形状不同，
但**排序口径必须一致**：两路各写一份 sort 键，就会有一路在反馈调完权重后给出不同答案，
而用户看不出哪条是真的——这正是本项目跨用户泄露之后定下"两路各自反例测试"的原因。
"""


def weighted_rank(entries, top_k: int) -> list:
    """按 relevance * weight 降序返回 payload，取前 top_k。

    entries 是可迭代的 (relevance, weight, payload)。

    - relevance 夹到 >= 0：余弦距离可以大于 1，于是 relevance = 1 - distance 为负。
      放任负数会让"权重越高、排得越靠后"，等于把反馈奖励变成惩罚。
    - 同分按原始顺序：chroma 的距离序在不同版本间不保证稳定，测试也不该依赖它。
    """
    scored = []
    for index, (relevance, weight, payload) in enumerate(entries):
        score = max(0.0, float(relevance)) * max(0.0, float(weight))
        scored.append((-score, index, payload))
    scored.sort()
    return [payload for _, _, payload in scored[:max(1, int(top_k))]]
