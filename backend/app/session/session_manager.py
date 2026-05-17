"""
会话管理器 - 基于内存存储（开发阶段使用，后续可迁移到数据库）
"""
import uuid
from datetime import datetime
from typing import Dict, Optional, List
from backend.app.models.session import SessionData


class SessionManager:
    """
    内存级会话存储
    注意：服务重启后数据丢失，仅用于开发和演示
    """

    def __init__(self):
        # session_id -> SessionData
        self._store: Dict[str, dict] = {}

    def create_session(self, model: str = "deepseek-chat") -> dict:
        """
        创建新会话
        返回会话元数据
        """
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        session = {
            "session_id": session_id,
            "title": "新对话",
            "created_at": now,
            "model": model,
            "messages": []
        }
        self._store[session_id] = session
        return {
            "session_id": session_id,
            "title": "新对话",
            "created_at": now,
            "model": model
        }

    def list_sessions(self) -> List[dict]:
        """
        获取所有会话列表（按时间倒序）
        """
        result = []
        for sid, data in self._store.items():
            result.append({
                "session_id": sid,
                "title": data["title"],
                "created_at": data["created_at"],
                "model": data["model"]
            })
        # 按创建时间倒序排列
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    def get_session(self, session_id: str) -> Optional[dict]:
        """
        获取指定会话的完整数据（包括消息列表）
        """
        return self._store.get(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        """
        向指定会话添加一条消息
        返回是否成功
        """
        if session_id not in self._store:
            return False

        self._store[session_id]["messages"].append({
            "role": role,
            "content": content
        })

        # 自动更新标题（取第一条用户消息的前20个字符）
        if len(self._store[session_id]["messages"]) <= 2:
            title = content[:20] if len(content) > 20 else content
            self._store[session_id]["title"] = title

        return True

    def delete_session(self, session_id: str) -> bool:
        """
        删除指定会话
        """
        if session_id in self._store:
            del self._store[session_id]
            return True
        return False

    def session_exists(self, session_id: str) -> bool:
        """
        检查会话是否存在
        """
        return session_id in self._store


# 全局单例
session_manager = SessionManager()