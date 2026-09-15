"""会话持久化存储：JSON 落盘，进程重启后历史不丢失。

uvicorn 的同步端点在线程池中执行，因此读写由一把锁保护；写入走临时文件 +
os.replace 原子替换，避免进程中途退出留下半截 JSON。
"""
import json
import os
import sys
import threading
import uuid
from datetime import datetime


def _default_path() -> str:
    env_path = os.getenv("SESSION_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, "data", "sessions.json")


class SessionStore:
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

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def create(self, model: str = "deepseek-chat") -> dict:
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id,
                "title": "新对话",
                "created_at": now,
                "model": model,
                "messages": [],
            }
            self._flush()
        return {"session_id": session_id, "created_at": now}

    def list_summaries(self) -> list:
        with self._lock:
            summaries = [
                {
                    "session_id": sid,
                    "title": data.get("title", "新对话"),
                    "created_at": data.get("created_at", ""),
                    "model": data.get("model", ""),
                }
                for sid, data in self._sessions.items()
            ]
        summaries.sort(key=lambda x: x["created_at"], reverse=True)
        return summaries

    def get(self, session_id: str):
        with self._lock:
            data = self._sessions.get(session_id)
            return json.loads(json.dumps(data)) if data else None

    def add_message(self, session_id: str, role: str, content: str,
                    message_id: str = None) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            entry = {"role": role, "content": content}
            if message_id:
                entry["message_id"] = message_id
            session["messages"].append(entry)
            if len(session["messages"]) == 1 and content:
                session["title"] = content[:20]
            self._flush()
            return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            self._flush()
            return True
