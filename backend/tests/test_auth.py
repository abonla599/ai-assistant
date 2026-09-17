"""身份存储单元测试（纯存储层，不涉及 HTTP）。

这里原先有 11 条针对旧"全局 ACCESS_TOKEN 口令中间件"的 HTTP 测试。该中间件已被
app/core/authz.py 的身份解析取代：凭据解析中仍然成立的部分（方案名大小写不敏感、
不写方案名、x-access-token 回退）按新语义迁到 test_authz_failclosed.py，其余只
服务于"一个口令放行所有人"模型的（典型如"未配口令即全开"）直接删除——在新模型里
那条恰恰是要堵的洞。
"""
import json
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core import auth as auth_module
from app.core.auth import AuthError, AuthStore


# ---------- 身份存储 app/core/auth.py（纯存储层，不涉及 HTTP） ----------


@pytest.fixture
def store(tmp_path):
    s = AuthStore(path=str(tmp_path / "users.json"),
                  invites_path=str(tmp_path / "invites.json"))
    s.create_invite("admin")
    return s


def test_register_returns_token_once_and_stores_only_hash(store):
    principal, token = store.register(code=store.list_invites()[0]["code"], username="张三")
    assert principal.username == "张三"
    assert principal.role == "user"
    assert principal.user_id.startswith("u_") and len(principal.user_id) == 10
    raw = open(store.path, encoding="utf-8").read()
    assert token not in raw, "明文令牌绝不能落盘"
    assert json.loads(raw)[principal.user_id]["token_hash"].startswith("sha256:")
    assert store.resolve(token).user_id == principal.user_id


def test_resolve_rejects_unknown_and_revoked(store):
    principal, token = store.register(code=store.list_invites()[0]["code"], username="李四")
    assert store.resolve("not-a-token") is None
    store.disable_user(principal.user_id)
    assert store.resolve(token) is None, "停用必须让旧令牌立即失效"


def test_username_dedup_is_case_insensitive(store):
    code = store.list_invites()[0]["code"]
    store.register(code=code, username="Alice")
    other = store.create_invite("admin")
    with pytest.raises(AuthError) as e:
        store.register(code=other, username="alice")
    assert "占用" in str(e.value)


def test_the_invite_code_is_validated_before_anything_about_the_username(store):
    """检查顺序就是注册端点的泄露面，所以它是存储层契约，不只是实现细节。

    注册端点免凭据。先查重名，等于一个邀请码都没有的人也能问出"这个名字被占了
    吗"，两个方向都得到真话。先验码之后，没码的人听到的只有同一句"邀请码无效"；
    而握着有效未用码的人照旧听得到真话——他本来就能注册，不多这一比特。
    """
    store.register(code=store.list_invites()[0]["code"], username="Alice")

    reasons = {}
    for label, username in (("撞名", "alice"), ("空闲名", "bob"), ("空用户名", ""),
                            ("保留字", "admin"), ("超长", "z" * 25)):
        with pytest.raises(AuthError) as e:
            store.register(code="NOPE-NOPE", username=username)
        reasons[label] = str(e.value)
    assert set(reasons.values()) == {reasons["空闲名"]}, \
        f"没有码的时候，用户名的任何差别都不该反映进答案：{reasons}"
    assert "邀请码" in reasons["空闲名"] and "占用" not in reasons["空闲名"]

    # 有效未用码 + 撞名：真话照旧要说，否则用户改不了名就只能去缠管理员
    valid = store.create_invite("admin")
    with pytest.raises(AuthError) as honest:
        store.register(code=valid, username="ALICE")
    assert "占用" in str(honest.value)
    assert [i for i in store.list_invites() if i["code"] == valid][0]["used_by"] == [], \
        "被拒的注册不该消耗邀请码，否则改名的人手里就剩一枚废码"


@pytest.mark.parametrize("bad", ["admin", "default_user", "", "  ", "x" * 25])
def test_reserved_and_malformed_usernames_rejected(store, bad):
    with pytest.raises(AuthError):
        store.register(code=store.list_invites()[0]["code"], username=bad)


