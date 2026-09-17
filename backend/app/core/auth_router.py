"""注册、登录与管理端点。

这里是身份存储（app/core/auth.py）与 HTTP 之间唯一的一层，规则三条：
1. 对外说话保守。登录失败只有一句"用户名或密码不正确"——区分"没这个用户"和
   "密码错"，就把这个免凭据端点变成了用户名探测器；存储层内部也刻意不分开
   （AuthError 只带那一句，见 auth.login）。注册端的"该用户名已存在"是有意
   保留的实话（改名是用户自己能解决的事），但邀请码退役之后它前面再没有闸门，
   所以这句改由**按真实 IP 计费**来限制——见 _note_failure 的口径。
2. 响应按字段白名单出。存储层的记录带着 pw_hash、tokens 和内建的 username_lc，
   顺手 return record 等于把口令摘要与会话令牌摘要交给前端与日志。
3. 凭据明文只在"必须被看见"的那一次出现：令牌见于注册与登录的响应，以及管理员
   轮换的响应。任何端点都不许复述调用方刚提交的密码或令牌。
"""
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import authz
from app.core.auth import NO_RECOVERY, RESET_FAIL, AuthError
from app.core.authz import CurrentPrincipal, Principal, RequireAdmin

router = APIRouter(tags=["身份"])

# 猜密码的代价：同一来源在窗口内失败太多次就拒一拒。进程内计数即可——
# 重启即清零是可接受的，因为真正的凭据是 bcrypt 校验与长密码。
# 账本只记失败，不记格式错（用户名打错字、密码太短）：那是当事人自己能改好的事，
# 把它算进预算只会让唯一的登录入口被自己的手滑锁死。
FAILURE_WINDOW_SECONDS = 600
MAX_FAILURES_PER_WINDOW = 10

# 注册开放之后，"能建多少个号"是唯一的成本闸门：按真实来源限成功数。
# 记成功而不是记失败，因为失败（撞名）本来就是零成本，而一个脚本可以无限撞名
# 却一个号也建不出来；能真正花钱的是"注册成功 + 拿去对话"。
REGISTER_WINDOW_SECONDS = 86400
MAX_REGISTRATIONS_PER_SOURCE = 3

# 来源键取自 CF-Connecting-IP：域名必经 Cloudflare，而它会把真实访客 IP 写在
# 这个头上；后端只监听 127.0.0.1:8000、外部唯一入口就是 cloudflared，没有旁路
# 可以伪造这个头。取不到该头时退回 uvicorn 看到的对端地址（本机直连与测试）。
# 以前只用 request.client.host，经过隧道后恒为 127.0.0.1——全网共用一个桶，
# 一个人手滑就能把所有人挡在门外，所以这既是功能也是修 bug。
MAX_TRACKED_SOURCES = 4096
_FAILS = defaultdict(list)
_REGISTERS = defaultdict(list)


def _store():
    """身份库要从 authz 现取，不能在 import 时绑死。

    中间件读的是 authz.auth_store（测试换库也只换它）。端点若绑住 app.core.auth
    里那个单例，enforced 模式下就会出现"中间件认 A 库、注册端点写 B 库"，
    发出去的令牌连它自己都解不出来——而这恰是路由层最容易悄悄错位的地方。
    """
    return authz.auth_store


def _now() -> float:
    """单调时钟，独立成函数只为让"窗口过期"这一条测得到。"""
    return time.monotonic()


def _recent(ledger, ip: str, window: float, moment: float) -> list:
    recent = [t for t in ledger[ip] if moment - t < window]
    ledger[ip] = recent
    return recent


def _prune(moment: float) -> None:
    for ledger in (_FAILS, _REGISTERS):
        if len(ledger) <= MAX_TRACKED_SOURCES:
            continue
        for key in [k for k, v in ledger.items()
                    if not any(moment - t < FAILURE_WINDOW_SECONDS for t in v)]:
            ledger.pop(key, None)


