"""管理员页 /admin 的契约：可达，而且它必须是个"没有数据"的空壳。

选独立地址而不是塞进 PWA，换来的是入口分离；这里锁的就是这个决定不变成漏洞：
页面谁都能打开（新用户也打得开），但任何一个字节的用户数据都只能经
`/v1/admin/*` 那套 require_admin 接口取。哪天有人图省事把用户列表直接渲染进
HTML，鉴权就等于被整页绕过了——那不会让现有任何一条测试变红，所以单独钉住。

后半截锁的是文案：界面上每句话都得对得上 app/core/auth.py 的实现。
「删号会连带删掉会话与记忆」「这枚令牌不发出去他就进不来」这类话在实现改掉之后
不会让任何一条行为测试变红，但它会把管理员的决策喂错——所以文案在这里当语料钉住。
"""
import os
import re
import sys
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# 读前端文本当判据之前先过那两把剥注释的尺子：解释性注释里完全可能出现被禁字样
# （「旧文案已经不说记录与令牌一起没了」就能把锁喂绿），而真删掉的文案只要还躺在
# 注释里就仍然命中"不许出现"那一半。尺子的实现与判据在 test_web_pwa.py。
backend_path = Path(__file__).resolve().parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.core.authz import _PROTECTED_PREFIXES
from app.main import app
from app.web.web_router import ADMIN_DIR, STATIC_DIR
from tests.test_auth import _code_lines                 # 只认真代码的那把尺子
from tests.test_web_pwa import _strip_html_comments, _strip_js_comments

ADMIN = Path(ADMIN_DIR)
STATIC = Path(STATIC_DIR)


def _js() -> str:
    return _strip_js_comments((ADMIN / "admin.js").read_text(encoding="utf-8"))


def _html() -> str:
    return _strip_html_comments((ADMIN / "index.html").read_text(encoding="utf-8"))


def test_admin_page_is_reachable_without_credentials(client):
    """管理页是个静态壳，挡它没有意义：数据在服务端，不在 HTML 里。"""
    res = client.get("/admin/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_admin_page_html_carries_no_user_data(enforced):
    """页面必须是空壳：用一个真实注册过的探针用户名当"漏了就红"的哨兵。"""
    probe = "探针用户ZK7Q"
    enforced(probe)                        # 经真鉴权路径注册一个用户

    # enforced 给的是普通用户的令牌，打管理面只会 403；列用户要用 bootstrap 管理员口令
    admin = {"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]}
    mine = TestClient(app).get("/v1/admin/users", headers=admin)
    assert mine.status_code == 200, "bootstrap 口令应当能列用户"
    assert probe in [u["username"] for u in mine.json()["users"]], "哨兵没建起来"

    stranger = TestClient(app)            # 不带任何凭据
    body = stranger.get("/admin/").text
    assert probe not in body, "页面被服务端渲染了用户数据，require_admin 形同绕过"


def test_admin_pages_data_source_stays_behind_admin(enforced):
    """同一台服务器上，没凭据的人打这个页面 200，打它的数据源 401。"""
    stranger = TestClient(app)
    assert stranger.get("/admin/").status_code == 200
    assert stranger.get("/v1/admin/users").status_code == 401


def test_admin_url_is_deliberately_not_an_auth_surface():
    """`/admin` 不在受保护前缀里是有意决定，钉住它以免被"顺手加一行"改成半开状态。

    如果真要连页面一起挡，正确做法是加进 _PROTECTED_PREFIXES 并同步更新这里——
    但那只挡机器人，挡不了任何能读到 HTML 的人，权限边界始终是 require_admin。
    """
    assert "/admin" not in _PROTECTED_PREFIXES
    assert "/v1/" in _PROTECTED_PREFIXES


# ---------- 样式拆出去：admin.css ----------

def test_admin_styles_live_in_admin_css_and_are_served(client):
    """内联 <style> 拆成 admin.css：样式得能被缓存、被复用，不能只长在首页里。

    判 `<style` 用原始文本而不是剥过注释的那份：把 `<style>` 整块换成注释等于样式
    消失，而"不许出现"这类断言在剥过注释的语料上会一起变绿。
    """
    assert (ADMIN / "admin.css").is_file(), "admin.css 不在这个目录里"
    html_raw = (ADMIN / "index.html").read_text(encoding="utf-8")
    assert "<style" not in html_raw, "还有内联样式没搬进 admin.css"
    html = _html()
    assert 'href="admin.css"' in html, "index.html 没链上 admin.css"
    # 设计系统仍然只有 PWA 那一份：本页链它 + 自己那份，不起第三套配色。
    assert 'href="/app/style.css"' in html, "丢了 PWA 那份设计系统的链接"
    for name in ("admin.css", "admin.js"):
        res = client.get("/admin/" + name)
        assert res.status_code == 200, name
        assert res.headers.get("cache-control") == "no-cache", f"{name} 没带 revalidate"
    css = (ADMIN / "admin.css").read_text(encoding="utf-8")
    assert "min-height: 44px" in css, "触控热区没到 44px（手机上点不准）"
    assert "var(--accent)" in css and "var(--surface)" in css, "自定义了一套配色"


def test_every_class_the_admin_page_uses_is_defined_somewhere():
    """admin 页链着两份样式表，所以"这个类还在不在"要看两份之和。

    真出事过一次：清理 PWA 死样式时把 `.spacer { flex: 1 }` 从 static/style.css 删了
    ——PWA 那页确实不用它，可 admin/index.html 的顶栏在用（它链的是同一份表）。
    删完页面不报错、测试不变红，只是"刷新/退出"两个按钮从此贴在标题后面。
    这类"共享样式表被另一个页面借用"的关系，编译器与运行时都不管，只能这样钉。

    只判 HTML 里那一份：admin.js 的字符串字面量混着 createElement 的标签名、事件名与
    属性名，按同样方式扫会得到四十多个假阳性（试过了）。JS 用到的 btn / dim / ico
    这些在 HTML 里都出现过，所以覆盖是间接拿到的。
    """
    css = ((ADMIN / "admin.css").read_text(encoding="utf-8")
           + (STATIC / "style.css").read_text(encoding="utf-8"))
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"/\*[\s\S]*?\*/", "", css)))
    used = set()
    for group in re.findall(r'class="([^"]+)"', _html()):
        used.update(group.split())
    assert used, "一个 class 都没扫到：这条锁在空转"
    missing = sorted(used - defined)
    assert not missing, (
        f"这些 class 页面在用、两份样式表里却没有定义：{missing}——"
        "元素照样渲染，只是完全没样式")


