"""每条 /v1/* 路由都必须声明身份依赖。

这是本计划最重要的回归锁：现有的洞（客户端自报 user_id、sessions 无归属）
全都是"新端点忘了挂鉴权"长出来的，靠人记住不可靠。

契约只有两句话：
1. 走 /v1/ 的每条路由，依赖树里必须出现 current_principal 或 require_admin；
2. 免凭据的面只有 authz.PUBLIC_PATHS 那一处，而它今天只有注册端点。

判定看的是**依赖树里的可调用对象本身**，不是参数名：参数名可以随便起，身份也
能藏在子依赖里（端点只依赖一个"取会话"的辅助函数，那个辅助函数才带 principal）。
只看签名就会既误报又漏报，而误报多了就有人往测试里加白名单——那正是这把锁被
拆掉的方式。所以本文件也测自己的扫描逻辑（末尾两条）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, get_dependant

from app.core import authz
from app.core.authz import (PUBLIC_PATHS, CurrentPrincipal, UNAUTHORIZED_DETAIL,
                            current_principal, require_admin)
from app.main import app

GUARD_CALLABLES = {current_principal, require_admin}
# 非 /v1 的系统端点：健康检查与静态首页。契约刻意只管 /v1——它们是运维探针和
# PWA 外壳，卷进"公开面清单"只会让人以为这里还能再加一个免鉴权端点。
EXEMPT_PATHS = {"/", "/health"}


def _is_v1(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def _callables_of(dependant):
    """依赖树里所有可调用对象，含子依赖，HTTP 与 websocket 两种形状都扫。

    FastAPI 0.13x 起 websocket 端点就是根 dependant.call，子依赖与 HTTP 共用
    .dependencies；更早的版本把 websocket 端点单独挂在 dependant.websocket 上。
    两种形状都走一遍，免得依赖升级/回退时这把锁自己先失效（写成 cur.websocket
    在当前版本上直接 AttributeError——测试红得毫无线索）。
    """
    stack = [dependant]
    while stack:
        cur = stack.pop()
        for sub in cur.dependencies:
            yield sub.call
            stack.append(sub)
        ws = getattr(cur, "websocket", None)
        if ws is not None:
            yield ws.call
            stack.append(ws)


def _route_guards(route) -> set:
    """该路由声明的身份守卫 = 依赖树 ∪ 路由级 dependencies（include_router 带的也算）。"""
    found = set(_callables_of(route.dependant))
    for dep in getattr(route, "dependencies", ()) or ():
        call = getattr(dep, "dependency", None)
        if call is not None:
            found.add(call)
    return found


def _v1_routes():
    """全部 /v1 路由，并确认它们真的带着可检查的依赖树。

    这里刻意用 assert 而不是 continue：/v1 下一条 Mount 同样能被请求命中，对本契约
    却是隐形的。静默跳过等于把"看不见"当成"合规"，而那正是最需要它说话的形状。
    """
    routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not _is_v1(path):
            continue
        assert isinstance(route, APIRoute) and getattr(route, "dependant", None) is not None, (
            f"{path} 是 {type(route).__name__}，没有依赖树可查："
            "契约扫不到它，也就护不住它。请改成带身份依赖的端点。")
        routes.append(route)
    return routes


# ---------- 契约本体 ----------


def test_every_v1_route_declares_an_identity_dependency():
    missing = []
    for route in _v1_routes():
        if route.path in PUBLIC_PATHS:
            continue
        if not (_route_guards(route) & GUARD_CALLABLES):
            missing.append(f"{sorted(route.methods - {'HEAD', 'OPTIONS'})} {route.path}")
    assert not missing, f"以下端点未声明身份依赖：{sorted(missing)}"


def test_public_allowlist_is_exactly_registration():
    assert PUBLIC_PATHS == {"/v1/auth/register"}


# ---------- 这把锁不能是空的 ----------


def test_the_scan_actually_sees_the_v1_surface():
    """路由没挂上、app 换了对象、前缀写法改了——三种都会让上面的契约零命中而变绿。"""
    routes = _v1_routes()
    paths = {r.path for r in routes}
    assert len(paths) >= 20, f"只扫到 {len(paths)} 条 /v1 路由，扫描本身大概坏了：{sorted(paths)}"
    guards = [_route_guards(r) for r in routes]
    # 两个守卫都得真的在用：若 require_admin 被整体换成 current_principal，"带身份"
    # 这条契约照样绿，而管理面已经对所有注册用户敞开。
    assert any(require_admin in g for g in guards), "没有任何路由用 require_admin"
    assert any(current_principal in g for g in guards), "没有任何路由用 current_principal"


def test_exempt_paths_stay_outside_the_contract():
    """契约只管 /v1：/, /health, /app 是刻意留在外面的。

    写成断言而不是注释，是因为"扩到全站"这条路有两个坏结局：给健康检查挂凭据
    （探针就没法用了），或者给非 /v1 路径也开一份例外名单（那就不再是"公开面只有
    注册一项"）。同时确认这几个端点还在——哪天它被删了，这条边界就该有人说一声。
    """
    assert all(not _is_v1(p) for p in EXEMPT_PATHS | {"/app"}), "豁免路径不该落在 /v1 之下"
    mounted = {getattr(r, "path", "") for r in app.routes}
    assert EXEMPT_PATHS <= mounted, f"豁免名单里的端点不见了：{sorted(EXEMPT_PATHS - mounted)}"


def test_admin_surface_never_settles_for_mere_identity():
    """/v1/admin/* 只挂 current_principal 也能过上面的契约，但那等于把管理面开放给任何
    注册用户（拿一枚邀请码就能注册）。所以这一面单独钉一次"必须是 require_admin"。"""
    weak = [r.path for r in _v1_routes()
            if r.path.startswith("/v1/admin/") and require_admin not in _route_guards(r)]
    assert not weak, f"管理端点未要求管理员角色：{sorted(weak)}"


def test_agent_and_task_surface_is_admin_only():
    """智能体与任务表：require_admin 是这里唯一不撒谎的守卫，别让它在无人注意时降级。

    task_store 是进程级全局 dict、条目没有 owner，所以"声明了身份"的主契约对它是瞎的：
    换成 current_principal 照样绿，而任何注册用户都能列出、取消、删除别人的任务。
    这里刻意只盯"降级"这一种改法（带身份却不带管理员），而不是把路径清单钉死——
    把这些遗留端点整个删掉是好事，不该被这条测试判红；漏挂守卫自有主契约说话。
    哪天要给普通用户开这一面，先给 task 加归属（与会话同一套规则），再来改这里。
    """
    demoted = [f"{sorted(r.methods - {'HEAD', 'OPTIONS'})} {r.path}" for r in _v1_routes()
               if (r.path.startswith("/v1/agent") or r.path.startswith("/v1/tasks"))
               and require_admin not in _route_guards(r)]
    assert not demoted, f"任务/智能体端点被降级为普通用户可用：{sorted(demoted)}"


def test_every_public_path_is_a_real_route():
    """PUBLIC_PATHS 里的死条目是一扇留着的门：端点改名或搬家后它仍然免凭据，只是后面
    没人接。允许它存在，等于允许公开面只增不减。"""
    mounted = {r.path for r in _v1_routes()}
    orphan = [p for p in PUBLIC_PATHS if p not in mounted]
    assert not orphan, f"公开名单里有已不存在的端点：{sorted(orphan)}"


def test_no_public_path_shadows_another_route():
    """公开项不许带结尾斜杠，也不许是别的路径的前缀。

    中间件现在用精确匹配，所以"前缀"这一条今天没有直接危害；危害在于有人把 == 换成
    startswith 的那一刻——届时 /v1/auth/register 会顺带放行 /v1/auth/register/anything。
    这条让那种改法在合入当天就变红。
    """
    paths = {r.path for r in _v1_routes()}
    assert all(not p.endswith("/") for p in PUBLIC_PATHS), "公开项不许以 / 结尾"
    for pub in PUBLIC_PATHS:
        shadowed = [p for p in paths if p != pub and p.startswith(pub + "/")]
        assert not shadowed, f"{pub} 是 {sorted(shadowed)} 的前缀，不能留在公开名单里"


# ---------- 中间件的放行判定（从 test_auth_endpoints.py 挪来） ----------


def test_trailing_slash_is_not_a_credential_free_door(client, enforced):
    """PUBLIC_PATHS 精确匹配，带斜杠的变体先被中间件挡在凭据之外。

    这条钉的是一个**决定**而不是巧合：免凭据的面只认那一条精确路径。哪天有人把匹配
    改成 startswith，或往名单里塞一条带斜杠的路径，这里就会红。
    斜杠变体本身仍然能用（307 归一化后照常烧同一份限流预算），见
    test_auth_endpoints.test_trailing_slash_cannot_dodge_the_throttle——两半合起来才是
    "斜杠既不是第二个免凭据入口，也不是第二个免计数的入口"。
    """
    enforced("路人")   # 让 enforced 库里有身份，排除 503 这条与斜杠无关的通路
    res = client.post("/v1/auth/register/", json={"code": "EEEE-EEEE", "username": "路人甲"})
    assert res.status_code == 401, res.text
    assert res.json()["detail"] == UNAUTHORIZED_DETAIL


def test_register_itself_is_reachable_without_credentials(client, enforced):
    """与上一条对称：挡斜杠的同时，无斜杠的真端点必须真的免凭据。

    只测斜杠被挡住的话，一扇焊死的门也算通过——而注册是唯一能把人放进这个系统的
    入口，它一旦需要凭据就没人注册得进来。enforced 先造一个身份，顺便排除 503。
    """
    enforced("发码的人")
    invite = authz.auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register", json={"code": invite, "username": "裸请求注册"})
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "user"
    token = res.json()["token"]
    assert client.get("/v1/auth/me",
                      headers={"Authorization": "Bearer " + token}).status_code == 200


def test_admin_endpoints_are_not_reachable_without_credentials(client, enforced):
    """403 的前提是有身份；没身份应当是 401，两者不能糊在一起。

    原处在 test_auth_endpoints.py：它断的是"中间件先于路由"，把 auth_router 整个摘掉
    它照样绿，所以守的其实是 authz 的放行判定，不是管理端点。
    """
    enforced("垫底用户")
    assert client.get("/v1/admin/users").status_code == 401
    assert client.post("/v1/admin/invites", json={}).status_code == 401


def test_no_protected_route_is_reachable_without_credentials(client, enforced):
    """静态契约只说"依赖被声明了"，这条说"没凭据的人在 HTTP 边界上就被拒"。

    两者不可互相替代：把 current_principal 改成"取不到身份就发一个默认 principal"
    这种为了让测试变绿的偷懒写法，静态契约一条都不会红（依赖确实挂在树上），而
    整站已经向匿名访客敞开。这里逐条要求 401 + 那句对客户端的语义承诺，
    覆盖的是全部无路径参数的 GET 端点（含 Task 6 补挂身份的那几条）。
    """
    enforced("垫底用户")
    paths = sorted({r.path for r in _v1_routes()
                    if "GET" in (r.methods or set()) and r.path not in PUBLIC_PATHS
                    and "{" not in r.path})
    assert paths, "没有可扫的 GET 端点，这条测试是空的"
    for path in paths:
        res = client.get(path)
        assert res.status_code == 401, f"{path} -> {res.status_code} {res.text[:150]}"
        assert res.json()["detail"] == UNAUTHORIZED_DETAIL, path


# ---------- 扫描逻辑的自测 ----------


def test_guard_detection_reaches_into_sub_dependencies():
    """身份经子依赖间接带上的路由必须算合规。

    这条测的是本文件自己的扫描器：只看端点签名的话，一条把 principal 藏在辅助函数
    后面的真实路由会被误报，而误报多了就往测试里加白名单。
    """
    async def fetch_thing(principal=CurrentPrincipal):
        return principal

    async def endpoint(_=Depends(fetch_thing)):
        return None

    dependant = get_dependant(path="/v1/thing", call=endpoint)
    assert set(_callables_of(dependant)) & GUARD_CALLABLES


def test_guard_detection_does_not_trust_parameter_names():
    """反面对照：参数名叫 principal、默认值是普通字符串，不算身份依赖。

    没有这条的话，上面的递归检测写成"签名里有个叫 principal 的参数"也一样能绿。
    而"从请求里读 user_id"正是本项目最初的洞——它长得很像有身份。
    """
    test_app = FastAPI()

    @test_app.get("/v1/thing")
    async def route(principal: str = "default_user"):   # 客户端自报身份
        return principal

    target = next(r for r in test_app.routes if _is_v1(r.path))
    assert not _route_guards(target) & GUARD_CALLABLES, "参数名不该被当成守卫"
