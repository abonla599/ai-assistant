"""会话持久化存储：JSON 落盘，进程重启后历史不丢失。

uvicorn 的同步端点在线程池中执行，因此读写由一把锁保护；写入走临时文件 +
os.replace 原子替换，避免进程中途退出留下半截 JSON。

每条会话都带 owner：同一份文件里住着多个人的聊天记录，任何一次读写都必须先
证明"这条会话属于你"，否则别人的会话就跟公开目录没区别。
"""
import json
import os
import shutil
import threading
import uuid
from datetime import datetime

from app.core.paths import data_root


def _default_path() -> str:
    env_path = os.getenv("SESSION_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "sessions.json")


class SessionStore:
    # 只用于两件事：给历史记录补 owner、给 bootstrap 身份起名字。
    # 绝不作为任何方法的默认参数——那样漏传 owner 的调用点会静默以
    # default_user（也就是管理员）身份执行，而这正是本层要堵的洞。
    LEGACY_OWNER = "default_user"

    def __init__(self, path: str = None):
        self.path = os.path.abspath(path or _default_path())
        self._lock = threading.Lock()
        self._sessions = {}
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            backup = self.path + ".corrupt"
            try:
                os.replace(self.path, backup)
                print(f"⚠️ 会话文件损坏（{e}），已备份为 {backup}，从空会话开始")
            except OSError:
                print(f"⚠️ 会话文件损坏且无法备份（{e}），从空会话开始")
            return
        if isinstance(data, dict):
            self._sessions = data
        self._backfill_owner()

    def _backfill_owner(self):
        """把身份层之前建的会话认给本机管理员：它们确实都是他一个人聊出来的。

        回填只在真的缺 owner 时发生一次，字段齐了就一个字节都不写；备份或写回
        失败一律向上抛，让进程起不来。带着半迁移的库对外服务更糟——那时 owner
        校验会静默放行，而这些会话里的聊天记录是要拿来当"归属"依据的。

        ⚠️ 本方法是导入期跑的：构造 SessionStore 就等于迁移 $SESSION_DB_PATH
        （未设置时是仓库真实的 data/sessions.json）。见 main.py 里
        `sessions_store = SessionStore()` 那段注释。
        """
        # 畸形记录先报错再说：`record["owner"] = ...` 抛的是
        # "TypeError: 'str' object does not support item assignment"，冻结成 EXE
        # 之后只剩这一行、连是哪个文件都不知道，用户根本没法自助修复。
        for session_id, record in self._sessions.items():
            if not isinstance(record, dict):
                raise ValueError(
                    f"会话文件 {self.path} 中的记录 {session_id!r} 不是对象（实际是 "
                    f"{type(record).__name__}），无法补 owner。请修复或还原该文件："
                    "宁可拒绝启动，也不带着认不出归属的库对外服务。")
        # 判据是 not s.get("owner")，不是 "owner" not in s：手工改成
        # {"owner": null} 也算没迁完。放过去的话它会被归属校验当成"已有 owner"
        # 跳过，此后与任何 user_id 都不相等，这条会话就永久隐身了。
        missing = [s for s in self._sessions.values() if not s.get("owner")]
        if not missing:
            return
        # 备份与写回任何一步失败都直接向上抛：SessionStore 在导入 app.main 时
        # 构造，异常会让进程启动失败——这正是我们要的失败方式。
        backup = f"{self.path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(self.path, backup)
        for record in missing:
            record["owner"] = self.LEGACY_OWNER
        self._flush()
        # 把数据文件的绝对路径打进日志：这次迁移动的是不是用户真实的 data/，
        # 看一眼输出的第一秒就知道，不用等事后去比对 md5。
        print(f"🧭 已为 {len(missing)} 条历史会话补 owner={self.LEGACY_OWNER}"
              f"（数据文件 {self.path}），原件备份于 {backup}")

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _entry(role, content, message_id=None, memory_ids=None):
        """规范化单条消息；形状不对就返回 None，由调用方决定丢弃还是拒绝。"""
        if not isinstance(role, str) or not isinstance(content, str):
            return None
        entry = {"role": role, "content": content}
        if message_id:
            entry["message_id"] = str(message_id)
        # 必须保留，否则客户端一次整体回写就会让该条回答失去反馈效力
        if memory_ids:
            entry["memory_ids"] = [str(m) for m in memory_ids]
        return entry

    @classmethod
    def _clean(cls, messages: list) -> list:
        """整份回写的逐条清洗。逻辑与 _entry 同源，两处各自演化迟早对不上。"""
        cleaned = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            entry = cls._entry(item.get("role"), item.get("content"),
                               item.get("message_id"), item.get("memory_ids"))
            if entry is not None:
                cleaned.append(entry)
        return cleaned

    def create(self, model: str, owner: str) -> dict:
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id,
                "title": "新对话",
                "created_at": now,
                "model": model,
                "messages": [],
                "owner": owner,
            }
            self._flush()
        return {"session_id": session_id, "created_at": now}

    def list_summaries(self, owner: str) -> list:
        with self._lock:
            summaries = [
                {
                    "session_id": sid,
                    "title": data.get("title", "新对话"),
                    "created_at": data.get("created_at", ""),
                    "model": data.get("model", ""),
                }
                for sid, data in self._sessions.items()
                if data.get("owner") == owner
            ]
        summaries.sort(key=lambda x: x["created_at"], reverse=True)
        return summaries

    def get(self, session_id: str, owner: str):
        """只认属主；别人的会话与不存在的会话返回同一个 None，不制造枚举信道。"""
        with self._lock:
            data = self._sessions.get(session_id)
            if not data or data.get("owner") != owner:
                return None
            return json.loads(json.dumps(data))

    def add_message(self, session_id: str, owner: str, role: str, content: str,
                    message_id: str = None, memory_ids: list = None) -> bool:
        entry = self._entry(role, content, message_id, memory_ids)
        if entry is None:
            return False
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            session["messages"].append(entry)
            if len(session["messages"]) == 1 and entry["content"]:
                session["title"] = entry["content"][:20]
            self._flush()
            return True

    def find_message(self, message_id: str, owner: str):
        """按 message_id 反查，返回 {"session_id", "message"}；不属于你就当作没有。"""
        if not message_id:
            return None
        with self._lock:
            for sid, session in self._sessions.items():
                if session.get("owner") != owner:
                    continue
                for msg in session.get("messages", []):
                    if msg.get("message_id") == message_id:
                        return {"session_id": sid, "message": dict(msg)}
        return None

    def delete(self, session_id: str, owner: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            del self._sessions[session_id]
            self._flush()
            return True

    def replace(self, session_id: str, owner: str, messages: list) -> bool:
        """整体替换会话消息，使前端编辑/删除/重新生成后的视图与存储一致。"""
        cleaned = self._clean(messages)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            session["messages"] = cleaned
            first_user = next((m for m in cleaned if m["role"] == "user"), None)
            if first_user:
                session["title"] = first_user["content"][:20]
            self._flush()
            return True
