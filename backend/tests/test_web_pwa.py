"""PWA 静态托管、前端契约与会话消息替换端点测试。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app
from app.web.web_router import STATIC_DIR

client = TestClient(app)

STATIC = Path(STATIC_DIR)
JS_FILES = ("api.js", "app.js", "markdown.js", "sw.js")


def test_app_serves_html_shell():
    res = client.get("/app/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "AI 智能助手" in body


def test_static_assets_reachable():
    assets = ("style.css", "app.js", "api.js", "markdown.js", "sw.js",
              "manifest.webmanifest", "icon.png",
              "vendor/marked.min.js", "vendor/purify.min.js",
              "vendor/highlight.min.js", "vendor/hljs-github-dark.min.css")
    missing = [a for a in assets if client.get("/app/" + a).status_code != 200]
    assert not missing, f"缺失静态资源: {missing}"


def test_static_assets_must_be_revalidated_not_reused():
    """不发 Cache-Control 的静态资源，等于把改版交给别人的缓存去决定。

    2026-09-17 实测：重建并重启后，公网 /app/style.css 仍是 80 分钟前那份旧的
    （cf-cache-status: HIT，Cloudflare 对 .css/.js 默认注入 max-age=14400）。
    源站自己没表态，浏览器与边缘就各自按启发式缓存——朋友那边看到的现象是
    "改了没生效"，而这正是本项目最容易被误判成代码坏了的一类形状。

    no-cache 不是"不缓存"：每次使用前必须回源问一次，而 ETag 就是那一次问价，
    答案通常是 304。ETag 一旦丢了，省下的请求就会变成整份重传，所以两个一起钉。
    """
    for path in ("/app/style.css", "/app/app.js", "/app/sw.js", "/app/", "/admin/"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", \
            f"{path} 的 cache-control 是 {res.headers.get('cache-control')!r}"
        assert res.headers.get("etag"), f"{path} 没有 ETag：no-cache 会退化成每次全量重传"


def test_frontend_uses_relative_api_paths_only():
    """前端不得出现绝对服务地址，否则换网络/换设备即失效。"""
    offenders = []
    for name in JS_FILES:
        src = (STATIC / name).read_text(encoding="utf-8")
        for m in re.finditer(r"""['"]https?://[^'"]+['"]""", src):
            offenders.append(f"{name}: {m.group(0)}")
    assert not offenders, f"前端出现绝对 URL: {offenders}"


def test_model_output_is_sanitized_before_html_render():
    """模型回复按 Markdown 渲染成 HTML，必须过 DOMPurify，否则可注入脚本。"""
    src = (STATIC / "markdown.js").read_text(encoding="utf-8")
    assert "DOMPurify" in src, "渲染链路缺少净化步骤"
    assert "sanitize" in src


def test_all_js_element_ids_exist_in_html():
    """app.js 引用的每个 id 都必须存在于 index.html。

    引用已删除的元素会让 bind() 抛 TypeError，并静默打断其后所有事件绑定
    （设置页、模型服务全部失灵），而页面看上去仍正常加载，极难排查。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    defined = set(re.findall(r'id="([^"]+)"', html))
    referenced = set(re.findall(r'\$\("([^"]+)"\)', js))
    missing = sorted(referenced - defined)
    assert not missing, f"app.js 引用了 HTML 中不存在的元素 id: {missing}"


def test_service_worker_does_not_cache_api():
    src = client.get("/app/sw.js").text
    assert "/v1/" in src


def test_root_still_reports_api_status():
    """新增前端挂载不应改变 / 的既有语义。"""
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_memory_list_shape_matches_frontend(enforced):
    """前端契约：列表只认令牌，响应里必须带 memories 数组。"""
    me = enforced("契约检查")
    client.post("/v1/memory/add", json={"content": "契约检查记忆", "summarize": False}, headers=me)
    data = client.get("/v1/memory/list?limit=5", headers=me).json()
    assert "memories" in data or "results" in data, f"实际字段: {list(data)}"
    assert [m["content"] for m in data["memories"]] == ["契约检查记忆"], \
        "列表里只能出现调用者自己的记忆"


