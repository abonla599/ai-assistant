"""管理员页 /admin 的契约：可达，而且它必须是个"没有数据"的空壳。

选独立地址而不是塞进 PWA，换来的是入口分离；这里锁的就是这个决定不变成漏洞：
页面谁都能打开（新用户也打得开），但任何一个字节的用户数据都只能经
`/v1/admin/*` 那套 require_admin 接口取。哪天有人图省事把用户列表直接渲染进
HTML，鉴权就等于被整页绕过了——那不会让现有任何一条测试变红，所以单独钉住。
"""
import os

from fastapi.testclient import TestClient

from app.core.authz import _PROTECTED_PREFIXES
from app.main import app


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
    assert stranger.get("/v1/admin/invites").status_code == 401


def test_admin_url_is_deliberately_not_an_auth_surface():
    """`/admin` 不在受保护前缀里是有意决定，钉住它以免被"顺手加一行"改成半开状态。

    如果真要连页面一起挡，正确做法是加进 _PROTECTED_PREFIXES 并同步更新这里——
    但那只挡机器人，挡不了任何能读到 HTML 的人，权限边界始终是 require_admin。
    """
    assert "/admin" not in _PROTECTED_PREFIXES
    assert "/v1/" in _PROTECTED_PREFIXES
