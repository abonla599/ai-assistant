import sys
import os

# 将 backend 目录添加到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

# 导入记忆路由
from app.memory.memory_router import router as memory_router

app = FastAPI(
    title="AI 智能助手 API",
    description="多模型、工具调用、记忆管理的智能助手系统",
    version="1.0.0"
)

# 注册记忆管理路由
app.include_router(memory_router)


# ============ 原有的聊天模型 ============

class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: List[dict]
    temperature: Optional[float] = 0.7


@app.post("/v1/chat")
async def chat(request: ChatRequest):
    return {"reply": "这是聊天接口占位", "message_id": "msg_001"}


@app.get("/")
async def root():
    return {"message": "AI 智能助手 API 运行中"}


# 启动命令
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)