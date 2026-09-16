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


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """幂等的定义是"第二次加载零写入"，不是"第二次没多出备份文件"。

    备份名只精确到秒（`%Y%m%d%H%M%S`）。把 _backfill_owner 里 `if not missing:
    return` 那道判断删掉，第二次加载会 copy2 到**同一个**文件名（shutil 直接
    覆盖），`.bak-*` 的计数一动不动，可 _flush 已经把用户的数据文件重写了一遍
    ——每次启动都动一次真实数据。只数文件的断言对这种回归是瞎的，所以这里盯
    的是写盘动作本身。
    """
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "messages": []}}', encoding="utf-8")
    SessionStore(path=str(path))
    first = len(list(tmp_path.glob("sessions.json.bak-*")))
    assert first == 1, "回填前留了原件备份"

    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    writes = []
    monkeypatch.setattr(SessionStore, "_flush", lambda self: writes.append(self.path))

    reopened = SessionStore(path=str(path))
    assert reopened.get("old", owner="default_user") is not None, "读路径不受打桩影响"
    assert writes == [], "重复加载一次都不该写盘"
    assert path.read_bytes() == before, "内容必须逐字节不变"
    assert path.stat().st_mtime_ns == mtime, "mtime 变了就是说被重写过"
    assert len(list(tmp_path.glob("sessions.json.bak-*"))) == first


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


def test_legacy_upload_migration_is_idempotent(tmp_path, monkeypatch):
    d = tmp_path / "up3"
    d.mkdir()
    (d / "x").write_text("hi", encoding="utf-8")
    (d / "index.json").write_text(json.dumps(
        {"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",
                "size": 1, "path": str(d / "x"), "created_at": "y"}}), encoding="utf-8")
    UploadStore(directory=str(d))
    index = d / "index.json"
    first = len(list(d.glob("index.json.bak-*")))
    assert first == 1, "回填前留了原件备份"

    # 备份名只到秒，删掉 `if not missing: return` 后第二次加载会把备份复制到
    # 同一个文件名上（覆盖），数文件数看不出来；所以断的是"一次都没写盘"。
    before = index.read_bytes()
    mtime = index.stat().st_mtime_ns
    writes = []
    monkeypatch.setattr(UploadStore, "_flush", lambda self: writes.append(self.index_path))

    reopened = UploadStore(directory=str(d))
    assert reopened.get("abc", owner="default_user") is not None
    assert writes == [], "重复加载一次都不该写盘"
    assert index.read_bytes() == before, "索引必须逐字节不变"
    assert index.stat().st_mtime_ns == mtime, "mtime 变了就是说被重写过"
    assert len(list(d.glob("index.json.bak-*"))) == first


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


def test_chat_pipeline_refuses_to_build_an_anonymous_identity():
    """`ChatPipeline(user_id="default_user")` 与本任务对 owner 禁默认值同形。

    默认身份就是 bootstrap 管理员：漏传的调用点会静默以管理员身份检索记忆、
    写记忆，而调用点上看不出任何区别。必填之后漏传直接 TypeError。
    """
    from app.pipeline import ChatPipeline

    with pytest.raises(TypeError):
        ChatPipeline()


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


# ---------- 回填判据：present-but-empty 也算没迁完 ----------

def test_owner_present_but_empty_counts_as_unmigrated(tmp_path):
    """手工写坏的 {"owner": null} 必须被认成没迁完。

    判据写成 `"owner" not in s` 时它会当成"已有 owner"跳过，此后这条记录与任何
    user_id 都不相等 —— 会话还在文件里，但谁也都看不见，连管理员自己都找不回。
    """
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "messages": [], "owner": null}}',
                    encoding="utf-8")
    store = SessionStore(path=str(path))
    assert store.get("old", owner="default_user") is not None
    assert json.loads(path.read_text(encoding="utf-8"))["old"]["owner"] == "default_user"


def test_upload_owner_present_but_empty_counts_as_unmigrated(tmp_path):
    d = tmp_path / "up5"
    d.mkdir()
    (d / "x").write_text("hi", encoding="utf-8")
    (d / "index.json").write_text(json.dumps(
        {"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",
                "size": 1, "path": str(d / "x"), "created_at": "y",
                "owner": ""}}), encoding="utf-8")
    store = UploadStore(directory=str(d))
    assert store.get("abc", owner="default_user") is not None
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert index["abc"]["owner"] == "default_user"