# 记忆 wrapper 的形参表——一份**逐字**的白名单。
# 只 grep "user_id" 这个字面量是拦不住把身份改叫 _legacyId / who 的：
# 上一轮就真这么绕过去了（一个被忽略的前导形参留在 api.js 里，把没接完的线
# 藏成了"看起来已经接完"）。所以断的是形参表本身，名字对不上就红。
MEMORY_WRAPPERS = {
    "addMemory": ["content"],
    "listMemory": ["limit"],
    "searchMemory": ["query", "topK"],
    "deleteMemory": ["memoryIds"],
}

IDENTITY_TOKENS = ("userid", "user_id", "uid", "owner", "principal", "legacy")


def _wrapper_params(src: str, name: str) -> list:
    """取 api.js 里 `name: (a, b = 1) => ...` 的形参名列表。"""
    m = re.search(rf"\b{name}\s*:\s*\(([^)]*)\)\s*=>", src)
    assert m, f"api.js 里找不到 {name} 的箭头函数定义（形状变了？）"
    return [p.split("=")[0].strip() for p in m.group(1).split(",") if p.strip()]


def _call_args(src: str, name: str) -> list:
    """app.js 里所有 API.<name>(...) 的实参列表。"""
    out = []
    for args in re.findall(rf"\bAPI\.{name}\s*\(([^)]*)\)", src):
        out.append([a.strip() for a in args.split(",") if a.strip()])
    return out


def test_memory_wrappers_take_no_identity_parameter():
    """记忆接口的身份只来自令牌，前端函数也就不能收身份参数。

    后端已经不读请求里的 user_id（见 memory_router 的请求模型），这条钉的是另
    一半：前端别再把它发出去，更别留一个"被忽略的前导身份形参"——那既骗人，
    又让调用点看起来已经接完。
    """
    src = (STATIC / "api.js").read_text(encoding="utf-8")
    for name, expected in MEMORY_WRAPPERS.items():
        declared = _wrapper_params(src, name)
        assert declared == expected, f"{name} 的形参表必须是 {expected}，实际 {declared}"
    assert "_legacyId" not in src, "历史兼容形参必须随调用点一起清掉，不能留"


def test_app_js_passes_no_identity_into_memory_calls():
    """app.js 不再把本机随机标识当身份传给任何记忆调用。"""
    api_src = (STATIC / "api.js").read_text(encoding="utf-8")
    app_src = (STATIC / "app.js").read_text(encoding="utf-8")

    total = 0
    for name in MEMORY_WRAPPERS:
        declared = _wrapper_params(api_src, name)
        calls = _call_args(app_src, name)
        total += len(calls)
        for args in calls:
            assert args, f"API.{name}() 一个参数都不传，等于把 limit/query 丢了"
            assert len(args) <= len(declared), \
                f"API.{name} 只收 {len(declared)} 个参数（{declared}），实参却是 {args}"
            for a in args:
                lowered = a.lower()
                assert not any(t in lowered for t in IDENTITY_TOKENS), \
                    f"API.{name} 仍在传身份参数: {a}"
    assert total >= 4, f"记忆调用点少于 4 处，接线大概被删了（实际 {total}）"


def test_replace_session_messages_endpoint():
    sid = client.post("/v1/sessions?model=deepseek-chat").json()["session_id"]
    client.post("/v1/chat", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "第一条"}],
        "session_id": sid,
    })
    res = client.put(f"/v1/sessions/{sid}/messages", json={
        "messages": [{"role": "user", "content": "改过的内容"}]
    })
    assert res.status_code == 200

    msgs = client.get(f"/v1/sessions/{sid}").json()["messages"]
    assert [m["content"] for m in msgs] == ["改过的内容"]
    # 标题应跟随首条用户消息，供侧栏显示
    assert client.get(f"/v1/sessions/{sid}").json()["title"].startswith("改过的")


