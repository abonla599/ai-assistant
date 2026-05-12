from fastapi import FastAPI
from pydantic import BaseModel  # 用于定义请求体格式
from app.core.llm_client import get_llm_response
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
    # 调用 LLM 获取真实回复
    try:
        reply_content = get_llm_response(
            model=request.model,
            messages=request.messages
        )
    except Exception as e:
        return {"error": str(e), "message_id": None}

    # 生成唯一消息 ID
    msg_id = str(uuid.uuid4())
    return {
        "reply": reply_content,
        "message_id": msg_id
    }
# 这段代码只有在你直接运行这个文件时才执行，启动服务器
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
