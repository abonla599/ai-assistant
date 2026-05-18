import sys
import os
import threading
import time
import auto_weight_adjuster
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
    # 守护线程，主服务关闭自动跟着退出
    bg_thread = threading.Thread(target=run_scheduler, daemon=True)
    bg_thread.start()
    print("🚀 后台偏好分析定时任务已启动")

def start_background_scheduler():
    def run_scheduler():
        while True:
            try:
                print("--- 开始执行周期性后台任务 ---")
                preference_analyzer.analyze_and_update_preference()
                memory_weight_updater.update_memory_weights_from_feedback()
                print("--- 周期性后台任务执行完毕 ---")
            except Exception as e:
                print(f"后台任务执行出错: {e}")
            time.sleep(300)
    # 启动周期性任务线程
    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()
    print("🚀 后台偏好分析器已启动，将每5分钟运行一次。")

    # 启动反馈文件监听器（关键！）
    watcher_thread = threading.Thread(target=auto_weight_adjuster.start_feedback_watcher, daemon=True)
    watcher_thread.start()
    print("🔁 实时反馈闭环监听器已启动！")


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
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import uuid
from datetime import datetime

# ==================== 创建 FastAPI 应用实例 ====================
app = FastAPI(title="AI 智能助手")

# ==================== 导入核心模块 ====================

# 导入 ChatPipeline（来自 develop 分支）
try:
    from app.core.llm_client import get_llm_response
    from app.pipeline import ChatPipeline
    USE_PIPELINE = True
except ImportError:
    USE_PIPELINE = False

# 导入智能体模块
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

try:
    from app.memory.memory_manager import MemoryManager
    memory_manager = MemoryManager()
except ImportError:
    memory_manager = None


# ==================== 数据模型定义 ====================

class ChatRequest(BaseModel):
    """聊天请求体"""
    model: str = "deepseek-chat"
    messages: list[dict]
    session_id: Optional[str] = None


class MemoryAddRequest(BaseModel):
    """添加记忆请求体"""
    user_id: str
    content: str
    metadata: Optional[dict] = None


class MemorySearchRequest(BaseModel):
    """搜索记忆请求体"""
    user_id: str
    query: str
    top_k: int = 5


class FeedbackRequest(BaseModel):
    """用户反馈请求体"""
    message_id: str
    rating: int
    comment: Optional[str] = None


class AgentRequest(BaseModel):
    """智能体任务请求体"""
    task: str
    max_turns: Optional[int] = 10
    max_duration: Optional[int] = 120


class OrchestrateRequest(BaseModel):
    """编排器任务请求体"""
    goal: str
    task_id: Optional[str] = None


# ==================== 内存中的会话存储 ====================
sessions_store = {}


# ==================== API 端点 ====================

@app.get("/")
async def root():
    """根路径"""
    return {
        "status": "running",
        "service": "AI 智能助手",
        "version": "1.0.0",
        "default_model": "deepseek-chat"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


# --- 聊天接口 ---

@app.post("/v1/chat")
async def chat(request: ChatRequest):
    """聊天接口"""
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
            from app.core.llm_client import get_llm_response
            reply = get_llm_response(
                model=request.model,
                messages=request.messages,
                temperature=0.7
            )
        except ImportError:
            reply = f"你刚才说：{user_msg}，我是AI，你好！"
    
    message_id = str(uuid.uuid4())
    
    if request.session_id and request.session_id in sessions_store:
        session = sessions_store[request.session_id]
        session["messages"].append(request.messages[-1])
        session["messages"].append({"role": "assistant", "content": reply})
        if len(session["messages"]) <= 2:
            title = user_msg[:20]
            session["title"] = title if title else "新对话"
    
    return {
        "reply": reply,
        "message_id": message_id
    }


# --- 会话管理 ---

@app.post("/v1/sessions")
async def create_session(model: str = "deepseek-chat"):
    """创建新会话"""
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
    """获取所有会话列表"""
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
    """获取特定会话"""
    if session_id not in sessions_store:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sessions_store[session_id]


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    if session_id in sessions_store:
        del sessions_store[session_id]
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="会话不存在")


# --- 模型列表 ---

@app.get("/v1/models")
async def list_models():
    """获取可用模型列表"""
    return {
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "description": "快速、高性价比"},
            {"id": "gpt-4o", "name": "GPT-4o", "description": "多模态、高质量"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "基础经济型"}
        ],
        "default": "deepseek-chat"
    }


# --- 记忆相关 ---

@app.post("/v1/memory/add")
async def add_memory(request: MemoryAddRequest):
    """添加记忆"""
    if memory_manager is None:
        return {"status": "error", "message": "记忆模块尚未就绪"}
    memory_manager.add_memory(
        user_id=request.user_id,
        content=request.content,
        metadata=request.metadata
    )
    return {"status": "added"}


@app.post("/v1/memory/search")
async def search_memory(request: MemorySearchRequest):
    """搜索记忆"""
    if memory_manager is None:
        return {"status": "error", "message": "记忆模块尚未就绪", "results": []}
    results = memory_manager.search_memory(
        user_id=request.user_id,
        query=request.query,
        top_k=request.top_k
    )
    return {"results": results}


# --- 反馈 ---

@app.post("/v1/feedback")
async def submit_feedback(request: FeedbackRequest):
    """提交用户反馈"""
    print(f"[Feedback] message_id={request.message_id}, rating={request.rating}, comment={request.comment}")
    if request.comment:
        try:
            from app.growth.feedback_processor import adjust_memory_weight
            adjust_memory_weight(request.comment, request.rating)
        except ImportError:
            pass
    return {"status": "received"}


# --- 智能体 ---

@app.post("/v1/agent/run")
async def run_agent(request: AgentRequest):
    """运行ReAct智能体"""
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
    """通过编排器执行复杂任务，支持断点续传"""
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器模块尚未就绪")
    result = orchestrator.run(
        goal=request.goal,
        task_id=request.task_id
    )
    return result


# --- 任务状态查询 ---

@app.get("/v1/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务执行状态"""
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
    """列出所有任务"""
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


# ⭐⭐⭐ 新增：任务取消端点 ⭐⭐⭐
@app.post("/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消正在执行的任务"""
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    
    task = task_store[task_id]
    
    # 检查任务是否已处于终态
    if TaskStatus and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
        return {
            "status": "warning",
            "task_id": task_id,
            "message": f"任务已处于终态: {task.status.value}，无需取消"
        }
    
    # 标记取消
    task.mark_cancelled()
    
    return {
        "status": "cancelled",
        "task_id": task_id,
        "message": "任务已标记为取消，将在当前子任务完成后停止"
    }


@app.delete("/v1/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务记录"""
    if task_id in task_store:
        del task_store[task_id]
        return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")


# ==================== 启动入口 ====================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