def test_replace_session_messages_rejects_unknown_session():
    res = client.put("/v1/sessions/nope/messages", json={"messages": []})
    assert res.status_code == 404


def test_replace_session_messages_drops_malformed_entries():
    sid = client.post("/v1/sessions").json()["session_id"]
    res = client.put(f"/v1/sessions/{sid}/messages", json={"messages": [
        {"role": "user", "content": "合法"},
        {"role": "user"},                 # 缺 content
        {"role": 1, "content": 2},        # 类型不对
    ]})
    assert res.status_code == 200
    msgs = client.get(f"/v1/sessions/{sid}").json()["messages"]
    assert [m["content"] for m in msgs] == ["合法"]


def test_replace_session_messages_rejects_non_object_items():
    sid = client.post("/v1/sessions").json()["session_id"]
    res = client.put(f"/v1/sessions/{sid}/messages", json={"messages": ["不是对象"]})
    assert res.status_code == 422


# ---------- Task 7：注册流与按角色收敛界面 ----------


def _ids_with_attr(html: str, attr: str) -> set:
    """带某个属性的标签的 id 集合（没有 id 的带这个属性的标签不计）。"""
    out = set()
    for tag in re.findall(r"<[^>]+>", html):
        if attr not in tag:
            continue
        m = re.search(r'id="([^"]+)"', tag)
        if m:
            out.add(m.group(1))
    return out


def _function_body(src: str, name: str) -> str:
    """取 `function name(...) { ... }` 的函数体（含末尾大括号）。

    需要按函数断言顺序/措辞时用它：在整份 app.js 里 grep "注册" 这种词，
    任何一处不相干的注释都能把它喂绿。
    """
    start = src.index(f"function {name}(") if f"function {name}(" in src else -1
    if start < 0:
        raise AssertionError(f"app.js 里没有 function {name}()——按角色分流的接线大概还没落地")
    open_at = src.index("{", start)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at:i + 1]
    raise AssertionError(f"{name} 的大括号没闭合（或函数被截断）")


def test_registration_ui_elements_wired():
    """注册界面缺元素会让 app.js 的绑定静默失败，整块输入区失灵。

    现在有两个入口（首屏弹层 + 设置页），所以两边的元素都要逐个对上：
    少一个 id 不会报错，只会让那个按钮点了没反应。

    首屏这层按参考图重排过：登录/注册不再是两个 tab，而是表单下面那对
    「忘记密码 / 立即注册」文字链接（切换模式的是后者）；密码框里多了一个眼睛，
    右侧多一张说明卡。旧的 authTab* 必须一起消失——留着就分不清哪套在用。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    defined = set(re.findall(r'id="([^"]+)"', html))
    for el in ("regUsername", "regPass", "registerBtn",
               "authModal", "authUser", "authPass", "authGo", "authHint",
               "authSwitch", "authForgot", "authEye", "authSide"):
        assert f'$("{el}")' in js, f"app.js 引用了 #{el} 但 HTML 未定义"
        assert el in defined, f"HTML 里没有 #{el}"
    assert "authTab" not in html and "authTab" not in js, "旧的 tab 还在：两套入口并存"


def test_auth_layer_keeps_the_two_column_layout_and_no_dead_rules():
    """版式的两条硬约束写在 CSS 里，只能在这里钉：没有浏览器测试跑得到它。

    宽屏两列、窄屏（<=860px）必须塌回单列——手机是这产品的主要入口，两列不塌陷
    就等于把表单挤成一条缝。旧的 .auth-tabs 规则一并删掉：留着的那条不是样式，
    是"下一次改版不知道哪套还在用"的起点。
    """
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".auth-grid" in css and ".auth-side" in css, "两列版式不在了"
    # 样式表里有多段 @media (max-width: 860px)（侧栏一段、首屏一段），
    # 只取"管首屏的那一段"来判，否则断言会打在毫不相干的块上。
    narrow = [b for b in re.findall(r"@media\s*\(max-width:\s*860px\)([\s\S]*?)\n\}", css)
              if ".auth-grid" in b]
    assert narrow, "窄屏没有把两列塌成单列"
    assert not re.search(r"\.auth-side[^{]*\{[^}]*display:\s*none", narrow[0]), \
        "窄屏又把说明卡藏了：手机上是决定要显示在表单下方的"
    assert ".auth-tabs" not in css and ".auth-tab" not in css, "tab 的死规则还留在样式表里"


def test_the_password_eye_reveals_only_that_field():
    """眼睛按钮必须真的翻 type，而且只翻首屏这一格。

    翻错框等于把设置页的密码也亮出来；只翻 type 不改 value 才不会把已输入的
    密码清空——用户点一下眼睛是为了核对，不是为了重敲。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "toggleAuthPass")
    assert '$("authPass")' in body, f"眼睛动的是别的框：{body}"
    assert '"regPass"' not in body
    assert re.search(r'type\s*=\s*.*text.*password|text.*:.*"password"', body), \
        f"没有真的在 text/password 之间来回切：{body}"
    assert ".value" not in body, "切明文不该顺手清空已输入的密码"
    assert '$("authEye").onclick = toggleAuthPass' in js


