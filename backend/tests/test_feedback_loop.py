"""自我成长反馈链路的单元级测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import app.preference_analyzer as pa
from app.main import app
from app.session.session_store import SessionStore

client = TestClient(app)


@pytest.fixture
def data_files(tmp_path, monkeypatch):
    """把反馈与偏好文件重定向到临时目录，避免污染仓库真实数据。"""
    fb = tmp_path / "feedback.json"
    pf = tmp_path / "preference.txt"
    monkeypatch.setattr(pa, "FEEDBACK_FILE", str(fb))
    monkeypatch.setattr(pa, "PREFERENCE_FILE", str(pf))
    return fb, pf


def test_negative_rating_counts_as_dislike(data_files):
    """此前判定写的是 rating == 0，而接口传的是 -1，导致点踩永远统计不到。

    这些行没有 user_id：按人分账之前所有反馈都出自"人人都是本机管理员"的年代，
    所以它们算在 default_user 名下，摘要也继续落在他那份历史文件里。
    """
    fb, pf = data_files
    json.dump([{"message_id": "a", "rating": 1},
               {"message_id": "b", "rating": -1},
               {"message_id": "c", "rating": -1}],
              open(fb, "w", encoding="utf-8"))

    pa.analyze_and_update_preference(pa.LEGACY_USER_ID)
    summary = open(pf, encoding="utf-8").read()
    assert "1 次满意" in summary
    assert "2 次不满意" in summary
    assert "反馈消极" in summary


def test_read_preference_roundtrip(data_files):
    """本机管理员照旧读那份 preference.txt —— 改名等于把他多年的反馈白扔掉。"""
    _, pf = data_files
    assert pa.read_preference(pa.LEGACY_USER_ID) == ""      # 文件不存在时不报错
    pf.write_text("用户偏好简洁回答", encoding="utf-8")
    assert pa.read_preference(pa.LEGACY_USER_ID) == "用户偏好简洁回答"
    assert pa.preference_path(pa.LEGACY_USER_ID) == str(pf), "管理员那份沿用老文件名"


def test_preference_is_aggregated_per_user(data_files):
    """每个人只统计自己的反馈行。

    这是 I3 的核心：反馈行上一轮已经带 user_id 了，但那只是数据前提——
    聚合仍然全局做一份，写进唯一的 preference.txt，再注入每个人的提示词。
    """
    fb, pf = data_files
    json.dump([{"message_id": "a", "rating": 1, "user_id": "u_a"},
               {"message_id": "b", "rating": 1, "user_id": "u_a"},
               {"message_id": "c", "rating": -1, "user_id": "u_b"}],
              open(fb, "w", encoding="utf-8"))

    pa.analyze_and_update_preference("u_a")
    pa.analyze_and_update_preference("u_b")

    a, b = pa.read_preference("u_a"), pa.read_preference("u_b")
    assert "2 条反馈" in a and "2 次满意" in a and "0 次不满意" in a, a
    assert "反馈积极" in a, "A 自己都是 👍，就该读到'可以继续简洁'"
    assert "1 条反馈" in b and "1 次不满意" in b and "反馈消极" in b, b
    assert "反馈积极" not in b and "反馈消极" not in a, "两份摘要不许互相渗透"

    # 落盘也是分开的两个文件，管理员那份历史文件一个字节都不动
    assert pa.preference_path("u_a") != pa.preference_path("u_b") != str(pf)
    assert not pf.exists(), "别人的反馈不能写进管理员那份 preference.txt"


def test_a_stranger_with_no_feedback_reads_nothing(data_files):
    """没有反馈的人读到空串：注入侧因此压根不加那段偏好，而不是套别人的结论。"""
    fb, _ = data_files
    json.dump([{"message_id": "a", "rating": -1, "user_id": "u_a"}],
              open(fb, "w", encoding="utf-8"))
    pa.analyze_and_update_preference("u_a")
    assert pa.read_preference("u_b") == ""


def test_analyze_all_preferences_rebuilds_every_user(data_files):
    """后台定时器没有"当前调用者"，它必须逐个重算而不是再汇成一份全局摘要。"""
    fb, pf = data_files
    json.dump([{"message_id": "a", "rating": 1, "user_id": "u_a"},
               {"message_id": "b", "rating": -1, "user_id": "u_b"},
               {"message_id": "c", "rating": -1, "user_id": "u_b"},
               {"message_id": "d", "rating": 1}],          # 历史行 → 本机管理员
              open(fb, "w", encoding="utf-8"))

    pa.analyze_all_preferences()

    assert sorted(pa.users_with_feedback()) == sorted(["u_a", "u_b", pa.LEGACY_USER_ID])
    assert "反馈积极" in pa.read_preference("u_a")
    assert "反馈消极" in pa.read_preference("u_b")
    assert "1 次满意" in open(pf, encoding="utf-8").read()


def test_a_hostile_user_id_cannot_escape_or_share_the_preference_file(data_files):
    """user_id 要进文件名，所以它得先被剥成无害字符。

    注册名不受控（`RESERVED_NAMES` 只管重名），于是 "../../evil" 这种身份不能把
    偏好文件写到数据目录之外；而 "a/b" 与 "ab" 是两个不同的人，也不能因为剥掉
    字符就撞进同一个文件——那等于互相改写对方的摘要。
    """
    _, pf = data_files
    for hostile in ("../../evil", "a/../b", "中文 身份", "/abs/path/x"):
        p = Path(pa.preference_path(hostile))
        assert p.parent == pf.parent, f"偏好文件写出了数据目录: {p}"
        assert p.name.startswith("preference-") and ".." not in p.name
        assert pa.read_preference(hostile) == ""
    assert pa.preference_path("a/b") != pa.preference_path("ab")


def test_pipeline_injects_the_callers_own_preference(data_files):
    """注入侧同一条链路上：A 的点踩只改 A 下一轮的语气，B 的一句都不沾。

    聚合分账了、读取还指回那一份全局文件，等于什么都没修——所以这条打的是
    ChatPipeline.inject_context，而不是 preference_analyzer 自己。
    """
    from app.pipeline import ChatPipeline

    fb, _ = data_files
    json.dump([{"message_id": "a", "rating": -1, "user_id": "u_a"},
               {"message_id": "b", "rating": -1, "user_id": "u_a"},
               {"message_id": "c", "rating": 1, "user_id": "u_b"}],
              open(fb, "w", encoding="utf-8"))
    pa.analyze_all_preferences()

    a_msgs, _ = ChatPipeline("u_a").inject_context([], "讲讲向量数据库")
    b_msgs, _ = ChatPipeline("u_b").inject_context([], "讲讲向量数据库")
    a_text = a_msgs[0]["content"]
    b_text = b_msgs[0]["content"]

    assert "反馈消极" in a_text, "A 自己踩出来的结论该注入给 A"
    assert "反馈积极" in b_text and "反馈消极" not in b_text, "B 拿到的只能是他自己那份"
    # 没有反馈的人：一句偏好都不注入（不是把别人的结论套给他）
    c_msgs, _ = ChatPipeline("u_nobody").inject_context([], "讲讲向量数据库")
    assert c_msgs == [], f"没有自己的偏好摘要时不该凭空造一段: {c_msgs}"


def test_memory_ids_survive_replace(tmp_path):
    """客户端整体回写会话时不能把记忆关联信息洗掉，否则反馈找不到加权对象。"""
    owner = "u_owner"
    store = SessionStore(str(tmp_path / "sessions.json"))
    sid = store.create("deepseek-chat", owner=owner)["session_id"]
    store.add_message(sid, owner, "user", "问题")
    store.add_message(sid, owner, "assistant", "回答", "m-1", ["mem-a", "mem-b"])

    assert store.replace(sid, owner, [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答",
         "message_id": "m-1", "memory_ids": ["mem-a", "mem-b"]},
    ]) is True

    found = store.find_message("m-1", owner)
    assert found["session_id"] == sid
    assert found["message"]["memory_ids"] == ["mem-a", "mem-b"]


def test_find_message_missing_returns_none(tmp_path):
    store = SessionStore(str(tmp_path / "s.json"))
    assert store.find_message("nope", "u_owner") is None
    assert store.find_message(None, "u_owner") is None


def test_feedback_endpoint_reports_no_memories_under_fake_store():
    """测试环境走内存假存储，没有记忆 id 可加权，但接口必须诚实说明而非假装生效。"""
    sid = client.post("/v1/sessions").json()["session_id"]
    res = client.post("/v1/chat", json={
        "model": "fake-model", "messages": [{"role": "user", "content": "你好"}],
        "session_id": sid})
    mid = res.json()["message_id"]

    fb = client.post("/v1/feedback", json={"message_id": mid, "rating": 1})
    assert fb.status_code == 200
    body = fb.json()
    assert body["status"] == "success"
    assert body["used_memories"] is False
    assert body["memory_weight_adjusted"] == 0
