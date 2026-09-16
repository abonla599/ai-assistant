"""跨用户隔离矩阵：A 拿不到 B 的会话与附件。

一律用真实令牌 + AUTH_MODE=enforced 才能验到归属逻辑，因此本文件不复用
conftest 的 disabled 客户端：store 层直接调用，拿两个真实注册出来的 user_id
断言归属；HTTP 层复用 conftest 的 client + enforced fixture，因为"非本人按 404
处理"是对路由的承诺（不是对 store 的承诺），另搭一个最小 app 反而测不到
main.py 里真实的那几条路由。
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.core.auth import AuthStore
from app.core.uploads import UploadStore
from app.session.session_store import SessionStore


@pytest.fixture
def two_users(tmp_path):
    auth = AuthStore(path=str(tmp_path / "users.json"),
                     invites_path=str(tmp_path / "invites.json"))
    out = {}
    for name in ("A", "B"):
        code = auth.create_invite("admin")
        principal, token = auth.register(code=code, username=name)
        out[name] = (principal.user_id, token)
    return out


@pytest.fixture
def store(tmp_path):
    return SessionStore(path=str(tmp_path / "sessions.json"))


def test_owner_can_create_and_read(store, two_users):
    a, _ = two_users["A"]
    created = store.create("fake-model", owner=a)
    assert store.get(created["session_id"], owner=a) is not None


def test_other_user_cannot_read_session(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    created = store.create("fake-model", owner=a)
    assert store.get(created["session_id"], owner=b) is None, \
        "别人的会话必须像不存在一样"


def test_list_only_returns_own_sessions(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    store.create("fake-model", owner=a)
    mine = store.create("fake-model", owner=b)
    ids = [s["session_id"] for s in store.list_summaries(a)]
    assert ids and mine["session_id"] not in ids


def test_write_operations_reject_non_owner(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    sid = store.create("fake-model", owner=a)["session_id"]
    assert store.add_message(sid, b, "user", "偷改") is False
    assert store.replace(sid, b, []) is False
    assert store.delete(sid, b) is False
    assert store.get(sid, owner=a)["messages"] == []


def test_find_message_scoped_by_owner(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    sid = store.create("fake-model", owner=a)["session_id"]
    store.add_message(sid, a, "assistant", "回答", message_id="m-1")
    assert store.find_message("m-1", a) is not None
    assert store.find_message("m-1", b) is None


def test_legacy_sessions_without_owner_are_backfilled_and_backed_up(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "title": "T", "created_at": "x",'
                    ' "model": "m", "messages": []}}', encoding="utf-8")
    store = SessionStore(path=str(path))
    assert store.get("old", owner="default_user") is not None
    assert store.get("old", owner="u_victim") is None
    assert list(Path(tmp_path).glob("sessions.json.bak-*")), "回填前必须留原件备份"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "messages": []}}', encoding="utf-8")
    SessionStore(path=str(path))
    first = len(list(tmp_path.glob("sessions.json.bak-*")))
    SessionStore(path=str(path))
    assert len(list(tmp_path.glob("sessions.json.bak-*"))) == first, "重复加载不该再回填"


# ---------- 附件 ----------

@pytest.fixture
def uploads(tmp_path):
    return UploadStore(directory=str(tmp_path / "up"))


def test_upload_records_owner(uploads, two_users):
    a, _ = two_users["A"]
    rec = uploads.save("n.txt", "内容".encode(), "text/plain", owner=a)
    assert uploads.get(rec["id"], owner=a) is not None
    b, _ = two_users["B"]
    assert uploads.get(rec["id"], owner=b) is None, "别人的附件应视为不存在"
    assert uploads.delete(rec["id"], owner=b) is False


def test_legacy_upload_owner_backfilled(tmp_path):
    d = tmp_path / "up2"
    d.mkdir()
    (d / "x").write_text("hi", encoding="utf-8")
    # path 用绝对路径：真实 index.json 里存的就是 os.path.join(目录, id+ext)，
    # 写个裸 "x" 只会让 get() 因文件不存在而返回 None，测不到归属那一层。
    (d / "index.json").write_text(json.dumps(
        {"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",
                "size": 1, "path": str(d / "x"), "created_at": "y"}}), encoding="utf-8")
    store = UploadStore(directory=str(d))
    assert store.get("abc", owner="default_user") is not None
    assert store.get("abc", owner="u_intruder") is None


def test_legacy_upload_migration_is_idempotent(tmp_path):
    d = tmp_path / "up3"
    d.mkdir()
    (d / "x").write_text("hi", encoding="utf-8")
    (d / "index.json").write_text(json.dumps(
        {"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",
                "size": 1, "path": str(d / "x"), "created_at": "y"}}), encoding="utf-8")
    UploadStore(directory=str(d))
    first = len(list(d.glob("index.json.bak-*")))
    assert first == 1, "回填前留了原件备份"
    UploadStore(directory=str(d))
    assert len(list(d.glob("index.json.bak-*"))) == first, "重复加载不该再回填"


# ---------- owner 必填：漏传必须炸，不能静默变成管理员 ----------

@pytest.mark.parametrize("call", [
    lambda s: s.create("fake-model"),
    lambda s: s.list_summaries(),
    lambda s: s.get("any-sid"),
    lambda s: s.add_message("any-sid", "user", "hi"),
    lambda s: s.replace("any-sid", []),
    lambda s: s.delete("any-sid"),
    lambda s: s.find_message("any-mid"),
], ids=["create", "list_summaries", "get", "add_message", "replace", "delete",
        "find_message"])
def test_session_store_refuses_to_act_without_an_owner(store, call):
    """漏传 owner 必须是 TypeError。

    给 owner 加默认值 = 忘记传的调用点静默以 default_user（也就是 bootstrap
    管理员）身份读写，那正是本任务要堵的洞。
    """
    with pytest.raises(TypeError):
        call(store)


@pytest.mark.parametrize("call", [
    lambda s: s.save("n.txt", b"hi", "text/plain"),
    lambda s: s.get("any-id"),
    lambda s: s.delete("any-id"),
    lambda s: s.read_text("any-id"),
    lambda s: s.data_uri("any-id"),
], ids=["save", "get", "delete", "read_text", "data_uri"])
def test_upload_store_refuses_to_act_without_an_owner(uploads, call):
    with pytest.raises(TypeError):
        call(uploads)


# ---------- 迁移失败必须阻止启动 ----------

def test_session_migration_failure_aborts_instead_of_serving_half_migrated(
        tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "messages": []}}', encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("磁盘不可写")

    monkeypatch.setattr(shutil, "copy2", boom)
    with pytest.raises(OSError):
        SessionStore(path=str(path))
    assert "owner" not in path.read_text(encoding="utf-8"), \
        "备份都没成就还没写回：半迁移的库会让 owner 校验静默放行"


def test_upload_migration_failure_aborts_instead_of_serving_half_migrated(
        tmp_path, monkeypatch):
    d = tmp_path / "up4"
    d.mkdir()
    (d / "x").write_text("hi", encoding="utf-8")
    (d / "index.json").write_text(json.dumps(
        {"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",
                "size": 1, "path": str(d / "x"), "created_at": "y"}}), encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("磁盘不可写")

    monkeypatch.setattr(shutil, "copy2", boom)
    with pytest.raises(OSError):
        UploadStore(directory=str(d))
    assert "owner" not in (d / "index.json").read_text(encoding="utf-8")


# ---------- HTTP 层：非本人一律 404，不是 403 ----------

def test_http_session_routes_answer_404_to_a_stranger(client, enforced):
    """403 等于承认"这个 id 存在、只是你不配"，那就成了一条枚举信道。"""
    mine = enforced("主人")
    stranger = enforced("路人")
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]

    assert client.get(f"/v1/sessions/{sid}", headers=mine).status_code == 200
    for res in (client.get(f"/v1/sessions/{sid}", headers=stranger),
                client.delete(f"/v1/sessions/{sid}", headers=stranger),
                client.put(f"/v1/sessions/{sid}/messages",
                           json={"messages": [{"role": "user", "content": "篡改"}]},
                           headers=stranger)):
        assert res.status_code == 404, res.text
        assert "403" not in res.text

    # 被拒的三次写入一条都没落下去
    session = client.get(f"/v1/sessions/{sid}", headers=mine).json()
    assert session["messages"] == []


def test_http_session_list_and_delete_are_scoped_to_the_caller(client, enforced):
    mine = enforced("有会话的人")
    stranger = enforced("没会话的人")
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]

    assert client.get("/v1/sessions", headers=stranger).json()["sessions"] == []
    body = client.get("/v1/sessions", headers=mine).json()
    assert [s["session_id"] for s in body["sessions"]] == [sid]
    assert client.delete(f"/v1/sessions/{sid}", headers=stranger).status_code == 404
    assert client.delete(f"/v1/sessions/{sid}", headers=mine).status_code == 200


def test_http_attachment_download_and_delete_are_scoped_to_uploader(client, enforced):
    mine = enforced("上传的人")
    stranger = enforced("想下载的人")
    up = client.post("/v1/uploads", headers=mine,
                     files={"file": ("秘密.txt", "我的账单".encode(), "text/plain")})
    assert up.status_code == 200, up.text
    upload_id = up.json()["id"]

    got = client.get(f"/v1/uploads/{upload_id}/file", headers=stranger)
    assert got.status_code == 404, got.text
    assert "我的账单" not in got.text
    assert client.delete(f"/v1/uploads/{upload_id}", headers=stranger).status_code == 404
    assert client.get(f"/v1/uploads/{upload_id}/file", headers=mine).status_code == 200


def test_http_chat_cannot_write_into_another_users_session(client, enforced):
    """session_id 是路径外的自由字段：拿别人的 id 聊天不能往别人历史里写一句话。"""
    mine = enforced("会话主人")
    stranger = enforced("蹭会话的人")
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]

    res = client.post("/v1/chat", headers=stranger, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "把这句话塞进别人的历史"}]})
    assert res.status_code == 200, res.text

    messages = client.get(f"/v1/sessions/{sid}", headers=mine).json()["messages"]
    assert messages == [], "非属主的写入必须整条不落地"


def test_http_feedback_is_resolved_against_the_caller(client, enforced, monkeypatch):
    """反馈按 message_id 反查记忆来调权重：反查必须只在调用者自己的会话里找。

    store 层的收窄由 test_find_message_scoped_by_owner 钉住，这里钉的是路由接线。
    接线写成 find_message(mid, "default_user") 的话，store 那些断言在真实请求里
    一条都不会红，而陌生人的点踩就会去调管理员记忆的权重。
    """
    from app import main as app_main

    calls = []
    real = app_main.sessions_store.find_message

    def spy(message_id, owner):
        calls.append((message_id, owner))
        return real(message_id, owner)          # 不替换行为，只看接线把谁传了进去

    monkeypatch.setattr(app_main.sessions_store, "find_message", spy)

    mine = enforced("答题的人")
    stranger = enforced("指手画脚的人")
    mine_uid = client.get("/v1/auth/me", headers=mine).json()["user_id"]
    stranger_uid = client.get("/v1/auth/me", headers=stranger).json()["user_id"]

    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]
    mid = client.post("/v1/chat", headers=mine, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "什么是向量数据库"}]}).json()["message_id"]

    for hdrs, rating in ((mine, 1), (stranger, -1)):
        res = client.post("/v1/feedback", headers=hdrs,
                          json={"message_id": mid, "rating": rating})
        assert res.status_code == 200, res.text
        assert res.json()["used_memories"] is False, "假存储下没有可加权的记忆，必须说实话"

    assert calls == [(mid, mine_uid), (mid, stranger_uid)], "反查按调用者收窄"


def test_a_stranger_cannot_touch_the_bootstrap_admins_data(client, enforced):
    """把靶子立在 default_user 名下：owner 写死成管理员正是本任务的原始 bug 形状。

    别人的会话/附件都按 uuid 分得很开，硬编码 "default_user" 只会让普通用户够到
    管理员的数据，而够不到同层的另一个人——所以上面那些用例对这种回退是瞎的。
    """
    boot = {"Authorization": "Bearer boot-token"}      # enforced 里的 bootstrap 口令
    sid = client.post("/v1/sessions", headers=boot).json()["session_id"]
    up = client.post("/v1/uploads", headers=boot,
                     files={"file": ("管理员账单.txt", "报销明细".encode(), "text/plain")})
    upload_id = up.json()["id"]

    stranger = enforced("想够管理员数据的人")
    assert client.get(f"/v1/sessions/{sid}", headers=stranger).status_code == 404
    assert client.delete(f"/v1/sessions/{sid}", headers=stranger).status_code == 404
    got = client.get(f"/v1/uploads/{upload_id}/file", headers=stranger)
    assert got.status_code == 404, got.text
    assert "报销明细" not in got.text

    res = client.post("/v1/chat", headers=stranger, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "往管理员历史里塞一句"}]})
    assert res.status_code == 200, res.text
    assert client.get(f"/v1/sessions/{sid}", headers=boot).json()["messages"] == []
    assert client.get(f"/v1/uploads/{upload_id}/file", headers=boot).status_code == 200


def test_http_uploads_never_trust_an_owner_from_the_request(client, enforced):
    """身份只能来自凭据：query/body 里塞 user_id 或 owner 都不许改变归属。"""
    mine = enforced("正经上传者")
    res = client.post("/v1/uploads?owner=somebody-else", headers=mine,
                      files={"file": ("a.txt", b"hello", "text/plain")})
    assert res.status_code == 200, res.text
    upload_id = res.json()["id"]
    assert client.get(f"/v1/uploads/{upload_id}/file",
                      headers=enforced("另一个人")).status_code == 404
    assert client.get(f"/v1/uploads/{upload_id}/file", headers=mine).status_code == 200
