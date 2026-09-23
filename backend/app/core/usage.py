"""用量账本：谁、用哪条 provider、花了多少 token、谁的钱。

为什么按天聚合而不是逐条流水：账本要回答的是判据里那句"每个人用了几次、多少 token、
谁的钱"，而流水会无限长——`data/` 没有任何清理机制，留着它等于留一个迟早要人手工删的
文件。按 (天, 人, provider) 一行，行数被人天数量天然框住，不需要轮转。

一处必须留字的边界：**记账用供应商回传的 `usage`，预算用估算，两者不是一个东西。**
上游各家分词器不同，本地算出来的数拿去对账是错的（2026-09-21 实测 DeepSeek 在
`stream_options.include_usage` 下会在最后一个块回 `prompt_tokens/completion_tokens/
total_tokens`，外加 `completion_tokens_details.reasoning_tokens` 与
`prompt_cache_hit_tokens`）。所以下面只接受"上游说多少就记多少"；上游没回的时候记 0
并留 `unknown` 计数，而不是拿估算冒充。
"""
import json
import os
import threading
from datetime import datetime

from app.core.paths import data_root

_lock = threading.RLock()
_days: dict = {}
_PATH: str = ""

FIELDS = ("calls", "ok", "failed", "prompt_tokens", "completion_tokens",
          "total_tokens", "reasoning_tokens", "cached_tokens", "tool_rounds", "unknown_usage")


def _default_path() -> str:
    env_path = os.getenv("USAGE_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "usage.json")


def _blank(paid_by: str = "operator") -> dict:
    # paid_by 不是计数器，但它是这张表里唯一说明"这口钱谁出"的列，
    # 所以它跟着行走：删掉它，snapshot 就答不出规划书判据里那句"谁的钱"。
    row = {f: 0 for f in FIELDS}
    row["paid_by"] = paid_by
    return row


def restore(path: str = None) -> int:
    """从盘上读回来（启动时一次；测试用它等价于"重启进程"）。返回已有行数。"""
    global _days, _PATH
    target = os.path.abspath(path or _default_path())
    with _lock:
        _PATH = target
        _days = {}
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return 0
        except (ValueError, OSError) as e:
            backup = target + ".corrupt"
            try:
                os.replace(target, backup)
                print(f"⚠️ 用量账本读不了（{e}），已备份为 {backup}，本轮从空账开始")
            except OSError:
                print(f"⚠️ 用量账本读不了且无法备份（{e}），本轮从空账开始")
            return 0
        days = (data or {}).get("days") or {}
        for day, per_user in days.items():
            for uid, per_provider in (per_user or {}).items():
                for pid, row in (per_provider or {}).items():
                    merged = _blank(paid_by=row.get("paid_by", "operator"))
                    merged.update({k: v for k, v in (row or {}).items() if k in merged})
                    _days.setdefault(day, {}).setdefault(uid, {})[pid] = merged
        return sum(len(u) for u in _days.values())


def _write() -> None:
    path = _PATH or _default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"days": _days}, f, ensure_ascii=False, indent=2)
        # replace 原子不等于落盘：fsync 之后 replace，断电不丢账。
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def record_call(user_id: str, provider_id: str, paid_by: str, *,
                prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0,
                reasoning_tokens: int = 0, cached_tokens: int = 0,
                tool_rounds: int = 0, ok: bool = True, day: str = None) -> None:
    """记一次模型调用。调用方在两条聊天路径上，都在池子里跑，不占事件循环。

    `user_id` 必填：一本不知道属于谁的账，和一条不知道属于谁的任务是同一个错误。
    上游没回 usage 时各项都是 0，但 `unknown_usage` 会 +1——"低估了用量"这件事
    必须能被看出来，而不是安静地记成 0。
    """
    if not (user_id or "").strip():
        raise ValueError("记账必须有归属人：user_id 不能为空")
    if paid_by not in ("operator", "user"):
        raise ValueError(f"paid_by 只许 operator 或 user，收到 {paid_by!r}")
    row_key = (day or datetime.now().astimezone().strftime("%Y-%m-%d"),
               user_id.strip(), provider_id or "unknown-provider")
    with _lock:
        day_map = _days.setdefault(row_key[0], {})
        row = day_map.setdefault(row_key[1], {}).setdefault(row_key[2], _blank(paid_by))
        # 以最后一次写入为准：管理员把某条 provider 从"我垫钱"改成"用户自带"之后，
        # 账上还挂着旧那一列的话，报出来的就是假话。
        row["paid_by"] = paid_by
        row["calls"] += 1
        row["ok" if ok else "failed"] += 1
        row["prompt_tokens"] += int(prompt_tokens or 0)
        row["completion_tokens"] += int(completion_tokens or 0)
        row["total_tokens"] += int(total_tokens or 0)
        row["reasoning_tokens"] += int(reasoning_tokens or 0)
        row["cached_tokens"] += int(cached_tokens or 0)
        row["tool_rounds"] += int(tool_rounds or 0)
        if not (int(total_tokens or 0) > 0):
            row["unknown_usage"] += 1
        _write()


def snapshot(day: str = None) -> list:
    """某一天的账，默认今天。给管理页与"谁的钱"这类问题用。"""
    target = day or datetime.now().astimezone().strftime("%Y-%m-%d")
    with _lock:
        rows = []
        for uid, per_provider in sorted(_days.get(target, {}).items()):
            for pid, row in sorted(per_provider.items()):
                item = dict(row)
                item.update({"day": target, "user_id": uid, "provider_id": pid})
                rows.append(item)
        return rows


def days() -> list:
    with _lock:
        return sorted(_days)


restore()
