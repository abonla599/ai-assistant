"""鉴权接线：把凭据解析成 Principal，并提供两个端点依赖。

与 core/auth.py 分家的原因：身份规则要能离线测，也不该被 web 框架绑住。
"""
import hmac
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import (BOOTSTRAP_TOKEN_ENV, Principal, _as_hash_bytes,
                           auth_store)
# 导出票据的路径形状只有存储层知道（长度跟着生成器走），这里引用而不是抄第二份。
from app.session.export_store import EXPORT_PATH_PREFIX, TICKET_PATH_RE

BOOTSTRAP_PRINCIPAL = Principal("default_user", "本机管理员", "admin")

# 无需凭据即可到达的端点，精确匹配。注册与登录本来就是给"还没有身份的人"用的，
# 所以它们必然公开——代价是这几个端点自己变成攻击面，防线全部落在 auth_router
# 的真实 IP 限流与"几种失败同一句话、同一份耗时"上（注册/登录/改密都是）。
# 找回只有改密这一条公开端点：三题是全站常量，页面自己渲染，不必问服务器要。
# 新增公开端点必须同时改这里，否则路由契约测试会红。
PUBLIC_PATHS = frozenset({"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset"})

# 免凭据的第二种形状：带变量段的公开路由。精确匹配的门今天只有票据兑换这一条
# 需要跨过去——链接本身就是凭据（128 位随机、5 分钟过期、一次作废，见
# app/session/export_store.py），壳 APK 的下载请求带不出 Authorization 头，
# 不匿名就没有任何文件能落进手机。
# 放行的是"整条路径恰好等于前缀+票据形状"（锚定的正则，不是前缀匹配）：
# /v1/exports/ 、/v1/exports/short、/v1/exports/x/y 都仍然要凭据。key 是路由
# 模板（给路由契约测试核对挂载表用），value 是中间件匹配具体请求用的正则。
# 新增一条带变量的公开路由 = 在这里点名 + 改 tests/test_route_auth_contract.py，
# 与 PUBLIC_PATHS 同一套"多一条就红"的规矩。
PUBLIC_ROUTE_TEMPLATES = {f"{EXPORT_PATH_PREFIX}{{ticket_id}}": TICKET_PATH_RE}

_PROTECTED_PREFIXES = ("/v1/", "/docs", "/redoc", "/openapi.json")

# 401 文案只有一份：中间件与依赖各写一遍迟早会漂移，而它是对客户端的语义承诺
# （"没有身份"，区别于 403 的"有身份但不够"）。
UNAUTHORIZED_DETAIL = "缺少或错误的访问凭据"


def is_public_path(path: str) -> bool:
    """这条**具体请求路径**免不免凭据：精确名单，或恰好整条命中票据形状。

    判定收在这一个函数里，中间件与测试共用同一份口径；写成两处各来一遍的
    话，改天漂移的那一半就是没人知道的免凭据门。
    """
    return path in PUBLIC_PATHS or any(p.match(path)
                                       for p in PUBLIC_ROUTE_TEMPLATES.values())


def _auth_mode() -> str:
    """每次请求现读。做成 import 期常量的话，测试就得 reload 模块才能切模式。"""
    return os.getenv("AUTH_MODE", "enforced").strip().lower()


def _bootstrap_token() -> str:
    return os.getenv(BOOTSTRAP_TOKEN_ENV, "").strip()


def docs_kwargs_for_mode(mode: str) -> dict:
    """按模式给出 FastAPI 构造参数——纯函数，不读 env，因此两种模式都断言得到。

    文档路由在 app 构造期就定死，请求期改不了。这个判定原先写在 main.py 里，
    测试要覆盖另一半就只能 importlib.reload(app.main)：reload 会把 sessions_store
    等模块级对象重新绑定到别处，而全局 client 早已抓住旧 app，后续任务给
    app.main 打补丁时就会静默错位。抽成函数后 main.py 只留一行传参。
    """
    if mode == "disabled":
        return {}
    # 路由表本身就是侦察材料，对外一律不给 openapi
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def _credential(request: Request) -> str:
    supplied = request.headers.get("authorization", "").strip()
    scheme, _, credential = supplied.partition(" ")
    # 认证方案名大小写不敏感（RFC 7235）；未写方案名时整值即凭据
    if not credential and scheme:
        credential = scheme
    elif scheme.lower() not in ("bearer", "token"):
        credential = ""
    return credential or request.headers.get("x-access-token", "").strip()


def _secrets_match(supplied: str, secret: str) -> bool:
    """常量时间比较，且不让畸形请求头变成 500。

    bootstrap 口令是外部可比对的秘密，所以不能图省事用 ==；但请求头是任意
    UTF-8，hmac.compare_digest 收到非 ASCII str 会抛 TypeError——那等于任何人
    用一个乱码令牌就能把鉴权打成 500。编码规则不在这里重写，直接沿用
    auth._as_hash_bytes：同一条"先按字节、非 ASCII 不崩"的规则只有一份权威，
    否则两处各自的边角情况迟早对不上。
    """
    return hmac.compare_digest(_as_hash_bytes(supplied), _as_hash_bytes(secret))


def resolve_principal(request: Request) -> Optional[Principal]:
    """解析请求身份。返回 None 表示"没有身份"，由调用方决定 401/403。"""
    credential = _credential(request)
    if not credential:
        return None
    bootstrap = _bootstrap_token()
    if bootstrap and _secrets_match(credential, bootstrap):
        return BOOTSTRAP_PRINCIPAL
    return auth_store.resolve(credential)


def _has_any_identity() -> bool:
    """库里没有任何管理员、也没配 bootstrap 时，服务端就没有可服务的身份。

    这里的 admin 检查只认运维手工写进 users.json 的逃生口：角色永不通过请求
    产生，否则任何人都能给自己提权。
    """
    return bool(_bootstrap_token()) or any(
        u.get("role") == "admin" for u in auth_store.list_users())


def install_auth(app) -> None:
    if _auth_mode() == "disabled":
        print("⚠️ AUTH_MODE=disabled：所有请求均以本机管理员身份运行，切勿用于公网")

    @app.middleware("http")
    async def authenticate(request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)
        if is_public_path(path):
            return await call_next(request)

        if _auth_mode() == "disabled":
            request.state.principal = BOOTSTRAP_PRINCIPAL
            return await call_next(request)

        if not _has_any_identity():
            return JSONResponse(status_code=503,
                                content={"detail": "服务端未配置访问凭据"})

        principal = resolve_principal(request)
        if principal is None:
            return JSONResponse(status_code=401, content={"detail": UNAUTHORIZED_DETAIL})
        request.state.principal = principal
        return await call_next(request)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)
    return principal


def require_admin(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


CurrentPrincipal = Depends(current_principal)
RequireAdmin = Depends(require_admin)