# ---------- 文案一：删号到底删了什么 ----------

def test_delete_user_still_does_not_cascade():
    """后端事实：delete_user 只删 users 里那一条记录，会话/附件/记忆都归别的存储管。

    这条锁的是"实现还不级联"，与下一条配对：哪天有人真给删除加上级联，上一条先红，
    他就必须同时去改界面那句话——两边不会有一个悄悄说谎。
    """
    from app.core.auth import AuthStore

    lines = _code_lines(AuthStore.delete_user)
    joined = "\n".join(lines)
    assert re.search(r"del\s+self\._users\[", joined), \
        "delete_user 已经不只删 users 里的这一条记录了：界面文案与这条锁都要重写"
    touches_other_stores = [line for line in lines
                            if re.search(r"session|upload|memory|chroma", line, re.I)]
    assert not touches_other_stores, \
        f"delete_user 开始碰别的存储了（{touches_other_stores}）：界面文案得跟着改"
    # 令牌存在用户记录里面，所以"删号连带令牌一起失效"这一半是真话，别删掉。
    assert re.search(r'record\["tokens"\]\s*=', "\n".join(_code_lines(AuthStore.rotate_token))), \
        "令牌不再挂在用户记录上：删号到底会不会作废令牌得重新确认"


def test_delete_copy_no_longer_claims_a_cascade():
    """界面事实：删号那句必须说清"留下什么"，而不是"记录与令牌一起没了"。"""
    js = _js()
    assert "记录与令牌一起没了" not in js, "删号文案还在说那三份数据跟着一起没了"
    for name in ("会话", "附件", "长期记忆"):
        assert name in js, f"删号文案没点名「{name}」不会被删"
    assert "留在磁盘上" in js, "删号文案没说明残留数据留在磁盘上"
    assert "没有恢复入口" in js, "不可撤销这一半是真话，不该跟着一起被改掉"


# ---------- 文案二：新令牌到底珍贵在哪 ----------

