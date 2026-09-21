"""日程：每个人「哪天要干什么」的那张清单。

为什么要有这一份：规划书里那句"今天该干什么"要能回答，前提是助手手里有一份**属于这个
人**的、跨会话活得下来的安排。它此前不存在——聊天里说"我明天要交周报"只有两种下场，
要么被当成一次性上下文（下一轮就没了），要么进向量库（那里存的是"关于这个人的事实"，
检索按相似度排序，答不出"今天有什么"这种按日期 exact 的问题）。所以这是一份独立存储，
不是给记忆库再加一个 kind。

形状：**按天分组的有序清单**，一天一组，一组若干条 `{id, text, at, done}`。刻意不做
的是这些：提醒推送（服务端没有任何出站推送通道，做出来只会是一个永远不响的铃铛）、
重复日程（`RRULE` 那一整套的复杂度与它的实际用法不成比例）、跨天时区的自动换算
（`at` 就是"本地几点"，谁填的谁负责它的意思）。

一处必须留字的边界：**归属人只从凭据里来**。HTTP 层用 `principal.user_id`，工具层用
执行器注入的那份——两个入口都不接受调用方自带的 user_id，否则"读谁的日程"就变成
模型或前端说了算。这条与 memory/session/usage 是同一个口径。
"""
import json
import os
import re
import secrets
import threading
from datetime import datetime

from app.core.paths import data_file, ensure_parent

MAX_ITEMS_PER_DAY = 40
MAX_TEXT_CHARS = 200

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_lock = threading.RLock()
_plans: dict = {}
_PATH: str = ""


class ScheduleError(ValueError):
    """请求本身不成立（日期格式、文字为空、超出上限……）。

    继承 ValueError 是为了让两条出口都能接住：HTTP 层直接转 400，工具层由
    `execute_tool` 的兜底 except 包成失败响应回灌给模型。
    """


def _default_path() -> str:
    return data_file("SCHEDULE_DB_PATH", "schedule.json")


def today() -> str:
    """服务端本地日期的 `YYYY-MM-DD`。

    "今天"以服务端为准而不是以浏览器为准：手机在别的网络里改了自己系统的日期，
    不该让它问出来的"今天"变成别人的那一天。
    """
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def normalize_day(day=None) -> str:
    """空 = 今天；非空必须恰好是 `YYYY-MM-DD`。

    宽松一点就是"传什么都给一份空清单"，那既不报错也看不出自己问错了——与
    `/v1/admin/usage` 的 `day` 同一个理由、同一份判据。
    """
    if day is None or not str(day).strip():
        return today()
    text = str(day).strip()
    if not _DAY_RE.match(text):
        raise ScheduleError("日期要写成 YYYY-MM-DD")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as e:
        raise ScheduleError(f"日期不存在：{text}") from e
    return text


def clean_text(text) -> str:
    stripped = str(text if text is not None else "").strip()
    if not stripped:
        raise ScheduleError("事项不能是空的")
    if len(stripped) > MAX_TEXT_CHARS:
        raise ScheduleError(f"一条事项最多 {MAX_TEXT_CHARS} 字，现在 {len(stripped)} 字")
    return stripped


def clean_at(at) -> str:
    """`at` 只收 24 小时的 `HH:MM`，或者干脆没有（留空 = "今天找时间做"）。"""
    if at is None or not str(at).strip():
        return ""
    text = str(at).strip()
    if not _TIME_RE.match(text):
        raise ScheduleError(f"时间要写成 24 小时的 HH:MM，收到 {text!r}")
    return text


def _blank_item(text: str, at: str = "") -> dict:
    return {"id": secrets.token_hex(4), "text": text, "at": at, "done": False}


def restore(path: str = None) -> int:
    """从盘上读回来（启动时一次；测试用它等价于"重启进程"）。返回已有条数。

    读不懂的文件改名 `.corrupt` 留着，而不是静默当空库——与 usage/auth 同一套：
    "数据坏了"必须是可诊断的事件，不是"我的日程凭空消失了"。
    """
    global _plans, _PATH
    target = os.path.abspath(path or _default_path())
    with _lock:
        _PATH = target
        _plans = {}
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return 0
        except (ValueError, OSError) as e:
            backup = target + ".corrupt"
            try:
                os.replace(target, backup)
                print(f"⚠️ 日程读不了（{e}），已备份为 {backup}，本轮从空清单开始")
            except OSError:
                print(f"⚠️ 日程读不了且无法备份（{e}），本轮从空清单开始")
            return 0
        plans = (data or {}).get("plans") or {}
        kept = 0
        for uid, per_day in (plans or {}).items():
            for day, items in (per_day or {}).items():
                rows = []
                for raw in (items or []):
                    row = _sanitise(raw)
                    if row:
                        rows.append(row)
                if rows:
                    _plans.setdefault(str(uid), {})[str(day)] = rows
                    kept += len(rows)
        return kept


