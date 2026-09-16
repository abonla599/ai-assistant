"""注册与管理端点：邀请码换身份，管理员发放与回收邀请码、管理用户。

这里是身份存储（app/core/auth.py）与 HTTP 之间唯一的一层，规则两条：
1. 对外说话保守。注册失败的原因一律收敛成"邀请码无效"——区分"码不存在"和
   "码已用尽"，就把这个免凭据端点变成了探测邀请码是否存在的信道。
2. 响应按字段白名单出。存储层的记录带着 token_hash 和内建的 username_lc，
   顺手 return record 等于把口令摘要交给前端与日志。
"""
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import authz
from app.core.auth import AuthError
from app.core.authz import CurrentPrincipal, Principal, RequireAdmin

router = APIRouter(tags=["身份"])

# 猜码的代价：同一来源在窗口内失败太多次就拒一拒。进程内计数即可——
# 重启即清零是可接受的，因为真正的凭据是 40 位随机邀请码。
FAILURE_WINDOW_SECONDS = 600
MAX_FAILURES_PER_WINDOW = 10
# 来源键来自请求头，谁都能造。字典不能无上限长大，所以超阈值时顺手丢掉
# 窗口内已无记录的来源（正常规模部署永远碰不到这个阈值）。
MAX_TRACKED_SOURCES = 4096
_FAILS = defaultdict(list)

# 存储层用一句面向用户的话表达失败原因，这里按它分类状态码。
# 耦合点写在明处：改 auth.py 里那几句话时，test_auth_endpoints.py 会红。
_REASON_CODE_BAD = "邀请码"
_REASON_NAME_TAKEN = "占用"


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


def _failures_of(ip: str, moment: float) -> list:
    recent = [t for t in _FAILS[ip] if moment - t < FAILURE_WINDOW_SECONDS]
    _FAILS[ip] = recent
    return recent


def _prune(moment: float) -> None:
    if len(_FAILS) <= MAX_TRACKED_SOURCES:
        return
    for key in [k for k, v in _FAILS.items()
                if not any(moment - t < FAILURE_WINDOW_SECONDS for t in v)]:
        _FAILS.pop(key, None)


def _throttled(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return len(_failures_of(ip, moment)) >= MAX_FAILURES_PER_WINDOW


def _note_failure(ip: str) -> None:
    _FAILS[ip].append(_now())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _register_error(e: AuthError) -> HTTPException:
    """把存储层的失败原因翻译成状态码，同时不外泄邀请码的存在性。"""
    if _REASON_NAME_TAKEN in e.reason:
        # 重名必须照实说：用户改名就能解决，让他以为码坏了只会去缠管理员。
        return HTTPException(status_code=409, detail=e.reason)
    if _REASON_CODE_BAD in e.reason:
        return HTTPException(status_code=403, detail="邀请码无效")
    # 用户名本身不合格（空、超长、保留字、含不可见字符）
    return HTTPException(status_code=422, detail=e.reason)


class RegisterRequest(BaseModel):
    code: str
    username: str


class InviteRequest(BaseModel):
    max_uses: int = 1


@router.post("/v1/auth/register")
async def register(req: RegisterRequest, request: Request):
    """唯一的免凭据端点：拿邀请码换一个可撤销的令牌。

    路由挂在 /v1/auth/register 精确路径上——authz.PUBLIC_PATHS 也是精确匹配，
    带斜杠的变体在 enforced 下先被中间件挡在凭据之外，不会成为第二个入口。
    """
    ip = _client_ip(request)
    if _throttled(ip):
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试",
                            headers={"Retry-After": str(FAILURE_WINDOW_SECONDS)})
    try:
        principal, token = _store().register(code=req.code, username=req.username)
    except AuthError as e:
        _note_failure(ip)
        raise _register_error(e)
    _FAILS.pop(ip, None)   # 成功即证明这来源是正当用户，别让它之前的手滑继续记账
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


@router.get("/v1/auth/me")
async def me(principal: Principal = CurrentPrincipal):
    """前端用它确认"我到底是谁"——凭据被解析成谁，只有这里说得准。"""
    return {"user_id": principal.user_id, "username": principal.username,
            "role": principal.role}


# ---------- 管理端 ----------
# role 不在任何请求体里：管理员只来自 ACCESS_TOKEN bootstrap，或来自运维手改
# users.json。给 API 开一个写 role 的口子，等于把整套身份体系作废。

@router.post("/v1/admin/invites")
async def create_invite(req: InviteRequest, actor: Principal = RequireAdmin):
    return {"code": _store().create_invite(actor.user_id, max_uses=req.max_uses)}


@router.get("/v1/admin/invites")
async def list_invites(_: Principal = RequireAdmin):
    keep = ("code", "max_uses", "created_at", "created_by", "expires_at", "used_by")
    return {"invites": [{k: i.get(k) for k in keep} for i in _store().list_invites()]}


@router.delete("/v1/admin/invites/{code}")
async def revoke_invite(code: str, _: Principal = RequireAdmin):
    # 码不必先归一化：存储层的 revoke 与 register 共用同一份规则（大写去空格）
    if not _store().revoke_invite(code):
        raise HTTPException(status_code=404, detail="邀请码不存在")
    return {"status": "revoked", "code": code}


def _public_user(record: dict) -> dict:
    """用户记录的对外视图。逐字段列出，是为了让 token_hash 无处可藏。"""
    return {
        "user_id": record.get("user_id"),
        "username": record.get("username"),
        "role": record.get("role"),
        "disabled": record.get("disabled"),
        "created_at": record.get("created_at"),
        # last_seen 粗粒度是设计使然：存储层为不把鉴权变成热路径写盘，把落盘
        # 节流到一小时以上，所以它读作"上次看见它至少是一小时前"，不是在线状态。
        "last_seen": record.get("last_used_at"),
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
    """换发新令牌。旧令牌立刻失效，且停用状态原样保留。"""
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