def test_forgot_password_says_so_instead_of_calling_a_missing_endpoint():
    """没有自助改密（这是当初写进部署文档的取舍），所以这个链接只许说人话。

    它一旦发出任何请求，就是把"我们其实做不到"伪装成"正在处理"；正确的一句话
    是让人去找管理员，因为 /admin 里确实有重置令牌/停用/删除这些能办事的按钮。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "showForgotHint")
    assert "API." not in body and "fetch(" not in body, "忘记密码不该发出任何请求"
    assert "admin" in body.lower() and "重置" in body, f"没指路到管理员：{body}"
    assert '$("authHint")' in body
    assert '$("authForgot").onclick = showForgotHint' in js


def test_memory_calls_no_longer_send_user_id():
    api = (STATIC / "api.js").read_text(encoding="utf-8")
    assert "user_id" not in api, "记忆接口已不接受客户端身份"


def test_register_and_me_wrappers_match_the_backend_contract(client, enforced):
    """前后端字段名对不上是静默失败：后端 422，界面只说"注册失败"。

    请求形状与 app.js 读的那几个响应键一起断，且响应是真的从 /v1/auth/register
    拿的，不是照抄一份字典——改名（token→access_token 这种）当天就该红。
    """
    from app.core import authz as authz_mod
    from app.core.auth_router import LoginRequest, RegisterRequest

    api = (STATIC / "api.js").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert set(RegisterRequest.model_fields) == {"username", "password"}
    assert set(LoginRequest.model_fields) == {"username", "password"}
    m = re.search(r"register:\s*\(([^)]*)\)\s*=>\s*request\(\"/v1/auth/register\"", api)
    assert m, "api.js 的 register 封装形状变了，这条契约要重看"
    assert [p.strip() for p in m.group(1).split(",")] == ["username", "password"]
    lg = re.search(r"login:\s*\(([^)]*)\)\s*=>\s*request\(\"/v1/auth/login\"", api)
    assert lg, "api.js 没有 login 封装：注册之后就没有第二条回到系统里的路"
    assert [p.strip() for p in lg.group(1).split(",")] == ["username", "password"]
    assert re.search(r'\bme:\s*\(\)\s*=>\s*request\("/v1/auth/me"\)', api), \
        "前端没有 me 封装：角色就只能靠猜"

    enforced("垫底用户")
    res = client.post("/v1/auth/register",
                         json={"username": "字段名契约", "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text
    body = res.json()
    for key in ("token", "user_id", "username"):
        assert key in body, f"后端没回 {key}：{sorted(body)}"
        assert f"res.{key}" in js, f"后端回的是 {key}，前端读的却是别的名字"


def test_admin_only_surfaces_are_marked_in_html_and_swept_by_role():
    """providers 转管理员之后，普通用户点进「模型服务」就是一个 403。

    约定是一条属性（data-admin-only）+ JS 里一处统一开关，而不是散落的 if：
    以后新加管理员专属控件只要带上这条属性就自动纳入，不必再改 app.js，也
    不会"改了三处漏一处"。
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    marked = _ids_with_attr(html, "data-admin-only")
    assert {"navProviders", "tabProviders"} <= marked, f"模型服务的入口没标出来：{sorted(marked)}"
    assert 'querySelectorAll("[data-admin-only]")' in js, "app.js 没有统一按属性收口"
    sweep = _function_body(js, "applyRole")
    assert re.search(r'classList\.toggle\("hidden"', sweep), "收口没有真的隐藏元素"
    assert "isAdmin()" in sweep