def _sanitise(raw) -> dict:
    """把盘上读来的一行收成界面与工具能assure的形状。"""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        return None
    at = str(raw.get("at") or "")
    return {
        "id": str(raw.get("id") or secrets.token_hex(4)),
        "text": text,
        "at": at if _TIME_RE.match(at) else "",
        "done": bool(raw.get("done")),
    }


def _write() -> None:
    path = _PATH or _default_path()
    ensure_parent(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"plans": _plans}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _require(user_id: str) -> str:
    uid = str(user_id or "").strip()
    if not uid:
        raise ScheduleError("日程必须有归属人：user_id 不能为空")
    return uid


def plan(user_id: str, day: str = None) -> list:
    """某一天这张清单的副本（改它不会动到存储）。"""
    uid = _require(user_id)
    with _lock:
        rows = _plans.get(uid, {}).get(normalize_day(day), [])
        return [dict(r) for r in rows]


def days_for(user_id: str) -> list:
    """这个人有过安排的那些天，升序。"""
    uid = _require(user_id)
    with _lock:
        return sorted(_plans.get(uid, {}))


def set_plan(user_id: str, items, day: str = None) -> list:
    """整天的清单一次替换。

    为什么是"整份替换"而不是逐条增删的 HTTP 接口：前端的编辑态本来就握着全天那份，
    一次 PUT 是幂等的（重放同一份请求结果相同），而"加一条/删一条/挪一位"三件套要在
    并发下互相对账。逐条的口子留给工具那侧——模型不该为了加一件事先读回全天再写回。
    """
    uid = _require(user_id)
    target = normalize_day(day)
    rows = []
    for raw in (items or []):
        if not isinstance(raw, dict):
            raise ScheduleError("每一项都要是对象：{text, at?, done?}")
        item = _blank_item(clean_text(raw.get("text")), clean_at(raw.get("at")))
        item["done"] = bool(raw.get("done"))
        rows.append(item)
    if len(rows) > MAX_ITEMS_PER_DAY:
        raise ScheduleError(f"一天最多 {MAX_ITEMS_PER_DAY} 条，现在 {len(rows)} 条")
    with _lock:
        _plans.setdefault(uid, {})[target] = rows
        _write()
        return [dict(r) for r in rows]


def add_item(user_id: str, text: str, at: str = "", day: str = None) -> dict:
    """往某天追加一条。返回加进去的那一条（带 id），否则模型只能说"我加了"而说不出加了哪条。"""
    uid = _require(user_id)
    target = normalize_day(day)
    item = _blank_item(clean_text(text), clean_at(at))
    with _lock:
        rows = _plans.setdefault(uid, {}).setdefault(target, [])
        if len(rows) >= MAX_ITEMS_PER_DAY:
            raise ScheduleError(f"这一天已经排满 {MAX_ITEMS_PER_DAY} 条，先删几条再加")
        rows.append(item)
        _write()
        return dict(item)


def render_for_model(user_id: str, day: str = None) -> str:
    """给模型看的那段文本——**唯一一份**"这张清单怎么念给人听"。

    工具与任何后来的调用方都从这里取，不要在提示词里再抄一种格式。
    开头带星期几：模型要把"明天""下周三"换算成 `YYYY-MM-DD` 才有依据，而它从提示词里
    拿不到今天几号（persona 那一节还没定稿，见 docs/项目规划书 的 A-1 待拍）。
    """
    target = normalize_day(day)
    weekday = _WEEKDAYS[datetime.strptime(target, "%Y-%m-%d").weekday()]
    rows = plan(user_id, target)
    if not rows:
        return f"{target} {weekday}：没有安排。"
    lines = [f"{target} {weekday}："]
    for row in rows:
        mark = "已做完" if row["done"] else "待做"
        at = row["at"] + " " if row["at"] else ""
        lines.append(f"- {at}{row['text']}（{mark}，id {row['id']}）")
    done = sum(1 for r in rows if r["done"])
    lines.append(f"共 {len(rows)} 条，已做完 {done} 条。")
    return "\n".join(lines)


restore()
