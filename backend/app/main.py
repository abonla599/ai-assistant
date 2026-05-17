"""
FastAPI 主应用 - 第四步完整版
包含：聊天、会话管理、流式输出、模型列表
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import sys
import json
import uuid
import uvicorn
from pathlib import Path

# 确保 backend 在 sys.path 中
sys.path.append(str(Path(__file__).parent.parent.parent))

# 导入核心模块
from core.llm_client import get_llm_response
from pipeline import ChatPipeline

# ============================================================
# 创建 FastAPI 应用实例
# ============================================================
app = FastAPI(
    title="AI 智能助手",
    description="支持多模型、会话管理、流式输出的智能助手 API",
    version="1.0.0"
)

# 允许跨域（Flutter App 需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 数据模型定义
# ============================================================
class ChatRequest(BaseModel):
    """聊天请求体"""
    model: str
    messages: list[dict]  # 例如 [{"role": "user", "content": "你好"}]
    session_id: Optional[str] = None  # 新增：关联的会话 ID


class FeedbackRequest(BaseModel):
    """反馈请求体"""
    message_id: str
    rating: int  # 1-5 评分
    comment: Optional[str] = None


# ============================================================
# 简易内存会话管理器（内嵌在 main.py 中）
# ============================================================
class SessionManager:
    """
    内存级会话存储
    注意：服务重启后数据丢失，仅用于开发和演示
    """

    def __init__(self):
        self._store: Dict[str, dict] = {}

    def create_session(self, model: str = "deepseek-chat") -> dict:
        """创建新会话"""
        from datetime import datetime

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

    def list_sessions(self) -> list:
        """获取所有会话列表（按时间倒序）"""
        result = []
        for sid, data in self._store.items():
            result.append({
                "session_id": sid,
                "title": data["title"],
                "created_at": data["created_at"],
                "model": data["model"]
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取指定会话的完整数据"""
        return self._store.get(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        """向指定会话添加一条消息"""
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
        """删除指定会话"""
        if session_id in self._store:
            del self._store[session_id]
            return True
        return False

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return session_id in self._store


# 全局会话管理器实例
session_manager = SessionManager()


# ============================================================
# 简易反馈存储
# ============================================================
feedback_store: Dict[str, dict] = {}


# ============================================================
# API 端点：模型列表
# ============================================================
@app.get("/v1/models")
async def list_models():
    """返回支持的模型列表"""
    return {
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "description": "快速、高性价比"},
            {"id": "gpt-4o", "name": "GPT-4o", "description": "多模态、高质量"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "基础经济型"}
        ]
    }


# ============================================================
# API 端点：会话管理
# ============================================================
@app.post("/v1/sessions")
async def create_session(model: str = "deepseek-chat"):
    """
    创建新会话
    返回 session_id 和创建时间
    """
    result = session_manager.create_session(model)
    return {
        "success": True,
        "data": result
    }


@app.get("/v1/sessions")
async def list_sessions():
    """
    获取所有会话列表
    """
    sessions = session_manager.list_sessions()
    return {
        "success": True,
        "data": sessions,
        "total": len(sessions)
    }


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    """
    获取指定会话的完整详情（包含历史消息）
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "success": True,
        "data": session
    }


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    删除指定会话
    """
    if not session_manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在或已删除")
    return {
        "success": True,
        "message": "会话已删除"
    }


# ============================================================
# API 端点：聊天（非流式，支持 session_id）
# ============================================================
@app.post("/v1/chat")
async def chat(request: ChatRequest):
    """
    发送消息并获取 AI 回复
    如果提供了 session_id，消息会自动保存到对应会话
    """
    # 验证会话是否存在
    if request.session_id and not session_manager.session_exists(request.session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    # 初始化聊天流水线
    user_id = "default_user"  # 后续可从 Header 或请求体扩展
    pipeline = ChatPipeline(user_id=user_id)

    # 处理消息（调用你的核心逻辑）
    result = pipeline.process(request.model, request.messages)

    reply_content = result.get("reply", "")
    message_id = str(uuid.uuid4())

    # 如果提供了 session_id，保存对话历史
    if request.session_id:
        # 保存用户消息（取最后一条）
        last_user_msg = request.messages[-1]
        if last_user_msg["role"] == "user":
            session_manager.add_message(
                request.session_id,
                "user",
                last_user_msg["content"]
            )

        # 保存 AI 回复
        session_manager.add_message(
            request.session_id,
            "assistant",
            reply_content
        )

    return {
        "reply": reply_content,
        "message_id": message_id,
        "session_id": request.session_id
    }


# ============================================================
# API 端点：流式聊天（SSE）
# ============================================================
@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式聊天接口（Server-Sent Events）
    实时返回 AI 生成的文本片段，支持打字机效果
    """

    # 验证会话是否存在
    if request.session_id and not session_manager.session_exists(request.session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    async def event_generator():
        """
        SSE 事件生成器
        逐个产出 text/event-stream 格式的数据
        """
        full_response = ""

        # 发送开始事件
        yield f"data: {json.dumps({'type': 'start', 'session_id': request.session_id}, ensure_ascii=False)}\n\n"

        try:
            # 初始化聊天流水线（获取完整回复）
            user_id = "default_user"
            pipeline = ChatPipeline(user_id=user_id)
            result = pipeline.process(request.model, request.messages)
            reply_content = result.get("reply", "")

            # 模拟流式输出：逐字发送（每次 2-3 个字符）
            i = 0
            chunk_size = 3
            while i < len(reply_content):
                chunk = reply_content[i:i + chunk_size]
                i += chunk_size
                full_response += chunk
                yield f"data: {json.dumps({'type': 'content', 'text': chunk}, ensure_ascii=False)}\n\n"

            # 发送完成事件
            yield f"data: {json.dumps({'type': 'done', 'full_response': full_response}, ensure_ascii=False)}\n\n"

            # 如果有 session_id，保存对话
            if request.session_id:
                last_user_msg = request.messages[-1]
                if last_user_msg["role"] == "user":
                    session_manager.add_message(
                        request.session_id,
                        "user",
                        last_user_msg["content"]
                    )
                session_manager.add_message(
                    request.session_id,
                    "assistant",
                    full_response
                )

        except Exception as e:
            # 发送错误事件
            error_msg = f"流式输出错误: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲（部署时需要）
        }
    )


# ============================================================
# API 端点：反馈
# ============================================================
@app.post("/v1/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """
    提交消息反馈（点赞/踩）
    """
    feedback_store[feedback.message_id] = {
        "rating": feedback.rating,
        "comment": feedback.comment,
        "message_id": feedback.message_id
    }
    print(f"[反馈] message_id={feedback.message_id}, rating={feedback.rating}, comment={feedback.comment}")

    return {
        "success": True,
        "message": "感谢您的反馈！"
    }


# ============================================================
# 健康检查
# ============================================================
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "service": "AI 智能助手",
        "version": "1.0.0"
    }


# ============================================================
# 启动服务器
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("🚀 AI 智能助手后端启动中...")
    print(f"📡 API 文档: http://0.0.0.0:8000/docs")
    print(f"🔍 Swagger: http://0.0.0.0:8000/redoc")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)