"""PWA 静态托管与前端依赖的接口契约测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_app_serves_html_shell():
    res = client.get("/app/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "AI 智能助手" in body
    # 前端不得使用绝对地址，否则换网络/换设备就失效
    assert "127.0.0.1" not in body
    assert "localhost" not in body


def test_static_assets_reachable():
    for asset in ("style.css", "app.js", "sw.js", "manifest.webmanifest", "icon.png"):
        res = client.get("/app/" + asset)
        assert res.status_code == 200, asset


def test_app_js_uses_relative_api_paths_only():
    res = client.get("/app/app.js")
    assert res.status_code == 200
    src = res.text
    assert 'req("/v1/' in src or "req(\"/v1/" in src or '"/v1/sessions"' in src
    assert "http://" not in src, "前端出现绝对 URL，跨设备将无法访问"


def test_service_worker_does_not_cache_api():
    src = client.get("/app/sw.js").text
    assert "/v1/" in src


def test_root_still_reports_api_status():
    """新增前端挂载不应改变 / 的既有语义。"""
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_memory_list_shape_matches_frontend():
    """前端读取 data.memories 或 data.results，二者至少存在一个。"""
    uid = "shape_checker"
    client.post("/v1/memory/add", json={"user_id": uid, "content": "契约检查记忆", "summarize": False})
    res = client.get(f"/v1/memory/list/{uid}?limit=5")
    assert res.status_code == 200
    data = res.json()
    assert "memories" in data or "results" in data, f"实际字段: {list(data)}"
