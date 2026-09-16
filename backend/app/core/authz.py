"""鉴权接线：把凭据解析成 Principal，并提供两个端点依赖。

与 core/auth.py 分家的原因：身份规则要能离线测，也不该被 web 框架绑住。
"""
import hmac
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import BOOTSTRAP_TOKEN_ENV, Principal, auth_store

BOOTSTRAP_PRINCIPAL = Principal("default_user", "本机管理员", "admin")

# 唯一无需凭据的端点。新增公开端点必须同时改这里，否则路由契约测试会红。
PUBLIC_PATHS = frozenset({"/v1/auth/register"})
_PROTECTED_PREFIXES = ("/v1/", "/docs", "/redoc", "/openapi.json")


def _auth_mode() -> str:
    """每次请求现读。做成 import 期常量的话，测试就得 reload 模块才能切模式。"""
    return os.getenv("AUTH_MODE", "enforced").strip().lower()


def _bootstrap_token() -> str:
    return os.getenv(BOOTSTRAP_TOKEN_ENV, "").strip()


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
    UTF-8，hmac.compare_digest 收到非 ASCII str 会抛 TypeError——那等于任何
    人用一个乱码令牌就能把鉴权打成 500。先按字节编码再比。
    """
    return hmac.compare_digest(supplied.encode("utf-8", "surrogatepass"),
                               secret.encode("utf-8", "surrogatepass"))


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
        if path in PUBLIC_PATHS:
            return await call_next(request)

        if _auth_mode() == "disabled":
            request.state.principal = BOOTSTRAP_PRINCIPAL
            return await call_next(request)

        if not _has_any_identity():
            return JSONResponse(status_code=503,
                                content={"detail": "服务端未配置访问凭据"})

        principal = resolve_principal(request)
        if principal is None:
            return JSONResponse(status_code=401, content={"detail": "缺少或错误的访问凭据"})
        request.state.principal = principal
        return await call_next(request)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="缺少或错误的访问凭据")
    return principal


def require_admin(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


CurrentPrincipal = Depends(current_principal)
RequireAdmin = Depends(require_admin)
