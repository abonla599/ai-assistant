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
    它在这份文件上从来就没工作过：app.js 里 `safeFilename()` 那句
    `.replace(/[\\/:*?"<>|\r\n]+/g, " ")` 的正则字面量带一个裸 `"`，引号状态一开着就
    一路跨行带到文件尾，其后 1400 多行全被当成"还在字符串里"原样抄走。实测旧写法剥完
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
    # 这一小段就是 app.js 的 safeFilename() 那一行的形状。
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


def test_the_settings_sheet_has_a_chat_pane_that_everyone_can_reach():
    """模型选择搬进设置后，那一页必须对普通用户也开着。

    它原先在顶栏，人人可用；而设置里唯一列模型的那页（模型服务）是管理员专属
    （后端 7 条路由都要管理员）。搬过去却只对管理员可见，等于把普通用户换模型的
    能力整个删掉——那不会报错，只会让人以为"这里没有模型可换"。
    """
    html = _html()
    assert '<button class="tab" data-tab="chat">' in html, "设置里没有「当前会话」这一页签"
    chat = re.search(r'<section class="pane" data-pane="chat">[\s\S]*?</section>', html)
    assert chat, "找不到 data-pane=\"chat\" 那一页"
    assert "data-admin-only" not in chat.group(0), "这一页对普通用户收起来了：他会没有模型可换"
    assert 'id="modelSel"' in chat.group(0) and 'id="exportBtn"' in chat.group(0), \
        "模型选择或导出没真的搬进来（只在顶栏删掉了）"



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
    about = re.search(r'data-pane="about"[\s\S]*?</section>', html).group(0)
    assert 'id="themeBtn"' in about, "主题按钮从侧栏搬走之后没落到设置弹层里"
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



def test_admin_only_surfaces_are_marked_in_html_and_swept_by_role():
    """providers 转管理员之后，普通用户点进「模型服务」就是一个 403。

    约定是一条属性（data-admin-only）+ JS 里一处统一开关，而不是散落的 if：
    以后新加管理员专属控件只要带上这条属性就自动纳入，不必再改 app.js，也
    不会"改了三处漏一处"。
    """
    html = _html()
    js = _js()
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

    发出去的后果不是报错本身，而是那句"记忆服务不可用"——服务明明好着，只是
    他没权限，用户于是去重启后端，而重启完全治不了这件事。
    """
    js = _js()
    body = _function_body(js, "loadAbout")
    assert "memoryStats" in body, "关于页已经不读记忆统计了？那这条契约该删还是该改，得有人说清"
    assert "isAdmin()" in body and body.index("isAdmin()") < body.index("memoryStats"), \
        "统计请求没有按角色分流"


def test_a_stale_token_does_not_read_like_a_first_run():
    """401 有两种，糊成一句就把人支使去填一个已经填对的框。

    本机压根没存过令牌 = 首启，该引导他注册；存过却被拒 = 管理员撤销或轮换过，
    再说"请填写口令"就是让人反复重试同一个废令牌。
    """
    js = _js()
    body = _function_body(js, "needsAuth")
    assert "需要访问口令" not in js, "旧的合并文案还在，两种 401 仍是一句话"
    assert re.search(r"err\.status\s*[!=]==\s*401", body), "needsAuth 只该管 401：403 是身份够了、角色不够"
    assert "403" not in body
    assert re.search(r'pref\.token', body), "未按本机是否已有令牌分叉"
    assert "失效" in body and "注册" in body, "两条分支的措辞都得在场"


def test_registration_locks_its_button_while_the_request_is_in_flight():
    """登录/注册没有在途闸门 = 手机双击发出第二个 POST。

    第二下拿回的是"该用户名已存在"（`auth.py` 里那句实话，措辞换过一次：从前写作"用户名
    已被占用"）或一次多余的 401：界面于是把一个已经成功的
    人标成红色失败，两次调用还一起抢 pref.token 的写入与
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
    # 收尾动作的先后是硬约束：先清输入框再写 pref.token 的话，中途抛异常就把
    # 唯一一次拿到令牌的机会连同输入一起丢了。
    assert tail.index("pref.token = res.token") < tail.index('$("authPass").value = ""'), \
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
    assert re.search(r"^  setStatus\(pref\.token", code, re.M), \
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
