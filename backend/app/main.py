import sys
import os
import uuid
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# ---------- 路径设置 ----------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------- 可选导入：张恒旭的后台任务模块 ----------
try:
    import auto_weight_adjuster
except ImportError:
    auto_weight_adjuster = None
    print("⚠️ auto_weight_adjuster 未安装，实时反馈监听不可用")

try:
    from app import preference_analyzer
    from app import memory_weight_updater
    HAS_BG_TASKS = True
except ImportError:
    HAS_BG_TASKS = False
    preference_analyzer = None
    memory_weight_updater = None
    print("⚠️ 偏好分析/记忆更新模块未找到，后台定时任务将跳过")

try:
    from backend.app.feedback_storage import save_feedback
    from backend.app.preference_analyzer import analyze_and_update_preference
except ImportError:
    save_feedback = None
    analyze_and_update_preference = None
    print("⚠️ 反馈存储/偏好分析模块未找到，反馈功能可能受限")

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

# 记忆模块路由（包含完整的 /v1/memory/* 端点）
from app.memory.memory_router import router as memory_router

# ---------- 创建 FastAPI 应用 ----------
app = FastAPI(
    title="AI 智能助手",
    description="多模型、工具调用、记忆管理的智能助手系统",
    version="1.0.0"
)

# 注册记忆路由（优先使用 Router 中的端点）
app.include_router(memory_router)

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
sessions_store = {}

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

@app.on_event("startup")
async def init_app():
    start_background_scheduler()

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
    if request.session_id and request.session_id in sessions_store:
        session = sessions_store[request.session_id]
        session["messages"].append(request.messages[-1])
        session["messages"].append({"role": "assistant", "content": reply})
        if len(session["messages"]) <= 2:
            title = user_msg[:20]
            session["title"] = title if title else "新对话"
    return {"reply": reply, "message_id": message_id}

# ---------- 会话管理 ----------
@app.post("/v1/sessions")
async def create_session(model: str = "deepseek-chat"):
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    sessions_store[session_id] = {
        "session_id": session_id,
        "title": "新对话",
        "created_at": now,
        "model": model,
        "messages": []
    }
    return {"session_id": session_id, "created_at": now}

@app.get("/v1/sessions")
async def list_sessions():
    result = []
    for sid, data in sessions_store.items():
        result.append({
            "session_id": sid,
            "title": data["title"],
            "created_at": data["created_at"],
            "model": data["model"]
        })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return {"sessions": result}

@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    if session_id not in sessions_store:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sessions_store[session_id]

@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id in sessions_store:
        del sessions_store[session_id]
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