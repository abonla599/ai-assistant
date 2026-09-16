"""SessionStore 持久化行为测试。

每条会话都要有 owner，所以这里固定两个身份：OWNER 是属主，OTHER 是拿了同一个
id 的陌生人。归属矩阵本身在 test_isolation.py，这里只管"落盘与重启后仍在"。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session.session_store import SessionStore

OWNER = "u_owner"
OTHER = "u_other"


def test_session_survives_new_instance(tmp_path):
    """重建实例（等价于重启后端）后会话仍在。"""
    path = str(tmp_path / "sessions.json")
    store = SessionStore(path)
    created = store.create("deepseek-chat", owner=OWNER)
    sid = created["session_id"]
    store.add_message(sid, OWNER, "user", "你好")
    store.add_message(sid, OWNER, "assistant", "你好，有什么可以帮你")

    reopened = SessionStore(path)
    session = reopened.get(sid, owner=OWNER)
    assert session is not None, "重启后会话丢失"
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]


def test_owner_survives_new_instance(tmp_path):
    """owner 必须跟着落盘：只在内存里判归属，重启后就成了没归属的裸数据。"""
    path = str(tmp_path / "sessions.json")
    sid = SessionStore(path).create("deepseek-chat", owner=OWNER)["session_id"]

    reopened = SessionStore(path)
    assert reopened.get(sid, owner=OWNER) is not None
    assert reopened.get(sid, owner=OTHER) is None


def test_title_from_first_user_message(tmp_path):
    path = str(tmp_path / "sessions.json")
    store = SessionStore(path)
    sid = store.create("deepseek-chat", owner=OWNER)["session_id"]
    store.add_message(sid, OWNER, "user", "帮我看看这段 ChromaDB 的维度报错怎么解决")
    assert store.get(sid, owner=OWNER)["title"].startswith("帮我看看")


def test_unknown_session_is_rejected(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.json"))
    assert store.add_message("does-not-exist", OWNER, "user", "x") is False
    assert store.delete("does-not-exist", OWNER) is False
    assert store.get("does-not-exist", owner=OWNER) is None


def test_get_returns_copy(tmp_path):
    """拿到的会话被外部改写不应污染存储（否则会绕过落盘）。"""
    path = str(tmp_path / "sessions.json")
    store = SessionStore(path)
    sid = store.create("deepseek-chat", owner=OWNER)["session_id"]
    store.add_message(sid, OWNER, "user", "原始")

    fetched = store.get(sid, owner=OWNER)
    fetched["messages"].append({"role": "user", "content": "伪造"})

    assert len(store.get(sid, owner=OWNER)["messages"]) == 1


def test_corrupt_file_recovered_not_crashing(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = SessionStore(str(path))
    assert store.list_summaries(OWNER) == []
    assert (tmp_path / "sessions.json.corrupt").exists()


def test_delete_removes_persisted_session(tmp_path):
    path = str(tmp_path / "sessions.json")
    store = SessionStore(path)
    sid = store.create("deepseek-chat", owner=OWNER)["session_id"]
    assert store.delete(sid, OWNER) is True

    reopened = SessionStore(path)
    assert reopened.get(sid, owner=OWNER) is None
    assert sid not in json.loads(Path(path).read_text(encoding="utf-8"))
