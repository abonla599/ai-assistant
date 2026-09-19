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


def test_unknown_paths_still_get_the_framework_json_404():
    """这条是 R7 的核心防线。

    如果哪天有人图省事改回 `app.mount("/")`,这个请求会被静态目录兜走,
    content-type 不再是 application/json —— 顺带 `/health/`、`/docs/` 的
    redirect_slashes 语义也会一起坏掉(那是 test_auth_endpoints.py:296 在钉的)。
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
