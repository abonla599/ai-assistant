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
    """旧断言是 `"status" in data`。它还在的话说明 @app.get("/") 没删干净。"""
    assert '"status"' not in client.get("/").text


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
    """联网搜索与代码执行只许出现在「当前未开启」那一节里。"""
    import re
    page = _page()
    section = re.search(r'当前未开启(.*?)</section>', page, re.S)
    assert section, "没有「当前未开启」这一节"
    body = section.group(1)
    assert "代码执行" in body and "联网搜索" in body
    head = page[:section.start()] + page[section.end():]
    assert "沙箱" not in head, "沙箱被当成现成能力写进了正文"


def test_download_points_at_latest_not_a_pinned_filename():
    page = _page()
    assert "releases/latest" in page
    assert "ai-assistant-0.13.apk" not in page, "钉死文件名的链接下一次发版就腐烂"


def test_domain_spelling():
    """域名只差一个字母,错一个就是别人的站(或一个不存在的站)。"""
    page = _page()
    assert "ai.fenever.xyz" in page
    for wrong in ("feverver", "fenerver", "fenevrr", "feverless"):
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


def test_page_is_a_single_html_with_no_js():
    assert "<script" not in _page()


def test_landing_page_has_the_six_sections():
    page = _page()
    for sid in ("can-do", "shell-only", "start", "not-now", "shots", "faq"):
        assert f'id="{sid}"' in page, f"缺这一节:{sid}"
