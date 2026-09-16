"""注册与管理端点的 HTTP 契约。conftest 已把请求当本机管理员。

需要"真实身份"的断言（401/403、me 解析到谁）一律走 enforced fixture：
全局 client 是 AUTH_MODE=disabled，在那里人人都是本机管理员，凭据根本不被解析。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.core.auth import auth_store
from app.main import app

client = TestClient(app)

# enforced fixture 把 bootstrap 口令设成这个值，管理端点在它之下才有意义
BOOT = {"Authorization": "Bearer boot-token"}

# 限流账本的清账 autouse fixture 在 conftest.py（整个套件共用，别再复制一份）。


def _budget_used() -> int:
    """账本里已记了多少次失败。_throttled() 读的时候会往 defaultdict 塞一个空
    列表，所以"有没有键"说明不了问题，只能数时间戳。"""
    from app.core.auth_router import _FAILS

    return sum(len(v) for v in _FAILS.values())


def _register(username="张三"):
    code = auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register", json={"code": code, "username": username})
    assert res.status_code == 200, res.text
    return res.json(), code


def _uid_of(c, username):
    """在 enforced 库里按用户名找到 user_id（该库里注册的用户只能这样拿到 id）。"""
    users = c.get("/v1/admin/users", headers=BOOT).json()["users"]
    return next(u["user_id"] for u in users if u["username"] == username)


# ---------- 注册 ----------


def test_register_returns_token_once():
    body, _ = _register("李四")
    assert body["token"] and body["username"] == "李四" and body["role"] == "user"
    assert body["user_id"].startswith("u_")


def test_duplicate_username_is_409():
    """握着有效未用码的人才听得到"这名字被占了"——改名就能继续，不必去缠管理员。
    没码的人问同一句话只会得到"邀请码无效"，见
    test_a_taken_username_reveals_nothing_to_a_caller_without_a_valid_code。"""
    _register("重复名")
    code = auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register", json={"code": code, "username": "重复名"})
    assert res.status_code == 409


def test_bad_invite_is_403_without_revealing_why():
    res = client.post("/v1/auth/register", json={"code": "ZZZZ-ZZZZ", "username": "某人"})
    assert res.status_code == 403
    detail = res.json()["detail"].lower()
    assert "不存在" not in detail and "已用" not in detail


def test_a_used_up_code_and_an_unknown_code_look_identical():
    """码存在但已用尽，与码压根不存在，必须是同一句话——否则这个端点就成了
    "哪些邀请码真实存在"的探测器。"""
    code = auth_store.create_invite("default_user")
    client.post("/v1/auth/register", json={"code": code, "username": "先占一个"})
    spent = client.post("/v1/auth/register", json={"code": code, "username": "再占一个"})
    missing = client.post("/v1/auth/register", json={"code": "QQQQ-QQQQ", "username": "再占一个"})
    assert (spent.status_code, missing.status_code) == (403, 403)
    assert spent.json()["detail"] == missing.json()["detail"]


def _outcome(res):
    return (res.status_code, res.json()["detail"])


def test_a_taken_username_reveals_nothing_to_a_caller_without_a_valid_code():
    """注册端点是唯一的免凭据端点：它不能回答"这个名字被占了吗"。

    存储层的检查顺序（先验码、后查占名）保证这一点——没有码的人，无论用户名
    撞没撞、合不合格，听到的都是同一句"邀请码无效"。反过来先查占名，公网隧道上
    任何人都能白嫖一份"谁在这里注册过"的名单，而用户名是拿去撞别的服务的通货。
    握着有效未用码的人仍然听得到真话（test_duplicate_username_is_409）。
    """
    _register("独占者")
    no_code = {"code": "ZZZZ-ZZZZ"}

    taken = client.post("/v1/auth/register", json={**no_code, "username": "独占者"})
    free = client.post("/v1/auth/register", json={**no_code, "username": "没人用的名字"})
    malformed = client.post("/v1/auth/register", json={**no_code, "username": "x" * 25})
    reserved = client.post("/v1/auth/register", json={**no_code, "username": "admin"})
    for res in (taken, free, malformed, reserved):
        assert _outcome(res) == (403, "邀请码无效"), _outcome(res)

    # 码已用尽 + 撞名：同样只许说"邀请码无效"，不许漏出 409
    spent = auth_store.create_invite("default_user")
    client.post("/v1/auth/register", json={"code": spent, "username": "先用掉的人"})
    assert _outcome(client.post("/v1/auth/register",
                               json={"code": spent, "username": "独占者"})) == (403, "邀请码无效")


def test_missing_fields_are_422():
    for body in ({"code": "AAAA-BBBB"}, {"username": "缺码"}, {}):
        res = client.post("/v1/auth/register", json=body)
        assert res.status_code == 422, body


def test_a_bad_username_is_not_reported_as_a_bad_invite():
    """用户名不合格要照实说：把它推给"邀请码无效"，用户会去要新码、再撞一次同样的墙。

    前提是这人手里确实有一枚有效码（没码的人听到的仍然是"邀请码无效"，见
    test_a_taken_username_reveals_nothing_to_a_caller_without_a_valid_code）；
    也正因为这是"已有资格的人在打错字"，它一律不烧限流预算。
    """
    res = client.post("/v1/auth/register",
                      json={"code": auth_store.create_invite("default_user"),
                            "username": "x" * 25})
    assert res.status_code == 422
    assert "邀请码" not in res.json()["detail"]
    assert _budget_used() == 0, "用户名不合格不是攻击，不该进限流账本"


def test_registration_throttles_repeated_failures():
    """猜码必须有代价。

    邀请码是 40 位随机值，"锁某个码"对猜测攻击没有意义（被猜的码根本不存在），
    真正有效的是限制同一来源的失败次数。
    """
    from app.core.auth_router import FAILURE_WINDOW_SECONDS, MAX_FAILURES_PER_WINDOW

    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register",
                          json={"code": f"AAAA-{i:04d}", "username": "猜一猜"})
        assert res.status_code == 403, f"第 {i} 次应当仍是 403"
    assert _budget_used() == MAX_FAILURES_PER_WINDOW, "前提：坏码确实被记了下来"
    blocked = client.post("/v1/auth/register",
                          json={"code": auth_store.create_invite("default_user"),
                                "username": "正当用户"})
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after") == str(FAILURE_WINDOW_SECONDS)


def test_only_bad_invite_codes_cost_the_throttle_budget():
    """限流的代价只由猜码付：撞名（409）与用户名不合格（422）一格都不记账。

    真实部署里 cloudflared 连的是本机 :8000，所有远程朋友在 uvicorn 眼里都是
    同一个 127.0.0.1 —— 一个共享的桶。把打字错误算进预算，后果是"一个人撞两次
    名，全网唯一的 onboarding 入口锁 10 分钟"，而收益是零：面对 40 位随机邀请码，
    猜测者的每一次尝试本来就记一格，多记不记撞名根本不改变他的代价。
    """
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    _register("占用者")
    code = auth_store.create_invite("default_user")

    for i in range(MAX_FAILURES_PER_WINDOW + 2):
        taken = client.post("/v1/auth/register",
                            json={"code": code, "username": "占用者"})
        assert taken.status_code == 409, f"第 {i} 次撞名应当仍是 409"
        malformed = client.post("/v1/auth/register",
                               json={"code": code, "username": "y" * 25})
        assert malformed.status_code == 422, f"第 {i} 次用户名不合格应当仍是 422"

    assert _budget_used() == 0, "无害的输入错误不该进限流账本"
    used = [i for i in auth_store.list_invites() if i["code"] == code][0]["used_by"]
    assert used == [], "失败的注册不该烧掉邀请码，否则第 2 次就变 403 了"
    ok = client.post("/v1/auth/register",
                     json={"code": auth_store.create_invite("default_user"),
                           "username": "手滑之后的人"})
    assert ok.status_code == 200, "预算完好，正当注册不该被别人的手滑挡住"

    # 反向：猜码依旧有代价，一格一格填满到 429
    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register",
                          json={"code": f"KKKK-{i:04d}", "username": "这回收钱"})
        assert res.status_code == 403, f"第 {i} 次应当仍是 403"
    assert _budget_used() == MAX_FAILURES_PER_WINDOW
    assert client.post("/v1/auth/register",
                       json={"code": auth_store.create_invite("default_user"),
                             "username": "被挡住的人"}).status_code == 429


def test_a_success_resets_the_throttle():
    """一次成功注册说明这来源确实是正当用户，别让他之前的手滑继续记账。"""
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    for i in range(MAX_FAILURES_PER_WINDOW - 1):
        client.post("/v1/auth/register", json={"code": f"CCCC-{i:04d}", "username": "手滑"})
    assert _budget_used() == MAX_FAILURES_PER_WINDOW - 1, "前提：坏码确实被记了下来"
    _register("重置者")
    assert _budget_used() == 0, "成功后这来源的计数必须清零"
    res = client.post("/v1/auth/register", json={"code": "DDDD-DDDD", "username": "再猜"})
    assert res.status_code == 403, "清零之后又重新计得起数"


def test_the_throttle_forgets_once_the_window_passes(monkeypatch):
    """限流必须自己松开：永不过期的计数器本身就是"让所有人注册不了"的 DoS 靶子。"""
    from app.core import auth_router
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    moment = [0.0]
    monkeypatch.setattr(auth_router, "_now", lambda: moment[0])

    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register",
                          json={"code": f"HHHH-{i:04d}", "username": "窗口内"})
        assert res.status_code == 403, f"第 {i} 次应当仍是 403"
    assert client.post("/v1/auth/register",
                       json={"code": auth_store.create_invite("default_user"),
                             "username": "窗口内的正当用户"}).status_code == 429

    moment[0] = float(auth_router.FAILURE_WINDOW_SECONDS) + 1
    res = client.post("/v1/auth/register",
                      json={"code": auth_store.create_invite("default_user"),
                            "username": "窗口后的正当用户"})
    assert res.status_code == 200, res.text


# 原先这里还有一条 test_trailing_slash_is_not_a_credential_free_door：它断的是
# authz 中间件"精确匹配 PUBLIC_PATHS"，把 auth_router 整个摘掉它照样绿，测的从来
# 不是注册端点。已挪到 tests/test_route_auth_contract.py，与公开面钉在一起；
# 留在下面的这条才是注册端点自己的事（斜杠变体烧的是同一份限流预算）。


def test_trailing_slash_cannot_dodge_the_throttle():
    """带斜杠不是第二条免计数的入口：它烧的是同一个来源的同一份预算。"""
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW, _FAILS

    # 先让斜杠变体把预算用光（它会经 307 归一化到真实端点，失败照常计数）
    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register/",
                          json={"code": f"FFFF-{i:04d}", "username": "斜杠猜码"})
        assert res.status_code == 403, f"第 {i} 次斜杠尝试应当仍是 403"
    canonical = client.post("/v1/auth/register",
                            json={"code": auth_store.create_invite("default_user"),
                                  "username": "斜杠后来者"})
    assert canonical.status_code == 429, "斜杠攒下的失败必须作用到无斜杠路径上"

    # 反向同理：无斜杠用光预算后，斜杠变体也别想拿到一次没被限流的注册。
    # 这一句必须在测试中间清账，autouse fixture 只保证进出用例时是空的。
    _FAILS.clear()
    for i in range(MAX_FAILURES_PER_WINDOW):
        client.post("/v1/auth/register", json={"code": f"GGGG-{i:04d}", "username": "无斜杠猜码"})
    slashed = client.post("/v1/auth/register/",
                          json={"code": auth_store.create_invite("default_user"),
                                "username": "斜杠正当用户"})
    assert slashed.status_code == 429, "斜杠变体必须与真实端点共用同一个限流窗口"


# ---------- /v1/auth/me ----------


def test_me_reports_resolved_identity(client, enforced):
    """必须走 enforced：disabled 下中间件不看凭据，me 只会返回本机管理员。"""
    hdrs = enforced("郑九")
    res = client.get("/v1/auth/me", headers=hdrs)
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "郑九" and body["role"] == "user"
    assert body["user_id"].startswith("u_")
    assert set(body) == {"user_id", "username", "role"}


def test_registered_token_works_on_every_protected_route(client, enforced):
    """注册 → 拿令牌 → 用令牌，全程走 HTTP。

    中间件读 authz.auth_store、端点也得读同一个库，否则发出去的令牌谁都解不出来：
    这条是这层接线唯一的端到端证据。
    """
    code = client.post("/v1/admin/invites", headers=BOOT, json={}).json()["code"]
    res = client.post("/v1/auth/register", json={"code": code, "username": "自助注册"})
    assert res.status_code == 200, res.text
    hdrs = {"Authorization": "Bearer " + res.json()["token"]}
    assert client.get("/v1/auth/me", headers=hdrs).json()["username"] == "自助注册"
    assert client.get("/v1/sessions", headers=hdrs).status_code == 200
    assert client.get("/v1/admin/users", headers=hdrs).status_code == 403


# ---------- 管理端：谁能用 ----------


def test_admin_endpoints_reject_non_admin(client, enforced):
    # 必须走 enforced 模式：全局 conftest 是 disabled，人人都是管理员，403 无从发生
    hdrs = enforced("王五")
    assert client.get("/v1/admin/users", headers=hdrs).status_code == 403
    assert client.post("/v1/admin/invites", json={}, headers=hdrs).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/v1/admin/users"),
    ("get", "/v1/admin/invites"),
    ("post", "/v1/admin/invites"),
    ("delete", "/v1/admin/invites/AAAA-BBBB"),
    ("post", "/v1/admin/users/u_some/disable"),
    ("post", "/v1/admin/users/u_some/enable"),
    ("post", "/v1/admin/users/u_some/rotate-token"),
    ("delete", "/v1/admin/users/u_some"),
])
def test_no_admin_route_leaks_past_the_role_check(client, enforced, method, path):
    """管理面每一条都要过 require_admin，漏一条等于把整套身份体系作废。"""
    hdrs = enforced("普通用户")
    kwargs = {"headers": hdrs}
    if method == "post":
        kwargs["json"] = {}
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 403, f"{method.upper()} {path} -> {res.status_code}"


# 原先这里还有一条 test_admin_endpoints_are_not_reachable_without_credentials：
# 401 是中间件在路由之前发的，把 auth_router 摘掉它仍然绿，所以它守的是"凭据先于
# 路由"这条中间件性质，不是管理端点。已挪到 tests/test_route_auth_contract.py。


# ---------- 管理端：邀请码 ----------


def test_invite_revoke_and_404_on_unknown():
    code = auth_store.create_invite("default_user")
    assert client.delete(f"/v1/admin/invites/{code}").status_code == 200
    assert client.delete("/v1/admin/invites/NOPE-NOPE").status_code == 404
    assert client.post("/v1/admin/users/u_deadbeef/disable").status_code == 404


def test_revoke_is_case_insensitive_like_the_http_path_promises():
    """管理员在手机上手输小写码，不该拿到一个假 404。"""
    code = auth_store.create_invite("default_user")
    assert client.delete(f"/v1/admin/invites/{code.lower()}").status_code == 200
    assert code not in [i["code"] for i in auth_store.list_invites()]


def test_revoking_an_invite_never_echoes_the_code_back():
    """全局约束：响应与日志里不得出现令牌/邀请码明文。

    撤销的响应没有任何理由复述客户端刚提交的那串码——多一处出口，就多一处被
    访问日志、浏览器历史或前端 console 留下来的机会。成功只回 status，
    失败只回一句与人无关的话。
    """
    code = auth_store.create_invite("default_user")
    res = client.delete(f"/v1/admin/invites/{code}")
    assert res.status_code == 200
    assert res.json() == {"status": "revoked"}
    assert code not in res.text

    missing = client.delete("/v1/admin/invites/NOPE-NOPE")
    assert missing.status_code == 404
    assert "NOPE-NOPE" not in missing.text


def test_invite_listing_exposes_only_what_an_admin_needs():
    code = auth_store.create_invite("default_user", max_uses=2)
    res = client.get("/v1/admin/invites")
    assert res.status_code == 200
    invite = [i for i in res.json()["invites"] if i["code"] == code][0]
    assert set(invite) == {"code", "max_uses", "created_at", "created_by",
                           "expires_at", "used_by"}
    assert invite["max_uses"] == 2 and invite["used_by"] == []
    assert "token" not in res.text


def test_max_uses_from_the_api_is_honoured():
    code = client.post("/v1/admin/invites", json={"max_uses": 2}).json()["code"]
    assert client.post("/v1/auth/register",
                       json={"code": code, "username": "共用一号"}).status_code == 200
    assert client.post("/v1/auth/register",
                       json={"code": code, "username": "共用二号"}).status_code == 200
    assert client.post("/v1/auth/register",
                       json={"code": code, "username": "共用三号"}).status_code == 403


# ---------- 管理端：用户 ----------


def test_user_list_never_leaks_token_hash():
    created, _ = _register("孙六")
    res = client.get("/v1/admin/users")
    assert res.status_code == 200
    text = res.text
    assert "token_hash" not in text and created["token"] not in text
    from app.core.auth import hash_token
    assert hash_token(created["token"]) not in text, "换个键名把摘要放出去同样是泄露"
    row = [u for u in res.json()["users"] if u["user_id"] == created["user_id"]][0]
    assert set(row) == {"user_id", "username", "role", "disabled", "created_at", "last_seen"}


def test_the_activity_field_is_named_last_seen_because_it_is_coarse():
    """存储层为省热路径写盘把落盘节流到一小时，它不是"最后活动时间"。
    键名叫 last_used_at 会诱导前端把它当在线状态用。"""
    created, _ = _register("观察")
    row = [u for u in client.get("/v1/admin/users").json()["users"]
           if u["user_id"] == created["user_id"]][0]
    assert "last_used_at" not in row and row["last_seen"]


def test_disable_takes_effect_immediately():
    created, _ = _register("周七")
    assert client.post(f"/v1/admin/users/{created['user_id']}/disable").status_code == 200
    # disabled 模式下中间件不看凭据，故这里用真实 resolve 断言撤销已生效
    assert auth_store.resolve(created["token"]) is None


def test_enable_undoes_disable_because_rotate_no_longer_does():
    created, _ = _register("被误停者")
    uid = created["user_id"]
    client.post(f"/v1/admin/users/{uid}/disable")
    assert auth_store.resolve(created["token"]) is None
    res = client.post(f"/v1/admin/users/{uid}/enable")
    assert res.status_code == 200
    assert res.json()["status"] == "enabled"
    assert auth_store.resolve(created["token"]) is not None, "重新启用必须让原令牌复活"
    assert client.post("/v1/admin/users/u_deadbeef/enable").status_code == 404


def test_disable_and_enable_change_real_access(client, enforced):
    """在真正会解析凭据的模式下走一遍停用→401→启用→200。"""
    hdrs = enforced("被停的人")
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 200
    uid = _uid_of(client, "被停的人")
    assert client.post(f"/v1/admin/users/{uid}/disable", headers=BOOT).status_code == 200
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 401, "停用必须立刻生效"
    assert client.post(f"/v1/admin/users/{uid}/enable", headers=BOOT).status_code == 200
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 200


def test_rotate_token_invalidates_old():
    created, _ = _register("吴八")
    res = client.post(f"/v1/admin/users/{created['user_id']}/rotate-token")
    assert res.status_code == 200
    new = res.json()["token"]
    assert new != created["token"]
    assert auth_store.resolve(created["token"]) is None
    assert auth_store.resolve(new) is not None


def test_rotate_keeps_a_disabled_user_disabled():
    """换令牌是凭证动作，不是重新启用账号——否则它把本任务的存在意义抵消掉。"""
    created, _ = _register("停用的轮换者")
    uid = created["user_id"]
    client.post(f"/v1/admin/users/{uid}/disable")
    fresh = client.post(f"/v1/admin/users/{uid}/rotate-token").json()["token"]
    assert auth_store.resolve(fresh) is None
    row = [u for u in client.get("/v1/admin/users").json()["users"] if u["user_id"] == uid][0]
    assert row["disabled"] is True


def test_unknown_user_is_404_because_it_is_absent_not_because_the_route_is_gone():
    """404 只在"同一条路对真用户回 200"的前提下才有意义。

    原先这里只断四个 404：把 auth_router 整个摘掉，每条路由都改成"路由不存在"的
    404，测试照样全绿——那不是断言，是巧合。所以先用一个真用户把这四条路走活，
    再要求不存在的 id 得到同一个 404（而不是 403/500，那才是要防的泄露）。
    """
    created, _ = _register("四条路都走一遍")
    uid = created["user_id"]
    assert client.post(f"/v1/admin/users/{uid}/disable").status_code == 200
    assert client.post(f"/v1/admin/users/{uid}/enable").status_code == 200
    assert client.post(f"/v1/admin/users/{uid}/rotate-token").status_code == 200
    assert client.delete(f"/v1/admin/users/{uid}").status_code == 200

    for path in ("/v1/admin/users/u_none/disable", "/v1/admin/users/u_none/enable",
                 "/v1/admin/users/u_none/rotate-token"):
        assert client.post(path).status_code == 404, path
    assert client.delete("/v1/admin/users/u_none").status_code == 404


def test_delete_user_removes_it():
    created, _ = _register("待删者")
    uid = created["user_id"]
    assert client.delete(f"/v1/admin/users/{uid}").json()["status"] == "deleted"
    assert auth_store.resolve(created["token"]) is None
    assert uid not in [u["user_id"] for u in client.get("/v1/admin/users").json()["users"]]


def test_admin_cannot_delete_the_identity_it_is_signed_in_as():
    """删掉自己正在用的身份 = 把自己关在门外，这种操作不能靠手滑发生。"""
    res = client.delete("/v1/admin/users/default_user")
    assert res.status_code == 400
    assert "自己" in res.json()["detail"]


def test_no_endpoint_can_grant_the_admin_role():
    """提权必须是 API 之外的动作（只有 ACCESS_TOKEN 是管理员）。
    一旦请求体能写 role，注册端点就成了给任何人发管理员的公开课。"""
    import inspect

    from pydantic import BaseModel

    from app.core import auth_router

    fields = set()
    for _, obj in inspect.getmembers(auth_router, inspect.isclass):
        if issubclass(obj, BaseModel) and getattr(obj, "__module__", "") == auth_router.__name__:
            fields.update(obj.model_fields)
    assert "role" not in fields, "请求体模型里出现 role 字段，就是提权入口"

    code = auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register",
                      json={"code": code, "username": "想当管理员", "role": "admin"})
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "user", "多塞的 role 必须被忽略，不能被采纳"

    for path in ("/v1/admin/users/x/role", "/v1/admin/promote"):
        assert client.post(path, json={"role": "admin"}).status_code == 404, path