def test_admin_only_surfaces_start_hidden_in_the_html_itself():
    """隐藏必须是 HTML 里的默认值，而不是等 JS 去收。

    applyRole() 只有在 /v1/auth/me 回来之后才跑得到，所以"默认可见 + JS 收起"
    这个组合等于每次冷启动都先给普通用户画出一个「模型服务」入口、再在他眼前
    收掉——闪烁之外，那一刻它是可点的，点进去就是一句必然的 403。默认写 hidden
    之后，特权入口只在**确认**是管理员时才出现，方向也从"漏出来再收"变成"收起
    再放"。这条断言只看 HTML，因此与 app.js 什么时候跑无关。
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    naked = []
    # 只扫真正的起始标签：注释里也会提到 data-admin-only 这个词，`<[^>]+>` 会把它
    # 当成一个元素读进来（`<!` 不匹配 `[a-zA-Z]`，正好被排除）
    for tag in re.findall(r"<[a-zA-Z][^>]*>", html):
        if "data-admin-only" not in tag:
            continue
        m = re.search(r'class="([^"]*)"', tag)
        classes = (m.group(1) if m else "").split()
        if "hidden" not in classes:
            naked.append(re.search(r'id="([^"]+)"', tag).group(1) if 'id="' in tag else tag[:48])
    assert not naked, f"这些管理员专属元素默认可见，/v1/auth/me 返回前会闪出来：{naked}"


def test_boot_learns_the_role_before_loading_server_data():
    """角色得在第一次渲染之前拿到。

    loadModels() 在没有可用模型时会直接把用户推进「设置 → 模型服务」，那条
    分支按角色分流；me 晚一步回来，普通用户就仍然被领进一个必然 403 的页签。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    boot = _function_body(js, "boot")
    assert "loadWho" in boot and "loadServerData" in boot
    assert boot.index("loadWho") < boot.index("loadServerData"), \
        "boot 先取身份再取数据，否则角色分流会慢一拍"


def test_memory_search_stays_inside_the_backends_own_cap():
    """搜索记忆原先发 topK=30，后端 SearchMemoryRequest.top_k 是 le=20 → 恒 422。

    上限只写一处（api.js 的常量），并且是**从后端读出来比对**的，不是抄一份
    数字：后端哪天收紧到 10，这条会红着提醒，而不是让用户再撞一次看不懂的报错。
    超限的请求夹回上限，不原样发出去挨 422。
    """
    from app.memory.memory_router import SearchMemoryRequest

    cap = SearchMemoryRequest.model_json_schema()["properties"]["top_k"]["maximum"]
    api = (STATIC / "api.js").read_text(encoding="utf-8")
    m = re.search(r"MEMORY_TOP_K_MAX\s*=\s*(\d+)", api)
    assert m, "api.js 未声明 MEMORY_TOP_K_MAX：上限散落在各调用点，迟早和后端对不上"
    assert int(m.group(1)) == cap, f"前端上限 {m.group(1)} ≠ 后端 le={cap}"
    assert re.search(r"Math\.min\s*\([^)]*MEMORY_TOP_K_MAX", api), "没有夹紧，超限照发"

    js = (STATIC / "app.js").read_text(encoding="utf-8")
    calls = re.findall(r"API\.searchMemory\(([^)]*)\)", js)
    assert calls, "app.js 里的搜索调用点不见了"
    for args in calls:
        for n in re.findall(r"\b(\d+)\b", args):
            assert int(n) <= cap, f"app.js 又要 {n} 条，超过后端上限 {cap} 就是 422"


