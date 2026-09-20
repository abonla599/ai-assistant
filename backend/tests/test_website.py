"""官网（`/`）的契约。

它和 PWA 同源、同一个 StaticFiles 类,所以缓存与 CSP 的行为必须和 /app 一致——
Cloudflare 已设「尊重现有标题」,源站一不表态,改版在朋友那边就是"改了没生效"
（2026-09-17 那次线上界面改版就是这么消失 80 分钟的）。
"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


# ---------- 1. 挂载与不回归 ----------

def test_root_serves_the_site_with_the_same_cache_policy_as_app():
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert "frame-src 'none'" in res.headers["content-security-policy"]


def test_site_assets_are_public_and_uncached():
    """`/site/...` 是官网资源。公开是刻意的(css 和截图不含任何用户数据),
    但必须和 /app 一样回 no-cache —— Cloudflare 已设「尊重现有标题」,
    源站不表态就等于让别人的缓存策略替我们决定。"""
    res = client.get("/site/site.css")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    # 整串相等而不是 in:`/` 那半只用了 in,两边合起来才真正钉住"页面与资源的头
    # 是同一组",而这一组字面量在 web_router 里只有一份(site_headers)。
    assert res.headers["content-security-policy"] == "frame-src 'none'; object-src 'none'"


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
    assert health.status_code == 200 and health.json() == {"status": "healthy"}
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


def test_unavailable_features_stay_in_the_not_now_section():
    """联网搜索、代码执行、扫描版 PDF 只许出现在「当前未开启」那一节里。

    节的边界按 `<section id="not-now">` 这个标签算，不按「当前未开启」这四个字算：
    正文里一句"见下面「当前未开启」"的指引会把后者锚点提前，那样 head 就悄悄漏掉了
    真那一节，"沙箱/支持联网不许出现在正文"这半条就没牙了（Task 3 实拍轮就是这么露馅的）。
    """
    page = _page()
    anchor = page.find('<section id="not-now">')
    assert anchor != -1, "没有「当前未开启」这一节"
    end = page.find("</section>", anchor)
    assert end != -1, "「当前未开启」那一节没闭合"
    body = page[anchor:end]
    for term in ("代码执行", "联网搜索", "扫描版 PDF"):
        assert term in body, f"「{term}」应当在该节里说明"
    head = page[:anchor] + page[end:]
    for banned in ("沙箱", "支持联网"):
        assert banned not in head, f"「{banned}」被当成现成能力写进了正文"


def test_download_points_at_latest_not_a_pinned_filename():
    page = _page()
    assert "releases/latest" in page
    assert "ai-assistant-0.13.apk" not in page, "钉死文件名的链接下一次发版就腐烂"


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
    assert 'src="/site/site.js"' in page, "交互脚本要走自己的 /site 前缀"
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

    轮播的图横向摆在 `overflow:hidden` 的轨道里，浏览器始终不认为它们"快滚进视口"，
    所以 `loading="lazy"` 单独用会翻车：翻到第二张时是个空壳（实测过）。正确的形状是
    HTML 里先 lazy 让路、`load` 之后由 site.js 提升成 eager——两头都要钉住。
    """
    import re
    imgs = re.findall(r"<img\b[^>]*>", _page())
    assert len(imgs) == 4, f"截图 <img> 数量变了，这条的假设要一起改：{len(imgs)}"
    assert 'fetchpriority="high"' in imgs[0], \
        f"首屏那张没有提到高优先：{imgs[0][:70]}"
    for tag in imgs[1:]:
        assert 'loading="lazy"' in tag, f"后面那张在抢首屏带宽：{tag[:70]}"
    js = _js()
    assert 'img.loading = "eager"' in js, \
        "缺了首屏后的 eager 提升：这三张会永远停在未下载，翻过去就是空壳"


def test_no_placeholder_left_in_shots():
    """占位块留着就是"页面还在施工"。Step 3-5 必须把它们换成真 img。"""
    assert "实拍位" not in _page()


def test_image_claim_always_carries_the_vision_qualifier():
    """「图片」只许出现在带视觉限定的那一行里。

    读图这件事取决于当前模型有没有视觉,不是产品开关:一旦页面上出现一句
    光秃秃的"发图片它就能读",来的人就会照着做然后发现读不出来。上一轮是靠
    人眼盯住的,这条把它变成断言——把那句限定删掉,这里立刻红。
    扫描版 PDF 那条在「当前未开启」节里,它自己就是否定句,不算数。
    """
    page = _page()
    cut = page.find('<section id="not-now">')
    assert cut != -1, "没有「当前未开启」那一节"
    for lineno, line in enumerate(page[:cut].splitlines(), 1):
        if "图片" in line or "拍照" in line:
            assert "视觉" in line, (
                f"页面第 {lineno} 行提到「图片/拍照」却没有视觉限定,"
                f"这句会被读成无条件能读图：{line.strip()[:90]}"
            )
    assert "图片" in page[:cut], "整页不再提图片——那这条守卫该跟着改,不是默默放行"


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

def test_carousel_shows_all_four_shots_and_can_be_stopped():
    """四张实拍收进一个轮播，但自动播的东西必须能停（WCAG 2.2.2）——
    一个停不下来的自动轮播，对手动操作页面的人是障碍，不是设计。"""
    page = _page()
    cut = page.find('<section id="shots"')
    assert cut != -1, "没有截图那一节"
    block = page[cut:page.find("</section>", cut)]
    for name in ("register", "chat", "memory", "settings"):
        assert f"/site/img/{name}.jpg" in block, f"{name}.jpg 没进轮播"
    assert "data-carousel" in block, "轮播要有自己的挂载点，脚本靠它找元素"
    assert 'aria-label="上一张"' in block and 'aria-label="下一张"' in block
    assert 'aria-label="暂停轮播"' in block, "自动轮播缺暂停键"


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
