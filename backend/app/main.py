import sys
import os
import uuid
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

# ---------- 路径设置 ----------
# 确保项目根目录 (backend) 在路径中，以便支持 from app.xxx import xxx
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # 指向 backend/
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ---------- 可选导入：张恒旭的后台任务模块 ----------

# 1. auto_weight_adjuster (假设它在 app 目录下)
try:
    from app import auto_weight_adjuster
except ImportError:
    auto_weight_adjuster = None
    print("⚠️ auto_weight_adjuster 未安装，实时反馈监听不可用")

# 2. preference_analyzer 和 memory_weight_updater (假设它们在 app 目录下)
try:
    from app import preference_analyzer
    from app import memory_weight_updater
    HAS_BG_TASKS = True
except ImportError as e:
    HAS_BG_TASKS = False
    preference_analyzer = None
    memory_weight_updater = None
    print(f"⚠️ 偏好分析/记忆更新模块未找到: {e}")

# 3. feedback_storage 和 analyze_and_update_preference (假设它们在 app 目录下)
# 注意：如果 preference_analyzer 已经在上面导入成功，这里可以直接从 app.preference_analyzer 导入函数
try:
    from app.feedback_storage import save_feedback
    # 如果 preference_analyzer 模块存在，从中导入具体函数
    if preference_analyzer:
        from app.preference_analyzer import analyze_and_update_preference
    else:
        analyze_and_update_preference = None
except ImportError as e:
    save_feedback = None
    analyze_and_update_preference = None
    print(f"⚠️ 反馈存储/偏好分析模块未找到: {e}")

# ---------- 其他核心导入 ----------
try:
    from app.core.llm_client import get_llm_response
    from app.pipeline import ChatPipeline
    USE_PIPELINE = True
except ImportError:
    USE_PIPELINE = False

try:
    from app.agents.orchestrator import Orchestrator
    orchestrator = Orchestrator(model="deepseek-chat")
except ImportError:
    orchestrator = None

try:
    from app.agents.task_store import task_store, get_task, TaskStatus
except ImportError:
    task_store = {}
    get_task = None
    TaskStatus = None

# ... (其余代码保持不变) ...

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    start_background_scheduler()
    yield
    # 关闭时执行（如果需要清理资源）
    print("服务正在关闭...")

# 记忆模块路由（包含完整的 /v1/memory/* 端点）
from app.memory.memory_router import router as memory_router

# PWA 前端（手机浏览器访问 /app 即可使用，与 API 同源）
from app.web.web_router import mount_pwa

# ---------- 创建 FastAPI 应用 ----------
app = FastAPI(
    title="AI 智能助手",
    description="多模型、工具调用、记忆管理的智能助手系统",
    version="1.0.0",
    lifespan=lifespan  # 注册 lifespan
)

# 注册记忆路由（优先使用 Router 中的端点）
app.include_router(memory_router)

# 注册 PWA 前端。挂载在 /app 下，接口仍走 /v1/*，两者互不干扰。
mount_pwa(app)

# ---------- 数据模型 ----------
class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: list[dict]
    session_id: Optional[str] = None

class FeedbackRequest(BaseModel):
    message_id: str
    rating: int
    comment: Optional[str] = None

class AgentRequest(BaseModel):
    task: str
    max_turns: Optional[int] = 10
    max_duration: Optional[int] = 120

class OrchestrateRequest(BaseModel):
    goal: str
    task_id: Optional[str] = None

# ---------- 会话存储 ----------
from app.session.session_store import SessionStore

sessions_store = SessionStore()

# ---------- 后台定时任务 ----------
def run_scheduler():
    while True:
        try:
            print("--- 开始执行周期性后台任务 ---")
            if HAS_BG_TASKS:
                if preference_analyzer:
                    preference_analyzer.analyze_and_update_preference()
                if memory_weight_updater:
                    memory_weight_updater.update_memory_weights_from_feedback()
            print("--- 周期性后台任务执行完毕 ---")
        except Exception as e:
            print(f"后台任务执行出错: {e}")
        time.sleep(300)

def start_background_scheduler():
    # 启动偏好分析线程
    bg_thread = threading.Thread(target=run_scheduler, daemon=True)
    bg_thread.start()
    print("🚀 后台偏好分析定时任务已启动")

    # 启动反馈文件监听器（仅当 auto_weight_adjuster 可用时）
    if auto_weight_adjuster is not None:
        watcher_thread = threading.Thread(
            target=auto_weight_adjuster.start_feedback_watcher,
            daemon=True
        )
        watcher_thread.start()
        print("🔁 实时反馈闭环监听器已启动！")
    else:
        print("⚠️ 实时反馈监听未启动（缺少 auto_weight_adjuster 或 watchdog）")



# ---------- API 端点 ----------
@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "AI 智能助手",
        "version": "1.0.0",
        "default_model": "deepseek-chat"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# ---------- 聊天接口 ----------
