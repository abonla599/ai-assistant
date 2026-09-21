"""`/health` 现在回的是「东西齐不齐」，不只是「进程还在」（app/core/selfcheck.py）。

进程活着这个判据，本仓已经证明过一次不够用：2026-09-20 那次 Error 1033，cloudflared
进程全程健在、看门狗每分钟全绿，而外面早就进不来。后端这边是同一个形状——密钥库
文件读不动、账本写不进、嵌入降级成全零伪嵌入、新限流账没登记，四种都不会让进程消失，
也全都不在旧 /health 的那一句 healthy 里。

所以这里两头都锁：坏东西必须被报出来（各条正向对照），以及**状态码不许跟着判决走**
（否则"配错了密钥"会变成看门狗每分钟重启一次后端，而重启修不好配置）。
"""
import json

import pytest

from app.core import selfcheck
from app.core.selfcheck import BROKEN, DEGRADED, OK, run


EXPECTED_CHECKS = {"模型服务", "密钥库", "用量账本", "记忆后端", "限流账", "数据落点"}


def test_health_reports_each_check_not_just_that_the_process_lives(client):
    body = client.get("/health").json()
    assert set(body["checks"]) == EXPECTED_CHECKS, body["checks"].keys()
    for label, item in body["checks"].items():
        assert item["status"] in (OK, DEGRADED, BROKEN), (label, item)
        assert item["detail"], f"{label} 报了状态却没说为什么"


def test_a_broken_verdict_still_returns_200(client, monkeypatch):
    """这条守的是一个决定，不是一个巧合。

    让 /health 在自检失败时返回 5xx，看门狗就会每分钟重启后端——而重启修不好任何
    配置问题，只会把正在进行的对话一起带走。判据交给日志与人。
    """
    monkeypatch.setattr(selfcheck, "CHECKS", (("模型服务", lambda: (BROKEN, "密钥库被删了")),))
    resp = client.get("/health")
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["status"] == BROKEN


def test_a_check_that_raises_becomes_broken_instead_of_500(monkeypatch):
    """自检自己也会坏（磁盘没了、依赖 import 失败）。坏成一行 broken，不许坏成异常。"""
    def boom():
        raise RuntimeError("读盘读到一半炸了")

    monkeypatch.setattr(selfcheck, "CHECKS", (("用量账本", boom),))
    body = run()
    assert body["status"] == BROKEN
    item = body["checks"]["用量账本"]
    assert item["status"] == BROKEN and "RuntimeError" in item["detail"], item


def test_overall_status_is_the_worst_item(monkeypatch):
    monkeypatch.setattr(selfcheck, "CHECKS", (
        ("a", lambda: (OK, "好")),
        ("b", lambda: (DEGRADED, "凑合")),
        ("c", lambda: (BROKEN, "坏了")),
    ))
    assert run()["status"] == BROKEN
    monkeypatch.setattr(selfcheck, "CHECKS", (
        ("a", lambda: (OK, "好")), ("b", lambda: (DEGRADED, "凑合")),
    ))
    assert run()["status"] == DEGRADED


# ------------------------------------------------------------------ 各条判据 --

def test_key_vault_disagreeing_with_the_records_is_broken(monkeypatch, tmp_path):
    """记录自报带密钥、密钥库文件却不在了——"搬了记录没搬密钥"那一类。

    症状是聊天全 401，而这一步之前界面上看一切都正常。
    """
    from app.core import providers

    class _Store:
        keys_path = str(tmp_path / "provider_keys.json")

        @staticmethod
        def all():
            return [{"id": "p1", "api_key": "sk-a" + "1" * 20}]

    monkeypatch.setattr(providers, "store", _Store)
    status, detail = selfcheck._check_key_vault()
    assert status == BROKEN, detail
    assert "1 条配置自报带密钥" in detail

    (tmp_path / "provider_keys.json").write_text("{ not json", encoding="utf-8")
    assert selfcheck._check_key_vault()[0] == BROKEN

    (tmp_path / "provider_keys.json").write_text(
        json.dumps({"p1": "sk-a" + "1" * 20}), encoding="utf-8")
    assert selfcheck._check_key_vault()[0] == OK


