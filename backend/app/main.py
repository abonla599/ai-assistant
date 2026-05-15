from fastapi import FastAPI
from pydantic import BaseModel  # 用于定义请求体格式
from app.core.llm_client import get_llm_response
from app.pipeline import ChatPipeline
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))  # 确保 backend 在 sys.path 中
import uuid
import uvicorn
# 创建 FastAPI 应用实例，就是你的后端“网站”
app = FastAPI(title="AI 智能助手")

# 定义请求体的格式：一个模型名字和一个消息列表
class ChatRequest(BaseModel):
    model: str
    messages: list[dict]   # 例如 [{"role": "user", "content": "你好"}]

# 这是一个 API 端点，POST 方法访问 /v1/chat
@app.post("/v1/chat")
async def chat(request: ChatRequest):
    # 从请求的 messages 或额外参数中获取 user_id，这里先用默认
    user_id = "default_user"  # 后续可从Header或请求体扩展
    pipeline = ChatPipeline(user_id=user_id)
    result = pipeline.process(request.model, request.messages)
    return result
# 这段代码只有在你直接运行这个文件时才执行，启动服务器
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
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
