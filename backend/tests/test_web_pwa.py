"""PWA 静态托管、前端契约与会话消息替换端点测试。"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app
# 前端契约用例拿它当尺子：界面上该有几道题、题面逐字是什么，都由后端这一条说了算。
# 在测试里 import 而不是抄一份数字，正是这条锁的全部意义。
from app.core.auth import RECOVERY_QUESTIONS
from app.web.web_router import STATIC_DIR

client = TestClient(app)

STATIC = Path(STATIC_DIR)
JS_FILES = ("api.js", "app.js", "markdown.js", "sw.js")


def test_app_serves_html_shell():
    res = client.get("/app/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    # 判语料的尺子在这里一样要过：这句话写进 HTML 注释里，浏览器一个字都不渲染，
    # 而原始文本断言照样绿。
    assert "AI 智能助手" in _strip_html_comments(res.text)


def test_static_assets_reachable():
    assets = ("style.css", "app.js", "api.js", "markdown.js", "sw.js",
              "manifest.webmanifest", "icon.png",
              "vendor/marked.min.js", "vendor/purify.min.js",
              "vendor/highlight.min.js", "vendor/hljs-github-dark.min.css")
    missing = [a for a in assets if client.get("/app/" + a).status_code != 200]
    assert not missing, f"缺失静态资源: {missing}"


def test_static_assets_declare_their_own_freshness():
    """不发 Cache-Control 的静态资源，等于把改版交给别人的缓存去决定。

    2026-09-17 实测：重建并重启后，公网 /app/style.css 仍是 80 分钟前那份旧的
    （cf-cache-status: HIT，Cloudflare 对 .css/.js 默认注入 max-age=14400）。
    源站自己没表态，浏览器与边缘就各自按启发式缓存——朋友那边看到的现象是
    "改了没生效"，而这正是本项目最容易被误判成代码坏了的一类形状。

    留下来的红线是**每个响应都得自己表态，而且带 ETag**；至于表态成哪一种，是两支：
    不带水印的资源与 sw.js 回 no-cache（每用一次先问一次，问价的 ETag 在那儿），
    HTML 与带当下水印的资源各归 tests/test_asset_versioning.py 钉（一笔是 30 秒的
    窗口，一笔是一年的长缓存），那两条锁改坏了会在那里红，不在这里。
    """
    for path in ("/app/style.css", "/app/app.js", "/app/sw.js", "/admin/admin.css"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", \
            f"{path} 的 cache-control 是 {res.headers.get('cache-control')!r}"
        assert res.headers.get("etag"), f"{path} 没有 ETag：no-cache 会退化成每次全量重传"
    for page in ("/app/", "/admin/"):
        head = client.get(page).headers.get("cache-control")
        assert head and head != "no-cache" and "max-age=" in head, \
            f"{page} 的 HTML 缓存口径没表态或退回了 no-cache：重复开门又要整趟回源"


def test_frontend_uses_relative_api_paths_only():
    """前端不得出现绝对服务地址，否则换网络/换设备即失效。"""
    offenders = []
    for name in JS_FILES:
        src = _js(name)
        for m in re.finditer(r"""['"]https?://[^'"]+['"]""", src):
            offenders.append(f"{name}: {m.group(0)}")
    assert not offenders, f"前端出现绝对 URL: {offenders}"


def test_model_output_is_sanitized_before_html_render():
    """模型回复按 Markdown 渲染成 HTML，必须过 DOMPurify，否则可注入脚本。"""
    src = _js("markdown.js")
    assert "DOMPurify" in src, "渲染链路缺少净化步骤"
    assert "sanitize" in src


def test_all_js_element_ids_exist_in_html():
    """app.js 引用的每个 id 都必须存在于 index.html。

    引用已删除的元素会让 bind() 抛 TypeError，并静默打断其后所有事件绑定
    （设置页、模型服务全部失灵），而页面看上去仍正常加载，极难排查。
    """
    js = _js()
    html = _html()
    defined = set(re.findall(r'id="([^"]+)"', html))
    referenced = set(re.findall(r'\$\("([^"]+)"\)', js))
    missing = sorted(referenced - defined)
    assert not missing, f"app.js 引用了 HTML 中不存在的元素 id: {missing}"


def test_service_worker_does_not_cache_api():
    """sw.js 里必须真有一句"这是接口，别碰缓存"的判断。

    走 _js() 而不是 client.get("/app/sw.js").text：那把尺子的理由是通用的——sw.js 顶上
    那段注释里本来就写着 `/v1/*`，原始文本判断在"整段代码被删掉、只留那句注释"时照样绿。
    静态资源能不能经 HTTP 取到由 test_static_assets_reachable 钉，两件事不必混在一条里。
    """
    assert "/v1/" in _js("sw.js")


def test_site_and_pwa_do_not_cover_each_other():
    """/` 现在是官网,`/app` 仍是 PWA —— 两条都要在,而且返回的不是同一份东西。

    这条前身叫 test_root_still_reports_api_status,钉的是"`/` 没被 PWA 挂载抢走"。
    `/` 换主之后如果直接删掉它,就没人管"`/app` 有没有被官网抢走"了。
    """
    root = client.get("/")
    app = client.get("/app/")
    assert root.status_code == 200 and app.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert app.headers["content-type"].startswith("text/html")
    assert root.text != app.text, "两个页面返回了同一份内容,说明有一个被覆盖了"


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
    src = _js("api.js")
    for name, expected in MEMORY_WRAPPERS.items():
        declared = _wrapper_params(src, name)
        assert declared == expected, f"{name} 的形参表必须是 {expected}，实际 {declared}"
    assert "_legacyId" not in src, "历史兼容形参必须随调用点一起清掉，不能留"


def test_app_js_passes_no_identity_into_memory_calls():
    """app.js 不再把本机随机标识当身份传给任何记忆调用。"""
    api_src = _js("api.js")
    app_src = _js()

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


def _up_to_matching_brace(text: str) -> str:
    """从开头的 `{` 走到它配对的那个 `}`（含）。走不到就明说，别把截断当"没有"。"""
    assert text.startswith("{"), f"这段文本不是以花括号开头：{text[:40]}"
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    raise AssertionError(f"花括号没闭合，取不到整段：{text[:60]}…")


def _handler_of(js: str, el: str, event: str = "onclick") -> str:
    """`$("el").onclick = <处理器>` 里的那一段处理器文本，**多行写法也整段拿到**。

    原来写的是 `= (.*)`，只吃一行：把这个处理器改成多行 arrow function（内容仍然
    经过 setAuthMode）就会红成"绕过了 setAuthMode"——红话说反了，而一条会说反话的锁
    最后的下场是被人删掉。所以块体按配对花括号整段取，表达式体取到下一条 `$(` 绑定之前。
    """
    m = re.search(r'\$\("%s"\)\.%s = ' % (re.escape(el), re.escape(event)), js)
    assert m, f"app.js 里没有 $({el}).{event} 这条绑定：接线被改了还是被删了？"
    rest = js[m.end():]
    if rest.lstrip().startswith("{"):
        return _up_to_matching_brace(rest.lstrip())
    nxt = re.search(r"\n\s*\$\(", rest)
    return rest[:nxt.start()] if nxt else rest


def _strip_js_comments(src: str) -> str:
    r"""把 JS 注释挖掉、换行留在原处：判语料之前先过这道。

    两类事故都真出现过："某词在不在函数体里"这种断言，一句解释性的注释就能把它喂绿
    （留着 `// setAuthMode(mode);` 而把真调用删掉，38 条全绿）；反过来一条**出现次数**
    的断言又会被同一句注释误判红。两头的代价都是"锁被人当噪声拆掉"，所以尺子先剥注释。
    字符串里的 `//` 不是注释，所以按字符扫并带一个引号状态机（漏了这一步，一句
    `"https://…"` 就会把其后整行吃掉）。它的判据见
    test_the_comment_ruler_needs_its_own_test。

    状态机走到行尾必须把单/双引号关掉（反引号除外：模板字符串真能跨行）。不加这一步
    它在这份文件上从来就没工作过：app.js 的旧导出路径里 `safeFilename()` 那句
    `.replace(/[\\/:*?"<>|\r\n]+/g, " ")` 的正则字面量带一个裸 `"`，引号状态一开着就
    一路跨行带到文件尾，其后 1400 多行全被当成"还在字符串里"原样抄走（那个函数已随
    导出改走服务端票据而删掉，形状本身由下面 tricky 那段自己钉住，不靠语料里还有它）。
    实测旧写法剥完
    还剩 49 行整行注释与 53 行块注释续行，改完之后是 0 行，而真代码一处不少（1595 行、
    `API.` 29 处、`$(` 246 处）——一把看起来在干活、实际上只剥了前 103 行的尺子，比没有
    尺子更糟，因为写锁的人以为自己有牙。判据（含"不许退化成恒真"那一半）在同一条测试里。
    """
    out = []
    quote = ""
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = ""
            elif ch == "\n" and quote != "`":
                quote = ""      # 这一行没有闭合的引号不是字符串，是状态机被骗了
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            end = src.find("\n", i)
            i = n if end < 0 else end          # 换行本身留着：行号不能错位
            continue
        if ch == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("\n" * src.count("\n", i, end))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_html_comments(src: str) -> str:
    r"""把 HTML 注释挖掉、换行留在原处：读 index.html 判事之前先过这道。

    与 _strip_js_comments 同一个理由，只是换个语法：`<!-- 三道全对才改得动… -->` 在浏览
    器里一个字都不渲染，在原始文本里却和真文案一模一样。终审实测把 index.html 那句安全
    说明删掉、原地改写成 HTML 注释，判"在不在文本里"的锁照绿——而它锁的是"别把秘密说成
    公开"（人会照着"反正大家一样"敷衍作答）。

    换行必须留着：好几把锁按行/按位置判（`html.find(...) < row.start()`），吃掉换行就等于
    把它们挪到别的地方去红。没闭合的 `<!--` 也当注释处理到文件尾——浏览器就是这么读的，
    而"删掉结尾那两个字符"正是一条让整段真文案消失、又能骗过原始文本断言的改法。
    判据见 test_the_html_comment_ruler_needs_its_own_test。
    """
    out = re.sub(r"<!--[\s\S]*?-->", lambda m: "\n" * m.group(0).count("\n"), src)
    head, opened, tail = out.partition("<!--")       # 剩下这个一定没闭合
    return head + "\n" * tail.count("\n") if opened else out


def _js(*names: str) -> str:
    """读前端 JS 当判据语料的**唯一**入口：一律先过剥注释那把尺子。

    默认 app.js。写锁的人不该每次自己想起来要剥——上一轮就是"这一处记得剥、那一处忘了"，
    而忘记的那处绿得和剥过的一样。收成一个入口之后，"绕过尺子读原始文本"这件事在本文件里
    只剩 read_text 那一行，由 test_every_corpus_read_goes_through_a_ruler 钉住。
    """
    files = names or ("app.js",)
    return _strip_js_comments("".join((STATIC / f).read_text(encoding="utf-8")
                                      for f in files))


def _html() -> str:
    """读 index.html 当判据语料的唯一入口：先剥 HTML 注释。"""
    return _strip_html_comments((STATIC / "index.html").read_text(encoding="utf-8"))


def _css() -> str:
    """读 style.css 的唯一入口：**刻意不剥注释**，与上面两个相反。

    用它的那条锁判的是"删掉的样式不许在表里留尸"（.auth-grid 这类），而一句
    `/* .auth-grid { … } */` 就是尸体本身：注释掉一条规则不会让它停止占位，只会让下
    一次改版的人读不出哪套还在用。这里剥了注释等于给那种改法放水，所以留着原始文本。
    它是 test_every_corpus_read_goes_through_a_ruler 里那条例外的全部理由。
    """
    return (STATIC / "style.css").read_text(encoding="utf-8")


# 没有闭合标签的元素：不能当祖先压进栈里，否则后面的 </div> 会错位
_VOID_TAGS = ("input", "img", "br", "hr", "meta", "link", "path", "circle", "source")


def _tag_is_hidden(tag: str) -> bool:
    """这个开标签是否被 hidden 挂住：class 里有 `hidden` 这个词，或自带 hidden 属性。

    不写 `"hidden" in tag` 是因为 `aria-hidden="true"` 会被误判成隐藏——那是一条无障碍
    属性，恰恰是给屏幕阅读器说"这一格存在"的。
    """
    classes = re.search(r'class="([^"]*)"', tag)
    if classes and "hidden" in classes.group(1).split():
        return True
    return re.search(r"\shidden[\s/>=]", tag) is not None


def _open_tags_to(html: str, container: str, el_id: str) -> list:
    """从 `id=container` 那个 div 的开标签走到 `id=el_id` 的开标签，返回沿途开标签。

    含容器自己（第一项，它默认 hidden、由 JS 摘掉），其余都是 #el_id 的祖先。
    走不通（容器不存在、#el_id 在容器外面或压根没有）返回 []：调用方据此判红。
    """
    m = re.search(r'<div\b[^>]*\bid="%s"[^>]*>' % re.escape(container), html)
    if not m:
        return []
    path = [m.group(0)]
    for tag in re.findall(r"<[a-zA-Z][^>]*>|</[a-zA-Z]+>", html[m.end():]):
        if tag.startswith("</"):
            if len(path) == 1:
                return []          # 容器的闭合标签到了：#el_id 不在容器内部
            path.pop()
            continue
        if f'id="{el_id}"' in tag:
            return path + [tag]
        if re.match(r"</?([a-zA-Z][\w-]*)", tag).group(1).lower() not in _VOID_TAGS:
            path.append(tag)       # void 元素不会有闭合标签，压进去就会把后面的配对全错位
    return []


def test_the_comment_ruler_needs_its_own_test():
    r"""剥注释这把尺子自己得有判据：它一坏，上面那些锁就退回"能不能被注释喂绿"。

    钉六件事：行注释里的字样消失、真语句一条不少地留下、**跨行**块注释里的字样不算调用
    （`\n\s*setAuthMode(...)` 那种形状锁最怕的就是它——注释的第二行看起来就是一行代码）、
    字符串里的 `//` 不被当成注释起点（否则那一行剩下的部分凭空消失，锁会绿在"没找到"上）、
    一个跨行都不配对的引号不许把其后整份文件判成字符串，以及在**真语料**上它今天确实剥掉了
    注释（前四条全过、真文件上却一个字没剥，是这把尺子上一轮的实际状态）。
    """
    src = ('function f() {\n'
           '  // 上一版的 setAuthMode(x) 就在这里\n'
           '  setAuthMode(mode || authMode);\n'
           '  /* 解释一句：showAuth() 负责\n'
           '     清格子，别照抄 setAuthMode() */\n'
           '  const url = "https://example.com/v1";\n'
           '}\n')
    stripped = _strip_js_comments(src)
    assert stripped.count("setAuthMode(") == 1, f"真语句没了或注释没剥净：{stripped!r}"
    assert "showAuth(" not in stripped, f"跨行块注释的第二行被当成了代码：{stripped!r}"
    assert '"https://example.com/v1"' in stripped, "字符串里的 // 被当成了注释起点"
    assert stripped.count("\n") == src.count("\n"), "换行被吃掉：行号错位会让按行的形状锁失真"

    # 第五件：正则字面量里那个裸双引号把状态机骗开之后，"引号没闭合"必须有尽头。
    # 这一小段就是当初把尺子骗过去的那一行的形状（app.js 里那个函数已随导出改走
    # 服务端票据被删，所以判据必须自带样本，不能依赖语料里还留着它）。
    tricky = ('const bad = /[:*?"<>|]+/g;\n'
              '// authFail(""); 这一整行是注释，不许留在语料里\n'
              'setUserError("");\n')
    got = _strip_js_comments(tricky)
    assert "authFail" not in got, (
        f"未闭合的引号让尺子把其后所有内容当成字符串抄走了：{got!r}")
    assert 'setUserError("");' in got, "修引号状态时把真语句也一起剥了"
    assert got.count("\n") == tricky.count("\n")

    # 第六件：在真语料上量一次。上面五条都对、这份文件上却一行没剥，是上一轮的实际状态。
    raw = (STATIC / "app.js").read_text(encoding="utf-8")
    assert any(l.lstrip().startswith("//") for l in raw.splitlines()), \
        "app.js 里已经没有整行注释了：这条判据退化成恒真，得换一种量法"
    assert any(l.lstrip().startswith("*") for l in raw.splitlines()), \
        "app.js 里已经没有多行块注释了：同上"
    left = _strip_js_comments(raw)
    lines = left.splitlines()
    assert not [l for l in lines if l.lstrip().startswith("//")], "真文件里的整行注释没被剥掉"
    assert not [l for l in lines if l.lstrip().startswith("*")], "真文件里的块注释续行没被剥掉"
    # 反向：剥注释不许顺手吃掉真代码，否则所有锁都会红在"没找到"上
    assert len(lines) == len(raw.splitlines()), "换行被吃掉：行号错位"
    for needle in ('API.', '$('):
        assert left.count(needle) == raw.count(needle), (
            f"剥注释前后 {needle} 的数量变了（{raw.count(needle)} → {left.count(needle)}）："
            "要么尺子吃掉了真调用（那是把尺子修没了），要么某句注释里写下了这个字样——"
            "后者同样得改，因为那正是能把 in 判断喂绿的东西")


def test_the_html_comment_ruler_needs_its_own_test():
    """**新尺子的判据**：`<!-- -->` 在浏览器里什么都不渲染，在原始文本里却和真文案同形。

    终审实测：把 index.html 那句"三道全对才改得动…"删掉、原地改写成 HTML 注释，判
    `"…那三句" in html` 与那条 `re.search` 全绿——于是这把尺子必须自己先有牙。钉四件事：
    注释里的字样消失、真文案一个字不少、换行留在原处（多把锁按位置判）、没闭合的 `<!--`
    按浏览器的读法一路当注释到文件尾（"删掉结尾那两个字符"是同一类改法）。
    """
    src = ('<p class="auth-sub">回答这三道题，再设一个新密码。</p>\n'
           '<!-- 上一版这里写着"三道全对才改得动"\n'
           '     第二行看起来还是一句正文 -->\n'
           '  <input id="rcAns1" placeholder="你的答案">\n'
           '<!-- 没闭合的注释从这里开始，其后整段都不该再被当成正文\n'
           '     <input id="ghost" placeholder="这一格其实不存在">\n')
    stripped = _strip_html_comments(src)
    assert "三道全对才改得动" not in stripped, f"注释里的字样还在：{stripped!r}"
    assert "这一句正文" not in stripped, "跨行注释的第二行被当成了正文"
    assert "回答这三道题，再设一个新密码。" in stripped, "真文案被一起吃掉了"
    assert 'id="rcAns1"' in stripped and 'id="ghost"' not in stripped, \
        f"没闭合的 <!-- 没当注释处理：{stripped!r}"
    assert stripped.count("\n") == src.count("\n"), "换行被吃掉：按位置判的锁会挪到别处去红"

    # 同样要在真语料上量一次，并先确认它不是恒真
    raw = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<!--" in raw, "index.html 里已经没有注释了：这条判据退化成恒真，换个量法"
    assert "<!--" not in _strip_html_comments(raw), "真文件里还留着注释的开头"


def test_every_corpus_read_goes_through_a_ruler():
    """入口收口的收口：**除三处指定读者之外**，本文件里不许有人直接读原始语料。

    上一轮的问题是"这一处记得剥、那一处忘了"，而忘了那处绿得和剥过的一样——所以这次不是
    再加一处 `_strip_js_comments(...)`，而是把读文件收成 `_js()` / `_html()` / `_css()`
    三个入口，再用这条锁钉住"只有这三个入口在读文件"。指定读者之外多一处 read_text 就红，
    红话说的是"新加的那条锁绕过了尺子"，而不是某个不相干的断言失败。

    两条尺子自己的判据是**故意**读原始文本的：不拿 raw 与剥完的作对照，就没法证明尺子真剥掉
    了东西（app.js 那把旧的就是这么坏掉的）。反向锁的固有性质是"没人违反时它就是绿的"，
    所以它的牙由变异验证给：临时加一处 raw 读取，它必须当场红。
    """
    designated = {"_js", "_html", "_css",
                  # 这两条拿原始文本与剥完的作对照，它们本身就是尺子的判据
                  "test_the_comment_ruler_needs_its_own_test",
                  "test_the_html_comment_ruler_needs_its_own_test"}
    bypassers = []
    for name, fn in sorted(globals().items()):
        # 只看本文件自己定义的函数：globals() 里还躺着 app（FastAPI 实例，可调用）、
        # client 这些外来的东西，getsource 对它们抛的是 TypeError，而这条锁管的是
        # "本文件里谁在读语料"，与外来对象无关。
        if name in designated or not inspect.isfunction(fn) or fn.__module__ != __name__:
            continue
        try:
            body = inspect.getsource(fn)
        except OSError as e:      # 打进 EXE 时没有 .py 源文件：明说没跑成，别判红
            pytest.skip(f"读不到 {name} 的源码，收口锁无从谈起：{e}")
        if re.search(r"read_text\(", body):   # 正则里那个反斜杠不是装饰：写成字面量
            bypassers.append(name)            # 会让这条判断咬到自己源码里的同一个词
    assert not bypassers, (
        f"这些函数绕开 _js()/_html()/_css() 直接读了语料原始文本：{bypassers}——"
        "原始文本能被注释喂绿，那正是终审点名的两处假绿的成因")


def test_registration_ui_elements_wired():
    """注册界面缺元素会让 app.js 的绑定静默失败，整块输入区失灵。

    两个入口（首屏弹层 + 设置页）的元素都要逐个对上：少一个 id 不会报错，
    只会让那个按钮点了没反应。

    首屏这一层现在有三块内容：登录/注册（注册第一步比登录多一格确认密码，第二步
    是三条固定题的答案格）、用户名那一格下面的就地红字、以及同层互换的找回表单。
    被删掉的东西也必须真的没了：说明卡（authSide/authHost）、旧的 tab 入口、
    以及"自设一个问题"那一格的形状（authQuestion/authAnswer/rcQuestion/rcAnswer）。
    """
    js = _js()
    html = _html()
    defined = set(re.findall(r'id="([^"]+)"', html))
    for el in ("openRegister",
               "authModal", "authUser", "authPass", "authGo", "authHint", "authEye",
               "authSwitch", "authUserErr", "authPass2", "authPass2Row", "authPassRow",
               "regStep2", "regBack",
               "recoverForm", "rcUser", "rcStep2", "rcNew", "rcNew2", "rcGo", "rcHint",
               "rcBack", "whoRow", "userName", "userAvatar", "topTitle"):
        assert f'$("{el}")' in js, f"app.js 里没有 $({el})：这个元素要么没接线要么已删"
        assert el in defined, f"HTML 里没有 #{el}"
    for gone in ("authTab", "authSide", "authHost", "navSettings",
                  "regUsername", "regPass", "registerBtn", "registerFromSettings",
                  "authExtra", "authQuestion", "authAnswer",
                  "rcQuestion", "rcAnswer", "rcQuestionRow", "rcAnswerRow",
                  "rcNewRow", "rcNew2Row",
                  # 顶栏瘦身与底栏整合删掉的两件：那颗没有文字说明的小圆点，和那颗
                  # 只是"进设置里角色那一页"的快捷入口。留着其中任何一件，就等于
                  # 承认"再占一个位置"是有道理的。
                  "connDot", "personaChip"):
        assert gone not in html and gone not in js, f"{gone} 还在：删剩的半套比没删更难读"


def test_the_topbar_holds_only_the_sidebar_toggle_and_the_title():
    """顶栏只剩「☰」与会话标题：模型选择、角色、导出全部搬进设置那一页。

    反向断言是重点——这三件东西每一个都有活着的理由，所以将来一定有人想"顺手加回
    顶栏"。它们回到顶栏的那一天，手机上那 4 个控件（其中一个在未登录时还是个空白
    框）就又回来了，而这正是本次要解决的问题。
    """
    html = _html()
    top = re.search(r'<header class="topbar"[\s\S]*?</header>', html)
    assert top, "顶栏不在了"
    block = top.group(0)
    assert 'id="openSidebar"' in block and 'id="topTitle"' in block
    for gone in ("modelSel", "personaChip", "exportBtn", '<select', 'class="chip"'):
        assert gone not in block, f"{gone} 又回到顶栏了"
    assert re.search(r'\$\("topTitle"\)\.textContent', _js()), "标题没人更新：它会一直写着「新对话」"


def test_boot_covers_the_shell_before_it_asks_the_server():
    """冷启动的第一屏只能是"正在确认身份"，不能是聊天外壳。

    原先的顺序是 `await loadWho()` → 拿回 401 → showAuth，中间那 0.5~2 秒（走隧道）
    用户看到一个空聊天界面加一个空白模型框——手机上 1→3→2 那个顺序就是它。
    上一轮只挡了"本机没令牌"那一路，**存过令牌的人仍然先看外壳**，所以这次把
    盖外壳这件事与有没有令牌脱钩：先盖一层中性状态，再按结果决定露出什么。
    """
    js = _js()
    body = _function_body(js, "boot")
    assert re.search(r"showAuthPending\(\);", body), "没有先盖中性层这一步"
    assert body.index("showAuthPending()") < body.index("await loadWho()"), \
        "盖层又排到 await 后面了：闪一下会原样回来"
    # 三条出路都得把中性层摘掉，否则"正在确认身份…"会变成第二块砖
    tail = body[body.index("await loadWho()"):]
    assert "hideAuth()" in tail and "showAuth(" in tail, "确认完没人收这层或没人换成表单"
    assert "clearAuthPending()" in _function_body(js, "showAuth"), \
        "showAuth 不清中性层：露出表单时那句「正在确认身份…」会一起挂着"
    assert "clearAuthPending()" in _function_body(js, "hideAuth"), \
        "hideAuth 不清中性层：下次再弹这层会直接停在「正在确认身份…」"


def test_a_backend_that_is_down_does_not_pretend_you_are_logged_out():
    """连不上后端时要露出外壳 + 那句人话，而不是把人锁在登录层或"确认中"里。

    这条不变量从上一轮就有（弹一个只会失败的登录框等于把"服务没起来"伪装成
    "你没登录"）。这次多了一个新的失败形状：中性层不摘掉，用户会永远盯着
    「正在确认身份…」——所以它得跟"登录层不许弹"一起钉住。
    """
    js = _js()
    body = _function_body(js, "boot")
    guard = re.search(r"else if \(unreachable\) (\w+)\(\);", body)
    assert guard, "后端连不上那一路没有分支处理"
    assert guard.group(1) == "hideAuth", "连不上时没收中性层，收的是：" + guard.group(1)
    assert "needsAuth(e)" in body, "401 仍然交给 needsAuth 弹层，没被这条分支吞掉"


def test_success_erases_the_stale_red_line():
    """状态条全仓 20 多处只在出事时写字，没有任何成功路径负责擦——于是注册成功、
    令牌落地、模型也拿到了，屏幕上仍然挂着三分钟前那句红的「还没有登录…」。
    实测（本机隔离实例）：boot 无令牌 → 红；afterAuth 成功 → 那句红一字未动。

    修法刻意收成两个必经点，而不是在每个成功处补一句 setStatus("")：
    后者会变成"谁想起来谁擦"，漏一处就是同一个 bug。冷启动、注册、登录、
    以后切换账户全都必须过 loadWho 与 loadModels，所以擦这两处就够。
    """
    js = _js()
    who = _function_body(js, "loadWho")
    assert re.search(r'state\.me = await API\.me\(\);\s*setStatus\(""\);', who), \
        "认出人之后没人擦那句红的"
    models = _function_body(js, "loadModels")
    assert re.search(r'\}\s*else\s*\{\s*setStatus\(""\);', models), \
        "拿到可用模型之后没人擦那句红的"
    # 反向：真没模型时那句实话必须还在。两分支按角色分流：管理员配共享，
    # 普通用户现在也能在同一页用自己的 API Key 添加"我的模型"（或联系管理员）。
    assert "联系管理员" in models and "设置 → 模型服务" in models and "API Key" in models, \
        "把实话一起擦掉了：零模型时用户该看见一句真话"


def test_the_pending_cover_exists_and_hides_the_form():
    """中性层要有自己的 DOM 与样式，且它盖住的是表单不是整层（品牌行得留着）。"""
    html = _html()
    css = _css()
    assert 'id="authPending"' in html, "没有 #authPending 这一句"
    # 收起来的是表单容器：.auth-grid 在删掉登录说明卡那一轮就没了，
    # authForm 与 recoverForm 现在共用 .auth-form，所以钉这个才是真的那两层。
    assert re.search(r"\.auth\.pending \.auth-form\s*\{[^}]*display:\s*none", css), \
        "pending 态没把表单收起来"
    assert re.search(r"\.auth-pending\s*\{[^}]*display:\s*none", css), "中性层默认就该不显示"
    assert re.search(r"\.auth\.pending \.auth-pending\s*\{[^}]*display:\s*block", css), \
        "pending 态没把那句「正在确认身份…」亮出来"


def test_model_choice_and_context_window_are_reachable_by_everyone():
    """「当前模型」与「上下文长度」两行必须对普通用户开着。

    模型服务那一页是管理员专属（后端 7 条路由都要管理员）。这两样一旦落在它里面，
    普通用户就没有"换模型"和"少带几条历史"的能力了——而且**不会报错**，只会以为
    这里没这个东西。上一版它们分别在「当前会话」页和管理员页的 .params 里，
    后者正是这个 bug 的形状。
    """
    html = _html()

    def row_of(el_id):
        """取包住这个 id 的那一行。id 可能写在 <button> 标签自己身上（exportBtn），
        也可能在行体里（modelSel），所以按位置回溯到最近的 set-row 开标签。"""
        at = html.find('id="%s"' % el_id)
        assert at > 0, f"设置列表里找不到 {el_id}"
        start = max(html.rfind('<button class="set-row', 0, at), html.rfind('<div class="set-row', 0, at))
        assert start > 0, f"{el_id} 不在任何一行 set-row 里"
        close = "</button>" if html[start] == "b" else "</div>"
        end = html.find(close, at)
        assert end > 0
        return html[start:end + len(close)]

    for el_id, what in (("modelSel", "换模型"), ("ctxRange", "改上下文条数"), ("exportBtn", "导出对话")):
        assert "data-admin-only" not in row_of(el_id), \
            f"{el_id} 那一行标了 data-admin-only：普通用户没有{what}的能力，而且不会报错"
    prov = html[html.index('data-page="providers"'):html.index('data-page="accounts"')]
    assert 'id="ctxRange"' not in prov and 'id="tokenInput"' in prov, \
        "滑杆还留在管理员那一页里，或手工填令牌没跟着收进同一页"



def test_the_identity_list_is_the_only_source_of_credentials():
    """方案 C：凭据根本不进 JS——清单只记"谁"，会话住在 httpOnly Cookie 里。

    这条锁原先钉的是"清单是凭据的唯一出处"（api.js 绕过 pref 读老键 = 第二个事实
    来源，见 [[ai-assistant-memory-cross-user-leak]]）。Cookie 化之后问题的形状变了：
    清单里根本不许有凭据，api.js 也不许再拼 Authorization——"界面是 B 请求头是 A"
    那种漂移在浏览器自动附带 Cookie 的世界里，唯一的防法就是让 JS 从头到尾没有
    明文可拼。这里钉的是明文不许回流：
    - api.js 不读任何令牌来源（pref.token、老键），显式声明凭 Cookie 走；
    - 明文的唯一消费口是 adopt，且必须发生在写进清单**之前**；
    - 老键 accessToken 只剩迁移那一处的宿命：读一次、无条件扫清；
    - 升级函数把清单里残留的 token 字段删干净。
    """
    js, api = _js(), _js("api.js")
    assert 'localStorage.getItem("accessToken")' not in api, \
        "api.js 又去读老键了：那是 JS 可见明文的入口"
    assert "pref.token" not in api, "api.js 又从清单取凭据：明文回到了 JS 手里"
    assert "credentials: \"same-origin\"" in api, "api.js 没显式声明请求靠同源 Cookie 走"
    assert "X-CSRF" in api, "api.js 不安全方法没带 CSRF 声明头"
    assert "function adopt(" in api, "凭据收编口没了：登录回来的明文无处安放"
    # 老键只能出现在迁移那一处（读一次 + 清单一处删名）
    assert js.count('"accessToken"') <= 2, "accessToken 这个键名出现在两处以上：迁移没做完"
    assert "function migrateLegacyIdentity()" in js, "没有一次性的老键迁移"
    body = _function_body(js, "migrateLegacyIdentity")
    for gone in ("accessToken", "userId", "sessionId"):
        assert gone in body, f"迁移没处理老键 {gone}"
    assert "token: res.token" not in js, "清单条目又要把明文写回去了"
    ab = _function_body(js, "afterAuth")
    assert "API.adopt(res.token)" in ab, "登录成功没把明文收编成 Cookie"
    assert ab.index("API.adopt") < ab.index("addIdentity"), \
        "明文先落了清单才收编：中间躺着的那段时间就是泄露窗口"
    up = _function_body(js, "upgradeIdentitiesToCookie")
    assert "delete x.token" in up, "升级没把老清单里的明文洗掉"


def test_every_api_method_the_pages_call_is_actually_exported():
    """页面脚本里出现过的每个 API.<name>，必须真的在 api.js 的导出对象里。

    断的那侧（调用）一直有锁，导出这侧没人看——adopt 就是"定义写了、导出漏了"：
    文件照常加载、别的按钮照常好用，只有注册/登录的成功路径在手机上炸出
    "API.adopt is not a function"（2026-09-23 v0.19 首发实测）。这正是本仓最怕的
    "线看起来接完了其实没接"那一类，所以断的是两侧集合的差，不是某个名字。
    """
    api_src = _js("api.js")
    block = re.search(r"return \{(.*?)\n  \};", api_src, re.S)
    assert block, "api.js 的导出对象形状变了，这条锁要跟着改"
    exported = set(re.findall(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*[:(,]", block.group(1), re.M))
    used = set(re.findall(r"\bAPI\.([A-Za-z][A-Za-z0-9]*)",
                          _js("app.js", "shell.js", "layers.js", "markdown.js")))
    missing = used - exported
    assert not missing, (
        f"这些 API 方法被页面调用却没被导出，手机上是运行期 'not a function'：{sorted(missing)}")



def test_the_current_identity_always_resolves_to_someone():
    """currentId 缺失或指向已经不在清单里的人时必须有兜底，且添加即设为当前。

    没有兜底会出现「记着 5 个人但开 app 说没登录」，而那条路有两种实现。
    """
    js = _js()
    cur = _function_body(js, "currentEntry")
    assert "CURRENT_KEY" in cur and "addedAt" in cur, "兜底没取最新那一条"
    assert "setCurrent" in _function_body(js, "addIdentity"), "注册/登录成功之后没把他设为当前身份"
    assert "IDENTITY_CAP = 5" in js, "清单上限不是 5：超出后顶掉谁没有依据"


def test_switching_clears_the_view_before_it_refills_it():
    """方案 C 的切换 = 预填登录面；"换指针→清屏→重取"的旧静默换人已死。

    旧锁钉的是切换三步的顺序——那是清单握着明文、能静默换 Cookie 年代的契约。
    现在 JS 没有其他人的凭据，会话还是 A 的时候把 currentId 指向 B，B 的偏好
    （lastSessionId/provider）就会被写进一次 A 的操作里——跨用户写就是当年那笔
    账的形状。所以这里反过来钉：switchTo 不许碰指针、不许自取身份，只许把
    登录表单递到人面前；真换人之后的清屏仍由 afterAuth 负责。
    """
    js = _js()
    sw = _function_body(js, "switchTo")
    assert "setCurrent(" not in sw, "switchTo 还抢跑换指针：会话是 A 的，偏好却写进 B 的清单"
    assert "loadWho()" not in sw, "switchTo 还想静默自取身份：它手里已经没有能换会话的东西"
    assert 'showAuth("login")' in sw, "切换没把人引到登录表单"
    assert '$("authUser").value' in sw, "切换没预填他的名字"
    assert "controller.abort()" in sw, "切走时没掐断正在输出的回答"
    # 注册/登录成功才是真换人：从设置里添加第二个账户时，屏幕上正挂着第一个人的对话。
    # 只靠 restore() 那句"没有指针就清空"兜是运气，这里要它显式清。
    assert "resetViewForIdentity()" in _function_body(js, "afterAuth"), \
        "afterAuth 换人不清屏"
    body = _function_body(js, "resetViewForIdentity")
    for gone in ("state.messages", "state.sessions", "state.pending", "state.memoryQuery",
                 "memoryList", "personaInput", "$(\"input\")"):
        assert gone in body, f"清屏漏了这一项：{gone}"
    # 反向：清屏清的是**视图**，不许顺手擦掉每个人自己"刚才在哪条会话"的指针。
    # 我第一版把它列进了上面那张名单，实测切回上一个人时 messages=0——对话还在服务端，
    # 只是再也没人记得回去。指针归各自那条记录所有，换人时读的就是新那个人自己的。
    assert "lastSessionId" not in body, "清屏擦了上一个人的会话指针：切回来会找不到刚才聊的"


def test_restore_no_longer_relies_on_a_404_to_lose_the_previous_person():
    """原先「切到人没有会话时清掉旧对话」是靠服务端回 404 挡的，那是运气不是设计。

    restore() 第一句是 if (!pref.sessionId) return —— 指针为空时旧消息整段留在
    屏幕上。清屏现在由 resetViewForIdentity 负责，这里钉它不再 return-而不作为。
    """
    body = _function_body(_js(), "restore")
    guard = re.search(r"if \(!pref\.sessionId\)[\s\S]{0,80}?\}", body)
    assert guard and "state.messages" in guard.group(0), \
        "sessionId 为空时 restore 只是 return：上一个人的对话会留在屏上"


def test_removing_an_identity_revokes_it_first():
    """从清单里移除一个人 = 让服务端作废他那一枚，顺序不能反。

    先删本机再撤销：撤销失败时本机条目已经找不回来，而那枚令牌在服务端还活着——
    "看起来删掉了但其实还能用"，比没删更糟。
    """
    js = _js()
    body = _function_body(js, "dropIdentity")
    assert "logout" in body and "catch" in body, "移除没有先撤销，或撤销失败时没有任何交代"
    assert body.index("logout") < body.index("saveIdentities"), "先删了本机条目才去撤销"


def test_logout_wrapper_sends_the_token_it_is_revoking():
    """撤销清单里另一个人时，请求头带的必须是**他那枚**令牌。

    走 authHeaders() 的默认路径只能撤销当前身份；先把他切成当前再退出，等于为了
    删除而把那个人的会话加载到共用设备的屏幕上。
    """
    api = _js("api.js")
    body = re.search(r"async function logout[\s\S]*?\n  \}", api)
    assert body and "Authorization" in body.group(0) and "token" in body.group(0), \
        "logout 没有接受显式令牌"


def test_the_settings_list_has_no_teaching_copy():
    """界面不教人怎么用：设置区不该再有写死的说明长文，也不该有假控件。

    反向断言钉的是**文案**，不是 `.pane-note` 这个 class——`#provTestResult`、
    `#registerHint`、`#whoInfo` 这些状态槽还在用它，按 class 钉会误伤。
    温度（`temperature=0.7` 在后端写死，滑杆只改 localStorage 和回显）连同它的
    单位字符串一起不许回来：一个不生效的控件比没有控件更坏。
    """
    html, js, css = _html(), _js(), _css()
    sheet = html[html.index('<div class="modal hidden" id="settings"'):html.index('<template id="msgTpl">')]
    for gone in ("用户名 + 密码自己设", "记忆存在后端向量库里", "角色设定会作为系统提示词",
                 "这一段对话用哪个模型", "界面里的模型下拉", "已有令牌（本机直跑服务",
                 "可添加到主屏幕", "凭据与找回是怎么工作的"):
        assert gone not in sheet, f"设置区里又出现说明文了：{gone}"
    assert "支持 Markdown 与代码高亮" not in js, "空状态那段教学文案回来了"
    for gone in ("温度", "temperature", "tempRange", "tempVal"):
        assert gone not in sheet, f"{gone} 回到了界面"
    # 后端 main.py 那个 temperature=0.7 还在：删掉滑杆删的是"能改"这个承诺，
    # 不是那份偏好数据。哪天真要透传，pref 上那个 getter 就是现成的落点。
    assert "get temperature()" in js, "pref.temperature 被一起删了：那是另一件事，别顺手"
    row = re.search(r"\.set-row\s*\{[^}]*\}", css)
    assert row and re.search(r"min-height:\s*(4[4-9]|[5-9][0-9])px", row.group(0)), \
        ".set-row 热区不到 44px：手机上就是「设置不好点」的根因"
    assert sheet.count("<svg") >= 6, "行图标不是内联 SVG：emoji 字形会渲染成彩色或方块"
    for glyph in "⚙🔧🌐📄🧠":
        assert glyph not in sheet and glyph not in css, f"用了 {glyph} 字形"


def test_the_sidebar_foot_is_one_row_that_opens_settings():
    """侧栏底部那一行同时是"我是谁"和"进设置"，两件事不必再占两个位置。

    原先"设置"是导航里单独一条（#navSettings），身份只在弹层里看得见；而
    "这台机器上是谁在用"恰恰是最常被问的事。合成一行之后要钉住四件事：

    1. 齿轮是内联 SVG。`⚙` 在部分字体里渲染成彩色 emoji、在部分里是方块。
    2. 整行是个 button 且真的开设置、收侧栏（手机上侧栏是抽屉，不收就是一片遮罩）。
    3. 头像取用户名首字母，未登录时不能留空格子。
    4. 「◐ 主题」从这一行搬走了，它得还在设置弹层里，别搬丢了。
    """
    html = _html()
    js = _js()
    row = re.search(r'<button class="who-row" id="whoRow"[\s\S]*?</button>', html)
    assert row, "侧栏底部没有 #whoRow 这一行"
    assert "<svg" in row.group(0), "齿轮没有自己的图标"
    assert "⚙" not in html and "⚙" not in js, "用了 ⚙ 字形：它会渲染成 emoji 或方块"
    assert html.find('id="sessionGroups"') < row.start(), "用户行没钉在会话列表之后"
    assert re.search(r'\$\("whoRow"\)\.onclick\s*=\s*\(\)\s*=>\s*\{\s*openSettings\(\);\s*closeSidebar\(\);', js), \
        "点击没有同时打开设置并收起侧栏"
    assert '$("userAvatar").textContent = name ? name[0]' in js, "头像没取首字母"
    # 「关于」那一整组，不是"到第一个 </div> 为止"：老写法用非贪婪匹配，
    # 组里第一行换成一个 <div>（版本）之后它就只截到那一行，把 themeBtn 落在外面，
    # 报了一条与主题毫无关系的假红。按"到下一组或最后一张卡片为止"切才对。
    MARK = '<p class="set-group">关于</p>'
    tail = html[html.index(MARK):]
    stops = [i for i in (tail.find('<p class="set-group">', len(MARK)),
                         tail.find("set-card set-last")) if i > 0]
    about = tail[:min(stops)] if stops else tail
    assert 'id="themeBtn"' in about and "外观" in about, \
        "主题入口从侧栏搬走之后没落到设置一级列表里，或它已经不改名成「外观」了"
    # 手机上"设置不好点"与"齿轮旁边那个点是什么鬼"两件事的判据：整行要有 44px 的
    # 可点高度，并且要**写出"设置"两个字**——一个没有文字的齿轮在手机上读起来像装饰。
    css = _css()
    foot = re.search(r"\.who-row\s*\{[^}]*\}", css)
    assert foot and re.search(r"min-height:\s*(4[4-9]|[5-9][0-9])px", foot.group(0)), \
        ".who-row 的点击高度不到 44px"
    assert "设置" in row.group(0), "齿轮旁边没有「设置」二字：整行可点这件事没人看得出来"




def test_auth_layer_is_one_column_with_no_dead_rules():
    """说明卡整块删掉之后，这一层就是单列居中；被删的东西不许在样式表里留尸。

    留着的那几条不是样式，是"下一次改版不知道哪套还在用"的起点。就地错误
    （.field-err）是这次新加的信道，所以它得真有一条规则，不能靠默认颜色。
    """
    css = _css()
    assert re.search(r"\.auth-inner \{[^}]*max-width: 420px", css), "登录层不是单列居中"
    assert ".field-err" in css and "--danger" in css, "就地红字没有自己的规则"
    for dead in (".auth-grid", ".auth-side", ".auth-host", ".auth-tabs", ".auth-tab",
                 ".conn-text", ".sb-config",
                 # 本次换形状留下的两处：一步式注册多出来的那一整块，以及"把题面念给
                 # 人听"那个只读框——readonly 的元素在整个前端已经一个都不剩了
                 ".auth-extra", "input[readonly]"):
        assert dead not in css, f"{dead} 还留在样式表里"




def test_the_password_eye_reveals_only_that_field():
    """眼睛按钮必须真的翻 type，而且只翻首屏这一格。

    翻错框等于把设置页的密码也亮出来；只翻 type 不改 value 才不会把已输入的
    密码清空——用户点一下眼睛是为了核对，不是为了重敲。
    """
    js = _js()
    body = _function_body(js, "toggleAuthPass")
    assert '$("authPass")' in body, f"眼睛动的是别的框：{body}"
    assert '"regPass"' not in body
    assert re.search(r'type\s*=\s*.*text.*password|text.*:.*"password"', body), \
        f"没有真的在 text/password 之间来回切：{body}"
    assert ".value" not in body, "切明文不该顺手清空已输入的密码"
    assert '$("authEye").onclick = toggleAuthPass' in js


def test_the_first_step_of_both_flows_asks_nothing_of_the_server():
    """第一步的「下一步」只做本地校验：答案还没填，发出去必然 422。

    注册第一步收的是用户名、密码、确认密码；找回第一步收的是用户名。两步之后
    才有资格谈请求，而 `/v1/auth/recovery` 这个端点**已经不存在了**——旧代码在这
    一步打的是"问服务器要问题"那一枪，如今三题是常量、前端自己渲染，那条信道整个
    没了。所以这里断的是"函数体里一个 API. 都不许出现"，而不是"别打错端点"。

    闸门那条断的是**字面短路形状** `{ goNext(); return; }`，不是"调用点排得早"：
    后者对一条已经写在函数开头的 `regNext(); return;` 来说恒成立，把 `return;` 删掉
    之后 395 条测试全绿（评审实测），而函数于是会继续往下走到发请求。可达后果不是
    理论值：第二步填过答案后点「上一步」（答案仍在框里）再点「下一步」，本地那道
    "三道答案都非空"的闸门就失效了，真的会发出 POST /v1/auth/register。

    修复轮 2 再钉**位置**：形状对了不代表闸门在进门的第一件事上。整段挪到本地校验之后
    （字面仍是 `{ regNext(); return; }`）时 37 条全绿，可 `$("authGo").disabled = true`
    已经执行完了才 return——按钮从此点不动，第一步再也进不了第二步，比原来那条更狠。
    """
    js = _js()
    for name, required in (("regNext", ("authUser", "authPass", "authPass2")),
                           ("rcNext", ("rcUser",))):
        body = _function_body(js, name)
        assert "API." not in body and "fetch(" not in body, \
            f"{name} 在第一步就发了请求：答案还没填，那一枪必然 422"
        for el in required:
            assert f'$("{el}")' in body, f"{name} 没校验 #{el}"

    # 确认密码只在前端判：后端多一个 confirm_password 字段只是把客户端语义塞进契约
    reg = _function_body(js, "regNext")
    assert '$("authPass2").value' in reg and '$("authPass").value' in reg, \
        "两次密码的一致性不在第一步判，就会带着不一致去挨一次 422"

    # 步骤闸门写在两张表单唯一的提交函数开头（不另设一层包装：多一层就多一处能漏判的地方）
    for name, go, api_call, btn in (("submitAuth", "regNext", "API.register", "authGo"),
                                    ("submitRecovery", "rcNext", "API.resetPassword", "rcGo")):
        body = _function_body(js, name)
        gate = re.search(r"\{ %s\(\); return; \}" % re.escape(go), body)
        assert gate, (f"{name} 的第一步闸门不是短路形状（`{{ {go}(); return; }}`）："
                      f"{go}() 之后没有 return，第一步就会穿着闸门继续往下走到发请求")
        assert api_call in body, f"{name} 里找不到 {api_call}：第二步那一枪不发了吗"
        assert gate.start() < body.index(api_call), \
            f"{name} 的短路闸门晚于发请求：第一步那一枪照样打出去"
        # 形状钉住了，位置也得钉：整段挪到本地校验之后时上面三条照绿，可那时
        # $("authGo").disabled = true 已经执行完才 return（return 在 try 之前 → finally
        # 不执行，在途标志一起卡死），第一步再也进不了第二步（那种挪法 37 条全绿）。
        # 只认本仓库通篇在用的这一种写法（不带花括号的 `if (state.registering) return`）：
        # 写成 `if (state.registering) { return; }` 是等价行为、这条会红——那是让改的人顺手
        # 统一写法，不是本条要承诺的契约，别把它读成"任何挡双击的形状都在锁里"。
        entering = re.search(r"if \(state\.registering\) return;?", body)
        assert entering, f"{name} 的在途闸门（进门先挡）不见了"
        assert entering.end() < gate.start(), \
            f"{name} 的步骤闸门排在了在途闸门之前：先 return 的那一条不再是挡双击的那一条"
        # 为什么位置要紧：中间那些语句会先跑完。上一版把这个"紧跟"钉成 160 字符的窗口
        # （现值 136），于是加一行二十几个字的注释就能把它弄红，而红话说的还是"锁按钮会
        # 先跑完"——注释什么都不跑。改成钉形状：这两行之间只许出现无副作用的变量声明，
        # 注释先剥掉（解释"为什么这里不写语句"的注释恰恰最可能长）。
        head, _, gate_line = body[entering.end():gate.start()].rpartition("\n")
        # gate 匹配的是那个花括号，所以闸门自己那一行的 `if (…)` 也在这个片段里：它得单独判
        # ——条件里不许有分号或花括号，那条语句才真是"这一行的第一件事"。
        assert re.fullmatch(r"\s*(?:if \([^;{]*\)\s*)?", gate_line), \
            (f"{name} 的闸门那一行在 if 条件之前还挂着别的东西（{gate_line.strip()!r}）："
             "它不再是紧跟在途闸门之后的第一条语句")
        for line in _strip_js_comments(head).splitlines():
            code_line = line.strip()
            if not code_line:
                continue
            assert re.fullmatch(r"(const|let) [A-Za-z_$][\w$]* = [^;()]+;", code_line), \
                (f"{name} 的在途闸门与步骤闸门之间多了一条会做事的语句：「{code_line}」"
                 f"会在闸门 return 之前跑完，而 #{btn} 已经禁用、在途标志还没进 try，"
                 f"于是第一步再也点不动（后果见修复轮 2 的行为探针）")
        assert gate.start() < body.index(f'$("{btn}").disabled = true'), \
            f"{name} 的短路闸门晚于 #{btn} 禁用：第一步走到这里就再也点不动了"


def test_the_recovery_questions_are_the_same_three_sentences_on_both_sides():
    """**唯一的锁**：前端写死的那三条题面必须与后端常量逐字相同。

    三题现在是全站固定常量、由前端自己渲染，服务器不再"报出问题"。于是两边的文字
    第一次变成两个独立维护的副本：后端改了题面而前端没改，人答的就是另一套问题，
    找回永远对不上、而界面还会说"答案不正确"——没有任何一处会报错。这条测试是
    唯一能挡住"两边各改一版题面"的东西，所以它比对了原始字面量，不是个数。

    顺手钉第二件事（M3）：**题面**固定不等于**答案**固定。找回第二步那句说明一旦把
    答案也写成"全站固定的那三道"，就是在安全流程里把秘密描述成公开的——人会照着
    "反正大家一样"敷衍作答（"随便填一个"），而一个敷衍的答案等于没有找回通道。

    这第二件事终审实测过它的假绿：判据对的是 index.html 的**原始文本**，于是把那一句
    安全说明删掉、原地改写成 `<!-- … -->`，39 条全绿（注释里那句还一字不差地留着）。
    浏览器一个字都不渲染它，人也就读不到"答案是注册第二步留的那三句"这句劝告。
    HTML 语料现在一律走 _html()（先过 _strip_html_comments），那把新尺子自己的判据见
    test_the_html_comment_ruler_needs_its_own_test。
    """
    from app.core.auth import ANSWER_COUNT

    js = _js()
    html = _html()
    m = re.search(r"\bRECOVERY_QUESTIONS\s*=\s*\[([^\]]*)\]", js)
    assert m, "app.js 里读不到 RECOVERY_QUESTIONS 的数组字面量：题面大概被抄成了两份"
    front = re.findall(r'"([^"]+)"', m.group(1))
    assert front == list(RECOVERY_QUESTIONS), \
        f"前后端题面不一致：前端 {front} ≠ 后端 {list(RECOVERY_QUESTIONS)}"
    assert len(front) == ANSWER_COUNT, f"题数对不上：前端 {len(front)} 后端 {ANSWER_COUNT}"
    # 前端只能有一份文字副本：HTML 里再写一遍，改一处漏一处就回来了
    for question in RECOVERY_QUESTIONS:
        assert question not in html, f"#{question} 被抄进了 index.html：题面有了第二份事实来源"
        assert js.count(question) == 1, f"{question} 在 app.js 里出现了 {js.count(question)} 次"

    # M3：找回第二步那句说明把话说反过一次（"题目与答案都是全站固定的那三道"）。
    # 反向锁点名那句错话，正面锁钉住它得说清答案是注册第二步留的那三句（同一块
    # 上半句刚说过"答案只有你自己知道才对得上"）。
    assert "题目与答案都是全站固定" not in html, \
        "找回的说明又把答案说成公开的：人会照着「反正大家一样」敷衍作答"
    assert re.search(r"题目是全站固定的那三道[^<]*答案是注册第二步留的那三句", html), \
        "找回第二步没说明答案是哪来的：人会以为随便填一个就行"


def test_the_answer_boxes_follow_the_backends_question_count():
    """三条答案格的数量与顺序都得跟着后端那一条常量走。

    后端加一句问题，这里就是第一个红的地方——而不是注册满 3 个号之后有人发现
    第 4 格没地方填。顺序同样钉住：记录里按 RECOVERY_QUESTIONS 的次序存三枚摘要，
    格子的次序错了就等于把第一题的答案送去比对第三题。

    "id 存在于 HTML"远远不够：格子藏在带 hidden 的容器里，人照样填不满三格，
    submitAuth 那道"三格都非空"的本地闸门就把他按在第二步，永远走不到请求——用户
    后果与"少一格"完全相同，而 id 锁与 test_all_js_element_ids_exist_in_html 全绿
    （评审实测：给 regAns2 外面那层 <div class="auth-qa"> 加 hidden → 35 条全绿）。
    所以这里断的是**可达**：这一格在对应那个步骤容器内部，且它自己与它的祖先都没被
    hidden 挂住。容器自己默认是 hidden 的，那是 JS 按步摘掉的，于是另断一次"有人摘"。

    app.js 一律写死 `$("regAns1")` 这种整串字面量、不用 `$("regAns" + i)`：拼接会
    绕过 test_all_js_element_ids_exist_in_html 那把锁，引用到不存在的 id 时
    `$()` 返回 null，而 null.textContent 抛错发生在渲染函数里——整个首屏静默失灵。
    """
    js = _js()
    html = _html()
    defined = set(re.findall(r'id="([^"]+)"', html))
    for i in range(1, len(RECOVERY_QUESTIONS) + 1):
        for prefix in ("regAns", "rcAns", "regQ", "rcQ"):
            el = f"{prefix}{i}"
            assert el in defined, f"HTML 里没有 #{el}：第 {i} 题在界面上没地方填或没地方看"
            assert f'$("{el}")' in js, f"app.js 没引用 #{el}"
            container = ("regStep2" if prefix.startswith("reg") else "rcStep2")
            path = _open_tags_to(html, container, el)
            assert path, f"#{el} 不在 #{container} 内部：第 {i} 题在第二步铺不出来"
            blocked = [t for t in path[1:] if _tag_is_hidden(t)]
            assert not blocked, f"#{el} 这一格被 hidden 挡住了，谁也摘不掉：{blocked}"
    for container, renderer in (("regStep2", "renderRegister"), ("rcStep2", "renderRecovery")):
        body = _function_body(js, renderer)
        assert re.search(r'\$\("%s"\)\.classList\.toggle\("hidden",\s*!step2\)' % container, body), \
            f"#{container} 的 hidden 没人按步摘掉：整个第二步永远铺不出来"
    for name, prefix in (("registerAnswers", "regAns"), ("recoveryAnswers", "rcAns")):
        body = _function_body(js, name)
        for i in range(1, len(RECOVERY_QUESTIONS) + 1):
            assert f'$("{prefix}{i}")' in body, f"{name} 漏了第 {i} 格"
        assert "return [" in body, f"{name} 没把答案按顺序交出去"
        positions = [body.index(f'{prefix}{i}') for i in range(1, len(RECOVERY_QUESTIONS) + 1)]
        assert positions == sorted(positions), f"{name} 的格子顺序与题面顺序不一致"

    # 整行配对来比"第 i 格取的是第 i 题"。用 index() 找子串首次出现的位置是假锁：
    # 第二组标签一定会比到第一组那一行上去，什么都拦不住。
    labels = _function_body(js, "renderRecoveryQuestions")
    pairs = re.findall(r'\$\("(?:reg|rc)Q(\d)"\)\.textContent\s*=\s*RECOVERY_QUESTIONS\[(\d+)\]',
                       labels)
    assert len(pairs) == 2 * len(RECOVERY_QUESTIONS), \
        f"题面渲染配对数不对：{pairs}（应当是注册与找回各 {len(RECOVERY_QUESTIONS)} 道）"
    for slot, index in pairs:
        assert int(index) == int(slot) - 1, f"第 {slot} 道标签取的是第 {int(index) + 1} 题"


def test_the_deleted_recovery_endpoint_is_gone_from_the_frontend_too():
    """**反向锁**：`/v1/auth/recovery` 这条信道在前端一个字都不许留。

    上一轮这条测试断的是"前端还在调 API.recovery"，端点被删掉之后它就变成一条
    过时的绿——留着等于给一个不存在的路由背书。现在断的是反方向：封装、调用点、
    路径字面量全都不能出现。
    """
    js = _js(*JS_FILES)
    html = _html()
    for gone in ("API.recovery", "/v1/auth/recovery", "recovery:"):
        assert gone not in js and gone not in html, f"{gone} 还在前端：那个端点已经删了"


def test_the_recovery_flow_collects_everything_before_it_asks_the_server():
    """「忘记密码」现在真的能改密，所以判据是"该发的那一枪按顺序发、不该发的不发"。

    四件事必须钉住：
    1. 三条答案与新密码**一次**提交。分开验答案 = 给外人一个"这个答案对不对"的
       oracle，免凭据端点上不该有这种东西；
    2. 新密码两格不一致时根本不该发请求（那是纯前端能判的事）；
    3. 改密成功之后回到登录而不是直接放人进去——服务端此刻已经把这个人名下
       所有令牌作废了，界面若继续"已登录"就是在撒谎；
    4. 那句提示必须说出"其他设备需要重新登录一次"。这是令牌全清的**设计后果**，
       藏起来只会让人以为别的设备坏了。
    """
    js = _js()
    body = _function_body(js, "submitRecovery")
    assert "API.recovery" not in body, "找回第一步不该再问服务器要问题：那个端点已删"
    assert "API.resetPassword" in body
    assert "recoveryAnswers()" in body and body.index("recoveryAnswers()") \
        < body.index("API.resetPassword"), "三条答案没在发请求之前收齐"
    assert body.index('$("rcNew2").value') < body.index("API.resetPassword"), \
        "确认密码没在发请求之前比对"
    assert "showAuth(\"login\")" in body, "改密成功要回到登录：令牌已全部作废，不能装作还登录着"
    # 锚点是那句**调用**，不是裸词 "showAuth"：拿词当锚时，app.js 里任何一句提到 showAuth
    # 的注释挪到发请求之前都会把这条弄红，而红话说的是不相干的"切换该在改密之后"。
    assert "API.resetPassword" in body.split('showAuth("login")')[0], "登录视图的切换该在改密之后"
    assert "其他设备需要重新登录一次" in body, "改密的连带后果没告诉人"
    assert re.search(r"if \(state\.registering\) return", body), "找回没有在途闸门"
    unlock = body[body.index("finally"):]
    assert '$("rcGo").disabled = false' in unlock and "state.registering = false" in unlock, \
        "找回的解锁不在 finally 里：失败一次就再也点不动了"
    assert '$("authForgot").onclick = () => showAuthView("recover")' in js
    assert '$("recoverForm").onsubmit' in js, "找回表单没有提交入口：流程走不动"




def test_memory_calls_no_longer_send_user_id():
    api = _js("api.js")
    assert "user_id" not in api, "记忆接口已不接受客户端身份"


# 浏览器这一侧发出去的三格：register 的三条答案与 reset 的三条答案是同一个东西，
# new_answers（轮换答案）是可选的，界面不提供，所以它压根不该出现在前端。
FRONTEND_WRAPPERS = {
    "register": (["username", "password", "security_answers"], "/v1/auth/register"),
    "resetPassword": (["username", "answers", "new_password"], "/v1/auth/reset"),
}


def test_register_and_me_wrappers_match_the_backend_contract(client, enforced):
    """前后端字段名对不上是静默失败：后端 422，界面只说"注册失败"。

    请求形状与 app.js 读的那几个响应键一起断，且响应是真的从 /v1/auth/register
    拿的，不是照抄一份字典——改名（token→access_token 这种）当天就该红。

    找回不再有"问服务器要问题"那一步：三题是常量，服务器上也没有那个端点了。
    `new_answers` 只在前端缺席，所以这条测试顺手真发一次三格的 reset：可选字段
    是不是真的可选，得由后端说了算，而不是由前端少写一格"看起来也能过"。
    """
    from app.core.auth_router import RegisterRequest, ResetRequest

    api = _js("api.js")
    js = _js()

    assert set(RegisterRequest.model_fields) == {"username", "password", "security_answers"}
    assert set(ResetRequest.model_fields) == {"username", "answers", "new_password",
                                              "new_answers"}
    for name, (params, path) in FRONTEND_WRAPPERS.items():
        m = re.search(r"\b%s:\s*\(([^)]*)\)\s*=>" % name, api)
        assert m, f"api.js 里没有 {name} 封装"
        assert [p.strip() for p in m.group(1).split(",")] == params, \
            f"{name} 的参数名与后端请求模型不一致"
        assert f'request("{path}"' in api, f"{name} 没打向 {path}"
        calls = _call_args(js, name)
        assert calls, f"app.js 里没有 API.{name}() 调用点"
        for args in calls:
            assert len(args) == len(params), \
                f"API.{name} 只收 {len(params)} 个参数（{params}），实参却是 {args}"
    assert re.search(r'\bme:\s*\(\)\s*=>\s*request\("/v1/auth/me"\)', api), \
        "前端没有 me 封装：角色就只能靠猜"

    enforced("发码的人")
    answers = ["新市场小学", "hehai2024", "李建国"]
    res = client.post("/v1/auth/register",
                      json={"username": "字段名契约", "password": "correct-horse-battery",
                            "security_answers": answers})
    assert res.status_code == 200, res.text
    body = res.json()
    for key in ("token", "user_id", "username"):
        assert key in body, f"后端没回 {key}：{sorted(body)}"

    reset = client.post("/v1/auth/reset",
                        json={"username": "字段名契约", "answers": answers,
                              "new_password": "a-brand-new-horse"})
    assert reset.status_code == 200, f"前端那三格形状后端不认：{reset.text}"
    assert reset.json() == {"status": "password_reset"}



def test_quick_model_switch_chip_is_wired_end_to_end():
    """发送框旁的快速切换模型芯片：HTML 有位置、默认收起、点击走 Layers、
    选择即写服务端「我的默认」——四段缺一半，功能就是假的。

    这条锁钉的是跨文件契约（HTML 结构 × app.js 渲染 × API 封装），单看任何
    一个文件都自洽，拼起来才成立，正是最容易在重构中被悄悄拆散的形状。
    """
    html = _html()
    js = _js()
    api = _js("api.js")

    # 1) 芯片在输入脚、发送键之前，且默认 hidden（清单没回来前不许闪空芯片）
    foot = html[html.index('class="input-foot"'):html.index("</form>")]
    assert 'id="modelChip"' in foot, "芯片没放进输入脚"
    assert foot.index('id="modelChip"') < foot.index('id="sendBtn"'), \
        "芯片排到了发送键后面（与参考设计相反）"
    chip_tag = re.search(r'<button[^>]*id="modelChip"[^>]*>', html).group(0)
    assert "hidden" in re.search(r'class="([^"]*)"', chip_tag).group(1), \
        "芯片默认可见：冷启动会闪一个没有名字的胶囊"
    assert 'id="modelMenu"' in html, "弹单容器没进 HTML"

    # 2) 渲染与切换：芯片随 renderModelSelect 一起刷，选择走服务端偏好
    sel_body = _function_body(js, "renderModelSelect")
    assert "renderModelChip()" in sel_body, "设置页下拉/清单刷新没带着芯片一起更新"
    chip_body = _function_body(js, "renderModelChip")
    assert "usable" in chip_body and "currentProvider()" in chip_body, \
        "芯片没有按可用清单与当前口径渲染"
    switch_body = _function_body(js, "switchModel")
    assert "setMyDefaultProvider" in switch_body and "pref.provider" in switch_body, \
        "切换没同步服务端「我的默认」或没落本机"
    assert 'Layers.open("modelMenu"' in _function_body(js, "setModelMenu"), \
        "弹单没走让位层栈（返回键会对不上界面）"

    # 3) API 封装存在（前端调的名字必须真在 api.js 里）
    assert "setMyDefaultProvider" in api


def test_admin_only_surfaces_are_marked_in_html_and_swept_by_role():
    """管理员专属控件靠一条属性（data-admin-only）+ JS 里一处统一收口，而不是散落的 if。

    v0.21 起「模型服务」入口和页体对所有人开放（普通用户在那里配"我的模型"），
    不再属于管理员专属；这一页里唯一还收着的特权入口是"添加共享模型服务"那颗
    按钮。契约随之改向：addSharedBtn 必须带属性，providers 三件套必须**不**带——
    谁把它们重新标记回去，就等于把普通用户的入口又锁进 403。
    """
    html = _html()
    js = _js()
    marked = _ids_with_attr(html, "data-admin-only")
    assert "addSharedBtn" in marked, \
        f"「添加共享模型服务」没标成管理员专属：{sorted(marked)}"
    assert not ({"navProviders", "rowProviders", "paneProviders"} & marked), \
        f"模型服务入口又被锁回管理员专属了：{sorted(marked)}"
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
    html = _html()
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
    js = _js()
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
    api = _js("api.js")
    m = re.search(r"MEMORY_TOP_K_MAX\s*=\s*(\d+)", api)
    assert m, "api.js 未声明 MEMORY_TOP_K_MAX：上限散落在各调用点，迟早和后端对不上"
    assert int(m.group(1)) == cap, f"前端上限 {m.group(1)} ≠ 后端 le={cap}"
    assert re.search(r"Math\.min\s*\([^)]*MEMORY_TOP_K_MAX", api), "没有夹紧，超限照发"

    js = _js()
    calls = re.findall(r"API\.searchMemory\(([^)]*)\)", js)
    assert calls, "app.js 里的搜索调用点不见了"
    for args in calls:
        for n in re.findall(r"\b(\d+)\b", args):
            assert int(n) <= cap, f"app.js 又要 {n} 条，超过后端上限 {cap} 就是 422"


def test_memory_stats_is_queried_only_for_admins():
    """/v1/memory/stats 是管理员端点：普通用户那儿不能发这一枪。

    发出去的后果不是报错本身，而是那句"记忆服务不可用"——服务明明好着，只是他
    没权限，用户于是去重启后端，而重启完全治不了这件事。

    设置重画时关于页那张 dl 回显整个删了（当前模型在上一行就能改、温度压根不生效，
    回显等于把同一件事说两遍还捎带一个假数字），于是这一枪**根本没人发**。
    这条锁因此从"必须按角色分流"改成"前端不发"。谁要把它加回来，先回答：
    普通用户点开那一行会看见什么。
    """
    js = _js()
    assert "memoryStats" not in js, \
        "app.js 又有人打 /v1/memory/stats 了：这一枪要么按角色分流，要么别发"


def test_a_stale_token_does_not_read_like_a_first_run():
    """401 有两种，糊成一句就把人支使去填一个已经填对的框。

    本机压根没记过任何人 = 首启，该引导他注册；记着人却被服务端拒 = 管理员撤销
    或轮换过会话，再让他"重试刚才的口令"就是把人往废会话上反复按。
    （方案 C 起分叉判据是"清单里有没有人"，不是"有没有令牌"——JS 没有令牌可看。）
    """
    js = _js()
    body = _function_body(js, "needsAuth")
    assert "需要访问口令" not in js, "旧的合并文案还在，两种 401 仍是一句话"
    assert re.search(r"err\.status\s*[!=]==\s*401", body), "needsAuth 只该管 401：403 是身份够了、角色不够"
    assert "403" not in body
    assert re.search(r'currentEntry\(', body), "未按本机记不记得人分叉（方案 C：JS 没有令牌可看）"
    assert "失效" in body and "注册" in body, "两条分支的措辞都得在场"


def test_registration_locks_its_button_while_the_request_is_in_flight():
    """登录/注册没有在途闸门 = 手机双击发出第二个 POST。

    第二下拿回的是"该用户名已存在"（`auth.py` 里那句实话，措辞换过一次：从前写作"用户名
    已被占用"）或一次多余的 401：界面于是把一个已经成功的
    人标成红色失败，两次调用还一起抢会话落地（adopt→addIdentity）与
    loadWho→loadServerData→renderMessages 的顺序。约定跟 send() 守 state.streaming
    一模一样——进门先挡、解锁放在 finally（失败也必须解，否则一次网络抖动就把唯一
    的入口按死到刷新页面为止），并且凭据一落地就把它清出输入框。

    入口现在有两处（首屏弹层与设置页），所以两处各验一遍：多一条路就多一处能双击。
    """
    js = _js()
    assert re.search(r"streaming: false,\s*\n\s*registering: false", js), \
        "state 里没有了 registering：在途闸门大概退回了只靠 disabled 一处"

    for name, btn, api_call in (("submitAuth", "authGo", "API.register"),):
        body = _function_body(js, name)
        assert re.search(r"if \(state\.registering\) return", body), f"{name} 不再挡双击"
        assert body.index("if (state.registering) return") < body.index(api_call), \
            f"{name} 的闸门得在发请求之前"
        assert "state.registering = true" in body and f'$("{btn}").disabled = true' in body, \
            f"{name} 请求在途时按钮还亮着"
        unlock = body[body.index("finally"):]
        assert f'$("{btn}").disabled = false' in unlock and "state.registering = false" in unlock, \
            f"{name} 的解锁不在 finally 里：失败一次就再也点不动了"

    # 成功之后密码框不许留下内容。失败时故意留着——逼人重敲一遍密码只会把人赶去
    # 用 "12345678"，安全上是净损失。
    tail = _function_body(js, "afterAuth")
    assert '$("authPass").value = ""' in tail, "首屏那格的密码没人清"
    # 收尾动作的先后是硬约束：先清输入框再落库的话，中途抛异常就把唯一一次拿到
    # 令牌的机会连同输入一起丢了。（多身份之后落库走 addIdentity，不再是一个键。）
    assert tail.index("addIdentity(res)") < tail.index('$("authPass").value = ""'), \
        "清空必须晚于令牌落库：早一步就是在丢凭据"
    # 确认密码与三条找回答案同样是凭据（答案走的是同一个慢哈希），落地之后一并清掉
    assert '$("authPass2").value = ""' in tail, "确认密码那一格还留着明文"
    for i in range(1, len(RECOVERY_QUESTIONS) + 1):
        assert f'$("regAns{i}").value = ""' in tail, f"第 {i} 格答案没清出输入框"


def test_leaving_a_flow_wipes_every_credential_from_the_boxes():
    """离开流程时凭据不许留在隐藏框里——**这两条流程内**清格子只有一处，且在 setAuthMode 收口。

    原来只有 submitRecovery 的成功分支清那五格：猜错拿 401 之后人还留在这一层，
    三句找回答案与新密码就躺在 DOM 里（hidden 是"看不见"，不是"没内容"——手机上是
    切回去就还在，凑过来是能看见的），点「回去登录」或被 needsAuth 重新弹层时一个字
    都没清。注册同理：setAuthMode 从前只把 regStep 归零，留着上一次没提交出去的答案。

    为什么收口选 setAuthMode：showAuth（首启、needsAuth 在弹层还没起来时、设置里的「注册
    一个新账号」、改密成功回登录）与 authSwitch 换模式全都经过它，清一处就覆盖所有"进出
    这一层"的路径。而留在屏内重试不受影响——setAuthMode 不在登录失败的那条路上，密码故意
    留着（见上一条测试的注释），用户名更不该抹。

    "只有一处"的适用范围要说准：它指的是 submitAuth / submitRecovery 这两个函数体内不许
    再各自逐格清。afterAuth（app.js，注册与登录成功收起弹层）仍然逐格清，而且
    test_registration_locks_its_button_while_the_request_is_in_flight 正**要求**它那么清
    ——那里钉的是"清空必须晚于令牌落库"。把它并进 setAuthMode 收口会牵动那条锁，不在本
    轮清单里。
    """
    js = _js()
    fields = (["authPass", "authPass2"]
              + [f"regAns{i}" for i in range(1, len(RECOVERY_QUESTIONS) + 1)]
              + [f"rcAns{i}" for i in range(1, len(RECOVERY_QUESTIONS) + 1)]
              + ["rcNew", "rcNew2"])

    clearer = _strip_js_comments(_function_body(js, "clearAuthCredentials"))
    for el in fields:
        assert f'$("{el}").value = ""' in clearer, \
            f"离开流程时 #{el} 没人清：格子里还躺着明文"
    assert "authUser" not in clearer, "清格子不许顺手抹掉用户名：留在屏内重试的人得重敲"

    # 唯一的收口：进出这一层、换模式都经过 setAuthMode。同一把尺子先剥注释——
    # "删掉 clearAuthCredentials() 这行、原地留一句提到它的注释"骗得过 in 判断。
    closer = _strip_js_comments(_function_body(js, "setAuthMode"))
    assert re.search(r"\n\s*clearAuthCredentials\(\);", closer), \
        "清格子退回成功分支了：猜错 401 之后离开流程就漏"

    # 上面那条只钉了"收口里在清"，没钉"有人走进这个收口"。下面这两根线才是全部的连接线。
    # 把 authSwitch 改绑成一段不经 setAuthMode 的等价写法、或删掉 showAuth 里那句
    # setAuthMode，397 条原本全绿（上一轮实测）——格子于是只在没人走的那条路上被清过。
    # 本轮再实测一次：把那两处真调用删掉、**原地留一句含 `setAuthMode(...)` 的注释**，
    # 上面那把"在不在字符串里"的锁照绿 38 条——这根线可以用注释喂。所以两条都先剥注释、
    # 再钉语句形状；authSwitch 那条还要整段取处理器：`= (.*)` 只吃一行，处理器写成多行
    # arrow function（仍然经过 setAuthMode）会被红成"绕过了 setAuthMode"，红话说反了。
    show = _strip_js_comments(_function_body(js, "showAuth"))
    assert re.search(r"\n\s*setAuthMode\([^;]*\);", show), \
        ("showAuth 里没有一行**语句形式**的 setAuthMode 调用（写在注释里的不算）："
         "首启、needsAuth 弹层、设置里的「注册一个新账号」、改密成功回登录这四条路"
         "全都不清格子了")
    switch = _strip_js_comments(_handler_of(js, "authSwitch"))
    assert "setAuthMode(" in switch, \
        "authSwitch 换模式绕过了 setAuthMode：登录↔注册来回切一次，上一模式留下的明文还在格子里"

    # 「回去登录」必须走过这个收口，而不是只把两块表单的 class 换一换
    assert '$("rcBack").onclick = () => showAuth("login")' in js, \
        "rcBack 绕过了 setAuthMode：找回失败后离开流程，三句答案与新密码还在 DOM 里"

    # 流程内清格子只有一处。成功分支再单独清一次，就是"哪条路径忘了清"的下一次起点
    # （submitRecovery 的成功分支正是这么漏掉失败分支的）
    recovery = _function_body(js, "submitRecovery")
    for el in fields:
        assert f'$("{el}").value = ""' not in recovery, \
            f"#{el} 又在成功分支里单独清了：清凭据的地方变成了两处"


def test_a_background_401_while_the_layer_is_up_keeps_what_you_typed():
    """后台一条 401 不许把用户正在敲的密码抹掉（修复轮 2 的回归锁）。

    上一轮把清凭据收口到 setAuthMode 之后，needsAuth 那条"任何 401 都重走 showAuth"的
    路就顺带有了抹格子的权力：弹层已经开着、人正敲到一半时来一发后台 401（同步会话消息
    replaceMessages、建会话 ensureSession、载入旧会话 restore 那几类调用都会打到），敲的
    就没了。清凭据是"离开/进入这一层"的卫生动作，而这一层压根没被离开。
    判据：弹层不可见时行为不变（首启与令牌被撤销都要它挡住），已可见时**只**更新状态条
    那一句话——那句更新是无条件的，两种 401 的说法不该跟着弹层一起被挡在 if 里。

    本轮把上面那两句话各自钉住（修复轮 2 的措辞说过头了：它写着「setStatus 必须无条件」，
    实际只钉了次序）：
    - 无条件 = `setStatus(` 落在函数体顶层那一格缩进上，不是嵌在某个 if 里；次序那条保留。
    - 不重走 = 整个函数里 `showAuth(` 只许出现一次。上一版只钉被判断那一行在不在，于是
      「保留那一行、后面补一句 `else showAuth(authMode);`」是 38 条全绿的（评审实测），
      而回归恰恰就藏在 else 那一支里。
    """
    js = _js()
    # 判语料之前剥注释：这一层的注释提到 showAuth( 完全正常（函数上面那段就写着「showAuth
    # 那一路经过 setAuthMode」），钉出现次数会被它误判红；反过来它也能喂绿那条 in 判断。
    code = _strip_js_comments(_function_body(js, "needsAuth"))
    assert 'if ($("authModal").classList.contains("hidden")) showAuth(' in code, \
        ("needsAuth 在弹层已经开着的时候还重走 showAuth：那条路经过 setAuthMode → "
         "clearAuthCredentials，人正在敲的密码会被一条不相干的后台 401 抹掉")
    assert re.search(r"^  setStatus\(currentEntry\(", code, re.M), \
        ("setStatus 不在 needsAuth 的顶层：它被嵌进了某个 if 里，于是「弹层已经开着」那一种 "
         "401 连状态条那一句都不再更新——这里要的是只重弹不重说话")
    assert code.index("setStatus(") < code.index("showAuth("), \
        "状态条那两种 401 的说法得先更新，再决定要不要把弹层挡回来"
    # 被钉住的那一行只说「这一行在」，不说「没有第二条重弹的路」；次数锁补的就是这一格。
    for needle, allowed in (("showAuth(", 1), ("setAuthMode(", 0),
                            ("clearAuthCredentials(", 0)):
        assert code.count(needle) == allowed, \
            (f"needsAuth 里 {needle} 出现了 {code.count(needle)} 次（只许 {allowed} 次）："
             "重弹与清格子各只有被钉住的那一条路，另开一条本条回归就回来了")
    assert not re.search(r"\belse\b[\s\S]{0,60}showAuth\(", code), \
        ("needsAuth 里有个 else 分支去走 showAuth：那正是「弹层已经开着」那一种，"
         "人敲到一半的密码会被一条不相干的后台 401 抹掉")


def test_going_back_a_step_leaves_no_orphan_message():
    """「上一步」与成功提示这两处颜色/文案的错位（M1 + M2）。

    1. 第二步留下的红字（authFail 与撞名的 setUserError）会跟人回到第一步，而第一步
       上压根没有那些格子——人只知道"点了没反应"。regNext 进门先清，regBack 也得清。
    2. 改密成功那句是**好消息**，写在 #authHint 上，而 `.auth-hint.err` 是红色的
       （style.css）。上一次登录失败留下的 err 态不清掉，这句话就显示成错误色。
       顺序因此是硬约束：showAuth→setAuthMode 先复位提示与 err 态，成功文案后写。

    终审实测过这条的假绿：判据拿的是 regBack 那一段的**原始文本**，于是把
    `authFail(""); setUserError("");` 两处真调用删掉、原地留一句含同样字样的行注释，
    39 条全绿——而后果正是这条锁写下的那句"第二步的红字会跟到第一步"。语料现在一律走
    _js()（先剥注释），注释不再算一次调用；尺子自己的判据见
    test_the_comment_ruler_needs_its_own_test。
    """
    js = _js()
    back = re.search(r'\$\("regBack"\)\.onclick = \(\) => \{([\s\S]*?)\};', js)
    assert back, "「上一步」的接线找不到了"
    assert 'authFail("");' in back.group(1) and 'setUserError("");' in back.group(1), \
        f"上一步不清旧提示，第二步的红字会跟到第一步：{back.group(1)}"
    assert '$("authPass2").focus()' in back.group(1), \
        "焦点没落回第一步最后填过的那一格（确认密码），却对着注释说落回来了"

    mode = _function_body(js, "setAuthMode")
    assert 'authFail("");' in mode, "进这一层不复位提示：成功文案会沿用上一次的 err 色"
    assert 'setUserError("");' in mode, "进这一层不清用户名那一格的旧红字"

    recovery = _function_body(js, "submitRecovery")
    assert recovery.index('showAuth("login")') < recovery.index('$("authHint").textContent'), \
        "成功文案写在复位之前：那句好消息显示成红色"


def test_the_second_step_is_the_only_one_that_registers():
    """注册第二步才是真提交：三条答案收齐了才发，少一格是本地手滑、不该挨 422。

    后端 `security_answers` 是必填且条数必须正好等于题数（少一条存储层就抛），
    所以前端少读一格 = 每次注册都稳定失败，而界面只会说"注册失败：<指着题数那句>"。
    """
    js = _js()
    body = _function_body(js, "submitAuth")
    assert "registerAnswers()" in body and body.index("registerAnswers()") \
        < body.index("API.register"), "三条答案没在发请求之前收齐"
    assert "API.register(username, password, answers)" in body, \
        "注册那一枪没按三格发：后端会当成缺字段"
    # "regStep 出现得比 API.register 早"这条装饰性断言已删（M4）：它在任何合理实现下
    # 都成立，闸门真正的形状由 test_the_first_step_of_both_flows_asks_nothing_of_the_server
    # 那条字面短路锁钉住。
    # 到了第二步，用户名那一格仍然在场——撞名的红字要有地方落
    assert '$("authUser")' in body, "第二步不再读用户名"


def test_a_taken_username_still_lands_under_the_username_field():
    """重名是"改一下就好"的事，所以那句话写在用户名那一格下面，并聚焦过去。

    把它混进表单末尾的通用提示，两句话同屏时人会先去改密码；而注册入口现在分了两步，
    第二步的屏幕上只剩答案格，红字再挂到末尾就等于让人摸黑回头找那一格。
    """
    js = _js()
    body = _function_body(js, "submitAuth")
    assert re.search(r"if \(e\.status === 409\) \{[\s\S]{0,200}setUserError\(e\.message\)", body), \
        "撞名不再走 setUserError：那句话会被混进表单末尾的通用提示"
    setter = _function_body(js, "setUserError")
    assert '$("authUserErr")' in setter and '$("authUser").focus()' in setter, \
        "红字没写在用户名那一格下面，或没聚焦到那一格"
    # 第二步不许把用户名那一格藏起来，否则这条信道没有落点
    renderer = _function_body(js, "renderRegister")
    assert "authUser" not in renderer, "renderRegister 动了用户名那一格：撞名的红字会没地方看"


def test_a_brand_new_identity_is_not_blocked_on_choosing_a_model():
    """刚注册/刚登录的人 providerId 是空的，但不该因此发不出消息。

    2026-09-18 真机 UI 上撞实：新身份点发送，send() 因为 currentProvider() 返回
    null 直接 return，界面只留一句红字「当前没有可用模型，请联系管理员配置模型服务」
    ——而服务端好得很。模型清单是一次网络往返，在那之前它必然是空的。
    判据两条：挑不到就用服务端给的那个默认兜底；清单还没回来就先拉一次再判。
    """
    js = _js("app.js")
    pick = _function_body(js, "serverDefaultProvider")
    assert "state.serverDefault" in pick, "兜底那条没在用服务端报的默认"
    cur = _function_body(js, "currentProvider")
    assert "serverDefaultProvider" in cur, "currentProvider 还是「清单里没命中就返回 null」"
    assert "serverDefaultProvider" in _function_body(js, "loadModels"), \
        "loadModels 另写了一套挑选规则：两处口径迟早分家"
    assert "await loadModels()" in _function_body(js, "send"), \
        "清单还没回来时该先去拉一次，而不是直接拒发"


def test_the_default_model_choice_comes_from_the_server_only():
    """/v1/models 的 default 是"默认用哪个"的唯一答案，前端不许再自己挑一遍。

    两侧各挑一次就是那条漂移：服务端 default() 当时不看密钥可用性，前端那句
    `usable.find(p => p.default) || usable[0]` 看，于是界面显示 B、实际调用用 A。
    现在服务端只在可用的里面挑（见 test_providers 那三条），前端改成信它的返回值。
    """
    js = _js("app.js")
    assert "usable[0]" not in js, "前端还在自己按清单顺序兜底挑默认"
    assert not re.search(r"usable\.find\(\s*\(?\s*p\s*\)?\s*=>\s*p\.default", js), \
        "前端还在拿 catalog 的 ★ 标记自己挑默认"
    assert "function pickUsableProvider" not in js, "那套自挑的规则还留着，早晚被人接回去"
    load = _function_body(js, "loadModels")
    assert "data.default" in load, "没把 /v1/models 报的 default 存下来"
    assert ".usable" in _function_body(js, "serverDefaultProvider"), \
        "服务端说了不可用还照用：那条就是会被 resolve() 拒掉的配置"


def test_account_rows_offer_switch_and_delete_and_the_current_one_offers_nothing():
    """账户列表里非当前的每人两颗按钮：切换账号 / 删除账号；当前那一行一颗都不给。

    2026-09-18 真机反馈：原先非当前行只有一颗「退出」，人按字面理解成"从这台机器上
    退出这条记录"，实际做的却是撤销那个人的登录；当前行没有按钮但整行可点，误触即换人。
    """
    js = _js("app.js")
    body = _function_body(js, "renderAccounts")
    assert "切换账号" in body and "删除账号" in body, "两颗按钮没到位"
    assert "退出" not in body, "还留着那颗含义不明的「退出」"
    assert "row.onclick" not in body, "整行可点：误触就换人，按钮该明确到动作"
    assert body.index("x.userId !== here") < body.index("切换账号"), \
        "按钮没被「当前那一行不给按钮」的判断罩住"


def test_removing_an_identity_survives_a_token_the_server_already_rejects():
    """服务端明确不认这枚令牌（401/403）时，本机的条目必须照样删得掉。

    顺序仍然是先撤销再删；但账号已经被人从管理员侧删掉时，撤销请求会在鉴权中间件
    就 401，原先那版把它当失败、拒绝移除条目——于是清单里留下一条永远移不掉的幽灵
    （真机上就是那行 u_d65141d2「需要重新登录」）。网络错误与 5xx 仍要留着条目：
    那种情况下令牌可能还活着，"看起来删掉了但还能用"比没删更糟。
    """
    body = _function_body(_js("app.js"), "dropIdentity")
    assert "401" in body and "403" in body, "没按 HTTP 状态区分「已作废」与「没送到」"
    assert "e.status" in body, "判据落在状态码上，不许落到错误文案的字面"


def test_logging_out_falls_back_to_the_newest_identity_not_the_first_in_the_list():
    """退出当前身份后退回谁，必须和 currentEntry 的兜底同一条规则。

    readIdentities() 的原始顺序里可能躺着已被删除的账号，取 [0] 会把人换成一枚死
    令牌——真机上表现为"登录已失效"但界面仍写着原来那个人。
    """
    body = _function_body(_js("app.js"), "logoutCurrent")
    assert "readIdentities()[0]" not in body, "还在按插入顺序取第一条"
    assert "addedAt" in body, "没按 addedAt 挑最新那条"


def test_export_asks_the_server_for_a_downloadable_url():
    """导出不能再走 blob：WebView 的下载回调收不到 createObjectURL 出来的地址。

    壳那边已经注册 DownloadListener，前端要给它一个真实的 https 链接——由服务端
    签发一次性票据（另一个提交里加的 /v1/sessions/{id}/export-ticket）。
    """
    body = _function_body(_js("app.js"), "exportCurrent")
    assert "exportTicket" in body, "导出还在本地拼 blob"
    assert "download(" not in body, "blob 那条路在 App 里点了没反应"
    api = _js("api.js")
    assert "export-ticket" in api, "api.js 里没有签发票据的口子"


# ---------- Task 4：原生壳桥的适配层（shell.js） ----------


def test_shell_degrades_without_the_bridge():
    """浏览器直接开网址、以及 headless Edge 跑 CDP 时没有 AssistantShell。

    这条不是兼容性装饰：同一份 JS 既服务 APK 也服务直接访问网址的人，而本项目的
    前端验收就是在没有壳的浏览器里跑的。降级必须是"什么都不做"，不能抛错——
    一处 TypeError 会把它所在的那条链路（登录、上传）一起带走。
    """
    src = _js("shell.js")
    assert "window.AssistantShell" in src
    assert "present" in src
    # 事件只有一个入口，且只认 {type,id}：内容一律由 JS 反查，
    # 否则分享来的文件名就会被拼进一段 JS（spec §2 铁律①）。
    assert "window.__shellEvent" in src
    assert "typeof evt.id" in src


def test_shell_reads_a_share_in_chunks_and_uploads_through_the_existing_api():
    """readShareChunk 单次上限 512KB，所以一块照片得循环取；上传只许走现有那条路。

    另写一套 fetch("/v1/uploads") 就是第二个事实来源：令牌头、错误解析、
    附件记录形状都得再各自对齐一次，而这套对齐已经做过（api.js 的 upload）。
    """
    src = _js("shell.js")
    assert re.search(r"512\s*\*\s*1024", src), "没按 512KB 分块：一张照片就该整块穿过桥了"
    assert "new Blob" in src, "分块取回的字节得拼回一个 Blob 才交得出上传"
    app = _js("app.js")
    assert "API.upload(" in app, "分享件没走 api.js 那个上传封装"
    assert 'request("/v1/uploads"' not in app, "app.js 自己另起了一次上传请求：那是第二条上传链路"


def test_rejected_shares_are_said_out_loud_on_the_page():
    """壳拒收一条分享时，网页必须把那句话画出来——不能只有系统 Toast。

    起因是 v0.13 的一条实机风险：`ShareActivity` 用的是 `Theme.NoDisplay`，全程没有
    窗口，而 Android 官方 Toast 文档写明文字 Toast 只在应用处于前台时显示（12 起重绘后
    更容易被掐）。spec §9 第 6 条要的是"分享超限有明确拒绝提示，不是静默没反应"，
    把唯一的希望押在一条系统随时可以不显示的 Toast 上，等于没做。

    刻意**不**在这里抄一份码名清单：那五个名字住在 `SharePolicy.Intake`（壳侧），
    复制过来就是第二个真相，壳加一个码这里就得跟着改一次，而没跟着改的那次
    表现为一条红——一个什么都没坏、只是没同步的红旗。新码在没有这条分支的页面上
    会落到下面锁住的兜底句，那句仍然为真。
    """
    handler = _function_body(_js("app.js"), "onShellEvent")
    assert "share_rejected" in handler, (
        "onShellEvent 不认 share_rejected：壳发过来的拒绝原因会被静默丢掉，"
        "用户在页面上看到的仍然是「什么都没发生」")
    assert re.search(r"setStatus\([^)]*,\s*true\s*\)", handler), (
        "拒绝提示没按错误态画：与成功提示同色同形，等于没说")

    src = _js("app.js")
    table = src[src.index("SHARE_REFUSAL = {"):]
    table = table[:table.index("};")]
    codes = re.findall(r"^\s*([a-z_]+)\s*:", table, re.M)
    assert len(codes) >= 5, f"拒收原因表只剩 {len(codes)} 条，少于壳那侧的五个码"
    assert len(codes) == len(set(codes)), f"同一个码写了两遍：{codes}"
    for line in table.splitlines():
        if re.match(r"\s*[a-z_]+\s*:", line):
            msg = line.split(":", 1)[1]
            assert "分享没收下" in msg, f"这条拒收文案没先说结果：{line.strip()}"
            assert not re.search(r"\d+\s*(MB|KB|G)", msg), (
                f"文案里抄了体积数字（{line.strip()}）：上限住在壳的 ShareInbox，"
                "壳改数字这句话就开始说谎")
    assert "分享没收下" in handler, "兜底句没了：壳将来加的新码会画成空白"


# ---------- 登录层开场动画（intro） ----------
#
# 这段动效是装饰，不是新的状态机。三条锁各自守一条会被"顺手改好看"破坏的东西：
# 晕动症用户的退路、"每次冷启动只播一次"这个决定的实现形状、以及动画不许拦住打字。


def test_the_auth_intro_has_a_reduced_motion_escape():
    """prefers-reduced-motion 下整段动画必须归零，而不是"稍微短一点"。

    判据取"动画名被关掉"而不是"时长变短"：把 .5s 改成 .1s 在减弱动效的偏好下仍然是
    动画，而晕动症要的是不要动。
    """
    css = _css()
    assert "prefers-reduced-motion" in css, "整张样式表里没有任何减弱动效的退路"
    media = re.search(r"@media[^{]*prefers-reduced-motion[^{]*\{(.*?)\n\}", css, re.S)
    assert media, "prefers-reduced-motion 那条媒体查询没写成块，退路无从谈起"
    body = media.group(1)
    assert "auth-" in body and "none" in body, (
        f"减弱动效的块里没有关掉开场动画：{body.strip()[:160]}")
    assert "auth-brand-in" in css and "auth-sheet-rise" in css, \
        "两段动画的名字不全，说明某一段根本没接上退路"


def test_the_sheet_beat_fires_when_the_form_actually_exists():
    """面板那段必须等"内容真的在屏上"再播，不能跟着弹层一起触发。

    这是实测抓出来的 bug，不是假想：pending 期间 `.auth.pending .auth-form` 是
    display:none，所以弹层一出现就跑"掀开面板"，掀开的是一个空盒子；等 0.5~2 秒后
    服务端答话、表单才出现，那时动画早跑完了。截图里表现为字标闪一下、表单凭空出现。
    判据因此是"clearAuthPending 里必须触发面板那一段"——它正是中性态被摘掉的那一刻。
    """
    js = _js()
    clear = _function_body(js, "clearAuthPending")
    assert "playAuthSheet" in clear, (
        f"面板动画没挂在中性态摘掉那一刻，它会在空盒子上跑完：{clear.strip()[:180]}")
    # 反向那半：字标那一段仍该在弹层一出现就跑（那是冷启动第一帧唯一有的东西）
    pending = _function_body(js, "showAuthPending")
    assert "playAuthIntro" in pending, "字标那一段没挂在冷启动第一帧，开场就没有第一段了"


def test_the_intro_does_not_fade_fields_one_by_one():
    """字段/按钮不许各自带延迟淡入——那会在面板中间留出一个洞。

    实测过：给 .auth-field 与 .auth-go 分别加 .42s/.52s 延迟之后，动画中途那一帧
    是"上面的说明文字在、中间三格透明、下面的链接在"，看着像渲染坏了。
    整块面板一次掀开就够了，所以这里钉"不许再出现按字段错峰的那两条规则"。
    """
    css = _css()
    for sel in re.findall(r"([^{}]*\.intro[^{}]*)\{", css):
        assert ".auth-field" not in sel and ".auth-go" not in sel, (
            f"又给单个字段加了独立入场规则，动画中途会空洞：{sel.strip()}")


def test_the_auth_intro_plays_once_per_page_load_and_is_not_persisted():
    """"每次冷启动播一次"= 内存标志。落盘的话就变成"这辈子只播一次"。

    这条区分的是两种很容易写混的做法：记在 pref.* 里（会进 localStorage，换浏览器/清缓存
    才重置）与记在模块变量里（刷新即重置）。选的是后者，所以判据是"intro 那段不许碰
    pref. 与 localStorage"，而不是"存在某个标志位"。
    """
    js = _js()
    body = _function_body(js, "playAuthIntro")
    assert "introPlayed" in js, "没有那个一次性标志：动画要么每次都播，要么每次都不播"
    assert "pref." not in body and "localStorage" not in body, (
        f"开场动画的播放状态被写进了持久层，那就不是「每次冷启动一次」了：{body.strip()[:200]}")
    decl = re.search(r"^(let|var)\s+introPlayed\s*=", js, re.M)
    assert decl, "introPlayed 必须是模块级可变变量（const 播不完第二次，落盘变成只播一次）"


def test_the_auth_intro_never_blocks_typing_or_reading():
    """动画期间表单必须仍可点、可读：1.05 秒不值得把人拦在门外。

    钉的是"每一条 .intro 规则里都不许出现 pointer-events / display:none / visibility:hidden"，
    而不是"动画时长 < 1.2s"——时长会被人调，拦住输入这件事本身才是要防的回归。
    也不许自动聚焦：手机上第一反应就是打字，抢焦点会让光标停在动画还没到位的那一格。
    """
    css = _css()
    assert ".auth-sheet" in css, "没有 .auth-sheet：弧形面板那层没落地"
    # 只扫带 .intro 的规则：`.auth.pending .auth-form { display:none }` 是冷启动中性态
    # 自己的机制（有别的锁守着），不该被这条"动画不许拦人"的锁误伤。
    intro_rules = re.findall(r"[^{}]*\.intro[^{}]*\{([^}]*)\}", css)
    assert intro_rules, "一条 .intro 规则都没有，那这条锁在空转"
    for rule in intro_rules:
        assert "pointer-events" not in rule, f"开场动画把内容设成不可点：{rule.strip()}"
        assert "display: none" not in rule and "visibility: hidden" not in rule, \
            f"动画规则把内容整块藏了（那等于假加载）：{rule.strip()}"
    js = _js()
    intro = _function_body(js, "playAuthIntro")
    assert ".focus()" not in intro, "开场动画里自动聚焦输入框：手机上抢光标"


def test_the_first_painted_frame_is_already_the_neutral_layer():
    """登录层在 HTML 里带 hidden，浏览器就先画聊天外壳，等 boot() 跑起来才盖上中性层。

    这就是"对话窗口先出现、然后才是确认身份界面"那一次闪变的**第 0 步**。#43 那一轮
    把「外壳 → 401 → 表单」的三步序压成了"不问完不露东西"，但它压的是 JS 执行之后的事；
    JS 还没跑起来的那一帧上没有任何代码能救场，能让它不露外壳的只有标记本身。
    所以这条判的是静态标记而不是运行时行为：把 hidden 加回去，它立刻红。
    """
    html = _html()
    tag = re.search(r"<div[^>]*id=\"authModal\"[^>]*>", html)
    assert tag, "找不到 #authModal 这个元素"
    class_attr = re.search(r"class=\"([^\"]*)\"", tag.group(0))
    assert class_attr, f"#authModal 连 class 都没有：{tag.group(0)}"
    classes = class_attr.group(1).split()
    assert "hidden" not in classes, (
        "首帧上登录层是 hidden，于是浏览器先给用户看了聊天外壳，等 DOMContentLoaded "
        "才盖上中性层——这一闪是 JS 修不掉的，只能靠标记自己默认就是挡着的")
    assert "pending" in classes, (
        f"登录层默认不带 pending（实际是 {classes}）：默认露出的是完整表单而不是中性层，"
        "令牌有效的人会在第一帧上看到一张根本不该出现的登录表")


def _declared_rules(css: str, selector: str) -> list[str]:
    """取某个选择器下所有规则的花括号内容（只按字面匹配，够用且不会误伤别的选择器）。"""
    return [body for sel, body in
            re.findall(r"([^{}]+)\{([^}]*)\}", css)
            if re.search(r"(^|[\s,])" + re.escape(selector) + r"($|[\s,{])", sel)]


def test_the_arc_sits_on_a_real_boundary_in_both_themes():
    """弧要看得见，前提是它两侧不是一档颜色——上一版就是因为这个白做了。

    那时面板底色只在动画里存在、落位即淡成 transparent，于是深主题下 --surface(#16191d)
    压在 --bg(#0e1013) 上根本分不出来，clip-path 擦了一遍弧没人看见。现在弧是常驻结构，
    两侧各取一个专用 token，所以判据有三条：品牌带与面板取的是【不同】的 token；
    这两个 token 在深浅两套主题里都真的定义了；而且两处的实际色值不相等。
    少任何一条，弧都会静默消失——这类回归不会报错，只会"效果没了"。
    """
    css = _css()
    band = _declared_rules(css, ".auth")
    panel = _declared_rules(css, ".auth-sheet")
    assert band and panel, ".auth 或 .auth-sheet 没有规则"
    assert any("--auth-band" in r for r in band), "品牌带没吃 --auth-band"
    assert any("--auth-panel" in r for r in panel), "表单面板没吃 --auth-panel"

    root = re.search(r":root\s*\{([^}]*)\}", css)
    light = re.search(r'\[data-theme="light"\]\s*\{([^}]*)\}', css)
    assert root and light, "找不到 :root 或浅色主题那一段"

    def var(block: str, name: str) -> str | None:
        m = re.search(r"--" + name + r"\s*:\s*([^;]+);", block)
        return m.group(1).strip() if m else None

    for block, label in ((root.group(1), "深主题"), (light.group(1), "浅主题")):
        a, b = var(block, "auth-band"), var(block, "auth-panel")
        assert a and b, f"{label}里 --auth-band / --auth-panel 没成对定义：弧会塌成一片"
        assert a.lower() != b.lower(), (
            f"{label}里带子与面板同为 {a}：没有色差的弧等于没有弧")


def test_the_sheet_panel_does_not_fade_back_out_at_rest():
    """掀开动画跑完，面板必须留在原地。

    钉的是 keyframes 里不许再出现 background-color——那正是"落位即透明"的写法，
    它让动画结束的瞬间把弧的两侧重新并成同一档颜色。
    """
    css = _css()
    frames = re.search(r"@keyframes\s+auth-sheet-rise\s*\{(.*)\n\}", css, re.S)
    assert frames, "找不到 auth-sheet-rise"
    assert "background-color" not in frames.group(1), (
        "掀开动画又在动 background-color 了：结束态一旦是 transparent，弧就白擦")


def test_the_neutral_state_hides_the_sheet_so_it_cannot_show_an_empty_slab():
    """中性态里两张表单是 display:none，但 .auth-sheet 有常驻 padding。

    不连面板一起收掉的话，pending 那一段会撑出一块 34+28 的空白板——比原来的问题更难看。
    """
    css = _css()
    hidden = _declared_rules(css, ".auth.pending .auth-sheet")
    assert any("display: none" in r or "display:none" in r for r in hidden), (
        "中性态没收起 .auth-sheet：表单藏了，面板的常驻内边距会露出一块空板")


def test_the_update_row_exists_and_never_becomes_a_dead_button():
    """设置 → 关于 那行「检查更新」必须在，且它的去向由【壳报没报 update】决定。

    三种坏法都不会报错：
    ① 行被改没了（动了 markup 忘了这里）——壳其实能查，用户却找不到入口；
    ② 无条件走原生那条（不看 caps.update）——旧壳的桥里没有 checkUpdate 这个方法，
       点一下什么也不发生，页面连一句错都不显示，而它看起来和新壳上一模一样；
    ③ 把下载页地址搬回 app.js ——那是 test_frontend_uses_relative_api_paths_only
       要拦的形状（那里拦的是绝对 URL，本条只负责"退路还得在"）。
    """
    html = _html()
    assert 'id="rowUpdate"' in html, "设置 → 关于 里那行「检查更新」没了"
    assert 'id="rowUpdateLink"' in html, "旧壳那条退路（去下载页）没了"
    assert 'href="https://github.com/abonla599/ai-assistant/releases/latest"' in html,         "退路指向的不是 releases/latest，写死文件名的链接下一次发版就腐烂"
    for ident in ("rowUpdate", "rowUpdateLink"):
        assert f'class="set-row hidden" id="{ident}"' in html,             f"#{ident} 默认必须是藏起来的：没有壳（手机浏览器）时这一行没有能办的事"

    js = _js("app.js", "shell.js")
    assert "rowUpdateLink" in js and "updateLinkVal" in js, "那一行只写了 markup，没有代码在管它"
    assert "checkUpdate" in js, "新壳那条路断了"

    body = js[js.index("function renderAboutRows()"):]
    # 判"有没有参与判断"，不判"这个标识符在不在"：只查 caps.update 出现过一次的锁，
    # 把 `!!caps.update` 换成 `true` 也照样绿——那等于没锁。
    assert "!!caps.update" in body, "分流不看壳报的能力，旧壳上就会摆一个死按钮"
    shown = [x for x in body.splitlines() if "const shown =" in x]
    assert shown, "读不到那一行选谁的决定"
    # 2026-09-21 他把这一行点名要放在「版本」旁边，包括手机浏览器里。于是"藏掉"不再是
    # 答案，但这条锁要防的两件事一件都没松：① 不许有死按钮——没有桥就不许露那颗
    # button，只能露会真的跳走的 <a>；② 不许让人以为网页能自己更新——所以链接那一颗
    # 的文字必须写明"去下载页"。
    assert "native ? btn : link" in shown[0], (
        "分流不再是「有桥才给 button」，旧壳与浏览器上就会出现点了没反应的死按钮："
        + shown[0].strip())
    assert "去下载页" in body, (
        "浏览器里那颗必须写明是去下载页：只写「检查更新」会被读成"
        "“这个页面能自己更新”，而界面本来就是每次从服务器现加载的那一版")


def test_the_update_row_sits_right_under_the_version_row():
    """「检查更新」紧跟在「版本」那一行下面——这是 2026-09-21 他点名的位置。

    位置本身就是要求，所以钉位置不是吹毛求疵：这一行在设备组里能跑、在服务地址下面
    也能跑，而"换安装包这件事和这是什么版本是同一个问题的两面"只有挨着才成立。
    它同时还得留在 关于 组里——挪回设备组会让这条锁当场红，那是有意留的对照。
    """
    html = _html()
    dev = html.index('class="set-group">设备')
    about = html.index('class="set-group">关于')
    ver = html.index('id="versionVal"')
    theme = html.index('id="themeBtn"')
    assert about > dev, "分组顺序被改了：正向对照不成立，下面那几条都是空的"

    for rid in ('id="rowUpdate"', 'id="rowUpdateLink"'):
        at = html.index(rid)
        assert at > about, f"{rid} 还在「设备」那一组里"
        assert ver < at < theme, (
            f"{rid} 要排在「版本」下面、「外观」上面（当前 ver={ver} at={at} theme={theme}）")


def test_关于_reports_a_version_instead_of_a_number_we_wrote_by_hand():
    """关于里那行版本只能来自壳报上来的那个值。

    在 HTML 里写死 `v0.16` 是这仓最眼熟的那种错：android/app/build.gradle 才是版本号的
    唯一来源，抄一份到前端就等于下一次升版必然有一处是旧的，而且它显示得理直气壮。
    """
    html, js = _html(), _js()
    about = html.index('class="set-group">关于')
    assert 'id="versionVal"' in html, "关于里没有版本这一行"
    assert html.index('id="versionVal"') > about, "版本行不在关于那一组里"
    assert "versionVal" in js, "HTML 里有位置、JS 里没人填——那会是永久空白"
    assert "caps.version" in js, "填进去的值不是壳报的那个版本"
    assert not re.search(r"[\"'`]v?\d+\.\d+", js), "JS 里出现了手写版本号字面量"


# ---------- 「发现版本更新」底部卡片 ----------
#
# 这一节全是"读源码"的锁，与 test_android_shell.py 同一个理由：本机没有真机，也没有
# 能跑 app.js 的 DOM。所以每条判据都挑**形状上可数**的东西（某一行的字面、出现次数、
# 两个位置的先后），并且逐条做过变异验证——把要防的那件事真改一遍，它必须红。


def _js_fn(js: str, name: str) -> str:
    """取 `function name(...) { ... }`：从签名到与它配对的那个右括号。

    为什么不用 `js[js.index("function name("):]`（本节之前那种"切到文件尾"的写法）：
    那一段里排在后面的**所有**函数都算在它头上，于是"某词不在这个函数里"这一类断言
    永远是绿的。而本节有一条锁锁的恰恰就是"补弹那条路上不许碰网络"——用错尺子，
    那条锁会一边绿着一边什么都不管。
    """
    if f"function {name}(" not in js:
        raise AssertionError(f"app.js 里找不到 {name}()：这一节的锁跟着改名一起失效了")
    at = js.index(f"function {name}(")
    open_at = js.index("{", at)
    depth = 0
    for i in range(open_at, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[at:i + 1]
    raise AssertionError(f"{name}() 的花括号没配上")


def _sheet_block() -> str:
    """index.html 里那张卡片**本体**（不含设置弹层里那两行「检查更新」）。

    切到第一个 <script 为止，不数配对的 </div>：这块里有嵌套 div 和 SVG，
    按标签配对的写法下一次多加一层就要重写，而它要的只是"卡片自己那一段"。
    """
    html = _html()
    start = html.index('<div class="update-sheet')
    end = html.index('<script src="vendor/marked.min.js"')
    assert end > start, "卡片排在脚本之后：首屏那一帧它根本不存在，JS 拿不到元素"
    return html[start:end]


def _tag_of(block: str, ident: str) -> str:
    pattern = r'<(?:button|a)\b[^>]*id="' + re.escape(ident) + r'"[^>]*>'
    m = re.search(pattern, block)
    assert m, f"卡片里没有 <button>/<a> #{ident}"
    return m.group(0)


def _balanced_css(css: str, at: int) -> str:
    """从 at 之后第一个 { 起、按花括号配对取整块（嵌套的也算进来）。"""
    open_at = css.index("{", at)
    depth = 0
    for i in range(open_at, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at:i + 1]
    raise AssertionError("从这里到文件末尾花括号没配上")


def _nested_rule_bodies(css: str, at_keyword: str, selector: str) -> list:
    """@supports / @media 这类嵌套块里，选择器 selector 自己的规则体。

    为什么不能直接用 _declared_rules：那把尺子只看扁平规则，遇到嵌套写法时外层的
    `@supports not (…) {` 被当成"选择器"、里面整条规则整个落进它的 body 里，于是
    `@supports` 里的那一条**永远查不到**。本仓给 .auth-sheet 写实心回退时就是这么
    躲过它的——所以这里自带一把会配对的，别指望那把尺子看见嵌套里的东西。
    """
    out = []
    for m in re.finditer(re.escape(at_keyword), css):
        block = _balanced_css(css, m.start())
        if selector not in block:
            continue
        for sub in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            sel = sub.group(1).strip()
            if re.search(r"(^|[\s,])" + re.escape(selector) + r"($|[\s,{])", sel):
                out.append(sub.group(2))
    return out


def test_the_update_sheet_is_there_quietly_and_says_nothing_of_its_own():
    """卡片存在、默认全藏、上面一个版本号都不写死。

    四种坏法都不会报错：① 元素被改名或删掉（`$("updateSheet")` 拿到 null，卡片永远不
    出来，页面其余部分照常——正是本仓最眼熟的那类"效果没了"）；② 默认不藏，于是每次
    冷启动先闪一张「发现版本更新」再收回去，而那一刻它还什么都不知道；③ 把 v0.17 抄进
    markup，下一次发版它就腐烂，而且腐烂得理直气壮（与 versionVal 那条同一个理由）；
    ④ 三颗按钮漏一颗，卡片弹出来却关不掉。
    """
    block = _sheet_block()
    assert 'class="update-sheet hidden" id="updateSheet"' in block, (
        "卡片要么不在，要么默认就是露出来的")
    js = _js()
    for ident in ("updateSheet", "updateVer", "updateLaterBtn", "updateGoBtn", "updateGoLink"):
        assert f'id="{ident}"' in block, f"卡片里少了 #{ident}"
        assert f'$("{ident}")' in js, f"#{ident} 只写了 markup，没有代码在管它"
    for ident in ("updateLaterBtn", "updateGoBtn", "updateGoLink"):
        assert " hidden" in _tag_of(block, ident), f"#{ident} 默认是露出来的"

    assert 'id="updateVer"></p>' in block, "版本那一格在 markup 里带了字：它只能由后端填"
    assert ">稍后</button>" in block, "「稍后」那颗的文字变了（他点名的两颗按钮之一）"
    assert block.count(">立即更新<") == 2, (
        "「立即更新」不是恰好两颗（button 与 a 各一颗，同一时刻只露一颗）："
        f"实际 {block.count('>立即更新<')} 颗")
    assert block.count('href="https://github.com/abonla599/ai-assistant/releases/latest"') == 1, (
        "卡片里那颗链接指的不是 releases/latest，或不只一处：写死文件名的下一次发版就腐烂")


def test_the_card_offers_the_same_one_way_out_as_the_settings_row():
    """两颗「立即更新」永远只露一颗，判据是壳报的 update 能力——与设置那一行同一条规则。

    老壳（v0.15 及更早）只报版本号、报不出 update 能力，桥里没有 checkUpdate 这个方法：
    给它那颗 button 就是又造一个点了没反应的死按钮，而它看起来和新壳上一模一样。
    同一件事在本文件里红过一次（test_the_update_row_exists_and_never_becomes_a_dead_button），
    所以这里不复用那条的语料，各判各的元素。
    """
    body = _js_fn(_js(), "offerUpdate")
    assert "!!caps.update" in body, "分流不看壳报的能力"
    assert "native ? btn : link" in body, "选的不再是「有桥才给 button」"
    assert 'classList.toggle("hidden", el !== shown)' in body, (
        "两颗按钮不再是按 shown 收的：会同时露出来，或者一颗都不露")
    assert 'later.classList.remove("hidden")' in body, "「稍后」没被放出来：卡片弹了却关不掉"


def test_the_card_opens_only_for_a_confirmed_update(monkeypatch):
    """开门的三句判据都必须排在**动任何 DOM 之前**：确认有更新、今天还没弹过。

    判据取"这一句出现在第一次 classList.remove 之前"而不是"这个标识符在函数里出现过"：
    后者把 return 挪到弹卡片之后照样绿。三种漏法各对应一种吵到人的样子——
    丢掉 has_update，"服务器可达、你已是最新"也会弹一张卡；丢掉 ok，GitHub 抽风那天
    每个人都会收到一张写着空版本号的卡；丢掉那把日期闸，"每天最多一次"当场失效，
    而**它不会报错**，只是每次 ☰ 都弹。
    """
    body = _js_fn(_js(), "offerUpdate")
    head = body[:body.index("classList.")]      # 第一句写 DOM 之前
    assert "return" in head, "判据排在弹卡片之后，等于没有判据"
    assert "info.ok" in head and "has_update" in head, f"开门判据不完整：{head.strip()[:120]}"
    assert "updateSheetShownToday()" in head, (
        "日期闸不在弹卡片之前：这条节流失效之后，☰ 每开一次就弹一次")


def test_the_card_reads_only_fields_the_backend_sends(monkeypatch):
    """卡片从 JSON 里取的每个字段名，都得是后端那份回答里真有的键。

    这是"效果没了但不报错"的正面对策：后端哪天把 latest 改叫 version，JS 里那句
    `info.latest` 就静默变成 undefined，界面上弹出"新版本 vundefined · 当前 v0.16"，
    而前后端各自的测试全都绿。字段名因此不许手抄一份对照表，直接从 probe() 的返回上取。
    """
    from app.core import releases

    def fake_fetch():
        return ({"version": "9.9", "url": "u", "asset_name": "a",
                 "asset_url": "d", "size": 1}), ""

    monkeypatch.setattr(releases, "_fetch", fake_fetch)
    releases.reset_for_tests()
    try:
        keys = set(releases.probe(have="0.1").keys())
    finally:
        releases.reset_for_tests()

    body = _js_fn(_js(), "offerUpdate")
    fields = set(re.findall(r"\binfo\.([A-Za-z_][A-Za-z0-9_]*)", body))
    assert fields, "offerUpdate 里一个 info.xxx 都没读到，这条锁在空转"
    assert fields <= keys, f"卡片在读后端没给的字段：{sorted(fields - keys)}；给的是 {sorted(keys)}"

    # 问的那一句里，参数名也要对上路由函数的形参名。对不上的表现是"永远回答
    # 不知道装的是哪版"——不报错，只是那张卡片从此再也不出现。
    ask = _js_fn(_js(), "maybeAskUpdate")
    assert "?have=" in ask, "没把装着的版本号告诉服务器"
    from app.main import release_latest
    assert "have" in inspect.signature(release_latest).parameters, "后端那个形参改名了"


def test_the_card_never_builds_a_download_target_out_of_the_response():
    """下载地址只在 markup 那一处，绝不由响应里的字段拼出来。

    后端回来的 JSON 里躺着 url 与 asset_url。把它们写成 <a> 的 href，等于让
    "谁能在服务端返回里动手脚"就能决定手机上点下去打开哪个地址——而这条端点是
    免鉴权的。指向 releases/latest 那个常量就没这个问题：它永远落在 GitHub 的发布页上。
    """
    body = _js_fn(_js(), "offerUpdate") + _js_fn(_js(), "hideUpdateSheet")
    for name in ("url", "asset_url", "asset_name"):
        assert f"info.{name}" not in body, f"卡片开始使用后端给的 {name} 了"
    assert "href=" not in body and "setAttribute" not in body, "卡片在运行时改 href"


def test_the_daily_stamp_is_the_local_calendar_not_an_utc_one():
    """"每天最多一次"那把日期戳必须按本机日历算，而且读写的是同一把钥匙。

    toISOString 是 UTC：北京时间早上八点之前它写的是"昨天"。用它的后果不是崩溃，是
    每天早上八点整"今天还没弹过"重新成立一次——同一天弹两次，恰好是这条节流要防的事。
    另一半更安静：写进去的和读出来比对的不是同一个 localStorage 键，那"今天不再弹"
    永远读不到自己刚写的戳，于是每次开侧栏都弹；反过来如果只写不滚日（存了个 true），
    就变成"这台设备的有生之年只弹一次"，第二天该提醒的时候它不提醒了。
    """
    js = _js()
    day = _js_fn(js, "localDay")
    assert "toISOString" not in day, "用的是 UTC 日期"
    assert "getMonth()" in day and "getDate()" in day, "没按本机日历取月/日"

    gate, writer = _js_fn(js, "updateSheetShownToday"), _js_fn(js, "markUpdateSheetShown")
    assert "UPDATE_SHEET_KEY" in gate and "UPDATE_SHEET_KEY" in writer, "读写不是同一把钥匙"
    assert "localDay()" in gate and "localDay()" in writer, (
        "日期戳不再按天算：写 true 就是有生之年只弹一次")
    assert "localDay()" in _js_fn(js, "updateSheetShownToday")
    # 无痕模式里读 localStorage 就是抛。那时按"今天没弹过"处理是有意选的：宁可多提一次，
    # 也不因为存储不可用而永远不提——这一句判的是那个方向，反过来写这条锁就白搭。
    assert "return false" in gate, "读不到时的默认方向变了：那会变成永不提醒"


def test_no_card_and_no_request_without_a_shell_version():
    """浏览器与不报版本号的老壳：既不弹卡片，也一次请求都不发。

    浏览器里没有安装包可换，弹一张「发现版本更新」只会让人以为网页能自更新（设置那一行
    为此专门写着"去下载页"）。老壳更直接：拿不到手上的版本号就没法回答"有没有更新"，
    而猜一个号出来弹脸，是比沉默更坏的选择。
    """
    js = _js()
    ask = _js_fn(js, "maybeAskUpdate")
    fetch_at = ask.index("fetch(")
    # 判的是"闸门认的那个东西就是版本号"，不是"函数里有个 return"：`if (false) return;`
    # 也满足后者，而它等于没有闸门。变异验证就是这么漏过一次。
    assert re.search(r"if\s*\(\s*!have\s*\)\s*return", ask[:fetch_at]), (
        "问服务器之前没有按「手上有没有版本号」放行——浏览器与老壳会照样收到一次探测")

    ver = _js_fn(js, "shellVersion")
    assert "caps.version" in ver, "版本号不是壳报的那个"
    assert 'return ""' in ver, "没有壳时不再返回空，而是编了一个号出来"


def test_the_sidebar_rehearses_from_memory_not_from_a_new_request():
    """☰ 补弹那一条只复用内存里那份结果，绝不重新问服务器。

    这条端点是全后端唯一会替调用人出一次网的免鉴权面（一次 GitHub 往返，最多 5 秒）。
    把它挂在 openSidebar 上，"晚上想起来翻三次"就是三次出站，而它换来的答案是同一份。
    所以判据是双向的：补弹那条路上不许出现 fetch，openSidebar 调的必须是补弹而不是探询。
    """
    js = _js()
    again = _js_fn(js, "reofferUpdate")
    assert "fetch(" not in again, "补弹那条路又去问了一次服务器"
    assert "offerUpdate(updateSeen)" in again, "补弹没走内存里那份"

    sidebar = _js_fn(js, "openSidebar")
    assert "reofferUpdate()" in sidebar, "☰ 不再补弹了（他点名的时机）"
    assert "maybeAskUpdate()" not in sidebar, (
        "openSidebar 调的是探询而不是补弹：每开一次侧栏就多一次出站")


def test_the_release_probe_is_the_last_thing_boot_does_and_is_never_awaited():
    """首屏不等它：问发布页排在 boot 最后一句，而且没人 await 它。

    挪到前面（或写成 await）的表现是"手机打开 App 转圈"——那 5 秒超时是 GitHub 的，
    不是后端的，本地服务再快也救不回来。这一条只判先后与有没有 await，
    因为这两件事都能静悄悄改掉，而代价是每个人每次冷启动。
    """
    boot = _js_fn(_js(), "boot")
    at = boot.index("maybeAskUpdate()")
    assert "await maybeAskUpdate" not in boot, "await 它就是把首屏交给 GitHub"
    assert at > boot.index("renderMessages()"), "问发布页排在渲染之前"
    assert at > boot.index('navigator.serviceWorker.register'), "它不在 boot 最后一段"

    # 一页一次：整份 app.js 里除了定义，只容许一处真的调用。☰ 那一路走的是 reofferUpdate，
    # 判据就在下一条锁里；这一条钉的是"多出来的那个触发点"，它不会报错，只会多要一次网络。
    js = _js()
    calls = [m.start() for m in re.finditer(r"\bmaybeAskUpdate\(\)", js)
             if "function " not in js[max(0, m.start() - 12):m.start()]]
    assert len(calls) == 1, f"探询的触发点有 {len(calls)} 处（只许 boot 那一处）"


def test_the_card_lands_above_the_sidebar_and_below_every_modal_layer():
    """层叠顺序：压得住侧栏，让得住所有模态层，两条都是行为不是审美。

    压在侧栏之上是"☰ 补弹"这条要求成立的前提——它要是被侧栏盖住，点三横线就等于
    什么都没发生。让在通用弹层(60)/拍照(70)/登录层(200)之下是另一半：那三层都是
    "有件事必须先做完"，一张可以稍后再看的卡片不该从它们中间探出来。
    只高侧栏 5 点不是随手取的：数值本身写在这里判的是**相对次序**，谁改谁负责说清
    它想插到哪一档。
    """
    css = _css()

    def bodies_of(selector):
        out = list(_declared_rules(css, selector))
        for kw in ("@media", "@supports"):
            out += _nested_rule_bodies(css, kw, selector)
        return out

    def z_of(selector):
        vals = [int(m.group(1)) for r in bodies_of(selector)
                for m in [re.search(r"z-index:\s*(\d+)", r)] if m]
        assert vals, f"{selector} 没有 z-index，本节其余判据失去基准"
        return vals

    card = z_of(".update-sheet")[0]
    assert card > max(z_of(".sidebar") + z_of(".backdrop")), "卡片会被侧栏或它的遮罩盖住"
    below = z_of(".modal") + z_of("#cameraModal") + z_of(".auth")
    assert card < min(below), f"卡片压到了模态层之上（它们现在是 {sorted(set(below))}）"

    assert any("var(--safe-bottom)" in r for r in bodies_of(".update-sheet")), (
        "底部没留安全区：Android 手势条会盖住那颗「立即更新」")
    fallback = _nested_rule_bodies(css, "@supports", ".update-sheet")
    assert fallback and any("var(--surface)" in r for r in fallback), (
        "没有 backdrop-filter 时的实心回退没了：半透明底配不上模糊就是糊成一团字")
    btn = " ".join(bodies_of(".us-btn"))
    hit = re.search(r"min-height:\s*(\d+)px", btn)
    assert hit and int(hit.group(1)) >= 44, "两颗按钮不足 44px 高（他对手机可点目标定过的下限）"
    assert "position: fixed" in " ".join(bodies_of(".update-sheet")), (
        "卡片不再是钉在屏幕底边的那一条")


def test_the_card_animation_has_a_reduced_motion_escape():
    """升起动画在减弱动效偏好下必须关掉，而不是"稍微短一点"。

    与开场动画那条锁同一个理由：晕动症要的是别动。判据取"动画所在的类被列进
    prefers-reduced-motion 那个块"，因为只查 @keyframes 还在不在是没用的——
    动画关不掉时关键帧当然也还在。
    """
    css = _css()
    media = re.search(r"@media[^{]*prefers-reduced-motion[^{]*\{(.*?)\n\}", css, re.S)
    assert media, "prefers-reduced-motion 那条媒体查询没写成块"
    assert "update-sheet" in media.group(1), "卡片的升起动画没有减弱动效退路"
    assert "update-sheet-rise" in css, "关键帧名字不见了：说明动画整个被删了"


# ---------- 壳的桥：调用约定与诊断（2026-09-22 真机排查加的） ----------

_SHELL_JS_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const mode = process.argv[3];              // ok | undefined | null | none | flip | later-null
const calls = [];
const replies = {
  capabilities: JSON.stringify({ shell: 1, update: 1, version: "0.17", notifications: 0,
                                 exactAlarms: 0 }),
  listReminders: "[]", pendingShares: "[]", checkUpdate: '{"ok":true}',
  setOwner: '{"ok":true}', scheduleReminder: '{"ok":true}',
  cancelReminder: '{"ok":true}', readShareChunk: '{"b64":""}', consumeShare: '{"ok":true}',
  openSettings: '{"ok":true}',
};
let capsCount = 0;
function capsReply() {
  capsCount += 1;
  if (mode === "flip") {
    // 第 3 次起"用户已经在系统那一页里把通知打开了"：加载时那次 + 驱动里两次
    return JSON.stringify({ shell: 1, update: 1, version: "0.17",
                            notifications: capsCount >= 3 ? 1 : 0, exactAlarms: 0 });
  }
  if (mode === "later-null") return capsCount === 1 ? replies.capabilities : "null";
  return replies.capabilities;
}
function bridge() {
  if (mode === "none") return undefined;                 // ① 对象压根没注入
  return new Proxy({}, { get(t, name) {
    if (typeof name !== "string") return undefined;
    return function () {
      calls.push(name + "/" + arguments.length);
      if (name === "capabilities") {
        if (mode === "undefined") return undefined;      // ② 派发没匹配上
        if (mode === "null") return "null";              // ③ 被 origin 拒
        return capsReply();
      }
      return replies[name];
    };
  }});
}
const sandbox = {
  window: {}, location: { protocol: "https:", host: "ai.fenever.xyz" },
  console, JSON, Math, String, Number, Array, Object, Promise, Error, RegExp, Date,
  atob: (s) => Buffer.from(s, "base64").toString("binary"), Uint8Array,
  Blob: class Blob { constructor(p, o) { this.parts = p; this.type = (o || {}).type; } },
};
sandbox.window.AssistantShell = bridge();
vm.createContext(sandbox);
const out = vm.runInContext(src + "\n;(function(){ SHELL.setOwner('u'); SHELL.listReminders();"
  + " SHELL.pendingShares(); SHELL.checkUpdate(); SHELL.openSettings('alarms');"
  + " const a = SHELL.capabilities().notifications;"
  + " const b = SHELL.capabilities().notifications;"
  + " return { present: SHELL.present, diag: SHELL.diagnostic(), capsA: a, capsB: b }; })()",
  sandbox);
process.stdout.write(JSON.stringify({ present: out.present, diag: out.diag, calls: calls,
                                      capsA: out.capsA, capsB: out.capsB }));
"""


def _run_shell_js(mode: str) -> dict:
    """在 node 里真跑仓库那份 shell.js，返回 {present, diag, calls}。

    为什么必须真跑：这条 bug 的现场是「JS 怎么调用 → WebView 按方法名+参数表派发 →
    Java 方法」，读源码看不出 `B[name]("")` 与 `B[name]()` 的差别，而壳那 42 条 JVM 单测
    是【直接调 Java 方法】的，永远碰不到派发规则。判据只能问运行时。

    这里也不走 _js() 那把尺子：它剥注释，而我们要执行的是磁盘上那份原文件。
    读文件的是 node 自己，不是本函数——所以这条不是"绕过尺子读语料做文本断言"，
    两回事。
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器上没有 node，跑不了这段 JS")
    harness = Path(tempfile.mkdtemp(prefix="shell-js-")) / "harness.cjs"
    harness.write_text(_SHELL_JS_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(harness), str(STATIC / "shell.js"), mode],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"harness 自己就跑失败了：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def test_zero_arg_bridge_methods_are_called_with_no_arguments():
    """Java 侧不收参数的那些方法，JS 就必须一个参数都不传。

    多传一个空串的代价不是报错，是**整个壳在页面上凭空消失**：桥按"方法名 + 参数表"
    去找 Java 方法，找不到就回 undefined，JSON.parse 抛错被 catch 咽成 null，
    present=false，于是界面和手机浏览器长得一模一样。2026-09-22 真机第一次装 v0.17
    就是这个表现——而它在此之前一直只在 JVM 单测里"通过"。
    下面这份名单对着 ShellBridge.java 的签名核过；方法名有没有漏接由
    test_android_shell.py 那条派生锁管，这里只管参数个数。
    """
    out = _run_shell_js("ok")
    calls = out["calls"]
    for name in ("capabilities", "listReminders", "pendingShares", "checkUpdate"):
        assert f"{name}/0" in calls, (
            f"{name}() 在 Java 侧不收参数，JS 却传了（实际记录：{calls}）")
    assert "setOwner/1" in calls, f"收一个参数的那个反而没传：{calls}"
    assert "openSettings/1" in calls, f"openSettings 收一段 JSON，JS 却没带参数：{calls}"
    assert out["present"] is True, f"桥一切正常时 present 必须是 true：{out}"


def test_capabilities_is_re_asked_rather_than_served_from_a_load_time_snapshot():
    """capabilities() 每次现问：权限是能在页面开着的时候被改掉的。

    假想场景就是这一版要修的那件事：提醒页那一行显示"没授权"，用户点「去设置」进去打开，
    回到应用——如果这一行读的是加载时那份快照，它会继续显示"没授权"，而这一次它是错的。
    "改了没生效"在本项目历史上被误判成代码坏了不止一次，所以这条不能靠读源码判定：
    快照与现问在文本上可以长得一样（都写 capabilities()），只有运行时知道答案。
    """
    out = _run_shell_js("flip")
    assert out["present"] is True, out
    assert out["capsA"] == 0, f"桥已经回了 1 之前那次读到的就不是 0：{out}"
    assert out["capsB"] == 1, f"第二次问还是旧值——那是快照不是现问：{out}"


def test_a_failed_re_ask_falls_back_to_the_snapshot_instead_of_vanishing():
    """现问失败时退回加载时那份，而不是把那一行抹掉。

    方向要分清：capabilities() 现问是为了"别把已改的显示成旧的"，不是为了"桥偶尔没应答
    就把整行撤掉"。后者会让设置里那一行忽有忽无，而"上一眼还有"是本仓最难复查的证词。
    """
    out = _run_shell_js("later-null")
    assert out["present"] is True, f"桥应答了一次就不该被判定为无桥：{out}"
    assert out["capsA"] == 0 and out["capsB"] == 0, \
        f"现问失败时要退回加载时那份，别回一个空对象让整行消失：{out}"


def test_the_bridge_diagnostic_names_which_layer_failed():
    """无桥时那行诊断要能分出三种坏法，否则它只是把困惑重说一遍。

    ① 对象没注入（typeof 不是 object）；② 注入了但调用没匹配上任何 Java 方法
    （回 undefined）；③ 调用通了、壳主动拒了（capabilities() 回字符串 "null"，
    那是 sameOrigin 闸门的口径）。③ 与"页面在别的源上"是同一件事的两面，所以顺带
    钉住诊断里那份 page 是页面自己看到的 scheme+host。
    """
    none = _run_shell_js("none")
    assert none["present"] is False and none["diag"]["object"] == "undefined", none

    unmatched = _run_shell_js("undefined")
    assert unmatched["present"] is False, unmatched
    assert "undefined" in unmatched["diag"]["reply"], unmatched["diag"]

    refused = _run_shell_js("null")
    assert refused["present"] is False, refused
    assert refused["diag"]["text"] == "null", refused["diag"]
    assert refused["diag"]["page"] == "https://ai.fenever.xyz", refused["diag"]


def test_the_bridge_diagnostic_reaches_the_page_only_when_there_is_no_bridge():
    """诊断行：markup 里默认藏起来，只有认不到壳时 app.js 才填它、放它出来。

    三个判据各防一种"看着做了其实没做"：① 只写了 JS 没写元素，$( ) 拿到 null，
    那一行永远不出现；② 默认不藏，于是壳正常的时候也挂着一条排查信息；
    ③ 用 innerHTML 写——那几个值全是从桥（外部可控）来的。
    """
    html = _html()
    js = _js()
    assert 'class="pane-note hidden" id="bridgeDiag"' in html, (
        "诊断行不在 markup 里，或默认不是藏着的")
    body = _js_fn(js, "renderAboutRows")
    assert 'const diag = $("bridgeDiag")' in body, "JS 里没人填那一行"
    assert "SHELL.diagnostic()" in body, "填进去的不是桥记录的那份"
    assert "SHELL.present" in body, "没有按有没有壳决定露不露：有桥时也会挂着排查信息"
    assert "textContent" in body and "innerHTML" not in body, "诊断文字来自桥，不许走 innerHTML"


_RENDER_JS_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const SC = JSON.parse(process.argv[3]);

class El {
  constructor(tag) {
    this.tag = tag; this.kids = []; this.dataset = {}; this.attrs = {};
    this.className = ""; this.textContent = ""; this.onclick = null;
  }
  appendChild(c) { this.kids.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.kids.push(c)); }
  setAttribute(k, v) { this.attrs[k] = v; }
}
function dump(el, out) {
  if (el.textContent) out.push(el.textContent);
  el.kids.forEach((k) => dump(k, out));
  return out;
}
function buttons(el, out) {
  if (el.onclick) out.push(el);
  el.kids.forEach((k) => buttons(k, out));
  return out;
}
const openCalls = [];
const statuses = [];
const SHELL = {
  openSettings(target) {
    openCalls.push(target);
    return (SC.openReply !== undefined) ? SC.openReply : { ok: true };
  },
};
function setStatus(text, isErr) { statuses.push((text || "") + (isErr ? "!" : "")); }
const sandbox = {
  SC,
  document: { createElement: (t) => new El(t) },
  SHELL, setStatus, console, JSON, Math, String, Number, Array, Object, Date, Boolean,
};
vm.createContext(sandbox);
const driver = "\n;(function(){ return { card: reminderStatusCard(SC.caps),"
  + " hist: (SC.reminders || []).map(fmtFiredHistory) }; })()";
const out = vm.runInContext(src + driver, sandbox);
const textsBefore = dump(out.card, []);
buttons(out.card, []).forEach((b) => b.onclick());
process.stdout.write(JSON.stringify({
  texts: textsBefore,
  afterClick: dump(out.card, []),
  clicks: openCalls,
  statuses: statuses,
  hist: out.hist,
  role: out.card.dataset.role || "",
}));
"""


def _run_render_js(scenario: dict) -> dict:
    """在 node 里真跑 app.js 的 reminderStatusCard / fmtFiredHistory，读回渲染出来的文字。

    为什么不走 `_js()` 那把尺子做文本断言：这一屏要钉的三件事全是"给定这几种输入，
    屏幕上出现哪句话"。文本断言只能证明那句话**在文件里**，而把 `pair[1] ? ok : bad`
    改成 `pair[1] ? bad : ok`（或者反过来把 undefined 当成 0）在文本上一字未改，
    症状却是"已授权的显示成没授权"和"老壳被说成从没响过"——两种都是说谎。
    渲染用的 DOM 是假的，被执行的**判定与文字选择**是仓库那一份。
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器上没有 node，跑不了这段 JS")
    snippet = "\n".join(_fn_text(_js(), n) for n in
                        ("reminderStatusCard", "fmtFiredHistory", "fmtReminderAt"))
    d = Path(tempfile.mkdtemp(prefix="render-js-"))
    (d / "fns.js").write_text(snippet, encoding="utf-8")
    (d / "harness.cjs").write_text(_RENDER_JS_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(d / "harness.cjs"), str(d / "fns.js"),
                        json.dumps(scenario)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, f"harness 自己就跑失败了：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def test_the_status_row_has_three_states_not_two():
    """1 / 0 / 这个键压根没有是三种情况，画成三种话；把"读不到"画成"没授权"是说谎。

    v0.17 及更早的壳不报 notifications 与 exactAlarms。如果那一行把缺键当成 0，
    老壳用户看到的是"没授权"——而他真去设置里翻一遍会发现本来就是开着的。
    这一版修的就是"看不见的状态"，别再造一个新的看不见的状态。
    """
    granted = _run_render_js({"caps": {"notifications": 1, "exactAlarms": 1}})
    denied = _run_render_js({"caps": {"notifications": 0, "exactAlarms": 0}})
    old = _run_render_js({"caps": {"shell": 1, "update": 1, "version": "0.17"}})

    assert any("已授权" in t for t in granted["texts"]), granted
    assert any("能准点" in t for t in granted["texts"]), granted
    assert any("没授权" in t for t in denied["texts"]), denied
    assert any("省电" in t for t in denied["texts"]), denied
    assert not any("没授权" in t or "能准点" in t for t in old["texts"]), \
        f"老壳不报这两个键，那一行却给了结论：{old['texts']}"
    assert any("读不到" in t for t in old["texts"]), old


def test_clicking_go_to_settings_does_not_flip_the_row_itself():
    """点「去设置」只负责把那一页递出去，不改这一行的字。

    改完权限回到应用才是状态该变的时刻（由 visibilitychange 现问）。在这里当场翻绿，
    等于把"用户可能根本没开"显示成"已经开了"——那一行从此不可信，而它是这一版唯一的依据。
    壳回 ok:false 时要把话说出来，别静默。
    """
    out = _run_render_js({"caps": {"notifications": 0, "exactAlarms": 0}})
    assert out["clicks"] == ["notifications", "alarms"], out["clicks"]
    assert out["afterClick"] == out["texts"], f"点一下自己就把状态改了：{out}"
    assert out["statuses"] == [], out["statuses"]

    refused = _run_render_js({"caps": {"notifications": 0, "exactAlarms": 0},
                              "openReply": {"ok": False, "error": "bad-reply"}})
    assert refused["statuses"], "那一页没递出去，屏幕上却一个字都没说"


def test_the_history_text_distinguishes_never_fired_from_an_old_shell():
    """firedAt/missed 缺键时一个字都不说；两个键都在且都是 0 才说"到点还没响过"。

    老壳的 listReminders 里没有这两个键。把它们当成 0 会显示成"还没响过"——那恰好是
    这一版要回答的那个问题，答错了比不答更糟，因为它读起来像查过了。
    """
    out = _run_render_js({"caps": {}, "reminders": [
        {"title": "旧壳那条"},
        {"title": "没响过", "firedAt": 0, "missed": 0},
        {"title": "响过又丢过", "firedAt": 1758450000000, "missed": 3},
    ]})
    assert out["hist"][0] == "", f"老壳没这两个键，却给出了结论：{out['hist']!r}"
    assert "还没响过" in out["hist"][1], out["hist"]
    assert "上次发出" in out["hist"][2] and "3 次到点没发出" in out["hist"][2], out["hist"]


def test_the_status_card_names_itself_so_the_page_can_refresh_only_it():
    """卡片自己带着身份：从系统那一页回来时只换这一张，不整页重画。

    整页重画会连带清掉他刚打进表单却没点"添加"的那句提醒。身份写在 dataset 上而不是
    "页面里第一个 .set-card"那种位置约定——下一个人往前面加一张卡片，位置就变了，
    而那种错法不会报错，只会让他刷不回来。
    """
    out = _run_render_js({"caps": {"notifications": 1, "exactAlarms": 1}})
    assert out["role"] == "reminder-status", out["role"]


_LAYERS_JS_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const ops = JSON.parse(process.argv[3]);
const stale = JSON.parse(process.argv[4] || "[]");

const hidden = [];      // which layer's hide() actually ran, in order
const flow = [];        // what the stack asked history to do: push / replace / go / exit
const snaps = [];       // labelled checkpoints the Python side asserts on

const entries = [{ layers: stale.slice() }];
let idx = 0, pending = false, rec = false;
const lay = (s) => (((s || {}).layers) || []).join(",");
const copy = (s) => ((((s || {}).layers) || []).slice());
const note = (v) => { if (rec) flow.push(v); };
const history = {
  get state() { return entries[idx]; },
  // 照抄浏览器的两处行为：pushState 丢掉"当前位置之后"的前进记录，go() 只留下一个
  // 待交付的 popstate。前一条不模仿，"关掉中间那一层再开一层"就会拿到一份假历史。
  pushState(s) {
    entries.splice(idx + 1);
    entries.push({ layers: copy(s) });
    idx = entries.length - 1;
    note("push:" + lay(s));
  },
  replaceState(s) { entries[idx] = { layers: copy(s) }; note("replace:" + lay(s)); },
  go(d) {
    const t = idx + d;
    if (t < 0 || t >= entries.length) { note("go-dropped:" + d); return; }
    idx = t; pending = true; note("go:" + d);
  },
};

const sandbox = { JSON, console, Array, Object, Math, Error };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const L = sandbox.makeLayerStack(history);
rec = true;                                          // 构造时那次 replaceState 不计入流水

function spy(id, bad) {
  return function () { hidden.push(id); if (bad) throw new Error("close refused"); };
}

for (const op of ops) {
  const k = op[0];
  if (k === "open") L.open(op[1], spy(op[1], op[2] === "bad"));
  else if (k === "close") L.close(op[1]);
  else if (k === "closeTop") { if (op[1] === "detached") { const f = L.closeTop; f(); } else L.closeTop(); }
  else if (k === "flush") { if (pending) { pending = false; L.reconcile(history.state); } }
  else if (k === "back") {                            // 返回键：浏览器自己走一步并派发 popstate
    if (idx === 0) note("exit");
    else { idx -= 1; pending = false; L.reconcile(entries[idx]); }
  } else if (k === "snap") {
    snaps.push({ at: op[1], depth: L.depth(), top: String(L.top()), hidden: hidden.slice() });
  } else throw new Error("unknown op: " + k);
}
process.stdout.write(JSON.stringify({
  hidden, flow, snaps, depth: L.depth(), top: String(L.top()),
  entries: entries.map((e) => (e && e.layers) || []),
}));
"""


def _run_layers_js(ops: list, stale: list = None) -> dict:
    """在 node 里真跑仓库那份 layers.js：按 ops 脚本操作一个假的 history，回收执行流水。

    判据只能问运行时。这一节的锁全是"返回键按下去到底退掉了哪一层"——读源码看得出写法
    对不对，看不出**多步回退只派发一次 popstate**、**开一层时让位层该被换掉而不是压上去**
    这两条行为，而那两条正是这次改动的全部难点。流水（flow）是逐字比对的：多一条、少一条、
    顺序换了都红，这样"绿"只有一种解释。

    与 _run_shell_js 同理，这里不经过 _js()：那把尺子剥注释，而要被执行的是磁盘上那份
    原文件，且读文件的是 node 不是本函数。
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器上没有 node，跑不了这段 JS")
    harness = Path(tempfile.mkdtemp(prefix="layers-js-")) / "harness.cjs"
    harness.write_text(_LAYERS_JS_HARNESS, encoding="utf-8")
    r = subprocess.run(
        [node, str(harness), str(STATIC / "layers.js"),
         json.dumps(ops), json.dumps(stale or [])],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"harness 自己就跑失败了：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def _snaps(out: dict) -> dict:
    return {s["at"]: s for s in out["snaps"]}


def test_the_back_key_closes_one_layer_at_a_time():
    """这次改动的验收标准本身：按一层、再按一层，最后一按才退应用。

    原来是一按就退桌面：壳写的是 canGoBack() ? goBack() : 退，而页面这些层从不产生
    历史条目，所以 canGoBack() 恒假。hidden 记"哪一层的收尾真跑了"，depth 记"栈里还剩
    几层"，两个都要——只看 hidden 会漏掉"层没退但界面关了"，只看 depth 会漏掉
    "层退了却没人关界面"。flow 逐字比对，第三条"exit"钉的是已拍的栈底行为。
    """
    out = _run_layers_js([
        ["open", "settings"], ["open", "setPage"], ["snap", "开着两层"],
        ["back"], ["snap", "第一次按"], ["back"], ["snap", "第二次按"],
        ["back"], ["snap", "第三次按"],
    ])
    s = _snaps(out)
    assert s["开着两层"]["depth"] == 2, s["开着两层"]
    assert s["第一次按"]["hidden"] == ["setPage"], "头一下该只退掉最上面那层（二级页回列表）"
    assert s["第二次按"]["hidden"] == ["setPage", "settings"], "第二下才该关设置弹层"
    assert s["第三次按"]["depth"] == 0, s["第三次按"]
    assert out["flow"] == ["push:settings", "push:settings,setPage", "exit"], \
        f"历史操作对不上（多一条就是多按一次才有反应）：{out['flow']}"


def test_a_drawer_yields_to_the_page_it_navigates_to():
    """从侧栏点进设置是"换页"，不是"叠一层"：历史条目被替换，深度不涨。

    压上去会怎样：栈成了 [侧栏, 设置]，而"关掉侧栏"只能往回退——那一退把刚打开的设置
    一起退掉，症状是"点了记忆，界面闪一下又回到聊天"。所以让位这一档走 replaceState。
    flow 里没有 push:settings,setPage 是这条的牙：把让位分支删掉，那个 push 立刻出现。
    """
    out = _run_layers_js([
        ["open", "sidebar"], ["open", "settings"], ["snap", "从侧栏进了设置"],
        ["back"], ["snap", "退掉设置"], ["back"],
    ])
    s = _snaps(out)
    assert s["从侧栏进了设置"]["hidden"] == ["sidebar"], "侧栏没让位：它正挡在设置前面"
    assert s["从侧栏进了设置"]["depth"] == 1, "设置被压到侧栏上面了，返回键要按两次才回聊天"
    assert out["flow"] == ["push:sidebar", "replace:settings", "exit"], out["flow"]
    assert out["entries"] == [[], ["settings"]], f"历史条目对不上：{out['entries']}"
    assert s["退掉设置"]["depth"] == 0, "关掉设置就该回到聊天，中间不该再有一层「只退侧栏」"


def test_closing_a_layer_below_the_top_rewinds_in_one_popstate_top_down():
    """关中间那一层 = 往回走 N 步，而 N 步只派发一次 popstate。

    所以 reconcile 必须按"目标链"整体对齐、从最上面往下收，不能假设一次只退一层。
    收的顺序是相机→二级页→设置：hideCamera 要停摄像头轨，反过来的话指示灯要等到
    下一次返回才灭（"效果晚了一拍"也算效果没了）。
    """
    out = _run_layers_js([
        ["open", "settings"], ["open", "setPage"], ["open", "camera"], ["snap", "三层"],
        ["close", "settings"], ["flush"], ["snap", "一次回退之后"],
    ])
    s = _snaps(out)
    assert s["三层"]["depth"] == 3, s["三层"]
    assert out["flow"] == ["push:settings", "push:settings,setPage", "push:settings,setPage,camera", "go:-3"], \
        f"没走一次多步回退：{out['flow']}"
    assert s["一次回退之后"]["hidden"] == ["camera", "setPage", "settings"], out["flow"]
    assert s["一次回退之后"]["depth"] == 0, "一次 popstate 只收了一层：剩下两层永远关不掉"


def test_reopening_an_existing_layer_rewinds_instead_of_stacking():
    """已经开着的层再开一次：回到它那一层，不产生第二条历史；已在最上层则什么都不做。

    设置里两个二级页之间来回切就是"最上层"那一档——调用方自己把 DOM 改完了，栈再记
    一条就等于"看一眼角色设定"之后要多按一次返回。逐字比对的 flow 是唯一能同时钉住
    "该有一次 go:-1"和"不该有第二次 push"的写法。
    """
    out = _run_layers_js([
        ["open", "settings"], ["open", "setPage"],
        ["open", "settings"], ["flush"], ["snap", "重开底下那层"],
        ["open", "settings"], ["snap", "重开最上层"],
    ])
    s = _snaps(out)
    assert s["重开底下那层"]["hidden"] == ["setPage"], s["重开底下那层"]
    assert s["重开最上层"]["depth"] == 1, "第二次 open 压出了第二层设置"
    assert out["flow"] == ["push:settings", "push:settings,setPage", "go:-1"], \
        f"重开的那两次动了多余的历史操作（每多一条就是白吃一次返回）：{out['flow']}"


def test_closing_a_layer_that_is_not_open_costs_nothing():
    """没开过的层去关它：不能真的往回走一步。

    这条是"同一层被关两次"的兜底（设置里那行改密码就是先 closeSettings 再点别的）。
    多退的那一步发生在用户看不见的地方，症状是"返回键按一次，跳回刚才那个页面"。
    """
    out = _run_layers_js([["open", "settings"], ["close", "camera"], ["snap", "还是设置"]])
    assert out["flow"] == ["push:settings"], f"关一层却动了历史：{out['flow']}"
    assert out["depth"] == 1 and out["top"] == "settings", out


def test_a_stale_history_entry_does_not_open_a_layer_that_is_not_there():
    """刷新之后历史条目还在，界面却是全新的：构造时那一次 replaceState 把它抹平。

    少了这一步，栈里凭空有"设置"这一层而屏上没有——返回键头一下什么都没发生，要按
    两下才关掉一个根本没开的弹层。这类"第一下没反应"最容易被当成手机卡。
    """
    out = _run_layers_js([["snap", "刚加载"], ["back"]], stale=["settings", "setPage"])
    assert out["snaps"][0]["depth"] == 0, "拿着旧的 state 建栈：返回键头一下会空按"
    assert out["entries"][0] == [], f"当前那条历史没被重置：{out['entries']}"
    assert out["flow"] == ["exit"], f"重置之后栈底就该是栈底：{out['flow']}"


def test_a_layer_that_refuses_to_close_does_not_wedge_the_back_key():
    """某一层的收尾自己抛错，不能把返回键整个卡死。

    hide 里要碰 DOM、要停摄像头轨，抛错不是假想。去掉那个 try 之后异常从 popstate
    监听里逃出去，栈与历史从这一刻起永久错位——此后每次返回都只退半层。所以要一路
    退到目标（depth 归零）才算修好：抛出异常的那层必须先摘掉再往下收。
    """
    out = _run_layers_js([
        ["open", "settings"], ["open", "setPage", "bad"],
        ["close", "settings"], ["flush"], ["snap", "一次回退之后"],
    ])
    assert out["snaps"][0]["hidden"] == ["setPage", "settings"], out["snaps"][0]
    assert out["snaps"][0]["depth"] == 0, "抛错的那层没被摘掉：它把剩下的高度永远占住了"
    assert out["flow"] == ["push:settings", "push:settings,setPage", "go:-2"], out["flow"]


def test_closeTop_survives_being_handed_over_as_a_callback():
    """`const f = Layers.closeTop; f()` 必须照常工作：Esc 那条路就是这么挂上去的。

    写成 closeTop() { this.close(...) } 的话，脱离 this 的调用直接 TypeError，
    而手机上没有控制台——Esc 从此没反应，返回键却一切正常。中间那次 flush 是
    popstate：closeTop 只朝历史发一个请求，收界面的是回退之后那一条路。
    """
    out = _run_layers_js([["open", "settings"], ["closeTop", "detached"],
                          ["flush"], ["snap", "脱离 this"]])
    assert out["snaps"][0]["hidden"] == ["settings"], out["snaps"][0]
    assert out["depth"] == 0, out
    assert out["flow"] == ["push:settings", "go:-1"], out["flow"]


def test_the_page_loads_the_stack_before_any_layer_code_runs():
    """layers.js 要在 app.js 之前加载，而且要真的挂进外壳清单。

    app.js 顶层就调 makeLayerStack()，脚本顺序写反得到的是 ReferenceError——整页 JS
    一起停摆，症状是"手机上一片空白"，比返回键失灵严重得多。sw.js 少一行则是离线时
    外壳缺这一块，网络一断就开不回来。
    """
    html = _html()
    at_layers, at_app = html.find('src="layers.js"'), html.find('src="app.js"')
    assert at_layers >= 0, "index.html 根本没加载 layers.js"
    assert at_app >= 0, "index.html 里没有 app.js？"
    assert at_layers < at_app, "layers.js 排在了 app.js 后面：app.js 顶层那次调用会直接抛错"
    js = _js()
    assert "makeLayerStack(window.history)" in js, "没建栈：返回键还是原来那副样子"
    assert 'addEventListener("popstate"' in js and "Layers.reconcile(" in js, \
        "没接 popstate：界面只在按 × 时收，历史条目却一路涨"
    assert '"layers.js"' in _js("sw.js"), "sw.js 的外壳清单里少了它：离线打开时层栈整个丢失"


def test_every_layer_is_closed_by_the_stack_and_only_by_it():
    """每层的"收起"在源码里只能出现一次，而且必须是被 open() 注册进栈的那一个。

    四段写死的 classList 各数一次出现次数：多出来的一处就是第二个执行者，它关掉界面
    却不退历史——历史里从此多一层，返回键要按两下才关一层。注册那一半（open 的第二
    个参数）钉的是反方向：只把 close 接上、open 没登记 hide，被返回键收掉的层就没人关
    （层从栈里消失了，DOM 还挂着）。
    """
    js = _js()
    for needle in ('$("settings").classList.add("hidden")',
                   '$("cameraModal").classList.add("hidden")',
                   '$("attachMenu").classList.add("hidden")',
                   '$("sidebar").classList.remove("open")'):
        assert js.count(needle) == 1, f"{needle} 在 app.js 里出现 {js.count(needle)} 次：只许栈收的那一处"
    for call in ('Layers.open("settings", hideSettings)',
                 'Layers.open("setPage", showSetList)',
                 'Layers.open("camera", hideCamera)',
                 'Layers.open("attachMenu", hideAttachMenu)',
                 'Layers.open("sidebar", hideSidebar)'):
        assert call in js, f"这一层没把收起的手法登记进栈：{call}"
    for name, lid in (("closeSettings", "settings"), ("closeSetPage", "setPage"),
                      ("closeCamera", "camera"), ("closeSidebar", "sidebar")):
        assert f'Layers.close("{lid}")' in _js_fn(js, name), f"{name}() 没走栈：它关的是界面不是历史"
    assert 'Layers.close("attachMenu")' in _js_fn(js, "setAttachMenu"), \
        "附件菜单的关法没走栈：它一关，历史里就多一条没人负责的条目"


def test_the_camera_button_does_not_close_the_menu_it_came_from():
    """点「拍照」时，收掉附件菜单的必须是栈（让位），不是那颗按钮自己。

    顺序在这里是要命的：关是 history.go(-1)（异步交付），开是 pushState（同步）。
    同一拍里先请求回退再压新条目，落点就不是"拍照"那条——从这一刻起返回键与界面对不上，
    而对不上的那一拍用户什么也看不见。
    """
    js = _js()
    handler = _handler_of(js, "pickCamera")
    assert "openCamera" in handler, f"拍照那颗按钮没接上开层：{handler}"
    assert "setAttachMenu" not in handler, \
        f"按钮自己关了菜单：异步回退紧跟着一次 push，历史会错位：{handler}"


def test_escape_takes_the_same_single_path_as_the_back_key():
    """Esc 只许调 closeTop()，不许自己点名该关哪一层。

    点名就是第二个执行者，而且它一定落后于现实：这次新增的两档（设置里的二级页、改走栈
    的相机）都没进原来那串 if，Esc 对它们一概不理而返回键管——两条路从那天起行为不同，
    下次改层的人只会去改"看起来对的那条"。
    """
    js = _js()
    at = js.index('document.addEventListener("keydown"')
    body = _up_to_matching_brace(js[js.index("{", at):])
    assert "Layers.closeTop()" in body, f"Esc 没接到层栈上：{body}"
    for named in ("closeSettings", "closeSidebar", "closeCamera", "setAttachMenu",
                  "cameraModal"):
        assert named not in body, f"Esc 还在自己点名该关哪一层（{named}）：它与返回键成了两套真相"


def test_the_update_card_stays_out_of_the_stack():
    """底部那张卡片不进层栈，而且理由要能被机器查到。

    它的"今天不再问"只该由他自己按掉。进了栈，"被别的层顶掉"也算收起，日期戳就被偷偷
    写上——在今天剩下的时间里再也不问，而他根本没看到第二眼。所以：没有
    Layers.open("update")，让位层名单里也没有它；收尾仍然是那两颗按钮。
    """
    js = _js()
    assert 'Layers.open("update"' not in js, "卡片进栈了：被顶掉也会写下「今天问过了」"
    assert 'const YIELDS = ["sidebar", "attachMenu"]' in _js("layers.js"), \
        "让位层名单改了：这条锁与 app.js 里那段理由必须同时改，否则注释与代码各说一遍"
    assert "hideUpdateSheet" in _handler_of(js, "updateLaterBtn"), "「稍后」不再走那份收尾：日期戳没人写"


# ---------- 首屏的串行网络链（2026-09-22：打开网页版到看见对话界面） ----------

_BOOT_JS_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const realFns = fs.readFileSync(process.argv[2], "utf8");
const SC = JSON.parse(process.argv[3]);

const PREAMBLE = `
const flow = [];
const statuses = [];
let inflight = 0, peak = 0;
const state = { messages: [], sessions: [], providers: [], me: { role: "user" } };
const pref = { sessionId: SC.sessionId || "", provider: "p1" };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function beat(tag, ms, failStatus) {
  flow.push("start:" + tag);
  inflight += 1; if (inflight > peak) peak = inflight;
  await wait(ms);
  inflight -= 1;
  if (failStatus) { flow.push("fail:" + tag); const e = new Error(tag + " boom"); e.status = failStatus; throw e; }
  flow.push("end:" + tag);
}
function setStatus(text, isErr) { statuses.push((text || "") + (isErr ? "!" : "")); }
function needsAuth(err) { flow.push("needsAuth:" + err.status); return err.status === 401; }
function currentProvider() { return SC.hasProvider === false ? null : { id: "p1" }; }
// 三个假叶子：只照抄真函数对外可见的那一处副作用（loadModels 拿到可用模型会
// setStatus("") 擦掉旧红字），因为本轮要钉的正是"批次里谁最后写状态条"。
async function loadModels() { await beat("models", SC.modelsMs, SC.modelsFail); setStatus("", false); }
async function loadSessions() { await beat("sessions", SC.sessionsMs, SC.sessionsFail); }
async function ensureSession() { flow.push("call:ensureSession"); }
const API = {
  async getSession(id) {
    await beat("getSession", SC.historyMs, SC.historyFail);
    return { messages: SC.messages || [] };
  },
  async fileBlobUrl(id) {
    flow.push("start:file:" + id);
    inflight += 1; if (inflight > peak) peak = inflight;
    await wait((SC.fileMs || {})[id] || 5);
    inflight -= 1;
    if ((SC.fileFail || []).indexOf(id) >= 0) { flow.push("fail:file:" + id); throw new Error("thumb 404"); }
    flow.push("end:file:" + id);
    return "blob:" + id;
  },
};
async function drive() {
  if (SC.entry === "hydrate") {
    const atts = SC.atts || null;
    let threw = null;
    try { await hydrateImageUrls(atts); } catch (e) { threw = String((e && e.message) || e); }
    return { flow: flow, statuses: statuses, peak: peak, threw: threw,
      urls: (atts || []).map((a) => (a.url === undefined ? null : a.url)) };
  }
  let threw = null, thrownStatus = null;
  flow.push("begin");
  try { await loadServerData(); }
  catch (e) { threw = String((e && e.message) || e); thrownStatus = (e && e.status) || null; }
  flow.push("settle");
  return { flow: flow, statuses: statuses, peak: peak, threw: threw,
    thrownStatus: thrownStatus, messages: state.messages.length, pointer: pref.sessionId };
}
`;

const sandbox = { setTimeout, console };
vm.createContext(sandbox);
const script = "const SC = " + JSON.stringify(SC) + ";\n" + PREAMBLE + "\n" + realFns + "\ndrive()";
const done = vm.runInContext(script, sandbox);
done.then((r) => process.stdout.write(JSON.stringify(r)),
          (e) => { console.error(e); process.exit(2); });
"""


def _fn_text(js: str, name: str) -> str:
    """`async function name(...) { ... }` 整段，**带 async 前缀**。

    _js_fn() 从 `function` 关键字起切，async 被切在外面——直接拿去执行会得到一个
    含 await 的非 async 函数，node 报的是语法错，而错话会说成"harness 自己坏了"，
    看不出是被测代码的形状变了。
    """
    text = _js_fn(js, name)
    at = js.index(f"function {name}(")
    return ("async " + text) if js[:at].endswith("async ") else text


def _run_boot_js(scenario: dict) -> dict:
    """在 node 里真跑仓库那份 loadServerData / restore / hydrateImageUrls。

    为什么必须真跑：这次改的东西**读源码读不出结论**。"三路之间没有 await"只说明
    写法，不说明行为——`Promise.all` 一个 reject 就立刻返回、剩下两路还在改 state
    却再没人等，那形状在文本上完全合规，症状却是本仓最贵的那一类（界面空着且不报错）。
    只有运行时能回答"到底谁等谁、坏一路时另外两路跑没跑完、状态条最后是谁写的"。
    叶子（loadModels/loadSessions/ensureSession/API）在这里是假的，因为真叶子要整个
    DOM 和整个后端；被执行的**控制流**是仓库那一份，逐字取自 _js()。

    与 _run_shell_js / _run_layers_js 同理：读文件的是 node，不是本函数；交给 node 的
    那段函数文本经 _js() 那把尺子（剥注释），执行不受影响。
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器上没有 node，跑不了这段 JS")
    js = _js()
    snippet = "\n".join(_fn_text(js, n) for n in
                        ("hydrateImageUrls", "restore", "loadServerData"))
    d = Path(tempfile.mkdtemp(prefix="boot-js-"))
    (d / "fns.js").write_text(snippet, encoding="utf-8")
    (d / "harness.cjs").write_text(_BOOT_JS_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(d / "harness.cjs"), str(d / "fns.js"),
                        json.dumps(scenario)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, f"harness 自己就跑失败了：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def _at(out: dict, tag: str) -> int:
    assert tag in out["flow"], f"{tag} 压根没发生：{out['flow']}"
    return out["flow"].index(tag)


def test_the_three_boot_reads_go_out_at_the_same_time():
    """开门那三趟（models / sessions / 历史）并发发出去，第四趟（建会话）仍排最后。

    flow 是逐字的时间线：串行的写法是 start:models,end:models,start:sessions,…，
    并发才是三个 start 连在一起。peak==3 是"同一刻在途三条"的第二种说法（万一有人
    把排队藏进 helper 里，顺序看不出来，同时在途数瞒不住）。
    第二趟跑的是"本机没有指针"那一路：ensureSession 必须排在 models 与 sessions 两个
    end 之后——它读 currentProvider()（loadModels 纠正过的 pref.provider）和
    state.sessions（loadSessions 的结果），这两样没回来就建会话，会拿错模型、
    或把清单里已有的那条再建一遍。restore 在那一路不发请求（没指针），所以三个 start
    只有两个，这正好也是"没指针就别多发一枪"的口径。
    """
    out = _run_boot_js({"sessionId": "s-1", "modelsMs": 40, "sessionsMs": 30,
                        "historyMs": 20,
                        "messages": [{"role": "user"}, {"role": "assistant"}]})
    assert out["threw"] is None, out
    assert out["flow"][1:4] == ["start:models", "start:sessions", "start:getSession"], \
        f"三路不是同时发出去的：{out['flow']}"
    assert out["peak"] == 3, f"同一刻在途的不是三条（退回串行就只剩一条）：{out['peak']}"
    assert out["messages"] == 2, f"历史读回来了却没落进 state：{out}"

    fresh = _run_boot_js({"modelsMs": 40, "sessionsMs": 20})
    assert fresh["flow"][-2:] == ["call:ensureSession", "settle"], fresh["flow"]
    for end in ("end:models", "end:sessions"):
        assert _at(fresh, "call:ensureSession") > _at(fresh, end), \
            f"建会话抢在 {end} 前面了：那一趟读的还是空清单"


def test_an_existing_pointer_skips_creating_a_session():
    """本机已经有指针时不该多发那一枪 POST——省一趟隧道，也省得把已有会话重置成空。

    ensureSession 的判据 `!pref.sessionId` 由 loadServerData 在批次**之后**才评：
    那时 restore() 已经跑完，它可能因为服务端回 404 把指针归零了（见 test_a_pointer_…），
    所以"要不要新建"用的不是开机那一刻的本机值，而是查过历史之后的值。
    """
    kept = _run_boot_js({"sessionId": "s-1", "modelsMs": 10, "sessionsMs": 10,
                         "historyMs": 10, "messages": [{"role": "user"}]})
    assert "call:ensureSession" not in kept["flow"], kept["flow"]
    assert kept["messages"] == 1 and kept["pointer"] == "s-1", kept


def test_a_failed_boot_read_waits_for_the_others_before_it_reports():
    """并行之后最贵的一种坏法：一路先失败，另外两路还在跑却没人等——boot 拿着半空的
    状态渲染完，晚到的历史填进 state 时已经没人重画，界面就"安静地空着"。

    所以这里等的是 Promise.allSettled（全部落定）而不是 Promise.all（第一个坏消息）。
    判据是时间线的形状：settle 必须是**最后一格**——换成 Promise.all，fail:models
    一落地（这一路故意最快）settle 就挤到中间去，end:sessions / end:getSession 掉在
    它后面，这条红。历史那一路照样写进了 state（不回滚：谁坏了说谁，其余照旧露出来）；
    抛给 boot 的那个 reason 是**按数组顺序**的第一个失败（第二趟：sessions 先在时间里
    坏掉，报的仍是 models，与旧的串行行为一致），于是 needsAuth(e) 那个唯一出口原样不动。
    """
    out = _run_boot_js({"sessionId": "s-1", "modelsMs": 5, "modelsFail": 503,
                        "sessionsMs": 40, "historyMs": 30,
                        "messages": [{"role": "user"}, {"role": "assistant"}]})
    assert out["flow"][-1] == "settle", f"有请求掉在 settle 之后：{out['flow']}"
    assert "end:sessions" in out["flow"] and "end:getSession" in out["flow"], out["flow"]
    assert _at(out, "end:getSession") < _at(out, "settle"), out["flow"]
    assert out["messages"] == 2, f"另一路失败却把已读回的历史丢了：{out}"
    assert out["thrownStatus"] == 503 and "models" in out["threw"], out

    both = _run_boot_js({"sessionId": "s-1", "modelsMs": 40, "modelsFail": 503,
                         "sessionsMs": 5, "sessionsFail": 500, "historyMs": 10})
    assert both["thrownStatus"] == 503, \
        f"报的是**时间上**第一个坏消息，不是数组顺序那个：{both['thrownStatus']}"


def test_a_broken_history_read_still_leaves_its_error_line_on_the_screen():
    """历史读失败时那句红字必须是**最后**写下的，否则等于什么都没发生。

    并发之后状态条是谁后回来谁抢：loadModels 成功时要 setStatus("") 擦掉上一轮的旧
    红字，如果 restore 在自己的 catch 里就地写"会话加载失败"、而 models 慢一步回来，
    那句实话就被擦掉了——症状正是"聊天区空着、也不报错"。所以 restore 只把错交出来，
    写由 loadServerData 在三路都落定之后做。这里故意让历史最快（5ms）、models 最慢
    （40ms）：谁在批内就地写，谁就红。
    """
    out = _run_boot_js({"sessionId": "s-1", "modelsMs": 40, "sessionsMs": 20,
                        "historyMs": 5, "historyFail": 500})
    assert out["threw"] is None, f"restore 不该把失败抛出去（那会盖过别的路的实话）：{out}"
    assert out["statuses"] and out["statuses"][-1].startswith("会话加载失败"), \
        f"最后写在状态条上的不是那句实话：{out['statuses']}"
    assert "" in out["statuses"], f"loadModels 那句擦除没发生，测例自己坏了：{out['statuses']}"


def test_a_pointer_the_server_no_longer_knows_still_becomes_a_new_chat():
    """指针指的会话在服务端没了：归零指针、清屏、按"新对话"补一条——不许留一句错误。

    404 这一路跨过了并发的那个边界："要不要新建"发生在批次之后，restore 的 404 处理
    才顺得下来。少这一步，用户守着一个空聊天区，发送键点下去还在往那个已经不存在的
    id 上写。状态条上只该有 loadModels 那句擦除（""），没有任何红色错误。
    """
    out = _run_boot_js({"sessionId": "gone", "modelsMs": 10, "sessionsMs": 10,
                        "historyMs": 10, "historyFail": 404})
    assert out["threw"] is None and out["statuses"] == [""], out
    assert out["pointer"] == "" and out["messages"] == 0, out
    assert _at(out, "call:ensureSession") > _at(out, "fail:getSession"), \
        "补建新会话抢在\"这条指针已经作废\"之前：它建完立刻又被下一句归零"


def test_a_401_from_the_history_read_still_reaches_the_auth_exit():
    """401 仍然走 needsAuth 那道出口，而不是被并发改成一句普通红字。

    boot 的那个出口决定"露登录层还是露外壳"，几路失败都得汇到它那儿。restore 把错误
    对象交出来（不自己吞掉也不自己写条），needsAuth 由批次之后统一调一次。
    """
    out = _run_boot_js({"sessionId": "s-1", "modelsMs": 10, "sessionsMs": 10,
                        "historyMs": 5, "historyFail": 401})
    assert "needsAuth:401" in out["flow"], out["flow"]
    assert not [s for s in out["statuses"] if s.startswith("会话加载失败")], out["statuses"]


def test_history_thumbnails_are_fetched_at_once_and_land_on_their_own_row():
    """N 张图 = N 趟串行往返那段（每趟 300~430ms）改成同时发，但每张仍写自己那条 url。

    延迟是**反序**的（a1 最慢、a3 最快）：并发时到达顺序与请求顺序相反，而三条 url
    必须还是各归各的——"先收齐再整批赋同一个值"那种写法在这里就红了。
    三个 start 连排管"没排队"，peak==3 管"不是靠嵌套回调装出来的并发"。
    """
    out = _run_boot_js({"entry": "hydrate", "atts": [
        {"kind": "image", "id": "a1"}, {"kind": "image", "id": "a2"},
        {"kind": "image", "id": "a3"}], "fileMs": {"a1": 30, "a2": 20, "a3": 10}})
    assert out["flow"][:3] == ["start:file:a1", "start:file:a2", "start:file:a3"], out["flow"]
    assert out["peak"] == 3, out
    assert out["urls"] == ["blob:a1", "blob:a2", "blob:a3"], \
        f"到达顺序反了就把 url 串错了（每张必须落回自己那条）：{out}"
    assert out["threw"] is None, out


def test_one_broken_thumbnail_costs_only_that_thumbnail():
    """原来那句 catch 的语义是"这一张取不到就只显示文件名"，不许变成"整批放弃"。

    a2 抛错，a1/a3 仍各自拿到 url、函数本身不 reject：catch 挂在每张自己的链上。
    把 catch 挪到整批（Promise.all 外面套一个 try），这一条立刻红——那次的代价不是
    少一张图，是历史里所有缩略图一起没了而没人说话。
    """
    out = _run_boot_js({"entry": "hydrate", "fileFail": ["a2"], "fileMs": {"a2": 5},
                        "atts": [{"kind": "image", "id": "a1"}, {"kind": "image", "id": "a2"},
                                 {"kind": "image", "id": "a3"}]})
    assert out["threw"] is None, f"一张坏图把整批带崩了：{out}"
    assert out["urls"] == ["blob:a1", None, "blob:a3"], \
        f"坏的那一张连累了别人（或它自己没被跳过）：{out}"


def test_hydration_still_asks_only_for_missing_images():
    """非图片、已经有 url 的、以及压根没有附件的历史：一枪都不该发。

    这条与并发无关，是原来 if 里那半句的语义——重排成 filter/map 时最容易顺手把条件
    丢掉，症状是每次切会话都把已有的图重新下载一遍（走隧道就是几百毫秒一张）。
    """
    out = _run_boot_js({"entry": "hydrate", "atts": [
        {"kind": "text", "id": "t1"}, {"kind": "image", "id": "a2", "url": "blob:cached"},
        {"kind": "image", "id": "a3"}]})
    assert [f for f in out["flow"] if f.startswith("start:file:")] == ["start:file:a3"], out["flow"]
    assert out["urls"] == [None, "blob:cached", "blob:a3"], out
    empty = _run_boot_js({"entry": "hydrate"})
    assert empty["threw"] is None, f"没有附件的历史直接把整次 restore 带崩了：{empty['threw']}"
    assert empty["flow"] == [] and empty["urls"] == [], empty


def test_the_boot_chain_does_not_slip_back_into_one_await_per_request():
    """文本锁兜底：跑不了 node 的机器上（那几条会 skip）也得能抓住"退回串行"。

    行为锁管"并发得真成立"，这把尺子管"三路之间不许再横着放 await、状态条不许有人
    在批内就地写"。判的是 _js()（已剥注释），所以"把 await 藏回注释里"喂不绿它。
    """
    js = _js()
    body = _function_body(js, "loadServerData")
    for name in ("loadModels", "loadSessions", "restore"):
        assert f"await {name}()" not in body, f"{name}() 又变成单独一等"
    marks = [body.index(f"{n}(") for n in ("loadModels", "loadSessions", "restore")]
    assert "await" not in body[min(marks):max(marks)], \
        "三路之间横着 await：写在同一个数组里也是排队"
    assert "Promise.allSettled" in body, "退回 Promise.all 了：一路失败就没人等其余两路"
    assert re.search(r"\bthrow\b", body), "没人把失败交回 boot 那个 needsAuth 出口"
    assert body.index("Promise.allSettled") < body.index("ensureSession"), \
        "建会话不再排最后：它读的是这三路的产物"
    assert "会话加载失败" in body, "restore 交出来的错误没人写了（空聊天区 + 一句实话那条）"

    hy = _function_body(js, "hydrateImageUrls")
    assert "Promise.all" in hy and "await API.fileBlobUrl" not in hy, "缩略图又一张张 await 了"
    assert hy.count("await") == 1, f"每张图自己 await 一次就是串行：{hy}"
    assert "setStatus" not in _function_body(js, "restore"), \
        "restore 又在批内就地写状态条：慢一步回来的 loadModels 会把它擦掉"


def test_a_stale_page_kicks_itself_once_and_only_once():
    """HTML 允许晚 30 秒，代价由这段兜：旧骨架配新脚本时自己跳一次，跳完就停。

    三条一起钉，因为漏哪一条症状都不一样：
    - 判据不许在"任何一边拿不到"时瞎跳（没登录、或这份 HTML 是上一次部署留下的，
      压根没有 window.__ASSETS__）——那会让人反复回到登录页；
    - 问的那一个文件必须是刻意不缓存的 `sw.js`，问 `/app/` 等于问缓存要答案；
    - 已经为"服务端那一版"跳过一次就不许再跳，否则对不上就一直跳，比原来的空白页更糟。
    """
    js = _js()
    stale = _function_body(js, "staleBuild")
    assert "Boolean(local)" in stale and "Boolean(server)" in stale, \
        "判据不再要求两边都有值：拿不到版本信息的时候它会瞎跳"
    assert "!==" in stale, "两边相等也算旧：那每次开页面都要重载一次"

    check = _function_body(js, "checkBuild")
    assert 'fetch("sw.js' in check, "不问 sw.js 了：改问一个会被缓存的东西，等于问缓存要答案"
    assert 'cache: "no-store"' in check, "没关缓存：这一问可能又拿回旧的那一份"
    assert "res.text()" in check, "响应不按文本读：正则取不到水印，对账静默失效"
    assert "const V = " in check, "读的不是 sw.js 里那一个水印：它换了写法就悄悄不匹配了"
    assert "jumped === live" in check, "没有止损点：对不上就一直重载"
    assert "location.replace" in check, "跳不动了：旧页面还是留在屏幕上"

    boot = _function_body(js, "boot")
    assert "checkBuild(window.__ASSETS__)" in boot, \
        "boot 不再对账：改版后那 30 秒的旧页面没人管了"