def test_dummy_embeddings_are_degraded_and_say_so(monkeypatch):
    """全零伪嵌入 add/query 都不报错，只是检索结果完全不可信。

    这条是 degraded 不是 broken：不影响"能不能聊"，影响"记不记得住"。但它必须说得出
    来——否则它就是一个没人知道的功能缺失，而这正是本项目最贵的那类问题。
    """
    from app.memory import memory_router

    class _M:
        _dummy_embed = True

    monkeypatch.setattr(memory_router, "memory_manager", _M, raising=False)
    status, detail = selfcheck._check_memory_backend()
    assert status == DEGRADED and "伪嵌入" in detail, (status, detail)

    monkeypatch.setattr(memory_router, "memory_manager", None, raising=False)
    monkeypatch.setattr(memory_router, "memory_init_error", "维度冲突", raising=False)
    status, detail = selfcheck._check_memory_backend()
    assert status == BROKEN and "维度冲突" in detail, (status, detail)


def test_a_ledger_without_a_window_is_caught(monkeypatch):
    """`_prune` 只遍历 `_LEDGERS`，且按每本账自己的窗口裁剪。

    新写一本账忘了登记，或者登记了但窗口给了 0：内存随来源数单向涨，限流本身照样
    生效，所以没有任何可观察症状——这种"只对一半"的账正是本文件存在的理由。
    """
    from app.core import auth_router

    extra = dict(test=dict())
    monkeypatch.setattr(auth_router, "_LEDGERS", ((extra, 0),), raising=False)
    status, detail = selfcheck._check_limiters()
    assert status == BROKEN and "窗口" in detail, (status, detail)

    monkeypatch.setattr(auth_router, "_LEDGERS", (), raising=False)
    assert selfcheck._check_limiters()[0] == BROKEN


def test_real_ledgers_all_carry_a_window():
    """正向对照的反面：今天真实登记的那几本必须过这条，否则上一条是空转的。"""
    from app.core import auth_router

    assert len(auth_router._LEDGERS) >= 5, "限流账少于五本，这条锁就检不到东西了"
    assert selfcheck._check_limiters()[0] == OK, selfcheck._check_limiters()[1]


def test_missing_data_directory_is_broken(monkeypatch, tmp_path):
    """数据落在不存在的目录里 = 下一次写才炸。提前到自检这里说。"""
    from app.core import paths

    monkeypatch.setattr(paths, "resolve_all_data_paths",
                        lambda: [("会话", str(tmp_path / "nope" / "sessions.json"))])
    status, detail = selfcheck._check_data_paths()
    assert status == BROKEN and "会话" in detail, (status, detail)

    assert selfcheck._check_data_paths.__doc__, "判据为什么在这儿，得写下来"


def test_a_label_that_happens_to_contain_a_key_shape_is_scrubbed(monkeypatch, tmp_path):
    """/health 是免鉴权的公开端点，而管理员给配置起的名字是人手打的。

    详情一句要经过出口脱敏才回得去——同一件事在 /v1/providers 上已经做过（A-2），
    这里是第二个出口，容易被忘记的就是这种"第二个出口"。
    """
    from app.core import providers

    secret = "sk-" + "a1b2c3d4" * 4

    class _Store:
        keys_path = str(tmp_path / "provider_keys.json")

        @staticmethod
        def all():
            return [{"id": "p1", "label": "我的", "api_key": secret, "model": "m"}]

        @staticmethod
        def resolve():
            return {"id": "p1", "label": f"我的{secret}号", "model": "m"}

    monkeypatch.setattr(providers, "store", _Store)
    body = json.dumps(run(), ensure_ascii=False)
    assert secret not in body, "自检详情把密钥原样带出去了"
    assert "已隐去" in body or "末四位" in body, body


def test_scrubbing_fails_closed_when_the_vault_itself_is_unreadable(monkeypatch):
    """脱敏要看密钥库，而"密钥库读不动"恰是这里要报的那类故障。

    两个方向差一个字：把详情原样发出去是失败开放（路人读到 key），换成一句
    "说不清"是失败封闭（少一条线索）。这里必须选后者。
    """
    from app.core import providers

    def boom(_text):
        raise OSError("密钥库文件被别的进程锁着")

    monkeypatch.setattr(providers, "scrub_secrets", boom)
    body = run()
    assert body["checks"], "自检什么都不肯报，这条锁就是空转"
    for label, item in body["checks"].items():
        assert item["detail"] == "（详情无法安全呈现）", (label, item)
        assert item["status"] in (OK, DEGRADED, BROKEN), item


def test_no_absolute_paths_leak_into_the_public_answer(client):
    """另一处泄露面：进程目录结构。探针要的判据是 ok/不 ok，不是这台机器长什么样。"""
    body = json.dumps(client.get("/health").json(), ensure_ascii=False)
    assert "Users" not in body and "\\" not in body, body