def test_invite_is_single_use(store):
    code = store.create_invite("admin")
    store.register(code=code, username="王五")
    with pytest.raises(AuthError):
        store.register(code=code, username="赵六")


def test_rotate_token_invalidates_previous_one(store):
    principal, old = store.register(code=store.list_invites()[0]["code"], username="孙七")
    new = store.rotate_token(principal.user_id)
    assert new != old
    assert store.resolve(old) is None
    assert store.resolve(new).user_id == principal.user_id


def test_invite_code_avoids_ambiguous_characters(store):
    codes = [store.create_invite("admin") for _ in range(40)]
    assert not (set("".join(codes)) & set("0O1I"))
    assert all(len(c) == 9 and c[4] == "-" for c in codes)


def test_flush_survives_reload(tmp_path):
    """写盘必须真的可回读：原子替换没生效时这条会红。"""
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    code = store.create_invite("admin")
    principal, _ = store.register(code=code, username="周八")
    reloaded = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    assert [u["user_id"] for u in reloaded.list_users()] == [principal.user_id]
    assert not os.path.exists(path + ".tmp"), "临时文件必须被原子替换掉"


# ---------- 评审修复：读令牌不该是写盘热路径 ----------


@pytest.fixture
def clocked(tmp_path, monkeypatch):
    """把时间交给测试：last_used_at 的降频写盘只有可控时钟下才测得准。"""
    moment = {"now": datetime(2026, 9, 16, 12, 0)}
    monkeypatch.setattr(auth_module, "_now_dt", lambda: moment["now"])
    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    store.create_invite("admin")
    return store, moment


def _count_flushes(store, monkeypatch):
    """数写盘次数，但不改变行为——这样连磁盘内容也能一起验。"""
    calls = []
    real = store._flush

    def spy():
        calls.append(len(calls))
        return real()

    monkeypatch.setattr(store, "_flush", spy)
    return calls


def test_resolve_updates_memory_but_never_writes_on_hot_path(clocked, monkeypatch):
    store, moment = clocked
    principal, token = store.register(code=store.list_invites()[0]["code"], username="张三")
    flushed = _count_flushes(store, monkeypatch)

    base = datetime(2026, 9, 16, 12, 0)
    for minutes in range(1, 50):
        moment["now"] = base + timedelta(minutes=minutes)
        assert store.resolve(token).user_id == principal.user_id
    assert flushed == [], "令牌兑换是每次请求都要走的热路径，盘满或被编辑器/杀软锁住时不能变成 500"


def test_resolve_persists_last_used_at_once_it_is_an_hour_old(clocked, monkeypatch):
    store, moment = clocked
    principal, token = store.register(code=store.list_invites()[0]["code"], username="李四")

    moment["now"] = datetime(2026, 9, 16, 12, 30)
    flushed = _count_flushes(store, monkeypatch)
    store.resolve(token)
    assert flushed == [], "半小时内重复读取不该再产生写盘"

    moment["now"] = datetime(2026, 9, 16, 13, 31)
    store.resolve(token)
    assert len(flushed) == 1, "攒够一小时的补写必须发生，否则活跃度永远不上盘"
    store.resolve(token)
    assert len(flushed) == 1, "刚补写过就又不该写了"

    disk = json.load(open(store.path, encoding="utf-8"))[principal.user_id]
    assert disk["last_used_at"].startswith("2026-09-16T13:31"), "补写要真的落盘"


def test_resolve_records_last_used_in_memory_even_when_it_skips_the_flush(clocked, monkeypatch):
    store, moment = clocked
    principal, token = store.register(code=store.list_invites()[0]["code"], username="王五")
    moment["now"] = datetime(2026, 9, 16, 12, 42)
    flushed = _count_flushes(store, monkeypatch)
    assert store.resolve(token).user_id == principal.user_id
    assert flushed == [], "这一次读取自己不写盘"
    assert store.disable_user(principal.user_id) is True
    disk = json.load(open(store.path, encoding="utf-8"))[principal.user_id]
    assert disk["last_used_at"].startswith("2026-09-16T12:42"), "跳写不等于不记：内存刷新要带得下去"
    assert disk["disabled"] is True


