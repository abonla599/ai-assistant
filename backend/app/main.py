import sys
import os

# 中文 Windows 的控制台默认 GBK，打包成 EXE 后任何 emoji 日志都会抛
# UnicodeEncodeError 并在导入阶段终止进程（开发终端能显示 UTF-8，所以看不出来）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uuid
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
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
    from app.feedback_storage import save_feedback, FEEDBACK_FILE
    # 如果 preference_analyzer 模块存在，从中导入具体函数
    # 偏好摘要按人分账之后，"重算一次"这个动作必须说清楚是替谁重算：
    # analyze_and_update_preference(user_id) 服务单个用户，
    # analyze_all_preferences() 服务后台定时器（它没有"当前调用者"这个身份）。
    if preference_analyzer:
        from app.preference_analyzer import (analyze_and_update_preference,
                                             analyze_all_preferences)
    else:
        analyze_and_update_preference = None
        analyze_all_preferences = None
except ImportError as e:
    save_feedback = None
    FEEDBACK_FILE = None
    analyze_and_update_preference = None
    analyze_all_preferences = None
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
    # 先把"这八份可变数据此刻到底写在哪儿"打出来：它原先只是文档里的一段散文，
    # 而 data_file() 会在 data/ 下没有同名文件时退回项目根那份历史文件——于是
    # "所有数据都在 data/ 下吗"取决于本机有没有一个老 preference.txt，光看文档猜不出来。
    from app.core.paths import log_data_locations
    log_data_locations()
    start_background_scheduler()
    yield
    # 关闭时执行（如果需要清理资源）
    print("服务正在关闭...")

# 记忆模块路由（包含完整的 /v1/memory/* 端点）
from app.memory.memory_router import router as memory_router

# 身份端点：邀请码注册 + 管理面。哪个端点免凭据由 authz.PUBLIC_PATHS 说了算，
# 这里只负责挂载，不在此处再判一遍凭据。
from app.core.auth_router import router as auth_router

# PWA 前端（手机浏览器访问 /app 即可使用，与 API 同源）
from app.web.web_router import mount_admin, mount_pwa

# ---------- 创建 FastAPI 应用 ----------
# 身份与文档开关的规则都在 app/core/authz.py，这里只负责装上。
# 模式仍由 authz 现读 env：本模块不自带 AUTH_MODE 默认值，免得两处默认不一致。
from app.core.authz import (_auth_mode, CurrentPrincipal, docs_kwargs_for_mode,
                            install_auth, Principal, RequireAdmin)

# 非 disabled 模式连文档路由都不生成（路由表本身就是侦察材料）
app = FastAPI(
    title="AI 智能助手",
    description="多模型、工具调用、记忆管理的智能助手系统",
    version="1.0.0",
    lifespan=lifespan,  # 注册 lifespan
    **docs_kwargs_for_mode(_auth_mode())
)

# ---------- 错误形状 ----------
# 形状错（整个字段缺失、字段类型不对）落在 FastAPI 的 RequestValidationError 上，默认
# detail 是一个结构化 list，而且 pydantic 会把提交进来的值原样放进 input——注册与找回
# 那两格里过的就是密码和找回答案。两个客户端因此同时坏：api.js 是 `new Error(detail)`，
# 数组过去显示成 [object Object]；管理页 admin.js 同一套写法。
#
# 这句话只在服务端说一次。找回那道 pydantic 条数闸门撤掉之后，"字段缺失/不是数组"这一类
# 仍然走结构化 detail，如果让每个客户端各自兜一层（Array.isArray(detail) ? ... : ...），
# 形状就有了 N 个事实来源，而 curl 与桌面壳这两个没兜的东西照样看不懂。
# 判据见 tests/test_auth_endpoints.py 的
# test_a_malformed_body_says_one_plain_chinese_sentence。
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MALFORMED_BODY_DETAIL = "提交的内容不完整或格式不对：请检查每一栏都填了"


