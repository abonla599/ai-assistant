"""鉴权模式行为：401 / 503 / disabled 放行。

模式在请求期读 env，所以这里只需 monkeypatch 环境变量并换掉 auth_store 单例，
不必 reload 模块或重造 app。

本文件同时接住 test_auth.py 里那批旧全局口令中间件的 HTTP 测试：凭据解析规则
（方案名大小写不敏感、无方案名、x-access-token 回退）在新中间件里原样保留，
所以那些断言按新语义迁到这里；只服务于"一个口令放行所有人"模型的那批已删除。
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core.auth import AuthStore, Principal
import app.core.authz as authz


@pytest.fixture
def wired(client, tmp_path, monkeypatch):
    """换成临时身份库，并允许逐条测试自行设置 AUTH_MODE / ACCESS_TOKEN。"""
    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    monkeypatch.setattr(authz, "auth_store", store)
    monkeypatch.setenv("AUTH_MODE", "enforced")
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")
    return client, store


def test_no_credentials_at_all_is_closed_not_open(wired, monkeypatch):
    """忘配 env 不该等于公网裸奔。"""
    client, _ = wired
    monkeypatch.setenv("ACCESS_TOKEN", "")
    res = client.get("/v1/sessions")
    assert res.status_code == 503
    assert "未配置" in res.json()["detail"]


def test_valid_token_is_accepted(wired):
    client, store = wired
    code = store.create_invite("admin")
    _, token = store.register(code=code, username="张三")
    res = client.get("/v1/sessions", headers={"Authorization": "Bearer " + token})
    assert res.status_code == 200


def test_bad_token_is_401(wired):
    client, _ = wired
    assert client.get("/v1/sessions", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_missing_token_is_401(wired):
    client, _ = wired
    assert client.get("/v1/sessions").status_code == 401


def test_bootstrap_access_token_maps_to_admin(wired):
    """本机 EXE 与已发出的 APK 靠这条继续可用。"""
    client, _ = wired
    res = client.get("/v1/providers", headers={"Authorization": "Bearer boot-token"})
    assert res.status_code == 200


def test_disabled_mode_treats_everything_as_admin(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    assert client.get("/v1/providers").status_code == 200


def test_register_stays_public(wired):
    client, store = wired
    code = store.create_invite("admin")
    res = client.post("/v1/auth/register", json={"code": code, "username": "公开注册"})
    # 端点本身要等 Task 3 才挂上，此刻它当然是 404。这里钉的是另一件事，而且
    # 是更要紧的那件：鉴权层绝不能把注册页挡在凭据后面——否则没人拿得到第一
    # 个身份，fail-closed 就成了永久锁死的门。
    assert res.status_code not in (401, 403, 503), "注册端点必须无需凭据即可访问"


def test_identity_store_is_redirected_away_from_real_data():
    """conftest 里那两行 env 一旦被删，测试就会去碰开发者真实的 data/users.json，
    而单例遇到它判定为"损坏"的文件是直接改名的——等于一次跑测就搬走别人的身份库，
    且不会有任何一条测试变红。所以把这个前提本身钉成断言。"""
    from app.core.auth import auth_store

    assert "ai-assistant-tests-" in auth_store.path
    assert "ai-assistant-tests-" in auth_store.invites_path


# ---------- 凭据解析（自 test_auth.py 的旧中间件测试迁移） ----------


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_auth_scheme_name_is_case_insensitive(wired, scheme):
    """RFC 7235：认证方案名大小写不敏感"""
    client, _ = wired
    res = client.get("/v1/providers", headers={"Authorization": f"{scheme} boot-token"})
    assert res.status_code == 200, scheme


def test_bare_credential_without_scheme_is_accepted(wired):
    """旧客户端整值塞口令，解析规则没变，就不该在升级后被挡。"""
    client, _ = wired
    assert client.get("/v1/providers", headers={"Authorization": "boot-token"}).status_code == 200


def test_x_access_token_header_still_works(wired):
    """APK 里用的是自定义头，换中间件不是让它掉线的理由。"""
    client, _ = wired
    assert client.get("/v1/providers", headers={"X-Access-Token": "boot-token"}).status_code == 200


def test_unknown_scheme_is_not_treated_as_a_credential(wired):
    client, _ = wired
    res = client.get("/v1/providers", headers={"Authorization": "Basic boot-token"})
    assert res.status_code == 401


def test_garbled_non_ascii_credential_is_401_not_500(wired):
    """字节头经 Starlette 按 latin-1 解码，于是"令牌"可以是任意非 ASCII str。
    hmac.compare_digest 收到非 ASCII str 会抛 TypeError——那等于任何人用一个
    乱码头就把鉴权打成 500。正确表现是"这枚凭据解不出来"。"""
    client, _ = wired
    res = client.get("/v1/providers",
                     headers={"Authorization": "Bearer 坏：令牌".encode("utf-8")})
    assert res.status_code == 401


def test_stream_endpoint_is_not_a_gap_in_the_guard(wired):
    """流式端点走的是另一条响应路径，也一样在 /v1/ 前缀下。"""
    client, _ = wired
    res = client.post("/v1/chat/stream",
                      json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 401


# ---------- 公开面与侦察面 ----------


def test_pwa_shell_and_health_stay_public(wired):
    """页面本身不含密钥；挡掉它等于让新用户连注册入口都找不到。"""
    client, _ = wired
    assert client.get("/app/").status_code == 200
    assert client.get("/health").status_code == 200


def test_docs_are_behind_credentials_when_enforced(wired):
    """接口文档列出全部端点，公网隧道上不该免凭据可读。"""
    client, _ = wired
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 401, path


def test_openapi_endpoints_only_exist_in_disabled_mode(monkeypatch):
    """非 disabled 模式下路由表本身都不生成。

    FastAPI 在构造 app 时就决定了文档路由，改不了，所以这条只能整模块重载；
    测完立刻按 disabled 再重载一次复位，免得把关掉文档的状态留给后面的测试。
    """
    import app.main as main_module

    monkeypatch.setenv("AUTH_MODE", "enforced")
    reloaded = importlib.reload(main_module)
    assert reloaded.app.docs_url is None
    assert reloaded.app.redoc_url is None
    assert reloaded.app.openapi_url is None

    monkeypatch.setenv("AUTH_MODE", "disabled")
    importlib.reload(main_module)


# ---------- 端点依赖 ----------


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """一个只挂了身份依赖的探针 app。

    Task 3-7 的每个端点都靠中间件把 request.state.principal 交到依赖手里，
    这条链路本身（而不是两个函数的返回值）才是它们共同的地基。
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from app.core.authz import current_principal, install_auth, require_admin

    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    monkeypatch.setattr(authz, "auth_store", store)
    monkeypatch.setenv("AUTH_MODE", "enforced")
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")

    probe_app = FastAPI()
    install_auth(probe_app)

    @probe_app.get("/v1/whoami")
    def whoami(p: Principal = Depends(current_principal)):
        return {"user_id": p.user_id, "role": p.role}

    @probe_app.get("/v1/admin-only")
    def admin_only(p: Principal = Depends(require_admin)):
        return {"ok": True}

    return TestClient(probe_app), store


def test_identity_reaches_the_endpoint_dependency(probe):
    client, store = probe
    code = store.create_invite("admin")
    principal, token = store.register(code=code, username="张三")
    res = client.get("/v1/whoami", headers={"Authorization": "Bearer " + token})
    assert res.status_code == 200
    assert res.json() == {"user_id": principal.user_id, "role": "user"}


def test_require_admin_lets_the_bootstrap_principal_through(probe):
    client, _ = probe
    res = client.get("/v1/admin-only", headers={"Authorization": "Bearer boot-token"})
    assert res.status_code == 200


def test_require_admin_rejects_a_normal_user_with_403_not_401(probe):
    """身份是有的、权限不够，两者必须分得开——后续所有管理端点都靠这一道。"""
    client, store = probe
    code = store.create_invite("admin")
    _, token = store.register(code=code, username="李四")
    assert client.get("/v1/admin-only",
                      headers={"Authorization": "Bearer " + token}).status_code == 403


def test_dependency_without_any_credential_is_401(probe):
    client, _ = probe
    assert client.get("/v1/whoami").status_code == 401
