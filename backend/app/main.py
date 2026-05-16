import sys
import os
import threading
import time
from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from . import preference_analyzer
import memory_weight_updater

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


import memory_weight_updater  # 需要添加到文件开头的导入区域

def start_background_scheduler():
    def run_scheduler():
        while True:
            try:
                print("--- 开始执行周期性后台任务 ---")
                # 调用偏好分析器（你第二步的工作）
                preference_analyzer.analyze_and_update_preference()
                # 调用记忆权重更新器（你今天的新工作！）
                memory_weight_updater.update_memory_weights_from_feedback()
                print("--- 周期性后台任务执行完毕 ---")
            except Exception as e:
                print(f"后台任务执行出错: {e}")
            time.sleep(300)  # 5分钟


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