@app.exception_handler(RequestValidationError)
async def on_malformed_body(_: Request, exc: RequestValidationError):
    """把结构化校验错误压成一句人话，状态码仍是 422。"""
    # 原始错误只进服务端日志，且只取字段路径：够定位是哪儿没填，
    # 又不会把密码或答案写进控制台与 EXE 的日志文件里。
    where = "、".join(".".join(str(part) for part in (err.get("loc") or ()))
                      for err in exc.errors()[:8])
    print(f"⚠️ 请求体形状不对（{where or '未知字段'}）")
    return JSONResponse(status_code=422, content={"detail": MALFORMED_BODY_DETAIL})

# 注册记忆路由（优先使用 Router 中的端点）
app.include_router(memory_router)

# 注册身份端点（/v1/auth/*、/v1/admin/*）
app.include_router(auth_router)

# 注册 PWA 前端。挂载在 /app 下，接口仍走 /v1/*，两者互不干扰。
mount_pwa(app)

# 管理员页挂在 /admin：一个不含数据的空壳，数据一律经 /v1/admin/* 取。
mount_admin(app)

# ---------- 访问鉴权 ----------
# 身份规则见 app/core/authz.py（import 在创建应用那一节）。这里只负责装上。
install_auth(app)

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

# ⚠️ 导入期副作用：这一行会把 $SESSION_DB_PATH 指向的文件读进来并做 owner 回填，
# 未设置该变量时就是仓库真实的 data/sessions.json —— 也就是说"只是 import 一下
# app.main"就会改写用户真实数据，并在旁边落下 sessions.json.bak-<时间戳>。
# 脚本与测试必须先定 SESSION_DB_PATH / UPLOAD_DIR / USERS_DB_PATH，
# 再导入本模块（backend/tests/conftest.py 就是为此存在）。
# fail-fast 是刻意的；改成惰性构造留给 Task 8。
sessions_store = SessionStore()

