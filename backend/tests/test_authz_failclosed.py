"""鉴权模式行为：401 / 503 / disabled 放行。

模式在请求期读 env，所以这里只需 monkeypatch 环境变量并换掉 auth_store 单例，
不必 reload 模块或重造 app。文档开关在构造期定死，改不了，所以它已抽成
authz.docs_kwargs_for_mode 纯函数——本文件因此一条 reload 都不需要。

本文件同时接住 test_auth.py 里那批旧全局口令中间件的 HTTP 测试：凭据解析规则
（方案名大小写不敏感、无方案名、x-access-token 回退）在新中间件里原样保留，
所以那些断言按新语义迁到这里；只服务于"一个口令放行所有人"模型的那批已删除。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core.auth import AuthStore, Principal
import app.core.authz as authz
from tests.conftest import RECOVERY_FIELDS


@pytest.fixture
def wired(client, tmp_path, monkeypatch):
    """换成临时身份库，并允许逐条测试自行设置 AUTH_MODE / ACCESS_TOKEN。"""
    store = AuthStore(path=str(tmp_path / "users.json"))
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


@pytest.fixture
def nothing_configured(client, tmp_path, monkeypatch):
    """AUTH_MODE 根本不存在（不是空串、也不是 disabled）+ 空 bootstrap + 空身份库。

    上面那些 enforced 用例都显式 setenv("AUTH_MODE", ...)，于是"env 压根没配"
    这个真实部署最常踩到的形态反倒无人钉：默认值一旦被改成 open-by-default，
    线上就是一台公网全开的 API，而测试全绿。这条 fixture 就是那个缺口。
    """
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("ACCESS_TOKEN", "")
    store = AuthStore(path=str(tmp_path / "users.json"))
    monkeypatch.setattr(authz, "auth_store", store)
    return client, store


def test_absent_auth_mode_defaults_to_enforced(nothing_configured):
    """默认值本身：漏配 env 必须落在 enforced，而不是 disabled / 空串之类。"""
    assert authz._auth_mode() == "enforced"


def test_absent_auth_mode_still_refuses_service(nothing_configured):
    """端到端一层：没有 env、没有口令、库里没有身份 → 拒绝服务，不是放行。"""
    client, _ = nothing_configured
    res = client.get("/v1/sessions")
    assert res.status_code == 503, "AUTH_MODE 缺失时必须 fail-closed"
    assert "未配置" in res.json()["detail"]


def test_absent_auth_mode_also_closes_docs(nothing_configured):
    """文档开关与中间件共用同一处默认值：默认下连路由表都不给。"""
    assert authz.docs_kwargs_for_mode(authz._auth_mode())["docs_url"] is None


def test_valid_token_is_accepted(wired):
    client, store = wired
    _, token = store.register(username="张三", password="correct-horse-battery")
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


def test_register_stays_public(wired, monkeypatch):
    client, store = wired
    # 连 bootstrap 口令一起清空：此刻服务端"没有任何身份"，除注册外一律 503。
    # 公开判定若被挪到 fail-closed 之后，第一个身份就永远申请不出来——门从里面
    # 焊死了。Task 3 之后端点已存在，所以这里断真 200：只看"没被鉴权挡下"的话，
    # 404（路由没了）和 500（注册逻辑炸了）都算通过。
    monkeypatch.setenv("ACCESS_TOKEN", "")
    res = client.post("/v1/auth/register",
                         json={"username": "公开注册", "password": "correct-horse-battery",
            **RECOVERY_FIELDS})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "user" and body["user_id"].startswith("u_")

    # fail-closed 的另一半：库里还没有任何管理员、也没配口令，除注册外一律拒绝
    assert client.get("/v1/sessions").status_code == 503

    # 配上口令之后这把令牌要立刻能用——公开注册发出去的身份不能被鉴权层拒认
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")
    me = client.get("/v1/auth/me",
                    headers={"Authorization": "Bearer " + body["token"]})
    assert me.status_code == 200, me.text
    assert me.json()["user_id"] == body["user_id"]


def test_identity_store_is_redirected_away_from_real_data():
    """conftest 里那行 env 一旦被删，测试就会去碰开发者真实的 data/users.json，
    而单例遇到它判定为"损坏"的文件是直接改名的——等于一次跑测就搬走别人的身份库，
    且不会有任何一条测试变红。所以把这个前提本身钉成断言。"""
    from app.core.auth import auth_store

    assert "ai-assistant-tests-" in auth_store.path


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


@pytest.mark.parametrize("mode", ["enforced", "Enforced", "", "dissabled"])
def test_docs_are_closed_for_anything_but_disabled(mode):
    """判定是纯函数，所以两种模式各自断言即可，不必 reload 整个 app.main。

    只认 "disabled" 这一个值，其余一律关掉文档——未知取值落到安全侧。
    """
    assert authz.docs_kwargs_for_mode(mode) == {
        "docs_url": None, "redoc_url": None, "openapi_url": None}


def test_docs_kwargs_are_fastapi_defaults_for_disabled():
    """disabled 才把三个参数交回 FastAPI 默认值（本机开发要看文档）。"""
    assert authz.docs_kwargs_for_mode("disabled") == {}


def test_docs_routes_exist_only_under_the_disabled_kwargs():
    """上一条只证字典，这条证它落到 app 上的结果：关掉的三条路由真的 404。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def probe_client(mode):
        probe_app = FastAPI(**authz.docs_kwargs_for_mode(mode))

        @probe_app.get("/v1/ping")
        def ping():
            return {"ok": True}

        return TestClient(probe_app)

    closed = probe_client("enforced")
    opened = probe_client("disabled")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert closed.get(path).status_code == 404, path
        assert opened.get(path).status_code == 200, path


