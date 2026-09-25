# backend/tests/test_cookie_auth.py
"""方案 C 的行为契约：会话凭据是 httpOnly Cookie，凭据明文不进 JS。

这份文件钉的是"换了传送带之后，哪些语义必须原样成立、哪些新缝必须焊死"：
1. 签发收口：全服务端只有 /v1/auth/adopt 会 Set-Cookie。登录/注册/改密的响应
   一个字都不许多带 Cookie——那既是响应契约（token 字段原样），也是会话固定
   的防线：跨站的 login 响应若能直接种 Cookie，攻击者就能拿自己的账号把受害
   者的浏览器钉在自己的会话上。
2. 只认请求头：adopt 只收编"这次请求头里出示的"凭据。只剩 Cookie 的会话没有
   新东西可收编，400 打发——顺带让这条路由对 CSRF 天然免疫（跨站带不出
   Authorization 头）。
3. Cookie 的传送属性逐个钉死：HttpOnly（JS 读不到）、SameSite=Lax（跨站 POST
   带不出去，第一道 CSRF）、Path=/v1（静态页与文档面收不到它）、会话级
   （没有 Max-Age/Expires，进程退出即蒸发）。Secure 反过来钉"默认开"：
   忘配 env 不许 fail-open。
4. CSRF 第二道闸：不安全方法 + 凭据出自 Cookie + 没有 X-CSRF 头 = 403。
   带请求头凭据的客户端不受检查（它本来就不是 CSRF 的攻击面）。
5. 撤销对称：logout 不带任何请求头也能作废 Cookie 里那枚并刮掉 Cookie——
   否则"退出"在 Cookie 时代会变成假动作。

模式说明：全局 client 是 AUTH_MODE=disabled，中间件在 Cookie 分支之前就短路，
这里几乎所有用例都要 enforced（它同时换掉身份库与 ACCESS_TOKEN）。
"""
import pytest

from app.core import authz


def _set_cookie_headers(res):
    return [v for k, v in res.headers.multi_items() if k.lower() == "set-cookie"]


def _register(client, username="cookie-user", password="correct-horse-battery"):
    res = client.post("/v1/auth/register",
                      json={"username": username, "password": password,
                            "security_answers": ["新市场小学", "hehai2024", "李建国"]})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def _adopt(client, token, **kwargs):
    return client.post("/v1/auth/adopt",
                       headers={"Authorization": "Bearer " + token}, **kwargs)


# ---------- 签发收口 ----------


def test_only_adopt_issues_the_session_cookie(enforced, client):
    """登录、注册、改密的响应都不许带 Set-Cookie：发 Cookie 的门只有 adopt 一扇。"""
    token = _register(client)
    reg_res = client.post("/v1/auth/register",
                          json={"username": "second-one", "password": "correct-horse-battery",
                                "security_answers": ["新市场小学", "hehai2024", "李建国"]})
    assert _set_cookie_headers(reg_res) == [], "注册响应种了 Cookie：固定会话的缝回来了"
    login_res = client.post("/v1/auth/login",
                            json={"username": "cookie-user", "password": "correct-horse-battery"})
    assert login_res.status_code == 200
    assert _set_cookie_headers(login_res) == [], "登录响应种了 Cookie：同上"
    # 登录的响应体契约原样：token 字段还在，老 API 客户端一根手指头都不用改
    assert "token" in login_res.json()
    reset_res = client.post("/v1/auth/reset",
                            json={"username": "cookie-user",
                                  "answers": ["新市场小学", "hehai2024", "李建国"],
                                  "new_password": "another-correct-horse"})
    assert reset_res.status_code == 200
    assert _set_cookie_headers(reset_res) == [], "改密响应种了 Cookie：同上"
    # 作废了全部令牌之后再 adopt 旧明文：门后没有身份，Cookie 一枚都不许出去
    stale = _adopt(client, token)
    assert stale.status_code == 401
    assert _set_cookie_headers(stale) == [], "凭据没认下来却发了 Cookie"


def test_adopt_sets_exactly_one_session_cookie_with_locked_attributes(enforced, client):
    token = _register(client)
    res = _adopt(client, token)
    assert res.status_code == 200
    cookies = _set_cookie_headers(res)
    assert len(cookies) == 1 and cookies[0].startswith(f"{authz.SESSION_COOKIE}={token};"), \
        f"Cookie 的形状不对：{cookies}"
    low = cookies[0].lower()
    assert "httponly" in low, "没有 HttpOnly：document.cookie 就能读到会话，方案 C 白做"
    assert "samesite=lax" in low, "SameSite 不是 Lax：跨站 POST 能带出 Cookie"
    assert "path=/v1" in low, "Path 不收口到 /v1：静态面与文档面也会收到凭据"
    assert "max-age" not in low and "expires" not in low, \
        "会话 Cookie 不该有生命周期：浏览器进程退出就该蒸发"
    # 响应体是 me 同形的答案：adopt 成功的第一个用处就是当场确认身份
    assert res.json()["username"] == "cookie-user"