def test_memory_stats_is_queried_only_for_admins():
    """/v1/memory/stats 是管理员端点：普通用户那儿不能发这一枪。

    发出去的后果不是报错本身，而是那句"记忆服务不可用"——服务明明好着，只是
    他没权限，用户于是去重启后端，而重启完全治不了这件事。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "loadAbout")
    assert "memoryStats" in body, "关于页已经不读记忆统计了？那这条契约该删还是该改，得有人说清"
    assert "isAdmin()" in body and body.index("isAdmin()") < body.index("memoryStats"), \
        "统计请求没有按角色分流"


def test_a_stale_token_does_not_read_like_a_first_run():
    """401 有两种，糊成一句就把人支使去填一个已经填对的框。

    本机压根没存过令牌 = 首启，该引导他注册；存过却被拒 = 管理员撤销或轮换过，
    再说"请填写口令"就是让人反复重试同一个废令牌。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "needsAuth")
    assert "需要访问口令" not in js, "旧的合并文案还在，两种 401 仍是一句话"
    assert re.search(r"err\.status\s*[!=]==\s*401", body), "needsAuth 只该管 401：403 是身份够了、角色不够"
    assert "403" not in body
    assert re.search(r'pref\.token', body), "未按本机是否已有令牌分叉"
    assert "失效" in body and "注册" in body, "两条分支的措辞都得在场"


def test_registration_locks_its_button_while_the_request_is_in_flight():
    """登录/注册没有在途闸门 = 手机双击发出第二个 POST。

    第二下拿回的是"用户名已被占用"或一次多余的 401：界面于是把一个已经成功的
    人标成红色失败，两次调用还一起抢 pref.token 的写入与
    loadWho→loadServerData→renderMessages 的顺序。约定跟 send() 守 state.streaming
    一模一样——进门先挡、解锁放在 finally（失败也必须解，否则一次网络抖动就把唯一
    的入口按死到刷新页面为止），并且凭据一落地就把它清出输入框。

    入口现在有两处（首屏弹层与设置页），所以两处各验一遍：多一条路就多一处能双击。
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert re.search(r"streaming: false,\s*\n\s*registering: false", js), \
        "state 里没有了 registering：在途闸门大概退回了只靠 disabled 一处"

    for name, btn, api_call in (
            ("submitAuth", "authGo", "API.register"),
            ("registerFromSettings", "registerBtn", "API.register")):
        body = _function_body(js, name)
        assert re.search(r"if \(state\.registering\) return", body), f"{name} 不再挡双击"
        assert body.index("if (state.registering) return") < body.index(api_call), \
            f"{name} 的闸门得在发请求之前"
        assert "state.registering = true" in body and f'$("{btn}").disabled = true' in body, \
            f"{name} 请求在途时按钮还亮着"
        unlock = body[body.index("finally"):]
        assert f'$("{btn}").disabled = false' in unlock and "state.registering = false" in unlock, \
            f"{name} 的解锁不在 finally 里：失败一次就再也点不动了"

    # 成功之后两个密码输入框都不许留下密码：首屏那个由共用的 afterAuth 清，设置页
    # 那个有自己的字段、必须自己清。失败时故意留着——逼人重敲一遍密码只会把人赶去
    # 用 "12345678"，安全上是净损失。
    tail = _function_body(js, "afterAuth")
    assert '$("authPass").value = ""' in tail, "首屏那格的密码没人清"
    assert '$("regPass").value = ""' in _function_body(js, "registerFromSettings"), \
        "设置页那格的密码没人清"
    # 收尾动作的先后是硬约束：先清输入框再写 pref.token 的话，中途抛异常就把
    # 唯一一次拿到令牌的机会连同输入一起丢了。
    assert tail.index("pref.token = res.token") < tail.index('$("authPass").value = ""'), \
        "清空必须晚于令牌落库：早一步就是在丢凭据"
