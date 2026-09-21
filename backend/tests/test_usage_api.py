"""`GET /v1/admin/usage`：账本得有人看得见的这一半。

只读接口，不含任何前端——`backend/app/web/**` 此刻归另一个会话在改。
"""
import pytest
from fastapi.testclient import TestClient

from app.core import usage
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def empty_ledger(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setenv("USAGE_DB_PATH", str(path))
    usage.restore(path=str(path))
    yield path


def _record(**kw):
    kw.setdefault("provider_id", "p-ds")
    kw.setdefault("paid_by", "operator")
    usage.record_call(**kw)


def test_the_route_declares_its_admin_dependency():
    """新面必须走 require_admin，不是"看起来在 /v1/admin 底下就安全了"。"""
    from app.core.authz import require_admin
    from fastapi.routing import APIRoute

    route = next(r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == "/v1/admin/usage")
    tree = [d.call for d in _all_dependencies(route.dependant)]
    assert require_admin in tree, "/v1/admin/usage 没挂管理员守卫"


def _all_dependencies(dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _all_dependencies(sub)


def test_it_answers_who_how_much_and_whose_money(empty_ledger):
    _record(user_id="u-1", prompt_tokens=100, completion_tokens=40, total_tokens=140)
    _record(user_id="u-1", prompt_tokens=60, completion_tokens=10, total_tokens=70)
    _record(user_id="u-2", paid_by="user", prompt_tokens=5, completion_tokens=5, total_tokens=10)

    body = client.get("/v1/admin/usage").json()
    assert len(body["rows"]) == 2, body
    by_uid = {r["user_id"]: r for r in body["rows"]}
    assert by_uid["u-1"]["total_tokens"] == 210 and by_uid["u-1"]["calls"] == 2
    assert by_uid["u-2"]["total_tokens"] == 10
    assert body["totals"]["operator"]["total_tokens"] == 210
    assert body["totals"]["user"]["total_tokens"] == 10, "「谁的钱」是这本账要回答的原话"


def test_an_empty_ledger_is_not_an_error(empty_ledger):
    body = client.get("/v1/admin/usage").json()
    assert body["rows"] == [] and body["totals"]["operator"]["calls"] == 0


def test_a_specific_day_can_be_asked_for(empty_ledger, monkeypatch):
    _record(user_id="u-1", prompt_tokens=1, completion_tokens=1, total_tokens=2, day="2026-09-01")
    assert client.get("/v1/admin/usage", params={"day": "2026-09-01"}).json()["rows"]
    assert client.get("/v1/admin/usage", params={"day": "2026-09-02"}).json()["rows"] == []
    assert "2026-09-01" in client.get("/v1/admin/usage").json()["days"]


def test_a_malformed_day_is_a_400_not_a_500(empty_ledger):
    res = client.get("/v1/admin/usage", params={"day": "昨天"})
    assert res.status_code == 400, res.text
