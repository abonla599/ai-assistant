from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import uuid
from datetime import datetime

# ==================== 创建 FastAPI 应用实例 ====================
app = FastAPI(title="AI 智能助手")

# ==================== 导入核心模块 ====================
# 注意：如果某些模块还没创建好，可以先注释掉，等对应同学完成后再取消注释

try:
    from app.agents.orchestrator import Orchestrator
    orchestrator = Orchestrator(model="deepseek-chat")
except ImportError:
    print("⚠️ Orchestrator 模块尚未就绪，智能体功能暂不可用")
    orchestrator = None

try:
    from app.agents.task_store import task_store, get_task, TaskStatus
except ImportError:
    print("⚠️ task_store 模块尚未就绪，任务状态查询功能暂不可用")
    task_store = {}
    get_task = None
    TaskStatus = None

try:
    from app.memory.memory_manager import MemoryManager
    memory_manager = MemoryManager()
except ImportError:
    print("⚠️ MemoryManager 模块尚未就绪，记忆功能暂不可用")
    memory_manager = None


# ==================== 数据模型定义 ====================

class ChatRequest(BaseModel):
    """聊天请求体"""
    model: str = "deepseek-chat"
    messages: list[dict]   # 例如 [{"role": "user", "content": "你好"}]
    session_id: Optional[str] = None  # 可选：会话ID，用于多轮对话


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
    rating: int  # 1-5分
    comment: Optional[str] = None


class AgentRequest(BaseModel):
    """智能体任务请求体"""
    task: str
    max_turns: Optional[int] = 10
    max_duration: Optional[int] = 120


class OrchestrateRequest(BaseModel):
    """编排器任务请求体"""
    goal: str
    task_id: Optional[str] = None  # 可选：用于恢复已有任务


# ==================== 内存中的会话存储 ====================
# 临时方案，后续可替换为数据库
sessions_store = {}


# ==================== API 端点 ====================

# --- 根路径和健康检查 ---

@app.get("/")
async def root():
    """根路径，返回API基本信息"""
    return {
        "status": "running",
        "service": "AI 智能助手",
        "version": "1.0.0",
        "default_model": "deepseek-chat"
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


# --- 聊天接口 ---

@app.post("/v1/chat")
async def chat(request: ChatRequest):
    """
    聊天接口
    支持多轮对话，可选会话管理
    """
    # 取最后一条用户说的话
    user_msg = request.messages[-1]["content"]
    
    # 尝试调用真实的 LLM（如果可用）
    try:
        from app.core.llm_client import get_llm_response
        reply = get_llm_response(
            model=request.model,
            messages=request.messages,
            temperature=0.7
        )
    except ImportError:
        # 如果 LLM 客户端还没准备好，使用假回复
        reply = f"你刚才说：{user_msg}，我是AI，你好！"
    
    # 生成消息ID
    message_id = str(uuid.uuid4())
    
    # 如果提供了 session_id，保存对话历史
    if request.session_id and request.session_id in sessions_store:
        session = sessions_store[request.session_id]
        session["messages"].append(request.messages[-1])  # 用户消息
        session["messages"].append({"role": "assistant", "content": reply})  # AI回复
        
        # 自动更新会话标题（取第一条用户消息的前20个字）
        if len(session["messages"]) <= 2:
            title = user_msg[:20]
            session["title"] = title if title else "新对话"
    
    return {
        "reply": reply,
        "message_id": message_id
    }


# --- 会话管理接口 ---

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
    # 按创建时间倒序排列
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return {"sessions": result}


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    """获取特定会话的详细信息和消息历史"""
    if session_id not in sessions_store:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sessions_store[session_id]


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除指定会话"""
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
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "provider": "DeepSeek"},
            {"id": "gpt-4o", "name": "GPT-4o", "provider": "OpenAI"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "provider": "OpenAI"}
        ],
        "default": "deepseek-chat"
    }


# --- 记忆相关接口 ---

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


# --- 反馈接口 ---

@app.post("/v1/feedback")
async def submit_feedback(request: FeedbackRequest):
    """提交用户反馈"""
    # 记录反馈
    print(f"[Feedback] message_id={request.message_id}, rating={request.rating}, comment={request.comment}")
    
    # 如果有评论，尝试调整记忆权重
    if request.comment:
        try:
            from app.growth.feedback_processor import adjust_memory_weight
            adjust_memory_weight(request.comment, request.rating)
        except ImportError:
            pass  # 成长模块尚未就绪，跳过
    
    return {"status": "received"}


# --- 智能体接口 ---

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
    """
    通过编排器执行复杂任务
    支持断点续传：如果提供task_id且任务未完成，将从中断处继续
    """
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器模块尚未就绪")
    
    result = orchestrator.run(
        goal=request.goal,
        task_id=request.task_id
    )
    return result


# ==================== 任务状态查询接口（刘吉洋 第四步 新增） ====================

@app.get("/v1/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    查询任务执行状态
    用于长任务的进度查询和断点续传
    
    Args:
        task_id: 任务ID（由 /v1/agent/orchestrate 接口返回）
    
    Returns:
        任务详细信息，包括：
        - task_id: 任务唯一标识
        - goal: 原始目标
        - status: 当前状态（pending/running/completed/failed）
        - current_subtask: 当前执行到第几个子任务
        - total_subtasks: 子任务总数
        - progress_percent: 完成百分比
        - final_answer: 最终答案（仅completed状态有值）
        - error: 错误信息（仅failed状态有值）
        - created_at: 任务创建时间
    """
    if get_task is None:
        raise HTTPException(status_code=503, detail="任务存储模块尚未就绪")
    
    task = get_task(task_id)
    
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"任务不存在: {task_id}"
        )
    
    # 构建返回信息
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
        "created_at": task.created_at
    }
    
    # 计算进度百分比
    if len(task.subtasks) > 0:
        response["progress_percent"] = round(
            (task.current_subtask / len(task.subtasks)) * 100, 1
        )
    else:
        response["progress_percent"] = 0
    
    return response


@app.get("/v1/tasks")
async def list_all_tasks():
    """列出所有任务（用于调试和管理）"""
    if task_store is None:
        return {"total": 0, "tasks": []}
    
    # 获取所有任务
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