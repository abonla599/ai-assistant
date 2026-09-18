"""每条 /v1/* 路由都必须声明身份依赖。

这是本计划最重要的回归锁：现有的洞（客户端自报 user_id、sessions 无归属）
全都是"新端点忘了挂鉴权"长出来的，靠人记住不可靠。

契约只有两句话：
1. 走 /v1/ 的每条路由，依赖树里必须出现 current_principal 或 require_admin；
2. 免凭据的面只有 authz 里那两处名单：PUBLIC_PATHS 的精确条目（今天是三条：
   注册、登录、自助改密——找回问题已是全站常量，所以"先问服务器要问题"那一步
   连同端点一起没了），以及 PUBLIC_ROUTE_TEMPLATES 的带变量条目（今天只有一条：
   导出票据兑换，凭据是链接里那段一次性票据本身）。两张名单都要逐个点名，
   多一条就红——见 test_public_allowlist_is_exactly_the_bootstrap_endpoints 与
   test_the_ticket_redemption_door_is_one_segment_thick。

判定看的是**依赖树里的可调用对象本身**，不是参数名：参数名可以随便起，身份也
能藏在子依赖里（端点只依赖一个"取会话"的辅助函数，那个辅助函数才带 principal）。
只看签名就会既误报又漏报，而误报多了就有人往测试里加白名单——那正是这把锁被
拆掉的方式。所以本文件也测自己的扫描逻辑（末尾几条）。

范围还有两句话（3、4 条）：
3. "每条路由"里的**路由**指的是带依赖树的可路由对象，HTTP（APIRoute）与
   websocket（APIWebSocketRoute）两类都算。websocket 必须在范围内，因为
   Starlette 的 `@app.middleware("http")` 只处理 http scope，握手根本不经过
   install_auth，实测见
   `test_the_http_middleware_does_not_protect_a_websocket_handshake`。
   但要把话说准：**这条契约对 websocket 保证的是"声明了身份依赖"，不是一条
   能用的守卫。** 实测本仓 FastAPI 0.136.1 不会把 `Request` 注进 websocket 的
   依赖：在 `@app.websocket` 上挂 `Depends(current_principal)` 会在连接时抛
   `TypeError: current_principal() missing 1 required positional argument:
   'request'`（依赖解析先于端点体，所以 accept() 都执行不到）。也就是说那一格
   既不是鉴权、也不是放行，而是**连管理员一起挡在门外的崩**——fail-closed 的
   形状，所以没有现行泄露，但绝不是"挂上依赖就完事"。今天全仓没有一条
   websocket 路由（`grep -rn "websocket(" backend/app/` 无命中），所以这是潜伏
   而非现行问题；将来真要加 /v1 长连接，凭据必须在端点体里自己解（握手前读
   `ws.headers` 的 authorization，解不出就 `close(code=1008)`），路由级依赖只是
   给契约看的。这一半范围因此**只许放宽、不许收窄**——把它写回
   `isinstance(route, APIRoute)` 的后果不是漏掉一个无人用的边角，而是给一条正确挂好身份依赖的
   `@app.websocket("/v1/...")` 判红，并叫作者"别用这个路由类"；那种消息只会把人
   推向删断言或加例外名单——就是上面说的拆锁方式。
4. 契约只管 /v1，所以"每条可路由路径至少被一把锁认领"必须由另一条测试钉住
   （`test_nothing_routable_lives_outside_both_locks`）：中间件按
   authz._PROTECTED_PREFIXES 放行，契约按 /v1 过滤，两条锁的差集就是无人区，
   新加一个顶层前缀（/api/... 这种）会同时落在两把锁之外。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute, APIWebSocketRoute, get_dependant
from fastapi.testclient import TestClient
from starlette.requests import Request

from tests.conftest import RECOVERY_FIELDS
from starlette.websockets import WebSocket

from app.core import authz
from app.core.authz import (PUBLIC_PATHS, PUBLIC_ROUTE_TEMPLATES,
                            CurrentPrincipal, Principal, UNAUTHORIZED_DETAIL,
                            current_principal, install_auth, require_admin)
from app.main import app
from app.session.export_store import EXPORT_PATH_PREFIX, TICKET_ID_CHARS, TICKET_ID_RE

GUARD_CALLABLES = {current_principal, require_admin}
# 非 /v1 的系统端点：健康检查与静态首页。契约刻意只管 /v1——它们是运维探针和
# PWA 外壳，卷进"公开面清单"只会让人以为这里还能再加一个免鉴权端点。
EXEMPT_PATHS = {"/", "/health"}
# EXEMPT_PATHS + PWA 外壳 = 今天全部"在 /v1 之外还能被请求命中"的路径（除文档路由）。
# 这个集合的**完备性**由 test_nothing_routable_lives_outside_both_locks 钉住：契约只管
# /v1，中间件只管 authz._PROTECTED_PREFIXES，新增一个顶层前缀就会同时落在两把锁之外，
# 那条测试就是为了让那种形状当场变红。
# /admin 是管理员页的外壳：公开的是**壳**，不是数据——它一个用户的名字都不含，
# 所有数据都要过 require_admin。那条边界由 backend/tests/test_admin_page.py 单独钉。
SYSTEM_PATHS = EXEMPT_PATHS | {"/app", "/admin"}


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


def _v1_routes(app_obj=None):
    """全部 /v1 路由（HTTP 与 websocket 都算），并确认它们真的带着可检查的依赖树。

    成员判定只看**有没有依赖树可查**，不看类名。理由见模块 docstring 的范围第 3 条：
    本仓 FastAPI 0.136.1 上 APIWebSocketRoute 不是 APIRoute 的子类，却同样带着
    .dependant（实测：`issubclass(APIWebSocketRoute, APIRoute)` 为 False），而
    websocket 握手不经过 HTTP 中间件，路由级依赖是它唯一的锁。用 isinstance 收窄
    就是把这一类整体推到契约之外，还给"守卫挂对了"的人判红。
    `_callables_of` 早就两种形状都走，所以这里不需要额外分支。

    但"扫到了"不等于"护住了"，这一点必须写在函数自己的 docstring 里而不是只写在
    模块开头：同一版 FastAPI 实测**不向 websocket 依赖注入 `Request`**，所以一条挂了
    `Depends(current_principal)` 的 `@app.websocket` 会顺利通过这里，却在客户端连接时
    抛 `TypeError: current_principal() missing 1 required positional argument:
    'request'`。这里保证的是**声明**，端点体里自己解凭据才是守卫。

    这里刻意用 assert 而不是 continue：/v1 下一条 Mount 同样能被请求命中，对本契约
    却是隐形的（它连 .dependant 都没有）。静默跳过等于把"看不见"当成"合规"，而那
    正是最需要它说话的形状。

    app_obj 只给本文件的探针用（造一条 /v1 websocket 看扫描器认不认），默认仍是真 app，
    契约本体扫的始终是应用真实挂载的那张路由表。
    """
    routes = []
    for route in (app if app_obj is None else app_obj).routes:
        path = getattr(route, "path", "")
        if not _is_v1(path):
            continue
        assert getattr(route, "dependant", None) is not None, (
            f"{path} 是 {type(route).__name__}，没有 .dependant 可查："
            "契约扫不到它，也就护不住它。/v1 下请改用带身份依赖的端点——"
            "HTTP 用 @app.<method>、长连接用 @app.websocket，两者都带 .dependant；"
            "Mount / 重定向这类没有依赖树，中间件对它们同样是瞎的。")
        routes.append(route)
    return routes


def _describe(route) -> str:
    """失败消息里的"哪条路由"：方法 + 路径。

    只报路径会让人以为补一个依赖就完事（同一 path 可以挂多种方法）。websocket 路由
    没有 .methods（APIWebSocketRoute 不继承 APIRoute），标成 WS——不能直接读属性，
    否则契约范围一放宽，这条消息自己就先 AttributeError。
    """
    methods = getattr(route, "methods", None)
    if methods is None:
        return f"['WS'] {route.path}"
    return f"{sorted(methods - {'HEAD', 'OPTIONS'})} {route.path}"


def _identity_gaps(app_obj=None):
    """该路由表里"没声明任何身份守卫"的 /v1 路由（两张公开名单除外）。

    PUBLIC_ROUTE_TEMPLATES 豁免的是**路由模板**——票据兑换那条是刻意不挂身份
    依赖的（凭据在链接里），它免不免凭据由中间件按正则判定，另有一条行为锁
    （test_the_ticket_redemption_door_is_one_segment_thick）守着"正则不许宽到
    漏进别的路径"。这里如果只看 PUBLIC_PATHS，契约会把那条合规的一次性链接
    端点判红，而误报的出口往往是往名单里塞例外——那才是这把锁被拆掉的方式。
    """
    return [route for route in _v1_routes(app_obj)
            if route.path not in PUBLIC_PATHS
            and route.path not in PUBLIC_ROUTE_TEMPLATES
            and not (_route_guards(route) & GUARD_CALLABLES)]


def _unclaimed_paths(app_obj) -> set:
    """路由表里"两把锁都不认领"的路径：不在 /v1 之下、不在系统端点里、
    也不在中间件的受保护前缀里。

    前缀直接读 authz._PROTECTED_PREFIXES，不在测试里抄第二份清单：抄的那份一定会漂移，
    而这里的语义本来就是"中间件管不管这条"。
    """
    return {path for path in {getattr(r, "path", "") for r in app_obj.routes}
            if not _is_v1(path) and path not in SYSTEM_PATHS
            and not path.startswith(authz._PROTECTED_PREFIXES)}


# ---------- 契约本体 ----------


def test_every_v1_route_declares_an_identity_dependency():
    missing = [_describe(route) for route in _identity_gaps()]
    assert not missing, f"以下端点未声明身份依赖：{sorted(missing)}"


def test_public_allowlist_is_exactly_the_bootstrap_endpoints():
    """免凭据面必须逐个点名，两张名单各钉各的，多一条就红。

    PUBLIC_PATHS 今天是三条：注册、登录、自助改密。它们不是"漏了鉴权"——没有身份的
    人本来就得能进来拿身份、也得能在忘了密码时自救。但每一条都是攻击面，所以这条钉的是
    "不许悄悄多第四条"；这三条自己的防线不在这里，在 auth_router（真实 IP 限流、
    几种失败同一句话）与 auth 存储层（同形措辞与同形耗时）里。

    PUBLIC_ROUTE_TEMPLATES 是带变量段的那一类，今天只有一条：导出票据兑换。它的凭据
    是链接里那段一次性票据本身，所以免登录是设计而非漏洞；"这条门只有一整段票据那么宽"
    由下面的 test_the_ticket_redemption_door_is_one_segment_thick 用真请求守着，这里先
    钉住"名单不许悄悄多第二条"。
    """
    assert PUBLIC_PATHS == {"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset"}
    assert set(PUBLIC_ROUTE_TEMPLATES) == {f"{EXPORT_PATH_PREFIX}{{ticket_id}}"}


def test_the_reset_step_is_reachable_without_credentials(client, enforced):
    """忘了密码的人手里没有任何凭据——这一步要凭据就是自救路径不存在。

    与注册那条对称：先证明真路径免凭据可达，再证明"免凭据"没有滑成前缀放行。
    这里没有"先问服务器要问题"那一步：三题是全站常量，页面自己渲染，于是那条
    能回答"这个用户名存在吗"的信道整个不存在了——顺手把路由也钉死。
    """
    enforced("垫底用户")
    assert "/v1/auth/recovery" not in {getattr(r, "path", "") for r in client.app.routes}, \
        "问题已是常量，报出问题的端点没有存在价值；它回来了就是多一条免凭据信道"

    res = client.post("/v1/auth/reset",
                      json={"username": "没留答案的", "answers": ["猜一个", "再猜一个", "还猜一个"],
                            "new_password": "correct-horse-battery"})
    assert res.status_code == 401, f"答案不对必须 401，而不是被鉴权层挡成 401 之外的话：{res.text}"
    assert res.json()["detail"] == "答案不正确"

    # 前缀化就漏：带斜杠与多一段子路径都不许免凭据
    for path in ("/v1/auth/reset/", "/v1/auth/reset/anything"):
        res = client.post(path, json={"username": "没留答案的"})
        assert res.status_code == 401, f"{path} 白拿到了免凭据通道 -> {res.status_code}"


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
    assert all(not _is_v1(p) for p in SYSTEM_PATHS), "豁免路径不该落在 /v1 之下"
    mounted = {getattr(r, "path", "") for r in app.routes}
    assert EXEMPT_PATHS <= mounted, f"豁免名单里的端点不见了：{sorted(EXEMPT_PATHS - mounted)}"


# ---------- 范围：websocket 与顶层前缀 ----------


def test_a_v1_websocket_route_is_scanned_and_not_skipped():
    """契约范围包含 /v1 websocket：守卫挂对了就通过，没挂就点名——只放宽、不失效。

    这条钉的是 `_v1_routes` 的成员判定，也就是"路由"这个词的定义。它存在的意义是
    防止下一次收窄：这里曾经写的是 `isinstance(route, APIRoute)`，而本仓 FastAPI
    0.136.1 上 APIWebSocketRoute 不是它的子类（`issubclass(...)` 实测 False），
    于是 `@app.websocket("/v1/...")` 会被判红，失败消息还叫作者"别用这个路由类"——
    那正是本文件 docstring 里警告过的形状：一条只会误报的断言，最后一定被人删掉或
    塞进例外名单，而 websocket 恰恰是中间件管不到、只能靠这条守卫的那一类（见下一条）。

    两半都要：临时 app 证明扫描器**接受**挂对了守卫的长连接（放宽），真 app 上临时
    挂一条裸的、要求主契约点名它（放宽之后仍然有牙）。真 app 那半在 finally 里摘干净，
    不给生产路由表留痕迹。
    """
    probe = FastAPI()

    @probe.websocket("/v1/ws-guarded")
    async def guarded(ws: WebSocket, _: Principal = CurrentPrincipal):
        await ws.accept()

    @probe.websocket("/v1/ws-bare")
    async def bare(ws: WebSocket):
        await ws.accept()

    routes = {r.path: r for r in _v1_routes(probe)}   # 不抛 assert = 两类都被接受
    assert set(routes) == {"/v1/ws-guarded", "/v1/ws-bare"}, "websocket 路由没进契约范围"
    gaps = [_describe(r) for r in _identity_gaps(probe)]
    assert gaps == ["['WS'] /v1/ws-bare"], f"裸 websocket 没被抓出来，或合规那条被误报：{gaps}"

    async def unguarded(ws: WebSocket):
        await ws.accept()

    probe_route = APIWebSocketRoute("/v1/ws-probe-bare", unguarded)
    app.router.routes.append(probe_route)
    try:
        named = [_describe(r) for r in _identity_gaps()]
    finally:
        app.router.routes.remove(probe_route)
    assert named == ["['WS'] /v1/ws-probe-bare"], \
        f"主契约没抓到一条裸的 /v1 websocket：{named}"
    assert not _identity_gaps(), "临时路由没摘干净，会污染后面的用例"


def test_the_http_middleware_does_not_protect_a_websocket_handshake(monkeypatch):
    """上一条为什么必须存在：install_auth 对 websocket 握手一个字都不做。

    Starlette 的 `@app.middleware("http")` 只包 http scope，websocket 连接直接落到
    路由上。同一个探针 app、同一套 enforced 环境：匿名 GET 拿 401，匿名 websocket
    照样连上——所以 /v1 长连接的路由级身份依赖是**唯一**那把锁，没有中间件兜底。
    这条不是在测鉴权，是在测"这里只剩一把锁"这个前提；前提哪天变了（有人把中间件
    改成 ASGI 级），它会失败并要求重写上面那段理由。
    """
    monkeypatch.setenv("AUTH_MODE", "enforced")
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")   # 有身份可服务，排除 503 那条通路

    probe = FastAPI()
    install_auth(probe)

    @probe.get("/v1/ping")
    async def ping():
        return {"ok": True}

    @probe.websocket("/v1/echo")
    async def echo(ws: WebSocket):
        await ws.accept()
        await ws.send_text("connected")
        await ws.close()

    probe_client = TestClient(probe)
    assert probe_client.get("/v1/ping").status_code == 401
    try:
        with probe_client.websocket_connect("/v1/echo") as ws:
            hello = ws.receive_text()
    except Exception as exc:                        # noqa: BLE001 - 要测的就是"没被挡住"
        pytest.fail(f"握手竟然被中间件挡住了（{type(exc).__name__}: {exc}）——"
                    "install_auth 已覆盖 websocket，本文件的范围理由需要重写")
    assert hello == "connected"


def test_nothing_routable_lives_outside_both_locks():
    """契约扫 /v1，中间件护 authz._PROTECTED_PREFIXES —— 两者的并集必须盖住整张路由表。

    这条管的是"两把锁的范围悄悄分叉"：中间件今天护 /v1/、/docs、/redoc、
    /openapi.json，契约只认 /v1。**新加一个顶层前缀**（/api/foo 这种）时两把锁都不会
    说话——中间件按前缀放过，契约按 /v1 过滤，于是它能带着零道鉴权上线而 CI 全绿。
    所以这里要求每条可路由路径至少被一把锁认领：在 /v1 之下、在 SYSTEM_PATHS 里、
    或落在 _PROTECTED_PREFIXES 里。前缀直接从 authz 取，不抄第二份清单——把 "/docs"
    从 _PROTECTED_PREFIXES 里删掉也会当场红，那正是"文档路由哪天变匿名可读"的形状。

    判别力当场自证：临时往真 app 挂一条 /api/... 路由，要求它被抓出来，然后在
    finally 里摘干净（不留任何被跟踪文件的改动）。
    """
    unclaimed_now = _unclaimed_paths(app)
    assert unclaimed_now == set(), f"这些可路由路径两把锁都不认：{sorted(unclaimed_now)}"

    async def beacon():
        return {"ok": True}

    probe_route = APIRoute("/api/beacon", beacon, methods=["GET"])
    app.router.routes.append(probe_route)
    try:
        unclaimed = _unclaimed_paths(app)
    finally:
        app.router.routes.remove(probe_route)
    assert unclaimed == {"/api/beacon"}, f"/api 探针没被抓出来，这条是空锁：{sorted(unclaimed)}"
    assert _unclaimed_paths(app) == set(), "临时路由没摘干净，会污染后面的用例"

    # 差集里剩下的只该是 FastAPI 自己生成的文档面：中间件认领、契约看不见（它们没有
    # .dependant）。写得具象一点，是为了让"哪天有人往 /docs 旁边挂个新前缀"变成一次
    # 显式的、要写理由的改动。disabled 模式下这几条才会存在，所以用 ⊆ 而不是 ==。
    paths = {getattr(r, "path", "") for r in app.routes}
    middleware_only = {p for p in paths
                       if not _is_v1(p) and p not in SYSTEM_PATHS
                       and p.startswith(authz._PROTECTED_PREFIXES)}
    assert middleware_only <= {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}, \
        f"出现了契约之外、只有中间件认领的新路径：{sorted(middleware_only)}"


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
    demoted = [_describe(r) for r in _v1_routes()
               if (r.path.startswith("/v1/agent") or r.path.startswith("/v1/tasks"))
               and require_admin not in _route_guards(r)]
    assert not demoted, f"任务/智能体端点被降级为普通用户可用：{sorted(demoted)}"


def test_every_public_path_is_a_real_route():
    """公开名单里的死条目是一扇留着的门：端点改名或搬家后它仍然免凭据，只是后面
    没人接。允许它存在，等于允许公开面只增不减。两张名单同一个口径。"""
    mounted = {r.path for r in _v1_routes()}
    orphan = [p for p in set(PUBLIC_PATHS) | set(PUBLIC_ROUTE_TEMPLATES)
              if p not in mounted]
    assert not orphan, f"公开名单里有已不存在的端点：{sorted(orphan)}"


def test_no_public_path_shadows_another_route():
    """公开项不许带结尾斜杠，也不许是别的路径的前缀。

    中间件现在用精确匹配（带变量那条用锚定正则），所以"前缀"这一条今天没有直接危害；
    危害在于有人把 == 换成 startswith 的那一刻——届时 /v1/auth/register 会顺带放行
    /v1/auth/register/anything。这条让那种改法在合入当天就变红。
    """
    paths = {r.path for r in _v1_routes()}
    all_public = set(PUBLIC_PATHS) | set(PUBLIC_ROUTE_TEMPLATES)
    assert all(not p.endswith("/") for p in all_public), "公开项不许以 / 结尾"
    for pub in all_public:
        shadowed = [p for p in paths if p != pub and p.startswith(pub + "/")]
        assert not shadowed, f"{pub} 是 {sorted(shadowed)} 的前缀，不能留在公开名单里"


def test_the_ticket_redemption_door_is_one_segment_thick(client, enforced):
    """带变量的免凭据门只有一整段票据那么宽——这不是前缀放行，也不许漂成前缀放行。

    PUBLIC_PATHS 是精确字符串，兑换的路径却带随机 id，中间件因此在 authz 里多了一份
    锚定正则（PUBLIC_ROUTE_TEMPLATES）。这条用真请求钉它两半：

    - 形状对但没签过（22 个合法字符）→ 已过中间件、死在票据表里：404，同一句话。
      它必须到得了处理函数，否则壳 APK 拿不到任何文件；它也拿不到东西，所以"免登录"
      没有漏成"免票据"。
    - 形状不对（带斜杠、太短、太长、两段式）→ 一律还是 401，与整条 /v1 面同一个
      凭据门。把正则改成前缀匹配会当场红在这里——那正是要防的"省事"改法。
    """
    enforced("垫底用户")   # 库里有身份，排除与形状无关的 503 通路
    shape_ok = EXPORT_PATH_PREFIX + "Z" * TICKET_ID_CHARS
    assert TICKET_ID_RE.fullmatch(shape_ok[len(EXPORT_PATH_PREFIX):]), "探针自身得先是票据形状"
    res = client.get(shape_ok)
    assert res.status_code == 404, f"没签过的票据形状竟被凭据门挡了：{res.status_code}"

    for near_miss in (EXPORT_PATH_PREFIX,                       # 目录本身
                      EXPORT_PATH_PREFIX + "short",             # 不够长
                      shape_ok + "Z",                           # 超长一字符
                      shape_ok + "/sub",                        # 多一段
                      shape_ok.replace("Z", "✦")):              # 非法字符
        res = client.get(near_miss)
        assert res.status_code == 401, \
            f"{near_miss!r} 白拿到了免凭据通道（{res.status_code}）——放行漂成前缀了"
        assert res.json()["detail"] == UNAUTHORIZED_DETAIL


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
    enforced("垫底用户")
    res = client.post("/v1/auth/register",
                         json={"username": "裸请求注册", "password": "open-reg-pw-123",
                      **RECOVERY_FIELDS})
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
    assert client.post("/v1/admin/users/u_nobody/disable").status_code == 401


def test_no_protected_route_is_reachable_without_credentials(client, enforced):
    """静态契约只说"依赖被声明了"，这条说"匿名请求在 HTTP 边界上真的拿不到东西"。

    这条测什么、不测什么，都用临时改造实测过（三组，见报告 §Fix round 1 的 F2 小节），
    别凭印象写：

    - 会红：任何一条无路径参数的 GET 端点对匿名请求吐出别的东西（200/403/500/301），
      包括**两把锁同时失守**的形状——中间件不再拒绝、这条端点又没挂依赖。逐条走一遍
      整张面是这里独有的价值：其余测试都只看抽查点，看不到"某条端点悄悄换人应答"。
    - 不会红（实测）：只删中间件里那句 401。每条端点自己的依赖仍然 401，
      红的是 test_trailing_slash_is_not_a_credential_free_door、
      test_the_http_middleware_does_not_protect_a_websocket_handshake（它的探针端点
      刻意不挂依赖，所以真的在测中间件那一步）和 test_authz_failclosed 的文档面。
      这正是"路由级依赖 = 纵深防御"的实测证据，不是修辞。
    - 不会红（实测）：往 PUBLIC_PATHS 里塞一条真端点（该路径这里会被跳过）。
      红的是 test_public_allowlist_is_exactly_registration 与
      test_no_public_path_shadows_another_route。
    - 不会红（实测）：把 current_principal 改成"取不到身份就发一个默认 principal"。
      中间件先于路由 401，请求走不到依赖那一层（test_authz_failclosed 的依赖探针也
      挂了 install_auth，同样被短路）。这一格过去无人覆盖，现在由
      `test_current_principal_refuses_to_invent_an_identity` 直接调依赖函数钉住。

    也就是说：这条是**中间件/响应形状**的锁，静态契约是**端点声明**的锁，各测各的。
    路由级依赖之所以仍然必要（而不是"有中间件就够了"），就是上面第二条那个实测：
    受保护前缀哪天收窄、挂载顺序哪天被人动、以及 websocket 握手压根不经过这个中间件。
    """
    enforced("垫底用户")
    paths = sorted({r.path for r in _v1_routes()
                    if "GET" in (getattr(r, "methods", None) or set())
                    and r.path not in PUBLIC_PATHS and "{" not in r.path})
    assert paths, "没有可扫的 GET 端点，这条测试是空的"
    for path in paths:
        res = client.get(path)
        assert res.status_code == 401, f"{path} -> {res.status_code} {res.text[:150]}"
        assert res.json()["detail"] == UNAUTHORIZED_DETAIL, path


# ---------- 依赖函数本体（中间件短路之外的最后一格） ----------


def test_current_principal_refuses_to_invent_an_identity():
    """没有 request.state.principal 时只能抛 401，不许凭空发一个身份。

    这一格只能**直调**才测得到：真 app 上中间件先于路由就把匿名请求挡成 401，请求
    走不到依赖那一层，于是"把 current_principal 改成取不到身份就返回
    BOOTSTRAP_PRINCIPAL"这个变异（MUT-E）在整套用例下全绿。而它一旦落地，任何绕过
    中间件的形状都会白送一个本机管理员身份——websocket 握手今天就绕（见模块
    docstring 第 3 条），受保护前缀哪天收窄、挂载顺序哪天被动过也一样。
    "身份只能由中间件写入"这条承诺要有人守，就不能只在中间件之后才被检查。

    反向对照（设了身份就原样交出）是这条不成为空锁的证明：只断"抛 401"的话，
    一个无条件抛异常的函数也能通过，而那种实现会把所有请求挡在门外——绿灯不代表
    有鉴别力。
    """
    def bare_request():
        return Request({"type": "http", "method": "GET", "path": "/v1/sessions",
                        "headers": [], "query_string": b""})

    with pytest.raises(HTTPException) as exc:
        current_principal(bare_request())
    assert exc.value.status_code == 401
    assert exc.value.detail == UNAUTHORIZED_DETAIL

    owned = bare_request()
    owned.state.principal = Principal("u_someone", "张三", "user")
    assert current_principal(owned) is owned.state.principal


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