# ---------- 畸形记录：照样拒绝启动，但要说清楚是哪个文件 ----------

def test_malformed_session_record_names_the_file(tmp_path):
    """旧行为是 `TypeError: 'str' object does not support item assignment`。

    冻结成 EXE 之后用户看到的就只有这一行：没有文件名、没有记录 id，等于没法
    自助修复。仍然拒绝启动，但错误必须点名 self.path。
    """
    path = tmp_path / "sessions.json"
    path.write_text('{"old": "这一行被手抖改成了字符串"}', encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        SessionStore(path=str(path))
    assert "sessions.json" in str(exc.value)
    assert str(tmp_path) in str(exc.value), "错误里要带上数据文件路径"


def test_malformed_upload_record_names_the_file(tmp_path):
    d = tmp_path / "up6"
    d.mkdir()
    (d / "index.json").write_text('{"abc": "这一行被手抖改成了字符串"}', encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        UploadStore(directory=str(d))
    assert "index.json" in str(exc.value)
    assert str(d) in str(exc.value), "错误里要带上索引文件路径"


# ---------- HTTP 层：非本人一律 404，不是 403 ----------

@pytest.fixture
def no_llm_calls(monkeypatch, _stub_llm_calls):
    """把"模型一次都不能被调用"变成一个可断言的事实。

    光断 404 不够：/v1/chat 这条路的代价是顺序——原先模型先调完、token 先花掉，
    之后才发现写不进去。所以除了状态码，还要钉住付费调用根本没发生。
    依赖 conftest 的 _stub_llm_calls（先跑），在它之上再包一层记录。
    """
    import app.core.streaming as streaming
    import app.pipeline as pipeline

    calls = []
    real_build = pipeline.build_client

    def spy_build(provider):
        calls.append("pipeline")
        return real_build(provider)

    async def spy_stream(model, messages, provider_id=None, temperature=0.7,
                         max_tokens=4096):
        calls.append("stream")
        yield "（测试回复）"

    monkeypatch.setattr(pipeline, "build_client", spy_build)
    monkeypatch.setattr(streaming, "stream_chat", spy_stream)
    return calls


@pytest.fixture
def pipeline_spy(monkeypatch):
    """记录每次 ChatPipeline 实例化用的 user_id：接线漏传在这里看得见。"""
    from app import main as app_main

    built = []
    real = app_main.ChatPipeline

    class Spy(real):
        def __init__(self, *args, **kwargs):
            built.append(kwargs.get("user_id", args[0] if args else None))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_main, "ChatPipeline", Spy)
    return built


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


def test_http_chat_cannot_write_into_another_users_session(client, enforced, no_llm_calls,
                                                           pipeline_spy):
    """拿别人的 session_id 聊天 = 会话不存在：404，且在花钱之前。

    这里原先断的是 200：add_message 的 False 被丢掉，请求成功返回、模型已经调完、
    转录一条没落。断 200 等于把这个洞写成契约，所以改成断 404 + 断"没调模型"。
    """
    mine = enforced("会话主人")
    stranger = enforced("蹭会话的人")
    stranger_uid = client.get("/v1/auth/me", headers=stranger).json()["user_id"]
    mine_uid = client.get("/v1/auth/me", headers=mine).json()["user_id"]
    assert stranger_uid != mine_uid, "两个 enforced 身份必须是两个人，否则下面全在自证"
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]

    res = client.post("/v1/chat", headers=stranger, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "把这句话塞进别人的历史"}]})
    assert res.status_code == 404, res.text
    assert "会话不存在" in res.text and "403" not in res.text
    assert no_llm_calls == [], "别人的会话 id：模型一次都不该被调用"
    assert pipeline_spy == [], "归属都没过，就不该先建 pipeline 去注入记忆"

    messages = client.get(f"/v1/sessions/{sid}", headers=mine).json()["messages"]
    assert messages == [], "非属主的写入必须整条不落地"

    # 反向：属主自己聊必须照常落盘，且是以他自己的身份（不是 default_user）
    ok = client.post("/v1/chat", headers=mine, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "这是我自己的会话"}]})
    assert ok.status_code == 200, ok.text
    assert no_llm_calls, "属主自己的请求要真走到模型"
    assert pipeline_spy == [mine_uid], "管道身份必须跟着调用者"
    assert [m["role"] for m in
            client.get(f"/v1/sessions/{sid}", headers=mine).json()["messages"]] == \
        ["user", "assistant"]