# ---------- 评审修复：轮换令牌不得反向解除停用 ----------


def test_rotate_token_leaves_a_disabled_user_disabled(store):
    principal, old = store.register(code=store.list_invites()[0]["code"], username="孙七")
    store.disable_user(principal.user_id)
    new = store.rotate_token(principal.user_id)
    assert new != old
    assert store.resolve(new) is None, "换令牌不是重新启用账号，撤销能力不能被它悄悄抵消"
    record = [u for u in store.list_users() if u["user_id"] == principal.user_id][0]
    assert record["disabled"] is True, "停用状态必须原样留在记录里"


# ---------- 评审修复：坏数据只该判"不匹配"，不该抛异常 ----------


def test_resolve_returns_none_for_hand_edited_token_hash(tmp_path):
    """hmac.compare_digest 收到非 ASCII str 会抛 TypeError——手改过 users.json
    或塞进 null 就必须表现为"这枚令牌解不出来"，而不是把 500 甩给调用方。"""
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    code = store.create_invite("admin")
    principal, token = store.register(code=code, username="钱六")
    disk = json.load(open(path, encoding="utf-8"))
    disk[principal.user_id]["token_hash"] = "sha256：被人为改成了中文"
    disk["u_broken"] = {"user_id": "u_broken", "username": "坏记录", "username_lc": "坏记录",
                        "token_hash": None, "disabled": False, "role": "user"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk, f, ensure_ascii=False)

    reloaded = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    assert reloaded.resolve(token) is None
    assert reloaded.resolve("another-token") is None


# ---------- 评审修复：重码检查必须在临界区内 ----------


class _LockProbe:
    """替下 store._lock，只为一件事：让测试能看见某次调用是否发生在临界区内。"""

    def __init__(self):
        self._inner = threading.RLock()
        self.held = False

    def __enter__(self):
        self._inner.acquire()
        self.held = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.held = False
        self._inner.release()
        return False


def test_create_invite_generates_and_checks_the_code_inside_the_lock(tmp_path, monkeypatch):
    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    probe = _LockProbe()
    store._lock = probe
    # 连发两次同一个码，逼出 while 重试分支；锁外检查时这些调用都发生在临界区外
    codes = iter(["XXXX-1111", "XXXX-1111", "YYYY-2222"])
    held: list = []

    def fake_new_code():
        held.append(probe.held)
        return next(codes)

    monkeypatch.setattr(auth_module, "_new_code", fake_new_code)
    assert store.create_invite("admin") == "XXXX-1111"
    assert store.create_invite("admin") == "YYYY-2222"
    assert held and all(held), "查重与写入之间让出锁，并发时会有人静默丢掉一个邀请码"
    assert sorted(i["code"] for i in store.list_invites()) == ["XXXX-1111", "YYYY-2222"]


# ---------- 评审修复：撤销与兑换对码的归一化必须一致 ----------


def test_revoke_invite_normalizes_code_the_way_register_does(store):
    code = store.create_invite("admin")
    assert store.revoke_invite(f"  {code.lower()} \n") is True, \
        "管理员从手机粘来的小写带空格码，不该得到一个假 404"
    assert code not in [i["code"] for i in store.list_invites()]
    with pytest.raises(AuthError):
        store.register(code=f" {code.lower()} ", username="新用户")
    assert store.revoke_invite("no-such-code") is False


# ---------- 评审修复：list_invites 不得把活对象交出去 ----------


def test_list_invites_snapshot_does_not_alias_the_live_used_by_list(store):
    code = store.create_invite("admin", max_uses=3)
    snapshot = [i for i in store.list_invites() if i["code"] == code][0]
    snapshot["used_by"].append("u_forged")
    snapshot["max_uses"] = 99
    again = [i for i in store.list_invites() if i["code"] == code][0]
    assert again["used_by"] == [], "返回嵌套活引用的浅拷贝，调用方 append 一下就改了库"
    assert again["max_uses"] == 3
    principal, _ = store.register(code=code, username="正常用户")
    assert [i for i in store.list_invites() if i["code"] == code][0]["used_by"] == [principal.user_id]