def test_secure_is_the_default_and_downgrade_must_be_explicit(enforced, client, monkeypatch):
    """忘配 env 的默认方向必须是 Secure=on：降级只能是主动行为。"""
    token = _register(client)
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    res = _adopt(client, token)
    assert "secure" in (_set_cookie_headers(res)[0].lower()), \
        "默认没开 Secure：现网忘配 env 就成了明文信道上的 Cookie"
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    assert "secure" not in (_set_cookie_headers(_adopt(client, token))[0].lower()), \
        "降级开关没生效：测试与本机 http 直连会被 Secure Cookie 卡死"


# ---------- adopt 只认请求头 ----------


def test_adopt_rejects_a_cookie_only_session(enforced, client):
    """只剩 Cookie 的人再来 adopt：没有新凭据可收编，400，Cookie 也不许重写。"""
    token = _register(client)
    assert _adopt(client, token).status_code == 200
    again = client.post("/v1/auth/adopt", headers={"X-CSRF": "1"})
    assert again.status_code == 400, "cookie-only adopt 放过了：每请求多一次静默续期面"
    assert _set_cookie_headers(again) == []


def test_adopt_is_protected_and_not_public(enforced, client):
    """adopt 不在任何公开名单里：没出示凭据就连 400 都轮不到，直接 401。"""
    res = client.post("/v1/auth/adopt")
    assert res.status_code == 401
    assert authz.is_public_path("/v1/auth/adopt") is False


# ---------- Cookie 会话真的能办事 ----------


def test_cookie_alone_authenticates_reads_on_protected_paths(enforced, client):
    token = _register(client)
    assert _adopt(client, token).status_code == 200
    me = client.get("/v1/auth/me")            # 不带任何凭据头，全靠 Cookie jar
    assert me.status_code == 200 and me.json()["username"] == "cookie-user"
    assert client.get("/v1/sessions").status_code == 200


def test_csrf_gate_blocks_unsafe_cookie_requests_and_passes_declared_ones(enforced, client):
    token = _register(client)
    assert _adopt(client, token).status_code == 200
    blocked = client.post("/v1/memory/add", json={"content": "跨站没声明", "summarize": False})
    assert blocked.status_code == 403, \
        "不安全方法带 Cookie 无 X-CSRF 竟然放行：CSRF 第二道闸没关门"
    assert authz.CSRF_DETAIL in blocked.text
    ok = client.post("/v1/memory/add", json={"content": "同源声明过了", "summarize": False},
                     headers={"X-CSRF": "1"})
    assert ok.status_code == 200, f"带声明的同源写被挡了：{ok.text}"
    # GET 不收检查：只读方法本来就不是改状态的那一半
    assert client.get("/v1/memory/list?limit=5").status_code == 200


def test_header_authenticated_clients_are_exempt_from_the_csrf_gate(enforced, client):
    """没换 Cookie 的老客户端（壳脚本、curl）不许被新闸门波及：带凭据头、不带声明，照旧通。"""
    header = enforced("legacy-script")
    res = client.post("/v1/memory/add", json={"content": "头认证不需要声明", "summarize": False},
                      headers=header)
    assert res.status_code == 200, res.text


def test_the_csrf_gate_fires_before_identity_housekeeping(enforced, client, monkeypatch):
    """坏 Cookie + 无声明的跨站 POST 先吃 403：不许替攻击者免费试探服务器的身份配置。"""
    client.cookies.set(authz.SESSION_COOKIE, "garbage-not-a-token", path="/v1")
    monkeypatch.setattr(authz, "auth_store", type("Empty", (), {
        "has_role": lambda self, role: False, "resolve": lambda self, c: None})())
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)
    res = client.post("/v1/memory/add", json={"content": "x", "summarize": False})
    assert res.status_code == 403, "本该先被 CSRF 挡下，而不是 503（没配身份的探测信道）"


# ---------- 撤销对称 ----------


def test_logout_without_headers_revokes_the_cookie_credential_and_clears_it(enforced, client):
    token = _register(client)
    assert _adopt(client, token).status_code == 200
    out = client.post("/v1/auth/logout", headers={"X-CSRF": "1"})   # 零凭据头
    assert out.status_code == 200
    cleared = _set_cookie_headers(out)
    assert cleared and authz.SESSION_COOKIE in cleared[0] and "expires" in cleared[0].lower(), \
        f"退出没刮掉 Cookie：{cleared}"
    # 服务端那枚真作废了：拿明文回头再认，认不出
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_a_rotated_or_admin_revoked_token_kills_the_cookie_session_too(enforced, client):
    """Cookie 只是同一枚令牌的传送带：撤销/轮换之后 Cookie 一起变废，不存在第二生命。"""
    token = _register(client)
    assert _adopt(client, token).status_code == 200
    store = authz.auth_store
    assert store.revoke(token) is not False
    assert client.get("/v1/auth/me").status_code == 401


def test_bootstrap_token_can_be_adopted_for_an_admin_cookie_session(enforced, client):
    """管理页粘贴 .env 口令走的就是这条：bootstrap 也能收编成 Cookie，角色原样。"""
    res = _adopt(client, "boot-token")
    assert res.status_code == 200 and res.json()["role"] == "admin"
    assert client.get("/v1/admin/users").status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