def test_http_stream_chat_refuses_a_foreign_session_before_answering(
        client, enforced, no_llm_calls, pipeline_spy):
    """流式那条路是同一个洞，而且更贵：状态码在流开始时已经锁死 200。

    校验必须发生在返回 StreamingResponse 之前——放进 generate() 里的任何检查都
    只能表现为"流里没内容"，客户端看不出失败，历史也一条不落。
    """
    mine = enforced("流式会话主人")
    stranger = enforced("蹭流式会话的人")
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]

    res = client.post("/v1/chat/stream", headers=stranger, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "把这句话流进别人的历史"}]})
    assert res.status_code == 404, res.text
    assert no_llm_calls == [] and pipeline_spy == [], "404 之前不许有任何模型工作"
    assert client.get(f"/v1/sessions/{sid}", headers=mine).json()["messages"] == []

    ok = client.post("/v1/chat/stream", headers=mine, json={
        "model": "fake-model", "session_id": sid,
        "messages": [{"role": "user", "content": "这是我自己的流式会话"}]})
    assert ok.status_code == 200, ok.text
    assert client.get(f"/v1/sessions/{sid}", headers=mine).json()["messages"] != [], \
        "属主的流式请求要照常落盘"


def test_chat_404_for_a_foreign_session_is_indistinguishable_from_a_made_up_one(
        client, enforced):
    """正因为两种情况响应逐字节相同，硬 404 才不是探测器。

    "不是你的"与"根本不存在"都经 store 返回同一个 None → 同一个 404 → 同一句话。
    拿这个端点猜 id，得到的信息量为零：它只对"你自己编的 id"回话。
    """
    mine = enforced("有会话的人")
    stranger = enforced("路人")
    sid = client.post("/v1/sessions", headers=mine).json()["session_id"]
    payload = {"model": "fake-model", "session_id": sid,
               "messages": [{"role": "user", "content": "你好"}]}

    foreign = client.post("/v1/chat", headers=stranger, json=payload)
    made_up = client.post("/v1/chat", headers=stranger,
                          json={**payload, "session_id": "0" * 32})
    assert foreign.status_code == made_up.status_code == 404
    assert foreign.text == made_up.text, "两种失败必须同形，否则就成了枚举信道"


def test_http_feedback_is_resolved_against_the_caller(client, enforced, monkeypatch):
    """反馈按 message_id 反查记忆来调权重：反查必须只在调用者自己的会话里找。

    store 层的收窄由 test_find_message_scoped_by_owner 钉住，这里钉的是路由接线。
    接线写成 find_message(mid, "default_user") 的话，store 那些断言在真实请求里
    一条都不会红，而陌生人的点踩就会去调管理员记忆的权重。
    """
    from app import main as app_main

    calls = []
    real = app_main.sessions_store.find_message

    def spy(message_id, owner=None):
        # owner 给了默认值：路由哪天改成 find_message(mid, owner=...) 这种关键字
        # 写法，位置参数的 spy 会先 TypeError，把"接线错了"演成"测试炸了"。
        calls.append((message_id, owner))
        return real(message_id, owner)           # 不替换行为，只看接线把谁传了进去

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

    # 断的是收集到的值，不是 spy 的参数形状（见上面 spy 的注释）
    assert [c[0] for c in calls] == [mid, mid], "两次反馈都按同一条 message_id 反查"
    assert [c[1] for c in calls] == [mine_uid, stranger_uid], "反查按调用者收窄"


def test_a_stranger_cannot_touch_the_bootstrap_admins_data(client, enforced,
                                                           no_llm_calls):
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
    assert res.status_code == 404, res.text
    assert no_llm_calls == [], "对管理员会话的越权请求同样不该先把模型调用做完"
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
