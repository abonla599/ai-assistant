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
    """此前判定写的是 rating == 0，而接口传的是 -1，导致点踩永远统计不到。"""
    fb, pf = data_files
    json.dump([{"message_id": "a", "rating": 1},
               {"message_id": "b", "rating": -1},
               {"message_id": "c", "rating": -1}],
              open(fb, "w", encoding="utf-8"))

    pa.analyze_and_update_preference()
    summary = open(pf, encoding="utf-8").read()
    assert "1 次满意" in summary
    assert "2 次不满意" in summary
    assert "反馈消极" in summary


def test_read_preference_roundtrip(data_files):
    _, pf = data_files
    assert pa.read_preference() == ""          # 文件不存在时不报错
    pf.write_text("用户偏好简洁回答", encoding="utf-8")
    assert pa.read_preference() == "用户偏好简洁回答"


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