def test_token_reveal_copy_stops_at_plaintext_being_one_shot():
    """明文确实只给一次，但"这枚令牌不发出去他就进不来"是假的。

    users.json 里存的是 sha256 摘要，所以关掉弹窗再查不到明文——这句真话留着。
    假的是它的言外之意：/v1/auth/login 一直在那儿，他拿自己的用户名和密码就能再
    领一枚；换发的语义是"把旧令牌全部作废"（撤销），不是发一张唯一的入场券。
    """
    from app.core.auth import AuthStore

    assert "hash_token(token)" in "\n".join(_code_lines(AuthStore._issue_token)), \
        "库里开始存明文令牌了，「明文只出现一次」这句得整个重写"
    assert any(path == "/v1/auth/login" and "POST" in methods
               for methods, path in _route_pairs()), \
        "登录端点不在了：换发令牌不再是「他能自己再领一枚」的动作"

    js, html = _js(), _html()
    assert "发给对方" not in js and "发给对方" not in html, \
        "令牌弹窗还在暗示只有手里这一枚能进得去"
    assert "填进 App" not in js and "只显示这一次" not in html, \
        "换发文案还在说「他必须把新令牌填进 App」"
    assert "只出现在这一次响应里" in js or "只出现在刚才那一次响应里" in html, \
        "「明文只给一次」这句真话被弄丢了"
    assert "重新登录" in js and "密码" in js, "没说他可以用密码自己再领一枚"
    assert "挡住人请用停用" in js, "没指向真正的拦截动作（停用）"


# ---------- 接后端点：页面上不许有编出来的路径 ----------

def _route_pairs() -> list:
    return [(m, r.path) for r in app.routes
            if isinstance(r, APIRoute) for m in r.methods]


def _matches(template: str, path: str) -> bool:
    """`/v1/tasks/{task_id}/cancel` 认 `/v1/tasks/x_1/cancel`。"""
    pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)) + "$"
    return bool(re.match(pattern, path))


def test_page_only_calls_routes_that_exist():
    """admin.js 里每个 "/v1/…" 字面量都必须落在真路由表上。

    这轮的目标是"接上后端已有但页面没用上的端点"，而"不新增后端端点"是同一个约束
    的另一面：编一个不存在的地址挂到按钮上，点了才 404，比不接更糟。带 id 的那一段
    是 encodeURIComponent 现拼的，所以以 `/` 结尾的字面量按前缀判，其余整段判。
    """
    routes = _route_pairs()
    # 查询串不是路径的一部分：`/v1/memory/decay?decay_factor=` 认的是 /v1/memory/decay
    paths = [p.split("?")[0] for p in re.findall(r'"(/v1/[^"]*)"', _js())]
    assert len(paths) >= 8, f"页面上的接口调用少到这个数，是被整段删了：{paths}"
    for path in paths:
        if path.endswith("/"):
            hit = [tpl for _, tpl in routes if tpl.startswith(path)]
        else:
            hit = [tpl for _, tpl in routes if _matches(tpl, path)]
        assert hit, f"admin.js 里的 {path} 在后端路由表上找不到"


def test_endpoints_that_had_no_button_are_now_wired():
    """后端早就带 require_admin、而此前整站没有任何入口的端点，现在页面在调。

    这份名单是枚举 `RequireAdmin` 路由得来的。providers 那一组不在里面：PWA 的
    设置页早就把它做成了可点的界面（app.js 的 providerRow / provForm），在这里
    再摆一份就是第二个真相。
    """
    js = _js()
    for needle in ("/v1/memory/stats", "/v1/memory/decay", "/v1/tasks",
                   "/v1/agent/run", "/v1/agent/orchestrate", "/cancel"):
        assert needle in js, f"{needle} 还是没有入口"
    # 反向那半：跨用户读记忆正文这条线不接，也不许靠猜数糊过去。
    assert "/v1/memory/list" not in js, "管理页在列别人的记忆正文"
    assert '"不支持"' in js, "后端不给按人的条数，界面就得写不支持"


def test_admin_js_builds_every_node_without_innerhtml():
    """用户名与任务目标都是别人写下的字符串：拼进 HTML 就是在他浏览器里执行。

    图标也走 createElementNS，所以 innerHTML 这个单词在本页一次都不该出现。
    """
    js = _js()
    assert "innerHTML" not in js, "出现了 innerHTML"
    assert "textContent" in js and "createElementNS" in js


def test_every_id_admin_js_touches_exists_in_html():
    """$() 引到一个 HTML 里没有的 id，第一下点击就是 TypeError，而且只在那一节坏。

    PWA 那份对着 app.js 有同一条锁（test_web_pwa.py），本页这轮从一节长到四节，
    没有它的话"某节的按钮全哑"只能靠手点才发现。
    """
    js, html = _js(), _html()
    ids = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert {"userRows", "memTotal", "taskList", "agentOut", "revealText", "askOk"} <= ids, \
        "这条锁在空转：四节里任何一节的 id 都不在名单上了"
    missing = sorted(i for i in ids if 'id="%s"' % i not in html)
    assert not missing, f"admin.js 引用了 HTML 里不存在的 id: {missing}"
