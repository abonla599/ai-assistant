from fastapi import FastAPI
from pydantic import BaseModel  # 用于定义请求体格式
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
    # 现在先不管模型，直接返回一个假回复
    user_msg = request.messages[-1]["content"]  # 取最后一条用户说的话
    fake_response = f"你刚才说：{user_msg}，我是AI，你好！"
    return {"reply": fake_response}

# 这段代码只有在你直接运行这个文件时才执行，启动服务器
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
