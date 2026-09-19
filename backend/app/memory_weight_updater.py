# backend/app/memory_weight_updater.py
"""记忆权重没有任何"按时间自动衰减"这回事——这里只留下这句实话。

这个文件原先只有一个 print（自己说自己是占位实现，什么都不做），却被
`app/main.py` 的 `run_scheduler` 每 300 秒调用一次，于是每轮日志都印一句
"执行记忆权重更新"。2026-09-19 之前那行话只是没用：`weight` 那时根本不参与
检索排序。从 `app/memory/ranking.py` 的 `weighted_rank` 把 weight 接进排序那天
起，它开始误导人——读日志的人会以为记忆在随时间自动衰减，而那一轮实际什么都没改。

修法是删掉这条调用，而不是把衰减补上，理由写在这里，免得上第二遍当：

1. **反馈已经同步生效，不等定时器。** `POST /v1/feedback` 直接调
   `MemoryManager.adjust_weights`（见 `app/main.py`），点赞点踩当场就改变下一次
   排序。这里缺的从来不是"实现的逻辑"，而是一个根本不需要的环节。
2. **衰减也已经存在，而且是手动的。** `POST /v1/memory/decay` →
   `decay_weights` / `FakeMemoryStore.decay`，由管理员触发、只作用于调用者本人
   （见 `backend/tests/test_isolation.py`）。把它自动化，等于替每个人决定哪些
   记忆该被遗忘——那是产品决定，不是定时器该顺手做的事。
3. **"按最近未被召回的时间衰减"没有可依据的数据。** 记忆里没有任何召回时间戳
   （`add_memory` 写的 metadata 只有 weight 与 user_id），要做就得在
   `search_memory` 里回写时间戳：检索是每轮对话的热路径，等于给每次召回的每条
   记忆加一次 chroma 写操作，换回来的是一条没人要求的曲线。
4. **它还会踩到夹逼之外。** 只有 `adjust_weights` 把结果夹在 `[0.1, 5.0]`，
   `decay_weights` 是乘完直接写回、不夹逼。再挂一个每 300 秒乘一次的自动衰减，
   权重会一路滑到 0 以下（`weighted_rank` 把负数截成 0），那时用户的点赞再也
   拉不回这条记忆——一个静默、不可逆、没人点过的写者。

所以：`app/main.py` 的定时任务不再调用本模块，本模块也不再提供那个名字，
只在启动时打印一次真实口径。权重的写入者以
`backend/tests/test_memory_weight_scheduler.py` 的那份清单为准——它一漂移，
下面这句话就该跟着改。
"""

WEIGHT_POLICY = ("记忆权重没有任何按时间的自动衰减：它只被显式动作改写——"
                 "用户反馈（POST /v1/feedback，夹在 0.1~5.0）、管理员手动衰减"
                 "（POST /v1/memory/decay）、改写单条记忆（POST /v1/memory/update）。"
                 "后台定时任务不改写权重。")


def log_weight_policy() -> str:
    """打印一次权重口径。只在启动时调用，不进周期任务。

    放在这里而不是内联在 `app/main.py`，是因为这段话必须与它描述的那两个模块
    一同维护：`adjust_weights` 的夹逼、`decay_weights` 的乘性衰减都在
    `app/memory/` 里，改它们的人顺手就能改到这句话，而不会只在翻 main.py 时才想起。
    """
    line = f"ℹ️ [MemoryWeight] {WEIGHT_POLICY}"
    print(line)
    return line
