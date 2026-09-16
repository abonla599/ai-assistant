import sys
import os

# 中文 Windows 的控制台默认 GBK，打包成 EXE 后任何 emoji 日志都会抛
# UnicodeEncodeError 并在导入阶段终止进程（开发终端能显示 UTF-8，所以看不出来）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import secrets
import uuid
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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
except ImportError as e:
    USE_PIPELINE = False
    print(f"❌ ChatPipeline 不可用，记忆注入与工具调用已失效（非流式对话将直接调用模型）: {e}")

try:
    from app.agents.orchestrator import Orchestrator
    orchestrator = Orchestrator(model="deepseek-chat")
except ImportError as e:
    orchestrator = None
    print(f"⚠️ 编排器不可用，/v1/agent/orchestrate 等端点将返回 503: {e}")

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

# ---------- 访问鉴权 ----------
# 服务经隧道暴露到公网时，没有口令就等于给出不记名的模型调用代理。
# 未设置 ACCESS_TOKEN 时保持开放，兼容本机开发与测试。
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
_PROTECTED_PREFIXES = ("/v1/", "/docs", "/redoc", "/openapi.json")

@app.middleware("http")
async def require_access_token(request, call_next):
    if not ACCESS_TOKEN:
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith(_PROTECTED_PREFIXES):
        return await call_next(request)

    supplied = request.headers.get("authorization", "").strip()
    scheme, _, credential = supplied.partition(" ")
    # 认证方案名大小写不敏感（RFC 7235）；未写方案名时整值即口令
    if not credential and scheme:
        credential = scheme
    elif scheme.lower() not in ("bearer", "token"):
        credential = ""
    if not credential:
        credential = request.headers.get("x-access-token", "").strip()

    if not secrets.compare_digest(credential, ACCESS_TOKEN):
        return JSONResponse(status_code=401, content={"detail": "缺少或错误的访问口令"})
    return await call_next(request)

# ---------- 数据模型 ----------
class ChatRequest(BaseModel):
    model: str = "deepseek-chat"          # 兼容字段：作为 provider 的别名解析
    provider: Optional[str] = None        # 模型服务 id（首选）
    attachments: List[str] = []           # /v1/uploads 返回的附件 id
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
from app.core.providers import (store as provider_store, ProviderError, PRESETS,
                                looks_placeholder, build_client)
from app.core.uploads import store as upload_store, build_user_content, UploadError