def test_current_app_keeps_docs_because_tests_build_it_in_disabled_mode():
    """main.py 确实把 docs_kwargs_for_mode(_auth_mode()) 传给了 FastAPI(...)。

    全局 app 在 conftest 的 AUTH_MODE=disabled 下装配，文档路由必须在。
    反向（enforced 下关掉）由上面三条钉；以前用 reload 覆盖这两半，现在不需要。
    """
    from app.main import app

    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"


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

    store = AuthStore(path=str(tmp_path / "users.json"))
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
    principal, token = store.register(username="张三", password="correct-horse-battery")
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
    _, token = store.register(username="李四", password="correct-horse-battery")
    assert client.get("/v1/admin-only",
                      headers={"Authorization": "Bearer " + token}).status_code == 403


def test_dependency_without_any_credential_is_401(probe):
    client, _ = probe
    assert client.get("/v1/whoami").status_code == 401


# ---------- 项目根的 .env.example 不能教运维把管理员凭据交出去 ----------
# 装这台服务的人会把它直接抄成项目根的 .env（`docs/安装部署指南.md`「安全建议」第 2 条
# 说的就是这个动作），所以这里的注释会被当成操作说明读一遍。
# 它曾经写着"ACCESS_TOKEN 留空=不鉴权，仅适合本机开发""前端在「设置 → 连接」里填这个
# 值"——两句话在身份层之后都是假的，而第二句的假法恰好等于给每个用户 role=admin
# （能改写模型服务与密钥、列出/停用/删除用户、轮换他人令牌）。代码改对了、说明书还教人
# 开门，这类事故不会有任何测试变红，所以这里直接对文件本身下断言。

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def env_example():
    return (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_env_example_no_longer_promise_an_open_door(env_example):
    """三条假话一句都不许留下：留空不鉴权、只适合本机开发、让用户填这把口令。"""
    for forbidden in ("留空=不鉴权", "仅适合本机开发", "填这个值"):
        assert forbidden not in env_example, f".env.example 仍在教运维：{forbidden}"


def test_env_example_describes_the_bootstrap_credential_honestly(env_example):
    """ACCESS_TOKEN 是 bootstrap 管理员凭据；用户拿到的必须是自己注册换出的个人令牌。"""
    assert "bootstrap" in env_example, "没说清这把口令是什么身份"
    assert "admin" in env_example
    assert "密码" in env_example, "没指出用户应该怎么拿到自己的令牌"
    assert "503" in env_example, "没说清留空的真实后果是 fail-closed 而不是敞开"


def test_env_example_documents_both_auth_modes(env_example):
    """两种取值的差别（401/文档路由）必须写在发出去的那份文件里，不只写在指南里。"""
    assert "AUTH_MODE" in env_example
    assert "enforced" in env_example and "disabled" in env_example


def test_env_example_documents_every_data_placement_var(env_example):
    """安装部署指南承诺"每一份数据都能用环境变量指走"，那七份都得在这份模板里。

    缺一个名字的后果不是不兼容，而是运维照模板配完才发现那份数据挪不走——
    而最挪不走的那几份恰好是最敏感的（users.json 里是密码摘要与会话令牌摘要）。
    """
    for var in ("SESSION_DB_PATH", "USERS_DB_PATH",
                "PROVIDERS_DB_PATH", "UPLOAD_DIR", "CHROMA_DB_PATH",
                "FEEDBACK_FILE", "PREFERENCE_FILE"):
        assert var in env_example, f".env.example 漏了 {var}，指南与模板对不上"
