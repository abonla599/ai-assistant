"""身份存储单元测试（纯存储层，不涉及 HTTP）。

2026-09-17 凭据模型换过一次：注册不再吃邀请码，改成用户名 + 自设密码换一枚会话
令牌。原先有 7 条专门钉邀请码语义的用例（单次使用、码字符集、max_uses、撤销归一
化、快照不交活引用、临界区重取码、"码先名后"的顺序）随功能一起删除——它们保护的
对象已经不在了。

但"码先名后"那条背后的**顾虑**一行都没消失，它只是换了位置：从前那个免凭据端点
靠邀请码挡住"任何人都能问这个名字被占了吗"，现在注册全开放，那道泄露面改由
auth_router 按真实 IP 计费来限（见 test_auth_endpoints.py），而登录端点则必须
对"查无此人 / 密码错 / 已停用"三件事给出逐字节相同的回答，包括耗时。
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

PW = "correct-horse-battery"
PW2 = "another-correct-horse"


# ---------- 身份存储 app/core/auth.py ----------


@pytest.fixture
def store(tmp_path):
    return AuthStore(path=str(tmp_path / "users.json"))


def test_register_returns_token_once_and_stores_only_hashes(store):
    principal, token = store.register(username="张三", password=PW)
    assert principal.username == "张三"
    assert principal.role == "user"
    assert principal.user_id.startswith("u_") and len(principal.user_id) == 10
    raw = open(store.path, encoding="utf-8").read()
    assert token not in raw, "明文令牌绝不能落盘"
    assert PW not in raw, "明文密码绝不能落盘——users.json 与会话数据同目录"
    disk = json.loads(raw)[principal.user_id]
    assert disk["pw_hash"].startswith("bcrypt:"), "密码必须走慢哈希，不是 sha256"
    assert disk["tokens"][0].startswith("sha256:")
    assert store.resolve(token).user_id == principal.user_id


def test_resolve_rejects_unknown_and_revoked(store):
    principal, token = store.register(username="李四", password=PW)
    assert store.resolve("not-a-token") is None
    store.disable_user(principal.user_id)
    assert store.resolve(token) is None, "停用必须让旧令牌立即失效"


def test_username_dedup_is_case_insensitive(store):
    store.register(username="Alice", password=PW)
    with pytest.raises(AuthError) as e:
        store.register(username="alice", password=PW)
    assert "已存在" in str(e.value)
    assert e.value.taken is True, \
        "重名必须带着 taken 出来：HTTP 层靠它决定是否计入按 IP 的失败预算，" \
        "而那句文案是要改的（这次就从「已被占用」改成了「已存在」），挂在字上会静默失效"


@pytest.mark.parametrize("bad", ["admin", "default_user", "", "  ", "x" * 25])
def test_reserved_and_malformed_usernames_rejected(store, bad):
    with pytest.raises(AuthError):
        store.register(username=bad, password=PW)


@pytest.mark.parametrize("bad", ["", "  ", "a" * 7, "x" * 600])
def test_malformed_passwords_rejected(store, bad):
    """太短等于没设防；太长只为挡"贴进来一本书"，真正的长度问题由预哈希解决。"""
    with pytest.raises(AuthError):
        store.register(username="形状用户", password=bad)


def test_login_accepts_the_right_password_and_rejects_the_wrong_one(store):
    store.register(username="老王", password=PW)
    principal, token = store.login("老王", PW)
    assert principal.username == "老王"
    assert store.resolve(token).user_id == principal.user_id
    with pytest.raises(AuthError):
        store.login("老王", PW2)


def test_login_is_case_insensitive_on_username_like_register(store):
    """去重按 casefold，登录就必须按同一份规则——否则"Alice 注册、alice 登不进"。"""
    store.register(username="Alice", password=PW)
    principal, _ = store.login("  aLiCe ", PW)
    assert principal.user_id.startswith("u_")


def test_login_failure_says_the_very_same_thing_for_all_three_causes(store):
    """查无此人 / 密码错 / 账号停用，三句必须一字不差地相同。

    注册全开放之后用户名本身就是可猜的公开信息，登录端点若在这三种情况上换了
    措辞，它就是一份免费的用户名存在性名单——而且"这个号被停了"还额外告诉别人
    该去找谁求情。
    """
    principal, _ = store.register(username="在册的", password=PW)
    reasons = {}
    for label, args in (("查无此人", ("从没注册过", PW)),
                        ("密码错", ("在册的", PW2)),
                        ("已停用", ("在册的", PW))):
        if label == "已停用":
            store.disable_user(principal.user_id)
        with pytest.raises(AuthError) as e:
            store.login(*args)
        reasons[label] = str(e.value)
    assert len(set(reasons.values())) == 1, f"三种失败说出了不同的话：{reasons}"


def test_unknown_username_still_pays_the_bcrypt_cost(store, monkeypatch):
    """文案一样还不够快慢一样：跳过 bcrypt 的"查无此人"会秒回，时序本身就是名单。"""
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    store.register(username="真人", password=PW)

    with pytest.raises(AuthError):
        store.login("真人", "肯定不对的密码")
    known = len(calls)
    calls.clear()
    with pytest.raises(AuthError):
        store.login("查无此人", "肯定不对的密码")
    assert len(calls) == known, "查无此人没付 bcrypt 的代价，响应快慢就泄露了用户名是否存在"


def test_long_passwords_sharing_a_prefix_are_not_interchangeable(store):
    """bcrypt 只吃前 72 字节：不预哈希的话，第 73 位起全都作废。

    后果是"我设了长密码"和"攻击者只要猜前 72 字节"等价，而且两个不同的长密码
    会互相开门。这条断言就是冲着"有人哪天把 _prehash 删了"写的。
    """
    head = "0123456789" * 8          # 80 位
    assert len(head) > 72
    store.register(username="长密码", password=head + "-tail-A")
    with pytest.raises(AuthError):
        store.login("长密码", head + "-tail-B")
    principal, _ = store.login("长密码", head + "-tail-A")
    assert principal.username == "长密码", "第 73 位之后的差异必须算两个不同密码"


def test_login_appends_a_session_token_and_keeps_the_old_ones(store):
    """手机 + 桌面 + 浏览器同时在用是正当需求，登录不能把别人顶下线。"""
    principal, first = store.register(username="多设备", password=PW)
    _, second = store.login("多设备", PW)
    assert second != first
    assert store.resolve(first).user_id == principal.user_id, "新登录不该踢掉旧设备"
    assert store.resolve(second).user_id == principal.user_id


def test_session_token_list_is_bounded_and_evicts_the_oldest(store):
    """只进不出的令牌表会让一个脚本把这个账号的记录无限撑大。"""
    _, oldest = store.register(username="刷令牌", password=PW)
    issued = [oldest]
    for _ in range(auth_module.MAX_SESSION_TOKENS + 2):
        _, token = store.login("刷令牌", PW)
        issued.append(token)
    record = [u for u in store.list_users() if u["username"] == "刷令牌"][0]
    assert len(record["tokens"]) == auth_module.MAX_SESSION_TOKENS
    assert store.resolve(oldest) is None, "超出上限要丢最老的那一枚"
    assert store.resolve(issued[-1]).username == "刷令牌", "最新的必须还活着"


def test_rotate_token_invalidates_every_previous_session(store):
    """多设备并存之后，"撤销"必须是清空整张令牌表。

    只换掉其中一枚等于什么都没撤销——别人手机上那枚还能继续用。
    """
    principal, phone = store.register(username="要踢的人", password=PW)
    _, laptop = store.login("要踢的人", PW)
    _, browser = store.login("要踢的人", PW)
    fresh = store.rotate_token(principal.user_id)
    for stale in (phone, laptop, browser):
        assert store.resolve(stale) is None, f"轮换后仍有 {len(store.list_users())} 人的旧令牌可用"
    assert store.resolve(fresh).user_id == principal.user_id


def test_rotate_token_leaves_a_disabled_user_disabled(store):
    principal, old = store.register(username="孙七", password=PW)
    store.disable_user(principal.user_id)
    new = store.rotate_token(principal.user_id)
    assert new != old
    assert store.resolve(new) is None, "换令牌不是重新启用账号，撤销能力不能被它悄悄抵消"
    record = [u for u in store.list_users() if u["user_id"] == principal.user_id][0]
    assert record["disabled"] is True, "停用状态必须原样留在记录里"


def test_flush_survives_reload(tmp_path):
    """写盘必须真的可回读：原子替换没生效时这条会红。"""
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path)
    principal, token = store.register(username="周八", password=PW)
    reloaded = AuthStore(path=path)
    assert [u["user_id"] for u in reloaded.list_users()] == [principal.user_id]
    assert reloaded.resolve(token).user_id == principal.user_id, "令牌摘要要能跨进程复用"
    assert not os.path.exists(path + ".tmp"), "临时文件必须被原子替换掉"


# ---------- 读令牌不该是写盘热路径 ----------


@pytest.fixture
def clocked(tmp_path, monkeypatch):
    """把时间交给测试：last_used_at 的降频写盘只有可控时钟下才测得准。"""
    moment = {"now": datetime(2026, 9, 16, 12, 0)}
    monkeypatch.setattr(auth_module, "_now_dt", lambda: moment["now"])
    return AuthStore(path=str(tmp_path / "users.json")), moment


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
    principal, token = store.register(username="张三", password=PW)
    flushed = _count_flushes(store, monkeypatch)

    base = datetime(2026, 9, 16, 12, 0)
    for minutes in range(1, 50):
        moment["now"] = base + timedelta(minutes=minutes)
        assert store.resolve(token).user_id == principal.user_id
    assert flushed == [], "令牌兑换是每次请求都要走的热路径，盘满或被编辑器/杀软锁住时不能变成 500"


def test_resolve_persists_last_used_at_once_it_is_an_hour_old(clocked, monkeypatch):
    store, moment = clocked
    principal, token = store.register(username="李四", password=PW)

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
    principal, token = store.register(username="王五", password=PW)
    moment["now"] = datetime(2026, 9, 16, 12, 42)
    flushed = _count_flushes(store, monkeypatch)
    assert store.resolve(token).user_id == principal.user_id
    assert flushed == [], "这一次读取自己不写盘"
    assert store.disable_user(principal.user_id) is True
    disk = json.load(open(store.path, encoding="utf-8"))[principal.user_id]
    assert disk["last_used_at"].startswith("2026-09-16T12:42"), "跳写不等于不记：内存刷新要带得下去"
    assert disk["disabled"] is True


# ---------- 坏数据只该判"不匹配"，不该抛异常 ----------


def test_resolve_returns_none_for_hand_edited_credentials(tmp_path):
    """hmac.compare_digest 收到非 ASCII str 会抛 TypeError——手改过 users.json
    或塞进 null 就必须表现为"这枚令牌解不出来"，而不是把 500 甩给调用方。"""
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path)
    principal, token = store.register(username="钱六", password=PW)
    disk = json.load(open(path, encoding="utf-8"))
    disk[principal.user_id]["tokens"] = ["sha256：被人为改成了中文"]
    disk["u_broken"] = {"user_id": "u_broken", "username": "坏记录", "username_lc": "坏记录",
                        "tokens": [None], "pw_hash": None, "disabled": False, "role": "user"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk, f, ensure_ascii=False)

    reloaded = AuthStore(path=path)
    assert reloaded.resolve(token) is None
    assert reloaded.resolve("another-token") is None
    # 同一份坏记录也不能把登录变成 500：没有可用密码摘要就是"登不进"
    with pytest.raises(AuthError):
        reloaded.login("坏记录", PW)


def test_legacy_account_without_a_password_cannot_log_in(tmp_path):
    """换模型之前的记录没有 pw_hash。它不该能登录，也不该抛异常。"""
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"u_old": {
        "user_id": "u_old", "username": "旧账号", "username_lc": "旧账号",
        "token_hash": "sha256:aa", "tokens": [], "disabled": False, "role": "user"}}),
        encoding="utf-8")
    store = AuthStore(path=str(path))
    with pytest.raises(AuthError):
        store.login("旧账号", PW)


# ---------- 临界区与账号 id ----------


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


def test_username_dedup_check_happens_inside_the_lock(tmp_path, monkeypatch):
    """查重与写入之间让出锁，两个人就能同时通过"这名字没人用"，后一个覆盖前一个。"""
    store = AuthStore(path=str(tmp_path / "users.json"))
    probe = _LockProbe()
    store._lock = probe
    held = []
    real_hash = auth_module.hash_password

    def spy_hash(pw):
        held.append(probe.held)
        return real_hash(pw)

    monkeypatch.setattr(auth_module, "hash_password", spy_hash)
    store.register(username="甲", password=PW)
    assert held and all(held), "注册必须在临界区内既查重又落库"


def test_a_colliding_user_id_is_redrawn_not_overwritten(tmp_path, monkeypatch):
    """`"u_" + token_hex(4)` 只有 32 bit，撞上的概率低，但撞上的后果不是报错而是
    `self._users[user_id] = record` 把已有那个人整行换掉：前一个人的令牌当场解不
    出来，日志里一个字都没有。检查只要一行。
    """
    real_hex = auth_module.secrets.token_hex
    seq = iter(["aaaa1111", "aaaa1111", "bbbb2222"])   # 第二次注册先撞回同一个号
    monkeypatch.setattr(auth_module.secrets, "token_hex",
                        lambda n: next(seq) if n == 4 else real_hex(n))
    store = AuthStore(path=str(tmp_path / "users.json"))

    first, _ = store.register(username="甲", password=PW)
    second, _ = store.register(username="乙", password=PW)

    assert (first.user_id, second.user_id) == ("u_aaaa1111", "u_bbbb2222")
    assert len(store.list_users()) == 2, "撞号必须让第二个人换一枚，而不是把甲整条覆盖"
    assert {u["username"] for u in store.list_users()} == {"甲", "乙"}


# ---------- 撤销必须有对称的还原动作 ----------


def test_enable_user_undoes_a_disable_and_reports_unknown_ids(store):
    """disable 是一扇单向门的话，运维就只能删号重建——那不是撤销，是赌气。"""
    principal, token = store.register(username="恢复用", password=PW)
    store.disable_user(principal.user_id)
    assert store.resolve(token) is None
    assert store.enable_user(principal.user_id) is True
    assert store.resolve(token).user_id == principal.user_id, "启用必须让原令牌立刻可用"
    disk = json.load(open(store.path, encoding="utf-8"))[principal.user_id]
    assert disk["disabled"] is False, "启用要落盘，否则重启后账号又躺回停用堆里"
    assert store.enable_user("u_nobody") is False, "不存在的用户要如实返回 False"


def test_disabled_user_cannot_log_in_even_with_the_right_password(store):
    """停用只挡令牌解析、不挡登录的话，等于给了被停的人一条自助恢复通道。"""
    principal, _ = store.register(username="被停的", password=PW)
    store.disable_user(principal.user_id)
    with pytest.raises(AuthError):
        store.login("被停的", PW)


# ---------- 坏身份库必须先改名留证：形状不对也算坏 ----------
# `_load` 上面那条不变量写的是"身份库坏了绝不能静默当空库继续跑"。原先只有
# parse 失败那一支照做，"读得懂但顶层不是对象"这一支什么都不 print、什么都不备份，
# 于是库以空表启动，下一次 register/disable 就把 users.json 整个覆盖掉
# ——不变量正好从这一支被绕过去。


@pytest.mark.parametrize("broken,desc", [
    ('{"u_1": {"username": "张三", "tokens": ["sha256:aa', "半截 JSON"),
    ('[{"user_id": "u_1", "username": "张三"}]', "读得懂但顶层是列表"),
])
def test_broken_identity_file_is_kept_and_never_clobbered(tmp_path, broken, desc):
    users = tmp_path / "users.json"
    users.write_text(broken, encoding="utf-8")

    store = AuthStore(path=str(users))
    assert (tmp_path / "users.json.corrupt").exists(), f"{desc}：必须先备份成 .corrupt"
    assert store.list_users() == [], f"{desc}：仍要能以空库启动，别让服务起不来"

    # 判据在这一行之后：把库写回去的那次注册，不能顺手抹掉唯一的原始材料
    principal, _ = store.register(username="重建者", password=PW)
    assert (tmp_path / "users.json.corrupt").read_text(encoding="utf-8") == broken, \
        f"{desc}：备份必须活过一次写盘"
    assert list(json.loads(users.read_text(encoding="utf-8"))) == [principal.user_id]


# ---------- 删号 ----------


def test_delete_user_removes_the_record_and_its_tokens(tmp_path):
    store = AuthStore(path=str(tmp_path / "users.json"))
    principal, token = store.register(username="周九", password=PW)
    _, token2 = store.login("周九", PW)
    assert store.delete_user(principal.user_id) is True
    assert store.resolve(token) is None, "删号后旧令牌必须立刻解不出来"
    assert store.resolve(token2) is None, "多设备并存时删号要一起清干净"
    assert store.list_users() == []
    assert store.delete_user(principal.user_id) is False, "删不存在的用户要如实返回 False"
    reloaded = AuthStore(path=str(tmp_path / "users.json"))
    assert reloaded.list_users() == []
    assert reloaded.resolve(token) is None


# ---------- 密码找回：安全问题 ----------
# 注册时留一个自己写的问题 + 答案，忘了密码就答一次。答案是人编的低熵值，
# 所以它和密码走同一个慢哈希；而这两个端点免凭据，措辞与耗时都必须保守。

Q = "我小学的校名？"
A = "河海大学附属小学"


def _reg(store, username="找回用", password=PW, question=Q, answer=A):
    return store.register(username=username, password=password,
                          security_question=question, security_answer=answer)


def test_the_answer_is_stored_as_a_slow_hash_only(store):
    """答案熵比密码还低（往往是能猜的地名），只存 sha256 等于把库给人拿去离线猜。"""
    principal, _ = _reg(store)
    raw = open(store.path, encoding="utf-8").read()
    assert A not in raw, "明文答案绝不能落盘"
    record = json.loads(raw)[principal.user_id]
    assert record["security_question"] == Q
    assert record["answer_hash"].startswith("bcrypt:")
    assert "answer" not in record or record.get("answer") is None


def test_recovery_question_says_the_same_thing_for_no_user_and_no_question(store):
    """免凭据的"报出问题"端点不能顺手回答"这个用户名存在吗"。

    没设过找回问题的真实用户，与压根不存在的用户，必须拿到同一句话；只有真设过
    问题的人才看到问题文本。剩下的泄露面只有"谁开了找回"这一比特，由 HTTP 层
    那份按 IP 的失败预算限着。
    """
    _reg(store, username="开了找回的")
    store.register(username="没开找回的", password=PW)

    missing = store.recovery_question("从没注册过")
    none_set = store.recovery_question("没开找回的")
    assert missing == none_set, f"这两种情况同形才对：{missing!r} / {none_set!r}"
    assert store.recovery_question("开了找回的") == Q


def test_every_wrong_answer_says_the_very_same_thing(store):
    """答案错、这个人没开找回、查无此人、账号被停用——四句必须一字不差。"""
    principal, _ = _reg(store, username="开着的")
    store.register(username="没开的", password=PW)
    store.disable_user(principal.user_id)

    reasons = {}
    for label, args in (("答案错", ("开着的", "肯定不对", PW2)),
                       ("没开找回", ("没开的", A, PW2)),
                       ("查无此人", ("没这个人", A, PW2)),
                       ("已停用", ("开着的", A, PW2))):
        with pytest.raises(AuthError) as e:
            store.reset_password(*args)
        reasons[label] = str(e.value)
    assert len(set(reasons.values())) == 1, f"四种失败说出了不同的话：{reasons}"


def test_a_bad_new_password_is_rejected_before_the_answer_is_consulted(store):
    """先问答案再看新密码合不合格，等于给攻击者一个"答案对了"的信号。

    顺序反过来之后，"密码太短"这句只描述调用方填的新密码，与答案对不对无关。
    """
    _reg(store, username="按顺序的")
    for answer in ("肯定不对的答案", A):
        with pytest.raises(AuthError) as e:
            store.reset_password("按顺序的", answer, "短")
        assert "密码" in str(e.value), f"新密码形状没先查：{e.value}"


def test_reset_swaps_the_password_and_kills_every_session_token(store):
    """改密的正确后果：旧密码登不进、新密码能登、之前发出去的令牌全部作废。

    只改哈希不清令牌的话，一个偷到旧令牌的人在新密码生效之后还能继续用——
    "我改了密码，因为手机丢了"这条最常见的自救动作就成了假的。
    """
    principal, first = _reg(store, username="丢了手机")
    _, second = store.login("丢了手机", PW)
    assert store.resolve(first) and store.resolve(second)

    store.reset_password("丢了手机", A, PW2)

    assert store.resolve(first) is None, "改密之后旧令牌必须立刻解不出来"
    assert store.resolve(second) is None, "每一台设备都得掉线，包括偷到令牌那台"
    with pytest.raises(AuthError):
        store.login("丢了手机", PW)
    fresh_user, fresh_token = store.login("丢了手机", PW2)
    assert fresh_user.user_id == principal.user_id
    assert store.resolve(fresh_token)


def test_the_answer_match_ignores_case_and_surrounding_spaces(store):
    """人会输入 "Hehai University " 而不是精确串：归一化只碰大小写与首尾空白。"""
    _reg(store, username="归一化", answer="Hehai University")
    store.reset_password("归一化", "  hehai university  ", PW2)
    assert store.login("归一化", PW2)


def test_reset_password_always_pays_the_bcrypt_cost(store, monkeypatch):
    """查无此人也要跑一次 bcrypt，否则"秒回"本身就是一份用户名名单。"""
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    _reg(store, username="有找回的")

    with pytest.raises(AuthError):
        store.reset_password("有找回的", "肯定不对", PW2)
    known = len(calls)
    calls.clear()
    with pytest.raises(AuthError):
        store.reset_password("查无此人", "肯定不对", PW2)
    assert len(calls) == known, "查无此人没付 bcrypt 的代价，快慢差泄露了用户名是否存在"


@pytest.mark.parametrize("question,answer", [
    ("", A), (Q, ""), (Q, "  "), ("问" * 80, A), (Q, "答" * 200),
])
def test_missing_or_absurd_recovery_fields_are_rejected(store, question, answer):
    """注册时的问题与答案都得是个能用的东西：空的、超长的一律 422 那一类。"""
    with pytest.raises(AuthError):
        store.register(username="不合格", password=PW,
                       security_question=question, security_answer=answer)


def test_giving_only_one_of_the_two_recovery_fields_is_rejected(store):
    """半套找回凭据比没有更糟：界面会以为能自助，走到第二步才发现答不上来。"""
    with pytest.raises(AuthError):
        store.register(username="半个", password=PW, security_question=Q)
    with pytest.raises(AuthError):
        store.register(username="半个", password=PW, security_answer=A)
    assert store.list_users() == [], "被拒的注册不该留下半成品记录"