def _prepare_chat(request: ChatRequest):
    """解析模型服务、拼装附件。

    返回 (provider, 发给模型的消息列表, 写入会话历史的用户文本)。
    历史里只记原始文本加附件名，避免把整份文件塞进会话记录。
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    provider = provider_store.resolve(request.provider, legacy_model=request.model)

    messages = list(request.messages)
    last = messages[-1] or {}
    raw = last.get("content")
    text = raw if isinstance(raw, str) else ChatPipeline._text_of(raw)

    content = build_user_content(text, request.attachments, provider["supports_vision"])
    messages[-1] = {**last, "content": content}

    history_text = text
    if request.attachments:
        names = "、".join(
            (upload_store.get(a) or {}).get("name", a) for a in request.attachments)
        history_text = (text + "\n" if text else "") + f"[附件] {names}"

    return provider, messages, history_text


@app.post("/v1/chat")
async def chat(request: ChatRequest):
    try:
        provider, messages, user_text = _prepare_chat(request)
    except (ProviderError, UploadError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        used_memory_ids = []
        if USE_PIPELINE:
            result = ChatPipeline(user_id="default_user").process(
                provider["model"], messages, provider_id=provider["id"])
            reply = result.get("reply", "")
            used_memory_ids = result.get("used_memory_ids") or []
        else:
            reply = get_llm_response(
                model=provider["model"], messages=messages,
                temperature=0.7, provider_id=provider["id"])
    except HTTPException:
        raise
    except Exception as e:
        # 失败就明确失败：把错误当回复返回会让它被写进会话历史、伪装成成功
        raise HTTPException(status_code=502,
                            detail=f"模型调用失败：{type(e).__name__}: {str(e)[:200]}")

    message_id = str(uuid.uuid4())
    if request.session_id:
        sessions_store.add_message(request.session_id, "user", user_text)
        sessions_store.add_message(request.session_id, "assistant", reply,
                                   message_id, used_memory_ids)
    return {"reply": reply, "message_id": message_id,
            "provider": provider["id"], "model": provider["model"]}

# ---------- 流式聊天接口 ----------
from fastapi.responses import StreamingResponse
import json as json_module

@app.post("/v1/chat/stream")
async def stream_chat_endpoint(request: ChatRequest):
    """流式聊天端点，返回 Server-Sent Events"""
    from app.core.streaming import stream_chat

    # 解析放在返回流之前：否则配置错误只能混在流里，HTTP 状态仍是 200
    try:
        provider, messages, user_text = _prepare_chat(request)
    except (ProviderError, UploadError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 流式此前直接调 stream_chat、绕过 pipeline，因此从未注入记忆与偏好；
    # 而 PWA 默认走流式，等于记忆功能在手机上是装饰。这里复用同一套注入。
    used_memory_ids = []
    if USE_PIPELINE and messages:
        try:
            query_text = ChatPipeline._text_of(messages[-1].get("content"))
            messages, used_memory_ids = ChatPipeline(
                user_id="default_user").inject_context(messages, query_text)
        except Exception as e:
            print(f"流式上下文注入失败（不影响本次对话）: {e}")

    async def generate():
        message_id = str(uuid.uuid4())
        # 发送开始事件
        yield f"data: {json_module.dumps({'type': 'start', 'message_id': message_id, 'model': provider['model']})}\n\n"

        # 用户消息先落盘：模型调用失败时也不该让用户刚发的话凭空消失
        if request.session_id:
            sessions_store.add_message(request.session_id, "user", user_text)

        full_text = ""
        try:
            async for chunk in stream_chat(provider["model"], messages,
                                           provider_id=provider["id"]):
                full_text += chunk
                yield f"data: {json_module.dumps({'type': 'content', 'text': chunk})}\n\n"
            
            # 保存助手回复到会话（如果提供了 session_id）
            if request.session_id:
                sessions_store.add_message(
                    request.session_id, "assistant", full_text, message_id, used_memory_ids)
            
            # 发送完成事件
            yield f"data: {json_module.dumps({'type': 'done', 'full_text': full_text, 'message_id': message_id, 'model': provider['model']})}\n\n"
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

class SessionMessagesRequest(BaseModel):
    messages: List[Dict[str, Any]] = []

@app.put("/v1/sessions/{session_id}/messages")
async def replace_session_messages(session_id: str, req: SessionMessagesRequest):
    """整体替换会话消息，使前端编辑/删除/重新生成后的视图与后端一致。"""
    if not sessions_store.replace(session_id, req.messages):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "updated", "count": len(req.messages)}

# ---------- 模型列表（由 Provider 配置派生） ----------
from fastapi import UploadFile, File
from fastapi.responses import FileResponse

@app.get("/v1/models")
async def list_models():
    return {
        "models": provider_store.catalog(),
        "default": (provider_store.default() or {}).get("id"),
        "presets": PRESETS,
    }

# ---------- 模型服务（Provider）配置 ----------
class ProviderRequest(BaseModel):
    id: Optional[str] = None
    label: str
    base_url: str
    api_key: str = ""
    model: str
    supports_vision: bool = False
    is_default: bool = False

@app.get("/v1/providers")
async def list_providers():
    # 绝不返回明文密钥，只给掩码与"是否已配置"
    return {"providers": provider_store.public_list(), "presets": PRESETS}

@app.post("/v1/providers")
async def add_provider(req: ProviderRequest):
    try:
        saved = provider_store.upsert(req.model_dump())
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "provider": provider_store._public(saved)}

@app.put("/v1/providers/{provider_id}")
async def update_provider(provider_id: str, req: ProviderRequest):
    record = req.model_dump()
    record["id"] = provider_id
    try:
        saved = provider_store.upsert(record)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "provider": provider_store._public(saved)}

@app.delete("/v1/providers/{provider_id}")
async def remove_provider(provider_id: str):
    if provider_store.delete(provider_id):
        return {"status": "deleted", "id": provider_id}
    raise HTTPException(status_code=404, detail="模型服务不存在")

@app.post("/v1/providers/{provider_id}/default")
async def set_default_provider(provider_id: str):
    if provider_store.set_default(provider_id):
        return {"status": "ok", "default": provider_id}
    raise HTTPException(status_code=404, detail="模型服务不存在")

@app.post("/v1/providers/{provider_id}/test")
async def test_provider(provider_id: str):
    """对已保存的配置真实发一次请求，用于验证密钥与地址是否可用。"""
    try:
        return provider_store.ping(provider_id)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/v1/providers/test")
async def test_provider_draft(req: ProviderRequest):
    """保存前用草稿配置试连，避免存了一个根本用不了的模型。"""
    try:
        candidate = provider_store._validate(req.model_dump())
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if looks_placeholder(candidate["api_key"]):
        return {"ok": False, "detail": "请先填写有效的 API Key"}
    try:
        client = build_client(candidate)
        client.chat.completions.create(model=candidate["model"],
                                       messages=[{"role": "user", "content": "ping"}],
                                       max_tokens=4)
        return {"ok": True, "detail": f"{candidate['model']} 响应正常"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:180]}"}

# ---------- 附件上传 ----------
@app.post("/v1/uploads")
async def upload_attachment(file: UploadFile = File(...)):
    blob = await file.read()
    try:
        record = upload_store.save(file.filename or "unnamed", blob, file.content_type or "")
    except UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return record

@app.get("/v1/uploads/{upload_id}/file")
async def download_attachment(upload_id: str):
    record = upload_store.get(upload_id)
    if record is None:
        raise HTTPException(status_code=404, detail="附件不存在或已清理")
    return FileResponse(record["path"], media_type=record["mime"], filename=record["name"])

@app.delete("/v1/uploads/{upload_id}")
async def delete_attachment(upload_id: str):
    if upload_store.delete(upload_id):
        return {"status": "deleted", "id": upload_id}
    raise HTTPException(status_code=404, detail="附件不存在")

# ---------- 反馈 ----------
FEEDBACK_WEIGHT_STEP = 0.1


@app.post("/v1/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """记录反馈，并立刻把它作用回系统。

    此前反馈只落盘到 feedback.json 和一份无人读取的 preference.txt，对模型行为
    零影响；这里改为真正闭环：调整本次回答所用记忆的权重 + 即时刷新偏好摘要。
    """
    from app.memory.memory_router import memory_manager

    if save_feedback is None:
        raise HTTPException(status_code=503, detail="反馈存储不可用")
    try:
        save_feedback(feedback.message_id, feedback.rating, feedback.comment or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"反馈保存失败：{e}")

    adjusted = []
    located = sessions_store.find_message(feedback.message_id)
    memory_ids = (located or {}).get("message", {}).get("memory_ids") or []
    if memory_ids and memory_manager is not None:
        delta = FEEDBACK_WEIGHT_STEP if feedback.rating > 0 else -FEEDBACK_WEIGHT_STEP
        try:
            adjusted = memory_manager.adjust_weights(memory_ids, delta).get("updated", [])
        except Exception as e:
            print(f"记忆权重调整失败: {e}")

    # 立即重算偏好，使下一轮对话就能生效，而不是等后台定时器
    try:
        if HAS_BG_TASKS and preference_analyzer:
            preference_analyzer.analyze_and_update_preference()
    except Exception as e:
        print(f"偏好刷新失败（不影响反馈记录）: {e}")

    return {
        "status": "success",
        "message": "反馈已记录并生效",
        "memory_weight_adjusted": len(adjusted),
        "used_memories": bool(memory_ids),
    }

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