# ---------- 后台定时任务 ----------
def run_scheduler():
    while True:
        try:
            print("--- 开始执行周期性后台任务 ---")
            if HAS_BG_TASKS:
                if preference_analyzer:
                    # 定时器没有"当前调用者"，所以它不能替某一个人决定归属：
                    # 反馈里出现过谁就重算谁那一份（偏好摘要按人分账，见
                    # preference_analyzer）。原先这里调用的是不带身份的
                    # analyze_and_update_preference()，它把所有人合并成一份
                    # 全站摘要再注入每个人的提示词。
                    preference_analyzer.analyze_all_preferences()
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
    if auto_weight_adjuster is not None and FEEDBACK_FILE:
        watcher_thread = threading.Thread(
            target=auto_weight_adjuster.start_feedback_watcher,
            kwargs={"feedback_file_path": FEEDBACK_FILE},
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


def _prepare_chat(request: ChatRequest, principal: Principal):
    """解析模型服务、拼装附件。

    返回 (provider, 发给模型的消息列表, 写入会话历史的用户文本)。
    历史里只记原始文本加附件名，避免把整份文件塞进会话记录。

    principal 一路传进来而不是在这里再取一次：附件归属必须由"谁在请求"决定，
    请求里那个 attachments 列表只是别人塞进来的 id 集合。
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    provider = provider_store.resolve(request.provider, legacy_model=request.model)

    messages = list(request.messages)
    last = messages[-1] or {}
    raw = last.get("content")
    text = raw if isinstance(raw, str) else ChatPipeline.text_of(raw)

    content = build_user_content(text, request.attachments, provider["supports_vision"],
                                 principal.user_id)
    messages[-1] = {**last, "content": content}

    history_text = text
    if request.attachments:
        names = "、".join(
            (upload_store.get(a, owner=principal.user_id) or {}).get("name", a)
            for a in request.attachments)
        history_text = (text + "\n" if text else "") + f"[附件] {names}"

    return provider, messages, history_text


def _require_session_owner(session_id, principal: Principal) -> None:
    """有 session_id 就先证明它属于调用者，否则 404，且必须在调模型之前。

    为什么不能只靠 add_message 返回 False：那样这条路会对"别人的会话 id"回一个
    200，而模型已经调完、token 已经花掉，转录则被静默丢掉——调用方看到的界面
    上一切正常，历史里一个字都没落。写没写进去是这条链路的契约，就不能靠一个
    被丢弃的返回值来表达。

    404 不会把它变成探测器："不是你的"和"根本不存在"经 store 返回同一个 None，
    因此发出的是同一个 404、同一句话，跟 /v1/sessions/{id} 那些路由一模一样。
    """
    if session_id and sessions_store.get(session_id, owner=principal.user_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")


@app.post("/v1/chat")
def chat(request: ChatRequest, principal: Principal = CurrentPrincipal):
    _require_session_owner(request.session_id, principal)
    try:
        provider, messages, user_text = _prepare_chat(request, principal)
    except (ProviderError, UploadError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        used_memory_ids = []
        if USE_PIPELINE:
            result = ChatPipeline(user_id=principal.user_id).process(
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
        sessions_store.add_message(request.session_id, principal.user_id, "user", user_text)
        sessions_store.add_message(request.session_id, principal.user_id, "assistant", reply,
                                   message_id, used_memory_ids)
    return {"reply": reply, "message_id": message_id,
            "provider": provider["id"], "model": provider["model"]}

# ---------- 流式聊天接口 ----------
from fastapi.responses import StreamingResponse
import json as json_module

@app.post("/v1/chat/stream")
def stream_chat_endpoint(request: ChatRequest,
                         principal: Principal = CurrentPrincipal):
    """流式聊天端点，返回 Server-Sent Events

    端点与 generate() 都必须是同步的：模型 token 是从阻塞 socket 上读出来的，
    留在事件循环里会让一个慢请求冻住整台服务（线上 524）。
    """
    from app.core.streaming import stream_chat

    # 归属必须在这里判，不能在 generate() 里判：流一开始 HTTP 状态就锁死在 200，
    # 那时再发现 session_id 不是你的，只能静默不落盘（原先正是这样）。
    _require_session_owner(request.session_id, principal)

    # 解析放在返回流之前：否则配置错误只能混在流里，HTTP 状态仍是 200
    try:
        provider, messages, user_text = _prepare_chat(request, principal)
    except (ProviderError, UploadError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 流式此前直接调 stream_chat、绕过 pipeline，因此既没注入记忆与偏好，
    # 也不会把本轮对话写回记忆。这里复用同一个 pipeline 实例补齐两者。
    used_memory_ids = []
    pipe = ChatPipeline(user_id=principal.user_id) if USE_PIPELINE else None
    if pipe is not None and messages:
        try:
            query_text = ChatPipeline.text_of(messages[-1].get("content"))
            messages, used_memory_ids = pipe.inject_context(messages, query_text)
        except Exception as e:
            print(f"流式上下文注入失败（不影响本次对话）: {e}")

    def generate():
        message_id = str(uuid.uuid4())
        # 发送开始事件
        yield f"data: {json_module.dumps({'type': 'start', 'message_id': message_id, 'model': provider['model']})}\n\n"

        # 用户消息先落盘：模型调用失败时也不该让用户刚发的话凭空消失
        if request.session_id:
            sessions_store.add_message(request.session_id, principal.user_id,
                                       "user", user_text)

        full_text = ""
        try:
            for chunk in stream_chat(provider["model"], messages,
                                     provider_id=provider["id"]):
                full_text += chunk
                yield f"data: {json_module.dumps({'type': 'content', 'text': chunk})}\n\n"
            
            # 保存助手回复到会话（如果提供了 session_id）
            if request.session_id:
                sessions_store.add_message(
                    request.session_id, principal.user_id, "assistant",
                    full_text, message_id, used_memory_ids)

            # 把本轮问答写入长期记忆，与非流式路径保持一致
            if pipe is not None:
                try:
                    pipe.save_interaction(user_text, full_text)
                except Exception as e:
                    print(f"流式记忆保存失败（不影响已返回的回复）: {e}")
            
            # 发送完成事件
            yield f"data: {json_module.dumps({'type': 'done', 'full_text': full_text, 'message_id': message_id, 'model': provider['model']})}\n\n"
        except Exception as e:
            yield f"data: {json_module.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")

# ---------- 会话管理 ----------
# 归属只由 principal 推导：路由不接受任何来自 body/query/path 的 user_id 或
# owner，否则"我是谁"就成了客户端说了算。
# 非本人一律 404 而不是 403：403 等于承认这个 id 存在，session_id 是 uuid4，
# 但只要有一次 403 漏出来，这个接口就成了"哪些会话真实存在"的探测器。
@app.post("/v1/sessions")
async def create_session(model: str = "deepseek-chat",
                         principal: Principal = CurrentPrincipal):
    return sessions_store.create(model, owner=principal.user_id)

@app.get("/v1/sessions")
async def list_sessions(principal: Principal = CurrentPrincipal):
    return {"sessions": sessions_store.list_summaries(principal.user_id)}

@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, principal: Principal = CurrentPrincipal):
    session = sessions_store.get(session_id, owner=principal.user_id)
    if session is None:
        # 404 而非 403：403 等于承认这个 id 存在，可以被拿来枚举
        raise HTTPException(status_code=404, detail="会话不存在")
    # 投影而不是裸记录：owner 是存储内部字段，列表侧早有白名单，详情没有就等于
    # 三条读路径各说各话，下一个加字段的人只能猜该抄哪一条。
    return sessions_store.public(session)

@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, principal: Principal = CurrentPrincipal):
    if sessions_store.delete(session_id, owner=principal.user_id):
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="会话不存在")

class SessionMessagesRequest(BaseModel):
    messages: List[Dict[str, Any]] = []

@app.put("/v1/sessions/{session_id}/messages")
async def replace_session_messages(session_id: str, req: SessionMessagesRequest,
                                   principal: Principal = CurrentPrincipal):
    """整体替换会话消息，使前端编辑/删除/重新生成后的视图与后端一致。"""
    if not sessions_store.replace(session_id, principal.user_id, req.messages):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "updated", "count": len(req.messages)}

# ---------- 模型列表（由 Provider 配置派生） ----------
from fastapi import UploadFile, File
from fastapi.responses import FileResponse

@app.get("/v1/models")
async def list_models(_: Principal = CurrentPrincipal):
    """模型清单：前端那个下拉就靠它渲染。

    身份在这里刻意不用取名（catalog() 是全站视图），挂它也不是为了挡住匿名读取
    ——那道门由 install_auth 的中间件在路由之前守着，但只在 **enforced 模式下、
    且只在 authz._PROTECTED_PREFIXES 那几个前缀（含 /v1/）之下**成立：disabled 模式
    人人都是本机管理员，websocket 握手更是压根不经过这个 HTTP 中间件（实测见
    tests/test_route_auth_contract.py）。要的理由就两条：路由契约
    （tests/test_route_auth_contract.py）不接受没有身份的 /v1 端点，且这是中间件
    之外多出来的一把锁。完整理由见下面 providers 段那段注释。
    key masking 原样保留——catalog() 只报 usable/reason，密钥永不出这道门。
    """
    return {
        "models": provider_store.catalog(),
        "default": (provider_store.default() or {}).get("id"),
        "presets": PRESETS,
    }

# ---------- 模型服务（Provider）配置 ----------
# 这一面补上了身份依赖，且角色已收归管理员。写侧升级是 Task 7 明写的活：
# 改默认 provider、改 base_url 或塞进一把密钥，就把**所有人**的对话改道到攻击者
# 指定的上游——那是跨用户外泄通道，不是"他能看到别人的会话"那种局部越权。
# 邀请码人人可换（Task 3），所以"注册用户"在这道门前不含任何信任量。
# 身份依赖本身仍然保留，理由与 /v1/models 那条一样：路由契约要求每条 /v1 路由声明
# 身份；install_auth 的中间件之外多一把锁（受保护前缀哪天收窄、挂载顺序哪天被动过，
# 锁不至于只有一把）；将来要按人记账时身份已经在手里。
# 前端（/app 的设置页）配合收起这些入口，但那只是不让普通用户点到一个必然 403 的
# 按钮：手搓请求仍旧由这里的依赖拒绝，界面从来不是边界。
class ProviderRequest(BaseModel):
    id: Optional[str] = None
    label: str
    base_url: str
    api_key: str = ""
    model: str
    supports_vision: bool = False
    is_default: bool = False

@app.get("/v1/providers")
async def list_providers(_: Principal = RequireAdmin):
    # 绝不返回明文密钥，只给掩码与"是否已配置"
    return {"providers": provider_store.public_list(), "presets": PRESETS}

@app.post("/v1/providers")
async def add_provider(req: ProviderRequest, _: Principal = RequireAdmin):
    try:
        saved = provider_store.upsert(req.model_dump())
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "provider": provider_store._public(saved)}

@app.put("/v1/providers/{provider_id}")
async def update_provider(provider_id: str, req: ProviderRequest,
                          _: Principal = RequireAdmin):
    record = req.model_dump()
    record["id"] = provider_id
    try:
        saved = provider_store.upsert(record)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "provider": provider_store._public(saved)}

@app.delete("/v1/providers/{provider_id}")
async def remove_provider(provider_id: str, _: Principal = RequireAdmin):
    if provider_store.delete(provider_id):
        return {"status": "deleted", "id": provider_id}
    raise HTTPException(status_code=404, detail="模型服务不存在")

@app.post("/v1/providers/{provider_id}/default")
async def set_default_provider(provider_id: str, _: Principal = RequireAdmin):
    if provider_store.set_default(provider_id):
        return {"status": "ok", "default": provider_id}
    raise HTTPException(status_code=404, detail="模型服务不存在")

@app.post("/v1/providers/{provider_id}/test")
async def test_provider(provider_id: str, _: Principal = RequireAdmin):
    """对已保存的配置真实发一次请求，用于验证密钥与地址是否可用。"""
    try:
        return provider_store.ping(provider_id)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/v1/providers/test")
async def test_provider_draft(req: ProviderRequest, _: Principal = RequireAdmin):
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
async def upload_attachment(file: UploadFile = File(...),
                            principal: Principal = CurrentPrincipal):
    blob = await file.read()
    try:
        record = upload_store.save(file.filename or "unnamed", blob,
                                   file.content_type or "", owner=principal.user_id)
    except UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return record

@app.get("/v1/uploads/{upload_id}/file")
async def download_attachment(upload_id: str, principal: Principal = CurrentPrincipal):
    # 附件是别人传的账单和论文。非属主给 404，不给 403：后者会把"id 存在"这件事
    # 白送出去，而 id 只有 16 位十六进制。
    record = upload_store.get(upload_id, owner=principal.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="附件不存在或已清理")
    return FileResponse(record["path"], media_type=record["mime"], filename=record["name"])

@app.delete("/v1/uploads/{upload_id}")
async def delete_attachment(upload_id: str, principal: Principal = CurrentPrincipal):
    if upload_store.delete(upload_id, owner=principal.user_id):
        return {"status": "deleted", "id": upload_id}
    raise HTTPException(status_code=404, detail="附件不存在")

# ---------- 反馈 ----------
FEEDBACK_WEIGHT_STEP = 0.1


@app.post("/v1/feedback")
async def submit_feedback(feedback: FeedbackRequest,
                          principal: Principal = CurrentPrincipal):
    """记录反馈，并立刻把它作用回系统。

    此前反馈只落盘到 feedback.json 和一份无人读取的 preference.txt，对模型行为
    零影响；这里改为真正闭环：调整本次回答所用记忆的权重 + 即时刷新偏好摘要。

    归属必须先于任何写入。原先的顺序是反的：先无条件往 feedback.json 加一行、
    再重算偏好，然后才按 message_id 反查记忆——而 preference.txt 会被注入
    **所有人**的提示词，于是任何持凭据者都能靠别人的 message_id 表态，改写的
    却是全站的行为。反查用带归属的 find_message：不属于你就当作不存在，
    两种情况同一个 404、同一句话，这个端点不是探测他人 message_id 的信道。

    偏好摘要现在按人分账：重算的是**调用者自己**那一份，别人的反馈进不了他的
    摘要（见 preference_analyzer.preference_path）。
    """
    from app.memory.memory_router import memory_manager

    if save_feedback is None:
        raise HTTPException(status_code=503, detail="反馈存储不可用")

    located = sessions_store.find_message(feedback.message_id, principal.user_id)
    if located is None:
        raise HTTPException(status_code=404, detail="消息不存在")

    try:
        save_feedback(feedback.message_id, feedback.rating, feedback.comment or "",
                      principal.user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"反馈保存失败：{e}")

    adjusted = []
    # 消息是你的，不代表消息上挂的 memory_ids 是你的：整份回写会话的端点接受
    # 客户端给的 memory_ids（前端编辑历史要用），于是别人家的记忆 id 能被种进
    # 你自己的会话，再点一次反馈就去调它的权重。筛选写在 adjust_weights 内部
    # （owner 必填），这里就不再自己预筛一遍——能被调用方忘记的守卫迟早会被忘记。
    claimed = located.get("message", {}).get("memory_ids") or []
    if claimed and memory_manager is not None:
        delta = FEEDBACK_WEIGHT_STEP if feedback.rating > 0 else -FEEDBACK_WEIGHT_STEP
        try:
            adjusted = memory_manager.adjust_weights(
                claimed, principal.user_id, delta).get("updated", [])
        except Exception as e:
            print(f"记忆权重调整失败: {e}")

    # 立即重算他自己那份偏好，使下一轮对话就能生效，而不是等后台定时器
    try:
        if HAS_BG_TASKS and preference_analyzer:
            preference_analyzer.analyze_and_update_preference(principal.user_id)
    except Exception as e:
        print(f"偏好刷新失败（不影响反馈记录）: {e}")

    return {
        "status": "success",
        "message": "反馈已记录并生效",
        "memory_weight_adjusted": len(adjusted),
        "used_memories": bool(adjusted),
    }

# ---------- 智能体 ----------
# 这一面挂的是 require_admin，不是 current_principal，理由是"没有归属可谈"：
# 编排与任务表（app/agents/task_store）是一个进程级全局 dict，条目上没有 owner，
# 所以这里若只声明"我是某个注册用户"，契约会是绿的，而任何注册用户都能列出、取消、
# 删除别人的任务；agent 本身还拿着本机自己的上游配置跑付费调用，跑在谁的账上
# 无人知道。要做成普通用户可用，先给 task 加 owner（与会话同一套归属规则），
# 那是另一端工程；在此之前管理员是唯一不撒谎的守卫。
# 仓库里没有任何客户端调这两组端点（PWA/Flutter/Android 都不用），所以不是破坏性变更。
@app.post("/v1/agent/run")
def run_agent(request: AgentRequest, _: Principal = RequireAdmin):
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
def orchestrate_task(request: OrchestrateRequest, _: Principal = RequireAdmin):
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器模块尚未就绪")
    result = orchestrator.run(
        goal=request.goal,
        task_id=request.task_id
    )
    return result

# ---------- 任务状态 ----------
# 与上面两组同一个守卫：读侧必须和写侧一样严，否则"谁的任务"这件事就只
# 在取消/删除那两条上被守住，列出全部目标一句话就能拿到。
@app.get("/v1/tasks/{task_id}")
async def get_task_status(task_id: str, _: Principal = RequireAdmin):
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
async def list_all_tasks(_: Principal = RequireAdmin):
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
async def cancel_task(task_id: str, _: Principal = RequireAdmin):
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
async def delete_task(task_id: str, _: Principal = RequireAdmin):
    if task_id in task_store:
        del task_store[task_id]
        return {"status": "deleted", "task_id": task_id}
    raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

# ---------- 启动入口 ----------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)