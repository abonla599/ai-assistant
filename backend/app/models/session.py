from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid


class SessionMeta(BaseModel):
    """会话元数据"""
    session_id: str
    title: str
    created_at: str
    model: str


class SessionData(BaseModel):
    """完整会话数据"""
    session_id: str
    title: str
    created_at: str
    model: str
    messages: List[Dict[str, Any]] = []


class ChatRequest(BaseModel):
    """聊天请求（扩展支持 session_id）"""
    model: str
    messages: List[Dict[str, str]]
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应"""
    reply: str
    message_id: str
    session_id: Optional[str] = None