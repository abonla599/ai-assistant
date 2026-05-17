import sys
import os
import threading
import time
from fastapi import FastAPI
from pydantic import BaseModel

# 修复Python模块搜索路径，彻底根治导入问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ✅ 全局统一导入，杜绝函数内局部导入引发的崩溃
from backend.app.feedback_storage import save_feedback
from backend.app.preference_analyzer import analyze_and_update_preference

# 创建FastAPI应用
app = FastAPI(title="AI 智能助手")


# ---------------------- 数据模型定义（解决422参数错误） ----------------------
class ChatRequest(BaseModel):
    model: str
    messages: list[dict]


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int
    comment: str


# ---------------------- 后台定时偏好分析任务（不阻塞主线程） ----------------------
def run_scheduler():
    while True:
        try:
            analyze_and_update_preference()
            print("✅ 定时用户偏好分析执行完成")
        except Exception as e:
            print(f"❌ 后台定时任务出错: {e}")
        # 每5分钟(300秒)执行一次
        time.sleep(300)


def start_background_scheduler():
    # 守护线程，主服务关闭自动跟着退出
    bg_thread = threading.Thread(target=run_scheduler, daemon=True)
    bg_thread.start()
    print("🚀 后台偏好分析定时任务已启动")


# 服务启动钩子，服务完全就绪后再开启后台任务
@app.on_event("startup")
async def init_app():
    start_background_scheduler()


# ---------------------- 核心业务API接口 ----------------------
# 聊天对话接口
@app.post("/v1/chat")
async def chat(request: ChatRequest):
    user_msg = request.messages[-1]["content"]
    fake_response = f"你刚才说: {user_msg}，我是AI，你好！"
    return {"reply": fake_response}


# 用户反馈提交接口（彻底修复500/422报错）
@app.post("/v1/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    try:
        # 调用保存反馈的函数
        save_feedback(feedback.message_id, feedback.rating, feedback.comment)
        return {
            "code": 200,
            "status": "success",
            "message": "反馈提交成功"
        }
    except Exception as e:
        print(f"反馈保存异常: {e}")
        return {
            "code": 500,
            "status": "error",
            "message": f"提交失败: {str(e)}"
        }


# ---------------------- 本地直接运行入口 ----------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)