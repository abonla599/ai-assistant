"""官网（`/`）的契约。

它和 PWA 同源、同一个 StaticFiles 类,所以缓存与 CSP 的行为必须和 /app 一致——
Cloudflare 已设「尊重现有标题」,源站一不表态,改版在朋友那边就是"改了没生效"
（2026-09-17 那次线上界面改版就是这么消失 80 分钟的）。
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app
from app.web.web_router import IMMUTABLE, asset_token

client = TestClient(app)


# ---------- 1. 挂载与不回归 ----------

def test_root_serves_the_site_with_the_same_cache_policy_as_app():
    """官网首页与 /app、/admin 是同一套页面口径——包括"允许晚 30 秒"这一笔。

    判据是拿 /app/ 的头来比，不是抄一份字面量：三处页面各自的价钱必须一起动，
    动一处就是第二份真相。窗口本身的大小钉在 tests/test_asset_versioning.py。
    """
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["cache-control"] == client.get("/app/").headers["cache-control"], \
        "官网首页的缓存口径和 PWA 走岔了"
    assert "frame-src 'none'" in res.headers["content-security-policy"]


def test_site_assets_are_public_and_uncached():
    """`/site/...` 是官网资源。公开是刻意的(css 和截图不含任何用户数据)。

    不带水印的那一个地址必须回 no-cache —— Cloudflare 已设「尊重现有标题」,
    源站不表态就等于让别人的缓存策略替我们决定。带当下水印的那一个才允许长缓存,
    两条一起写是因为它们是一对：省掉回源的收益,不能拿"把人钉在旧文件上"去换。
    """
    res = client.get("/site/site.css")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    fresh = client.get("/site/site.css", params={"v": asset_token()})
    assert fresh.headers["cache-control"] == IMMUTABLE, "官网资源拿不到长缓存：首屏还是每趟回源"
    # 整串相等而不是 in:`/` 那半只用了 in,两边合起来才真正钉住"页面与资源的头
    # 是同一组",而这一组字面量在 web_router 里只有一份(site_headers)。
    assert res.headers["content-security-policy"] == "frame-src 'none'; object-src 'none'"
    assert fresh.headers["content-security-policy"] == "frame-src 'none'; object-src 'none'"


def test_unknown_paths_still_get_the_framework_json_404():
    """没被认领的路径仍是框架那副 JSON 404,没被静态兜底吞掉。

    但别把这条当成 R7 的防线:它抓不住 `app.mount("/")` 本身 —— StaticFiles
    找不到文件时抛的是 HTTPException(404),FastAPI 同样把它序列化成
    application/json,换上根 Mount 这条照样绿(本轮 RED 就是这么露馅的)。
    真正抓得住根 Mount 的是另外两条:test_trailing_slash_semantics_are_untouched
    (`/health/` 只有 redirect_slashes 还活着才回 200;同一条语义的安全侧由
    test_auth_endpoints.py:296 钉)与 test_site_assets_are_public_and_uncached
    (`/site/site.css` 得真有这么个前缀,根 Mount 只会拿它去找 SITE_DIR 下的
    site/site.css)。
    """
    res = client.get("/no-such-page-anywhere")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")


def test_the_json_status_page_is_gone():
    """旧接口回的是 {"status":"running","service":...,"version":"1.0.0"}。

    原来这里扫的是子串 `"status"`，2026-09-19 它误伤了自己：复制链接的提示用了
    `role="status"`（那是给读屏用户的实时区域，该留）。改成扫那段 JSON 独有的字段，
    钉的还是同一件事——根路径不该再回机器话。
    """
    page = client.get("/").text
    for marker in ('"service"', '"default_model"', '"version": "1.0.0"'):
        assert marker not in page, f"根路径还在回那段机器话：{marker}"


def test_app_admin_health_and_docs_still_work():
    """第一轮变异检验证明:真正抓得住"挂载位置放错"的是这一条,不是别的。"""
    assert client.get("/app/").status_code == 200
    assert client.get("/admin/").status_code == 200
    health = client.get("/health")
    # 判据是"答得出那张 checks 表"，不是某个具体的 status 值：CI 里没有真密钥，
    # 钉死 ok 会让这条锁随配置漂移。
    assert health.status_code == 200 and health.json()["checks"]
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_trailing_slash_semantics_are_untouched():
    """官网上线不许动 redirect_slashes:`/health/` 仍应被引导回 /health。"""
    assert client.get("/health/").status_code == 200


# ---------- 2. 文案红线（诚实账）----------
# 每条都对应 spec §4 的一行证据。页面写谎比不写更糟：来的人照着做完发现对不上,
# 就再没有第二次机会。

def _page() -> str:
    return client.get("/").text


def test_no_admin_entry_on_the_official_page():
    assert "/admin" not in _page(), "把管理面地址印到官网上,等于白送攻击面"


def test_no_desktop_installer_claims():
    page = _page()
    for banned in ("Flutter", "Setup.exe", "AI智能助手_Setup", "Program Files"):
        assert banned not in page, f"{banned} 是 2026-09 之前的桌面版文案,已经不成立"


def test_invite_code_is_stated_as_not_needed():
    page = _page()
    assert "不需要邀请码" in page
    assert "向作者要" not in page, "v0.11 起自助注册,这句会把人挡在门外"


def test_what_stays_unavailable_and_what_became_real():
    """代码执行与扫描版 PDF 仍留在「当前未开启」；联网搜索 2026-09-22 起是现成能力。

    两半都要钉：
    - 还开不了的东西不许被写成现成的（"沙箱"仍禁在正文）；
    - 已经开得了的东西不许继续装作开不了——官网低估自己也是不诚实，而且这条一旦
      反向钉住，将来谁把搜索源改回坏的那条路，正文与代码就当场对不上。
    节的边界按 `<section id="not-now">` 这个标签算，不按「当前未开启」这四个字算：
    正文里一句指引会把锚点提前，那样 head 就悄悄漏掉真那一节（Task 3 实拍轮露过馅）。
    """
    page = _page()
    anchor = page.find('<section id="not-now">')
    assert anchor != -1, "没有「当前未开启」这一节"
    end = page.find("</section>", anchor)
    assert end != -1, "「当前未开启」那一节没闭合"
    body = page[anchor:end]
    for term in ("代码执行", "扫描版 PDF"):
        assert term in body, f"「{term}」仍应在该节里说明"
    assert "联网搜索" not in body and "能查实时信息" not in body,         "搜索已经能用了，还挂在「当前未开启」里就是低估"
    head = page[:anchor] + page[end:]
    assert "沙箱" not in head, "「沙箱」被当成现成能力写进了正文"
    can = head.find('<section id="can-do">')
    can_end = head.find("</section>", can)
    assert can != -1 and can_end != -1, "找不到「已有功能」那一节"
    assert "能查实时信息" in head[can:can_end], "搜索没被写进「已有功能」"
    assert "来源" in head[can:can_end] and "查不到" in head[can:can_end],         "那条卡片必须自带限定：结果附来源、查不到就说查不到"


def test_the_alarm_permission_claim_matches_the_manifest():
    """官网那句关于「精准闹钟」的话必须与清单里真正声明的那条权限是同一件事。

    2026-09-22 之前两处都写着"不申请"。这类分裂不需要想象力：改清单的人不会想起去改官网，
    而官网谎了对外一声不响——没人会在决定装不装的时候去翻 AndroidManifest。所以这条从清单
    反向推页面（页面那句话只是它的投影），同时要求页面必须提这件事一次：否则"把那句谎删掉"
    也能让这条锁悄悄绿掉。
    """
    manifest = (Path(__file__).resolve().parents[2]
                / "android" / "app" / "src" / "main" / "AndroidManifest.xml")
    declared = "SCHEDULE_EXACT_ALARM" in manifest.read_text(encoding="utf-8")
    page = _page()
    denial = re.search(r"不(获取|申请)(精确|精准)闹钟", page)
    assert "精准闹钟" in page, "官网对这件事一个字不提了：那句承诺悄悄消失也是谎"
    assert bool(denial) is (not declared), (
        f"清单声明了精准闹钟权限={declared}，官网却在说"
        f"「{denial.group(0) if denial else '（没有否认）'}」：两处得说同一件事")


def test_the_android_button_downloads_through_our_own_endpoint():
    """点「安卓版」要直接落盘，而不是把人丢到 GitHub 页面上自己找那颗按钮。

    页面里不许留任何带版本号的资产名：下一次发版它就腐烂（这条判据从原来那条
    "指向 releases/latest" 的锁继承下来，换了方向但换了理由——现在指向的是我们自己的
    代理端点，版本号那件事全部留在服务端那份 10 分钟缓存里判断）。
    """
    page = _page()
    button = re.search(r'<a[^>]*class="btn[^"]*"[^>]*href="([^"]+)"[^>]*>\s*安卓版', page)
    assert button, f"找不到安卓版那颗按钮：{page[:200]}"
    assert button.group(1) == "/site/android.apk", f"按钮指向 {button.group(1)}，不是我们自己的下载端点"
    assert not re.search(r"ai-assistant-[0-9][\d.]*\.apk", page), "页面里出现了带版本号的资产名"
    assert "github.com/abonla599/ai-assistant" in page, "源码入口还在，别顺手把它也删了"


def test_the_page_never_states_a_version_number():
    """页脚原先写着「当前版本 v0.13」,而 v0.14 已经在昨天发出去了——那句话现在就是谎。

    和上一条同一个形状:凡是能从别处实时读到、且会自己变的事实,不抄进静态页面。
    版本号在 GitHub Releases 那一页,那个链接不会腐烂。
    """
    import re
    hits = re.findall(r"v\d+\.\d+", _page())
    assert not hits, f"页面硬编码了版本号，下一次发版它就变成谎：{hits}"


def test_domain_spelling():
    """域名只差一个字母,错一个就是别人的站(或一个不存在的站)。

    `feverner` 是 2026-09-20 真写进过发布说明的那一种（两个字母换了位置），
    当时的清单里没有它，所以这条锁放过了它——清单要跟着栽过的跟头长。
    """
    page = _page()
    assert "ai.fenever.xyz" in page
    for wrong in ("feverver", "fenerver", "fenevrr", "feverless", "feverner", "feniwer"):
        assert wrong not in page, f"域名拼错：{wrong}"


# ---------- 3. 零外部请求 ----------

def test_no_third_party_subresources():
    """样式和图片必须都在站内。第三方 src 既是外部请求、也是别人挂了我们不知道的
    东西(它今天还在,明天不一定)。"""
    import re
    page = _page()
    for src in re.findall(r'<(?:img|link|script)[^>]*\b(?:src|href)="([^"]+)"', page):
        assert not src.startswith("http"), f"外部子资源:{src}"
        assert not src.startswith("//"), f"协议相对的外部子资源:{src}"


def test_absolute_links_only_to_the_two_hosts_we_own():
    import re
    page = _page()
    allowed = ("https://ai.fenever.xyz", "https://github.com/abonla599/ai-assistant")
    for href in re.findall(r'href="(https?://[^"]+)"', page):
        assert href.startswith(allowed), f"链到了别人的地方:{href}"


def test_scripts_are_same_origin_only():
    """2026-09-19 改口：原来这条叫"零 JavaScript"，轮播和滚动渐显把它作废了。

    留下来的红线是**零外部依赖**：脚本只许从 /site 下自己拿，不许出现任何
    第三方 src、内联事件处理器或 import()。CDN 上"今天还在、明天不一定"的东西
    不该出现在一个自己托管的官网上。
    """
    page = _page()
    # 水印后缀见 tests/test_asset_versioning.py；这条只管"脚本从哪来"，把 ?v= 剥掉再看。
    srcs = [s.split("?")[0] for s in re.findall(r'src="([^"]+)"', page)]
    assert "/site/site.js" in srcs, "交互脚本要走自己的 /site 前缀"
    for bad in ("<script src=\"http", "<script src='http", "import(", "onclick=",
                "onload=", "addEventListener(\"click\",window."):
        assert bad not in page, f"外部依赖或内联事件处理器：{bad}"


def test_landing_page_has_the_six_sections():
    page = _page()
    for sid in ("can-do", "shell-only", "start", "not-now", "shots", "faq"):
        assert f'id="{sid}"' in page, f"缺这一节:{sid}"


# ---------- 4. 实拍图：存在、被引用、不许把页面压垮 ----------

def test_four_real_screenshots_are_present_and_small():
    import os
    from app.web.web_router import SITE_DIR
    shots = ("img/register.jpg", "img/chat.jpg", "img/memory.jpg", "img/settings.jpg")
    total = 0
    for name in shots:
        path = os.path.join(SITE_DIR, name.replace("/", os.sep))
        assert os.path.isfile(path), f"缺实拍图：{name}"
        total += os.path.getsize(path)
    page = _page()
    for name in shots:
        assert name in page, f"{name} 没被页面引用"
    budget = 600 * 1024
    assert total < budget, f"四张图合计 {total} 字节,超了 {budget}:朋友用移动网络也要能打开"


def test_only_the_first_screenshot_loads_eagerly():
    """四张实拍合计约 500 KB。一起下载的话，首屏那次绘制是在给三张看不见的图让路。

    第一张 `fetchpriority="high"`（它是这一页的 LCP），其余三张 `loading="lazy"`。
    轮播时代还要在 JS 里把它们提升成 eager——轨道是 `overflow:hidden`，浏览器始终
    不认为它们"快滚进视口"，光加 lazy 翻过去就是空壳。现在四张摆在正常网格里，
    滚动到才下载这件事交回给浏览器原生行为，那段提升代码跟着轮播一起删了。
    """
    import re
    imgs = re.findall(r"<img\b[^>]*>", _page())
    assert len(imgs) == 4, f"截图 <img> 数量变了，这条的假设要一起改：{len(imgs)}"
    assert 'fetchpriority="high"' in imgs[0], \
        f"首屏那张没有提到高优先：{imgs[0][:70]}"
    for tag in imgs[1:]:
        assert 'loading="lazy"' in tag, f"后面那张在抢首屏带宽：{tag[:70]}"


def test_no_placeholder_left_in_shots():
    """占位块留着就是"页面还在施工"。Step 3-5 必须把它们换成真 img。"""
    assert "实拍位" not in _page()


def test_image_claim_always_carries_the_vision_qualifier():
    """提到"能看图"的那一行，必须带视觉限定。

    读图取决于当前模型有没有视觉，不是产品开关：一旦页面上出现一句光秃秃的
    "发图片它就能读"，来的人就会照着做然后发现读不出来。上一轮是靠人眼盯住的，
    这条把它变成断言——把那句限定删掉，这里立刻红。
    触发词在 2026-09-22 跟着文案扩了一次：措辞从"图片"改成"截图/识图"，要求没变，
    但守卫盯的词得跟着换，否则它检查的是一个页面上已经不存在的词。
    扫描版 PDF 那条在「当前未开启」节里，它自己就是否定句，不算数。
    """
    page = _page()
    cut = page.find('<section id="not-now">')
    assert cut != -1, "没有「当前未开启」那一节"
    for lineno, line in enumerate(page[:cut].splitlines(), 1):
        if any(w in line for w in ("图片", "拍照", "截图")):
            assert "视觉" in line, (
                f"页面第 {lineno} 行提到看图却没有视觉限定,"
                f"这句会被读成无条件能读图：{line.strip()[:90]}"
            )
    assert any(w in page[:cut] for w in ("图片", "截图", "识图")), \
        "整页不再提任何「能看图」的说法——那这条守卫该跟着改,不是默默放行"


# ---------- 5. 打包：spec 里没列目录,冻结版就没有这一页 ----------

def test_pyinstaller_spec_ships_the_site_dir():
    """这条存在,是因为 `run_backend.spec` 自己的注释写着"这条不会让任何测试变红",
    而同一形状的 static 缺失崩过一次 EXE 启动。CI 不跑 PyInstaller,所以断言落在
    spec 文本上：它是构建的真相源,漏了它就该在这里红,而不是等线上 404。"""
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "run_backend.spec"), encoding="utf-8") as f:
        spec = f.read()
    assert "backend/app/web/site" in spec, "datas 里没带 site/,冻结版 EXE 的 / 会找不到页面"
    assert "'app/web/site'" in spec or '"app/web/site"' in spec, \
        "datas 的目标解包路径必须是 app/web/site,和 _site_dir() 的 frozen 分支一致"



# ---------- 6. 计算器：实测通过才许上架 ----------

def test_calculator_card_states_it_uses_a_tool():
    """2026-09-19 真机验过才算数：让线上模型算 math.factorial(50)，日志里出现
    `[Stream] 调用工具: calculator(...)`，回灌后答出与本地 math.factorial(50)
    逐位相同的 65 位数。

    断言的是"走工具"而不是"会算数"——上一版卡片写的就是"会算数"，而模型当时
    是心算的（第一次答 420 对，第二次要求用工具时它答"我无法调用外部计算器工具"）。
    """
    page = _page()
    cut = page.find('<section id="can-do">')
    assert cut != -1
    card = page[cut:page.find("</section>", cut)]
    assert "会算数" in card, "计算器实测通过了，这张卡片还没回来"
    assert "计算器" in card, "只说会算数不说怎么走：读者会以为是模型心算"
    assert "心算" in card or "不是" in card, "要把它和'模型自己算'区分开"


# ---------- 7. 轮播与主题切换：交互也得有红线 ----------

def test_all_four_shots_are_visible_at_once():
    """四张实拍一次全铺，且页面上没有任何东西在自己动。

    2026-09-22 改判：原来是轮播（一次一张 + 前后键 + 指示点 + 自动播 + 横滑）。
    他的问题是"怎么就只剩下一张图片了"——那不是 bug，是轮播的定义，但一个四张静态
    截图的展示位需要访客去翻页、还需要"自动播必须能停"这条无障碍要求，本身就说明
    这套机器是多余的。现在桌面四列、手机两列。
    判据跟着换：四张都在这一节里、不许再有轮播挂载点，且 site.js 里不许出现
    setInterval——没有自动播，才轮到不要求暂停键。
    """
    page = _page()
    cut = page.find('<section id="shots"')
    assert cut != -1, "没有截图那一节"
    block = page[cut:page.find("</section>", cut)]
    for name in ("register", "chat", "memory", "settings"):
        assert f"/site/img/{name}.jpg" in block, f"{name}.jpg 没进这一节"
    assert "data-carousel" not in block, "轮播又回来了：四张实拍不需要翻页"
    assert 'aria-label="暂停轮播"' not in block, "没有自动播就不该有暂停键"
    assert "setInterval" not in _js(), "site.js 里出现了定时器——页面在自己动"
    assert "repeat(4" in _css(), "宽屏下没有排成四列，等于还是只看得见一两张"


def test_theme_toggle_exists_and_dark_is_the_default():
    """界面截图全是深色的，浅色页配深色截图会打架；默认改成深色，
    但保留切换——有人就是要浅色。偏好要落 localStorage，否则每开一次都要重选。"""
    page = _page()
    assert 'aria-label="切换到浅色"' in page, "缺主题切换键"
    css = _css()
    assert "prefers-color-scheme" in css, "首屏不该闪一下才变深色"
    assert "localStorage" in _js(), "主题偏好要记住，不能每次回到默认"
    # 图标不靠 JS 换 innerHTML（Blink 下赋 innerHTML 的 svg 有时不渲染），而是两个
    # 都在 DOM 里靠 CSS 换着显示。少了这条规则，按钮里会上下叠出太阳和月亮。
    assert "#themeBtn .i-moon" in css, "主题图标都挂在页面上，CSS 里却没有切换规则"


def _css() -> str:
    import os
    from app.web.web_router import SITE_DIR
    with open(os.path.join(SITE_DIR, "site.css"), encoding="utf-8") as f:
        return f.read()


def _js() -> str:
    import os
    from app.web.web_router import SITE_DIR
    path = os.path.join(SITE_DIR, "site.js")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_nothing_is_fetched_from_the_outside_at_runtime():
    """脚本里也不许偷偷连外网：一个 fetch 到别人域名，就等于把可用性押在别人的 uptime 上。"""
    js = _js()
    for bad in ("http://", "https://"):
        assert bad not in js, f"脚本里出现了外部地址：{bad}"


# ---------- 直连探测页（/site/probe.html） ----------

def _probe_code():
    """探测页去掉 HTML 与 JS 注释之后的正文。

    必须剥注释再判：这个文件的注释里写着"不发 /v1/*、不读 localStorage"，
    拿原文去 grep 这些字符串，锁会因为它自己说的话而变红。
    """
    import os
    from app.web.web_router import SITE_DIR
    from tests.test_web_pwa import _strip_html_comments, _strip_js_comments
    with open(os.path.join(SITE_DIR, "probe.html"), encoding="utf-8") as f:
        src = _strip_html_comments(f.read())
    return _strip_js_comments(src)


def test_the_probe_page_is_anonymous_and_only_talks_to_the_vendor():
    """这页存在的唯一理由是"key 不出浏览器"，所以它对本站的反向承诺要钉住。

    它是**匿名可访问**的（不是 /v1/ 前缀，中间件不管它），这没错——它不碰任何数据。
    但也正因为匿名，任何一次"顺手加个 /v1/ 调用"都会把一个装着用户 key 的页面
    接到我们自己的鉴权面上，那是形状 A 整条路线的反面。
    """
    code = _probe_code()
    # 正向对照：文件空了/改名了也不许"通过"
    assert "CHECKS" in code and code.count('["') >= 6, "探测页没读到内容或六项检查不见了：这条锁在空转"

    assert "/v1/" not in code, "探测页不许调本站接口：它一旦能碰 /v1，key 就进了我们的鉴权面"
    assert "XMLHttpRequest" not in code
    for storage in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert storage not in code, f"探测页不落盘任何东西，出现了 {storage}"
    # 每一次 fetch 都必须打到 endpoint()（用户在页面上填的供应商地址），不是相对路径
    fetches = [ln for ln in code.splitlines() if "fetch(" in ln]
    assert fetches, "一个 fetch 都没有：这页什么都不测"
    assert all("endpoint()" in ln for ln in fetches), \
        "有 fetch 没走 endpoint()，等于偷偷换目标：" + "；".join(fetches)


def test_the_report_never_carries_the_key():
    """报告是设计成"直接贴给工程师"的，所以它连 key 的长度与首尾片段都不许有。

    `$("key").value` 在报告拼装区里只许出现在一个判空的三元里；写成
    `+ $("key").value` 那种"顺手带上好排查"的方便，在这里就是泄露本身。
    """
    code = _probe_code()
    start = code.index("const head = ")
    end = code.index('$("report").textContent')
    region = code[start:end]
    assert '$("key").value ?' in region, "判空那个三元不在了：报告头的形状被改过，这条锁要看住的东西也变了"
    assert region.count('$("key").value') == 1, "报告拼装区里第二次读了 key 的值——它会被带进可复制的文本"


def test_no_probe_check_can_hang_forever():
    """2026-09-21 他在手机上跑到第 4 项"半天没反应"，界面上它和"没跑"长得一模一样。

    上游其实 0.2 秒就回了 401（实测），所以卡住的是这一页自己：`callStream` 没有任何
    时间上限，而 `[DONE]` 那行写的是 continue——服务商发完 [DONE] 不马上关连接的话，
    循环就永远等在 reader.read() 上。三条一起钉：

    ① 每次等待都有上限（超时要变成一个结论，不是一次沉默）；
    ② 正在跑的那一项要显示出来，否则"卡在哪"这条唯一有用的信息就丢了；
    ③ 收尾放 finally——以前它写在函数最后一行，任何一项在 try 外面抛了，
      两个按钮就永远灰着，而这页全部的作用就是让人再跑一遍。
    """
    code = _probe_code()
    limit = re.search(r"IDLE_LIMIT_MS\s*=\s*(\d+)", code)
    assert limit and int(limit.group(1)) >= 5000, (
        "等待上限不在了（或被写成 0）：任何一项都可能永远不落地，"
        "而 0 会让每一项当场失败——两种都不是「跑一遍看看」")
    assert "withDeadline(" in code, "期限没挂在 await 上：只靠 abort，打不断这个内核的请求就等于没有期限"
    body = code[code.index("async function callStream"):code.index("async function runAll")]
    # 真正的坑在这里：上一版在 `if (!res.ok)` 分支里先 stopArm() 再 await res.text()，
    # 于是六项里唯一读错误响应体的第 4 项，卡在了唯一一个没有看门狗的 await 上。
    assert "await withDeadline(res.text())" in body, "错误响应体的读取没有过期限——第 4 项就是这么卡死的"
    assert "await withDeadline(reader.read())" in body, "流式读取没有过期限"
    assert "await withDeadline(fetch(" in body, "第一个 fetch 没有过期限"
    assert "stopArm" not in body, "撤表机制又回来了：撤掉的那一刻正是最容易卡住的那一刻"
    assert "timeout: true" in code, "超时没有变成一句报告，只是被吞掉"
    # 判那一行本身，不判变量在不在：把 break 换成 continue 时 broke_on_done 依然
    # 声明着、依然被 if 用着，只查存在性的锁会照样绿（这条锁刚才就是这么漏的）。
    done_line = [ln for ln in code.splitlines() if '"[DONE]"' in ln]
    assert done_line and all("break" in ln for ln in done_line), (
        "[DONE] 又变回 continue：发完不关连接的服务商会把这一项挂死：" + str(done_line))
    assert code.count("running(") >= 5, "有步骤没有进行中标记，卡住时看不出来卡在哪一步"
    assert "} finally {" in code, "按钮的复原不再放在 finally 里"


def test_the_site_screenshots_stay_opaque():
    """官网那四张实拍不许被压成半透明。

    轮播时代给非当前张压到 .28 + scale(.93)，手机上轨道窄、相邻两张会露边，四张里
    三张是 ghost，整块读起来像没加载。他第一次看这块的反馈就是"完全看不到"。
    图片是内容不是装饰层。轮播删掉后 `.slide` 那组选择器跟着没了，所以这里改成扫
    任何提到 `.shot` 或 `.phone` 的规则——下一条 `html.js .slide` 那种写法以后换个
    名字也照样能被抓住。
    """
    css = _strip_css_comments(_css())
    checked = 0
    for rule in re.finditer(r"(?m)^([^{}\n][^{}]*)\{([^}]*)\}", css):
        sel = rule.group(1)
        if ".shot" not in sel and ".phone" not in sel:
            continue
        checked += 1
        m = re.search(r"opacity:\s*([0-9.]+)", rule.group(2))
        assert not m or float(m.group(1)) >= 1, f"实拍图又被压透明度了：{rule.group(0).strip()}"
    assert checked >= 2, f"选择器改名了，这条抓不到任何规则（只扫到 {checked} 条）——跟着改，别让它空转"


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_health_reports_the_build_stamp(client):
    """「设置 → 关于」那一行要知道服务端这份是第几版——判据来自 /health。

    取不到戳时宁可给空字符串（前端会退回"网页版"那句），不许猜一个号：
    写死的版本号是这仓最眼熟的那种错。
    """
    body = client.get("/health").json()
    assert "build" in body, "/health 不再报构建戳：关于那一行就没有来源了"
    assert isinstance(body["build"], str)
    assert body["status"] in ("ok", "degraded", "broken")


def test_the_build_stamp_comes_from_a_file_we_generate_at_build_time(tmp_path, monkeypatch):
    """version.txt 是构建时生成的，不进版本库；这条钉住"唯一来源是 git tag"这个安排。

    正向对照：文件不存在时必须是空串——宁缺毋错。写进源码/前端的那一份才是问题，
    所以另一头由 test_关于_reports_a_version_instead_of_a_number_we_wrote_by_hand 看着。
    """
    import os
    from app.core import buildinfo

    monkeypatch.setattr(buildinfo, "data_root", lambda: str(tmp_path))
    monkeypatch.setattr(buildinfo, "candidates_for_test", None, raising=False)
    buildinfo.build_version.cache_clear()
    assert buildinfo.build_version() == "", "没有 version.txt 时它凭空造出了一个版本号"

    (tmp_path / "version.txt").write_text("v0.16\n", encoding="utf-8")
    buildinfo.build_version.cache_clear()
    assert buildinfo.build_version() == "v0.16"
    buildinfo.build_version.cache_clear()

    assert not os.path.exists(os.path.join(os.path.dirname(buildinfo.__file__), "version.txt"))


def test_the_packaging_spec_lists_the_version_file(tmp_path):
    """spec 漏列 version.txt，冻结版就读不到构建戳，而这不会让任何测试变红。

    所以这里直接读 spec 的写法判：条件列项（文件不在就不列）要保住——
    构建失败的时候人是会慌的，而少一个显示用的版本号不该换来一次失败构建。
    """
    root = Path(__file__).resolve().parents[2]
    spec = (root / "run_backend.spec").read_text(encoding="utf-8")
    assert "'version.txt'" in spec, "spec 不再把构建戳打进包里：冻结版将永远显示不出服务端版本"
    assert "os.path.isfile('version.txt')" in spec, "变成了无条件列项：没有该文件时 PyInstaller 会直接报错"


# ---------- 2b. 服务端代取 APK ----------

def _fake_plan(monkeypatch, plan=(None, "模拟：没有快照")):
    from app.core import releases

    monkeypatch.setattr(releases, "download_plan", lambda: plan)
    return releases


def test_the_apk_route_hands_over_bytes_with_a_save_header(monkeypatch):
    """一次点击 = 直接落盘。类型与 Content-Disposition 就是"别在浏览器里打开它"。"""
    releases = _fake_plan(monkeypatch, ({
        "url": "https://github.com/o/r/releases/download/v0.17/ai-assistant-native-0.17.apk",
        "name": "ai-assistant-native-0.17.apk", "size": 5, "version": "0.17"}, ""))
    monkeypatch.setattr(releases, "fetch_asset", lambda url: (b"12345", ""))

    res = client.get("/site/android.apk")          # 不带任何凭据：朋友没登录也要能下
    assert res.status_code == 200, res.status_code
    assert res.headers["content-type"].startswith("application/vnd.android.package-archive")
    assert res.headers["content-disposition"] == 'attachment; filename="ai-assistant-native-0.17.apk"'
    assert res.content == b"12345"
    assert res.headers["cache-control"] == "no-cache", "缓存住了就等于让人下到上一版"


def test_the_apk_route_falls_back_to_the_release_page(monkeypatch):
    """拿不到包（没快照、或取字节失败）时退回发布页，而不是回一个 404。

    退这一步不是把用户丢回去自生自灭：按钮原来就在那儿，所以最坏情况等于改动之前。
    但绝不允许"回 200 空文件"——那是手机上"下载已完成，打开无反应"的形状。
    """
    releases = _fake_plan(monkeypatch)
    res = client.get("/site/android.apk", follow_redirects=False)
    assert res.status_code in (302, 307), res.status_code
    assert res.headers["location"] == releases.RELEASES_PAGE

    _fake_plan(monkeypatch, ({"url": "https://github.com/o/a.apk", "name": "ai-assistant-native-0.17.apk",
                             "size": 5, "version": "0.17"}, ""))
    monkeypatch.setattr(releases, "fetch_asset", lambda url: (None, "上游回 404"))
    res = client.get("/site/android.apk", follow_redirects=False)
    assert res.status_code in (302, 307), f"取不到字节却没退回发布页：{res.status_code}"
    assert res.headers["location"] == releases.RELEASES_PAGE


def test_the_apk_route_says_which_layer_failed(monkeypatch, capsys):
    """失败原因要落在服务日志里，不然线上永远只能猜。

    这台机器的 EXE 是隐藏窗口启动的，stdout 平时没人看得见——所以这条只保证
    那句 reason 真被打出来了（打印而不是吞进返回值，是这里唯一能被抓到的形状）。
    """
    import io
    import contextlib

    releases = _fake_plan(monkeypatch, (None, "模拟：白名单外的主机"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        client.get("/site/android.apk", follow_redirects=False)
    out = buf.getvalue() + capsys.readouterr().out
    assert "白名单外的主机" in out, f"没把失败原因说出来，只看见一次跳转：{out!r}"