def _throttled(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return len(_recent(_FAILS, ip, FAILURE_WINDOW_SECONDS, moment)) >= MAX_FAILURES_PER_WINDOW


def _note_failure(ip: str) -> None:
    _FAILS[ip].append(_now())


def _registrations_full(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return (len(_recent(_REGISTERS, ip, REGISTER_WINDOW_SECONDS, moment))
            >= MAX_REGISTRATIONS_PER_SOURCE)


def _note_registration(ip: str) -> None:
    _REGISTERS[ip].append(_now())


def _client_ip(request: Request) -> str:
    # 只信 Cloudflare 那一个头。X-Forwarded-For 是一条可被追加的链，取首项等于
    # 取攻击者写的第一句假话；cf-connecting-ip 由边缘改写，才是可信来源。
    real = request.headers.get("cf-connecting-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(status_code=429, detail="尝试次数过多，请稍后再试",
                         headers={"Retry-After": str(retry_after)})


class RegisterRequest(BaseModel):
    username: str
    password: str
    # 必填：存储层允许记录没有找回凭据（真实库里就有那之前的老账号），但经
    # API 新注册的人必须留下它，否则"忘记密码"这条路对新人永远走不通。
    security_question: str
    security_answer: str


class LoginRequest(BaseModel):
    username: str
    password: str


class RecoveryRequest(BaseModel):
    username: str


class ResetRequest(BaseModel):
    username: str
    answer: str
    new_password: str


@router.post("/v1/auth/register")
async def register(req: RegisterRequest, request: Request):
    """开放注册：用户名 + 自设密码，成功即发一枚会话令牌（注册即登录）。

    路由挂在精确路径上——authz.PUBLIC_PATHS 也是精确匹配，带斜杠的变体在
    enforced 下先被中间件挡在凭据之外，不会成为第二个入口。
    """
    ip = _client_ip(request)
    if _throttled(ip):
        # 与登录共用同一份失败预算。少了这一句，"撞名要计费"就是空话：格子照扣、
        # 谁也不收，枚举用户名依然是免费的。
        raise _too_many(FAILURE_WINDOW_SECONDS)
    if _registrations_full(ip):
        raise _too_many(REGISTER_WINDOW_SECONDS)
    try:
        principal, token = _store().register(
            username=req.username, password=req.password,
            security_question=req.security_question, security_answer=req.security_answer)
    except AuthError as e:
        raise _register_error(e, ip)
    _note_registration(ip)
    _FAILS.pop(ip, None)
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


def _register_error(e: AuthError, ip: str) -> HTTPException:
    """把存储层的失败原因翻译成状态码，并给唯一那条可被滥用的信道计费。"""
    if e.taken:
        # 实话保留，但它现在是免凭据的用户名枚举信道：每问一次扣一格登录预算。
        # 真人改名一次就过了，脚本则要每 10 次换一枚真实访客 IP——而换 IP 意味着
        # 它背后真有一张分布式网络，那时限流本来也挡不住，只是把成本抬上去。
        _note_failure(ip)
        return HTTPException(status_code=409, detail=e.reason)
    # 用户名、密码、找回问题本身不合格：都是当事人自己能改好的，不计费也不该挡别人的路。
    return HTTPException(status_code=422, detail=e.reason)


@router.post("/v1/auth/login")
async def login(req: LoginRequest, request: Request):
    """用户名 + 密码换一枚新的会话令牌；旧令牌继续有效（多设备并存）。"""
    ip = _client_ip(request)
    if _throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    try:
        principal, token = _store().login(username=req.username, password=req.password)
    except AuthError as e:
        _note_failure(ip)
        # 401 而不是 403：这里没有"身份是真的但角色不够"这一说，只有"没认出来"。
        raise HTTPException(status_code=401, detail=e.reason)
    _FAILS.pop(ip, None)
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


@router.post("/v1/auth/recovery")
async def recovery(req: RecoveryRequest, request: Request):
    """报出这个用户名的找回问题；答对才能进下一步改密。

    它免凭据，所以两件事一起做：先过登录那份按真实 IP 的失败预算，问到"没有
    设置密码找回"就记一格——查无此人与没开找回的人在响应里同形（见存储层
    recovery_question），这一格是那条信道唯一还剩下的代价。
    """
    ip = _client_ip(request)
    if _throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    question = _store().recovery_question(req.username)
    available = question != NO_RECOVERY
    if not available:
        _note_failure(ip)
    # recovery_available 是这一格唯一的额外信息：false 同时覆盖"没这个人"与
    # "有这个人但没设找回"，所以它没有把用户名存在性多说出去一个字。
    return {"question": question, "recovery_available": available}


@router.post("/v1/auth/reset")
async def reset(req: ResetRequest, request: Request):
    """答案正确就换密码；该人名下所有会话令牌同时作废（每一台设备都掉线）。"""
    ip = _client_ip(request)
    if _throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    try:
        _store().reset_password(req.username, req.answer, req.new_password)
    except AuthError as e:
        if e.reason == RESET_FAIL:
            # 答案错、没开找回、查无此人、已停用：同一句、同一格预算、同一个 401
            _note_failure(ip)
            raise HTTPException(status_code=401, detail=e.reason)
        # 新密码本身不合格（太短、太长、空）：那是当事人自己能改好的，不计费
        raise HTTPException(status_code=422, detail=e.reason)
    _FAILS.pop(ip, None)
    return {"status": "password_reset"}


@router.get("/v1/auth/me")
async def me(principal: Principal = CurrentPrincipal):
    """前端用它确认"我到底是谁"——凭据被解析成谁，只有这里说得准。"""
    return {"user_id": principal.user_id, "username": principal.username,
            "role": principal.role}


# ---------- 管理端 ----------
# role 不在任何请求体里：管理员只来自 ACCESS_TOKEN bootstrap，或来自运维手改
# users.json。给 API 开一个写 role 的口子，等于把整套身份体系作废。

def _public_user(record: dict) -> dict:
    """用户记录的对外视图。逐字段列出，是为了让 pw_hash 与 tokens 无处可藏。"""
    return {
        "user_id": record.get("user_id"),
        "username": record.get("username"),
        "role": record.get("role"),
        "disabled": record.get("disabled"),
        "created_at": record.get("created_at"),
        # last_seen 粗粒度是设计使然：存储层为不把鉴权变成热路径写盘，把落盘
        # 节流到一小时以上，所以它读作"上次看见它至少是一小时前"，不是在线状态。
        "last_seen": record.get("last_used_at"),
        # 有几台设备在线是运维要知道的，但只报数量：令牌摘要本身是凭据。
        "sessions": len(record.get("tokens") or []),
    }


@router.get("/v1/admin/users")
async def list_users(_: Principal = RequireAdmin):
    return {"users": [_public_user(u) for u in _store().list_users()]}


@router.post("/v1/admin/users/{user_id}/disable")
async def disable_user(user_id: str, _: Principal = RequireAdmin):
    if not _store().disable_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "disabled", "user_id": user_id}


@router.post("/v1/admin/users/{user_id}/enable")
async def enable_user(user_id: str, _: Principal = RequireAdmin):
    # 轮换令牌不再顺手解除停用，因此撤销必须有对称的还原动作。
    if not _store().enable_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "enabled", "user_id": user_id}


@router.post("/v1/admin/users/{user_id}/rotate-token")
async def rotate_user_token(user_id: str, _: Principal = RequireAdmin):
    """强制全端重登：旧令牌全部作废，且停用状态原样保留。"""
    try:
        return {"token": _store().rotate_token(user_id)}
    except AuthError:
        raise HTTPException(status_code=404, detail="用户不存在")


@router.delete("/v1/admin/users/{user_id}")
async def remove_user(user_id: str, actor: Principal = RequireAdmin):
    if user_id == actor.user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if not _store().delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "deleted", "user_id": user_id}