# ---------- 补齐接口承诺但先前没有测试钉住的行为 ----------


def test_delete_user_removes_the_record_and_its_token(tmp_path):
    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    code = store.create_invite("admin")
    principal, token = store.register(code=code, username="周九")
    assert store.delete_user(principal.user_id) is True
    assert store.resolve(token) is None, "删号后旧令牌必须立刻解不出来"
    assert store.list_users() == []
    assert store.delete_user(principal.user_id) is False, "删不存在的用户要如实返回 False"
    reloaded = AuthStore(path=str(tmp_path / "users.json"),
                         invites_path=str(tmp_path / "invites.json"))
    assert reloaded.list_users() == []
    assert reloaded.resolve(token) is None


def test_invite_with_max_uses_two_admits_exactly_two_registrations(store):
    code = store.create_invite("admin", max_uses=2)
    first, _ = store.register(code=code, username="一号")
    second, _ = store.register(code=code, username="二号")
    assert first.user_id != second.user_id
    used = [i for i in store.list_invites() if i["code"] == code][0]["used_by"]
    assert used == [first.user_id, second.user_id]
    with pytest.raises(AuthError):
        store.register(code=code, username="三号")
    assert [u["username"] for u in store.list_users()] == ["一号", "二号"]


# ---------- 评审修复：轮换不再顺手启用，所以启用必须是它的对称动作 ----------


def test_enable_user_undoes_a_disable_and_reports_unknown_ids(store):
    """disable 是一扇单向门的话，运维就只能删号重建——那不是撤销，是赌气。"""
    principal, token = store.register(code=store.list_invites()[0]["code"], username="恢复用")
    store.disable_user(principal.user_id)
    assert store.resolve(token) is None
    assert store.enable_user(principal.user_id) is True
    assert store.resolve(token).user_id == principal.user_id, "启用必须让原令牌立刻可用"
    disk = json.load(open(store.path, encoding="utf-8"))[principal.user_id]
    assert disk["disabled"] is False, "启用要落盘，否则重启后账号又躺回停用堆里"
    assert store.enable_user("u_nobody") is False, "不存在的用户要如实返回 False"


# ---------- 坏身份库必须先改名留证：形状不对也算坏 ----------
# `_load` 上面那条不变量写的是"身份库坏了绝不能静默当空库继续跑"。原先只有
# parse 失败那一支照做，"读得懂但顶层不是对象"这一支什么都不 print、什么都不备份，
# 于是库以空表启动，下一次 create_invite/register/disable 就把 users.json 整个覆盖
# 掉——不变量正好从这一支被绕过去。


@pytest.mark.parametrize("broken,desc", [
    ('{"u_1": {"username": "张三", "token_hash": "sha256:aa', "半截 JSON"),
    ('[{"user_id": "u_1", "username": "张三"}]', "读得懂但顶层是列表"),
])
def test_broken_identity_file_is_kept_and_never_clobbered(tmp_path, broken, desc):
    from app.core.auth import AuthStore

    users = tmp_path / "users.json"
    users.write_text(broken, encoding="utf-8")

    store = AuthStore(path=str(users), invites_path=str(tmp_path / "invites.json"))
    assert (tmp_path / "users.json.corrupt").exists(), f"{desc}：必须先备份成 .corrupt"
    assert store.list_users() == [], f"{desc}：仍要能以空库启动，别让服务起不来"

    # 判据在这一行之后：把库写回去的那次注册，不能顺手抹掉唯一的原始材料
    code = store.create_invite("bootstrap")
    principal, _ = store.register(code=code, username="重建者")
    assert (tmp_path / "users.json.corrupt").read_text(encoding="utf-8") == broken, \
        f"{desc}：备份必须活过一次写盘"
    assert list(json.loads(users.read_text(encoding="utf-8"))) == [principal.user_id]