@app.post("/v1/chat")
async def chat(request: ChatRequest):
    user_msg = request.messages[-1]["content"]
    if USE_PIPELINE:
        try:
            user_id = "default_user"
            pipeline = ChatPipeline(user_id=user_id)
            result = pipeline.process(request.model, request.messages)
            reply = result.get("reply", "抱歉，处理出错")
        except Exception as e:
            reply = f"处理出错: {str(e)}"
    else:
        try:
            reply = get_llm_response(
                model=request.model,
                messages=request.messages,
                temperature=0.7
            )
        except Exception:
            reply = f"你刚才说：{user_msg}，我是AI，你好！"
    
    message_id = str(uuid.uuid4())
    if request.session_id:
        sessions_store.add_message(request.session_id, "user", user_msg)
        sessions_store.add_message(request.session_id, "assistant", reply, message_id)
    return {"reply": reply, "message_id": message_id}

# ---------- 流式聊天接口 ----------
from fastapi.responses import StreamingResponse
import json as json_module

@app.post("/v1/chat/stream")
async def stream_chat_endpoint(request: ChatRequest):
    """流式聊天端点，返回 Server-Sent Events"""
    from app.core.streaming import stream_chat
    
    async def generate():
        message_id = str(uuid.uuid4())
        # 发送开始事件
        yield f"data: {json_module.dumps({'type': 'start', 'message_id': message_id})}\n\n"

        # 用户消息先落盘：模型调用失败时也不该让用户刚发的话凭空消失
        if request.session_id:
            sessions_store.add_message(
                request.session_id, "user", request.messages[-1]["content"])

        full_text = ""
        try:
            async for chunk in stream_chat(request.model, request.messages):
                full_text += chunk
                yield f"data: {json_module.dumps({'type': 'content', 'text': chunk})}\n\n"
            
            # 保存助手回复到会话（如果提供了 session_id）
            if request.session_id:
                sessions_store.add_message(
                    request.session_id, "assistant", full_text, message_id)
            
            # 发送完成事件
            yield f"data: {json_module.dumps({'type': 'done', 'full_text': full_text, 'message_id': message_id})}\n\n"
        except Exception as e:
            yield f"data: {json_module.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")

# ---------- 会话管理 ----------
@app.post("/v1/sessions")
async def create_session(model: str = "deepseek-chat"):
    return sessions_store.create(model)

@app.get("/v1/sessions")
async def list_sessions():
    return {"sessions": sessions_store.list_summaries()}

@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    session = sessions_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session

@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    if sessions_store.delete(session_id):
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="会话不存在")

# ---------- 模型列表 ----------
@app.get("/v1/models")
async def list_models():
    return {
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "description": "快速、高性价比"},
            {"id": "gpt-4o", "name": "GPT-4o", "description": "多模态、高质量"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "基础经济型"}
        ],
        "default": "deepseek-chat"
    }

# ---------- 反馈 ----------
@app.post("/v1/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    try:
        if save_feedback:
            save_feedback(feedback.message_id, feedback.rating, feedback.comment or "")
        return {"status": "success", "message": "反馈提交成功"}
    except Exception as e:
        print(f"反馈保存异常: {e}")
        return {"status": "error", "message": f"提交失败: {str(e)}"}

# ---------- 智能体 ----------
@app.post("/v1/agent/run")
async def run_agent(request: AgentRequest):
    try:
        from app.agents.react_agent import ReActAgent
        agent = ReActAgent(
            model="deepseek-chat",
            max_turns=request.max_turns
        )
        result = agent.run(
            task=request.task,
            max_duration=request.max_duration
        )
        return {"result": result}
    except ImportError:
        return {"result": "智能体模块尚未就绪，请稍后再试"}

@app.post("/v1/agent/orchestrate")
async def orchestrate_task(request: OrchestrateRequest):
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器模块尚未就绪")
    result = orchestrator.run(
        goal=request.goal,
        task_id=request.task_id
    )
    return result

# ---------- 任务状态 ----------
@app.get("/v1/tasks/{task_id}")
async def get_task_status(task_id: str):
    if get_task is None:
        raise HTTPException(status_code=503, detail="任务存储模块尚未就绪")
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    response = {
        "task_id": task.task_id,
        "goal": task.goal,
        "status": task.status,
        "current_subtask": task.current_subtask,
        "total_subtasks": len(task.subtasks),
        "subtasks": task.subtasks,
        "results": task.results if task.status == "completed" else None,
        "final_answer": task.final_answer,
        "error": task.error,
        "created_at": task.created_at,
        "cancelled": task.cancelled
    }
    if len(task.subtasks) > 0:
        response["progress_percent"] = round(
            (task.current_subtask / len(task.subtasks)) * 100, 1
        )
    else:
        response["progress_percent"] = 0
    return response

@app.get("/v1/tasks")
async def list_all_tasks():
    if task_store is None:
        return {"total": 0, "tasks": []}
    tasks = list(task_store.values())
    return {
        "total": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "goal": t.goal[:50] + "..." if len(t.goal) > 50 else t.goal,
                "status": t.status,
                "progress": f"{t.current_subtask}/{len(t.subtasks)}",
                "created_at": t.created_at
            }
            for t in tasks
        ]
    }

@app.post("/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    task = task_store[task_id]
    if TaskStatus and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
        return {
            "status": "warning",
            "task_id": task_id,
            "message": f"任务已处于终态: {task.status.value}，无需取消"
        }
    task.mark_cancelled()
    return {
        "status": "cancelled",
        "task_id": task_id,
        "message": "任务已标记为取消，将在当前子任务完成后停止"
    }

@app.delete("/v1/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id in task_store:
        del task_store[task_id]
        return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

# ---------- 启动入口 ----------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)