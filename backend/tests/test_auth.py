"""身份存储单元测试（纯存储层，不涉及 HTTP）。

2026-09-17 凭据模型换过一次：注册不再吃邀请码，改成用户名 + 自设密码换一枚会话
令牌。原先有 7 条专门钉邀请码语义的用例（单次使用、码字符集、max_uses、撤销归一
化、快照不交活引用、临界区重取码、"码先名后"的顺序）随功能一起删除——它们保护的
对象已经不在了。

但"码先名后"那条背后的**顾虑**一行都没消失，它只是换了位置：从前那个免凭据端点
靠邀请码挡住"任何人都能问这个名字被占了吗"，现在注册全开放，那道泄露面改由
auth_router 按真实 IP 计费来限（见 test_auth_endpoints.py），而登录端点则必须
对"查无此人 / 密码错 / 已停用"三件事给出逐字节相同的回答，包括耗时。

2026-09-18 密保从"每人自设一个问题"换成全站固定的三题。`recovery_question()` 与
"报出问题"那条信道一起删掉，针对它的两条用例随之删除——问题已是常量，没有服务器
可问；单答案那条 bcrypt 耗时判据也一并没了，它被"三条必须付满三次"那条覆盖。剩下
的判据换成三件事：三条全对才放行、比对不许短路（拿 bcrypt 次数当尺子）、答案能在
重置时轮换。

同一天评审第一轮：产品行为判定为对，但对的是"约束成立却没人看着"这四条，本轮补锁
——reset 侧的条数闸门（少一条必须在比对之前抛出，判据是 bcrypt 零次）、耗时判据从
两支扩到四条失败支路（答案错／没留答案／查无此人／已停用）、"改密绝不写 disabled"
改成读源码的结构性锁（该状态公路上不可达，手法同 test_web_pwa.py 读 JS 文本）、三句
固定问题逐字钉住。另把一条退化成恒真的断言（"三" 或 "答案" 在话里——RESET_FAIL 也
能满足）改成断题数。

Task 2（端点收口）在本文件里只做两件事，产品代码一行未动：给 new_answers 侧补一条
与 answers 侧对称的零次 bcrypt 判据（旧的那条只断"被拒之后库没变"，看不见顺序），
以及修掉那条读源码的结构锁的两处卫生问题——注释与 docstring 里提到被禁写法就误伤、
打包环境（本仓要进 EXE）读不到源码时误报成失败而不是跳过。

修复轮 1（F4）接着修同一把尺子的第三处卫生问题：上一轮只跳过了整行注释，行尾挂着代码
的那种（`return x  # record["disabled"] = False`）照旧参与匹配，将来一句解释性行尾注释
就能把锁误红。现在按 "#" 先切再判非空，判据是 test_the_structural_ruler_ignores_trailing_comments
——两支都在本文件里：误伤的那半与真赋值仍咬得到的那半。
"""
import inspect
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.core import auth as auth_module
from app.core.auth import RESET_FAIL, AuthError, AuthStore

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


def test_revoke_drops_only_that_one_token(store):
    """退出这台机器只能踢掉这台机器的令牌。

    清空整张令牌表是 rotate_token 的语义（"我怀疑密码泄露了"）。如果"退出"顺手
    把别人的设备一起踢下线，那它就是一个能跨设备使坏的动作，没人敢点。
    """
    principal, phone = store.register(username="两台设备", password=PW)
    _, laptop = store.login("两台设备", PW)
    assert laptop != phone
    store.revoke(laptop)
    assert store.resolve(laptop) is None, "退出的那枚还能用：那「退出」就是个假动作"
    assert store.resolve(phone).user_id == principal.user_id, "退出把另一台设备一起踢了"
    # 重启后仍然是退出状态：只改内存不落盘，等于没退出
    again = AuthStore(path=store.path)
    assert again.resolve(laptop) is None, "revoke 没有落盘"
    assert again.resolve(phone).user_id == principal.user_id


def test_revoke_says_nothing_about_a_token_it_does_not_know(store):
    """不存在的令牌与已摘掉的令牌走同一条路：不抛、不返回"在不在"。

    返回值上的任何差别都是一个 oracle——外面可以拿它试探某枚令牌曾经有效过。
    """
    _, token = store.register(username="话少的人", password=PW)
    assert store.revoke("显然不是系统发的那一枚") is None
    assert store.revoke(token) is None
    assert store.revoke(token) is None, "重复退出不该抛：它本来就是「没登进来」那个状态"


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


# ---------- 密码找回：全站固定三题 ----------
# 问题不再是用户自设，而是 auth.RECOVERY_QUESTIONS 那三句常量，所以每个人名下只存
# 三个答案的慢哈希（顺序与常量对齐）。三条必须全对，比对还不许短路——否则"第几题
# 猜对了"就漏进响应耗时里，三题被拆成三份独立预算，整条路的强度除以三。

ANS = ["新市场小学", "hehai2024", "李建国"]


def test_the_three_fixed_questions_are_pinned_verbatim():
    """这三句是全站唯一的一份，不钉住就等于没人看着。

    问题已是常量：界面按它渲染三格输入、Task 3 的文案按它抄、找回凭据的"顺序即
    answer_hashes 的下标"也按它排。没有这条断言时，改掉一个字（哪怕只把一个全角
    问号换成半角）在存储层一条测试都不会红，要等到 Task 3/4 的前端契约测试才炸
    ——所以这里逐字钉。
    """
    assert auth_module.RECOVERY_QUESTIONS == ("你的手机号后四位是什么？",
                                             "你小学在哪上？",
                                             "你父母姓氏的拼音首字母各一个是什么？"), \
        "找回问题是全站固定的事实来源，改它要走计划，不能顺手改文案"
    # 条数是形状闸门、比对次数与那句"3 题"文案共同的事实来源；它跟着常量走，
    # 所以钉住数字才算钉住"三题"这件事本身。
    assert auth_module.ANSWER_COUNT == 3


def _reg(store, username="找回用", password=PW, answers=ANS):
    return store.register(username=username, password=password, security_answers=answers)


def test_register_needs_all_three_answers_or_none(store):
    for bad in ([], ["新市场小学"], ANS[:2]):
        with pytest.raises(AuthError) as e:
            store.register(username="甲", password="correct-horse-battery", security_answers=bad)
        # 断的是"这句话报出了题数"，不是"话里带答案三字"——后者连 RESET_FAIL
        # （"答案不正确"）都能满足，等于没断。实现写的是阿拉伯数字 "找回的 3 题…"，
        # 中文量词那一支留给人以后改写文案。
        assert "3 题" in str(e.value) or "三题" in str(e.value), \
            f"{bad} 的报错没说到三题：{e.value}"
    principal, _ = store.register(username="甲", password="correct-horse-battery", security_answers=ANS)
    record = json.loads(open(store.path, encoding="utf-8").read())[principal.user_id]
    assert len(record["answer_hashes"]) == 3
    assert "security_question" not in record, "问题已是常量，不该再存进每个人名下"
    for answer in ANS:
        assert answer not in open(store.path, encoding="utf-8").read(), "明文答案绝不能落盘"


def test_reset_requires_every_answer_and_never_short_circuits(store):
    """三题全对才放行，而且错在哪一题不许有可观察差别。

    短路返回等于把"第一题猜对了"漏出去：攻击者于是有三份独立预算，
    每 10 分钟各猜一题，整条路的强度除以三。这里用耗时把这条钉住——
    只错第 1 题与只错第 3 题，都必须付满三次 bcrypt 的代价。
    """
    store.register(username="乙", password="correct-horse-battery", security_answers=ANS)
    for wrong in (0, 1, 2):
        bad = list(ANS)
        bad[wrong] = "错的答案"
        with pytest.raises(AuthError) as e:
            store.reset_password(username="乙", answers=bad, new_password="another-correct-horse")
        assert str(e.value) == RESET_FAIL
    store.reset_password(username="乙", answers=ANS, new_password="another-correct-horse")
    assert store.login("乙", "another-correct-horse")[0].username == "乙"


def test_answer_comparison_pays_three_bcrypts_whatever_the_answer(store, monkeypatch):
    """耗时判据：四条失败支路都必须跑满三次 bcrypt，一支都不许秒回。

    只错第 1 题就返回的话，这里只会数到 1 次——那正是 R3 要挡的事。
    "没留答案""查无此人""已停用"这三支各自被挪到比对之前，就会成为一条比正常快
    三倍的信道：那三个字面意思就是"这个用户名不存在"／"这个人没设找回"／
    "这个人被停用了"，免凭据端点当场变成一份状态名单。文案同形（见
    test_every_wrong_answer_says_the_very_same_thing）挡不住快慢差，所以四支
    都拿 bcrypt 次数当尺子量一遍。
    """
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(pw, hashed):
        calls.append(1)
        return real(pw, hashed)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    principal, _ = store.register(username="丙", password=PW, security_answers=ANS)
    store.register(username="没留答案的", password=PW)

    def one(label, username, answers):
        calls.clear()
        with pytest.raises(AuthError) as e:
            store.reset_password(username=username, answers=answers, new_password=PW2)
        # 这一支若换了措辞，就说明它走的不是比对之后那条出口，次数也就白数了
        assert str(e.value) == RESET_FAIL, f"{label} 说了别的话：{e.value}"
        assert len(calls) == 3, f"{label} 只跑了 {len(calls)} 次 bcrypt：比对被短路或挪到了判断之后"

    one("答案错", "丙", ["x1", "x2", "x3"])
    one("没留答案", "没留答案的", ANS)
    one("查无此人", "查无此人", ANS)
    store.disable_user(principal.user_id)
    one("已停用", "丙", ANS)


@pytest.mark.parametrize("answers", [ANS[:2], ANS + ["第四条"]])
def test_the_reset_answer_count_gate_sits_in_front_of_the_comparison(store, monkeypatch,
                                                                     answers):
    """reset 侧的条数闸门是唯一的守门人，这里用"零次比对"钉住它。

    闸门一改成宽容写法（[:3] 之类），"只答对两题"就能改走别人的账号——三题里
    少一题仍然放行，整条路的强度不是除以三而是直接少了一题的熵。而 HTTP 层的
    形状校验挡不住绕过去直接调存储层的调用方，注册侧那条测试（走的是 register）
    也看不见这一支。条数不合格必须在读库比对之前抛出，所以 bcrypt 必须一次都不跑：
    跑了一次就说明这批答案被当有效凭据比对过了。
    """
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(pw, hashed):
        calls.append(1)
        return real(pw, hashed)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    store.register(username="庚", password=PW, security_answers=ANS)
    calls.clear()
    with pytest.raises(AuthError) as e:
        store.reset_password(username="庚", answers=answers, new_password=PW2)
    assert str(e.value) != RESET_FAIL, \
        f"{len(answers)} 条答案被判成了『答案不正确』，说明条数闸门没挡住、走到了比对"
    assert calls == [], f"{len(answers)} 条答案却跑了 {len(calls)} 次 bcrypt：条数闸门失效"


def test_answers_can_be_rotated_at_reset(store):
    """答案泄露过就该换得掉——固定问题不等于固定答案。"""
    store.register(username="丁", password="correct-horse-battery", security_answers=ANS)
    store.reset_password(username="丁", answers=ANS, new_password="another-correct-horse",
                         new_answers=["沙北", "hehai2025", "李建国"])
    with pytest.raises(AuthError):
        store.reset_password(username="丁", answers=ANS, new_password="third-correct-horse")
    store.reset_password(username="丁", answers=["沙北", "hehai2025", "李建国"],
                         new_password="third-correct-horse")
    assert store.login("丁", "third-correct-horse")[0].username == "丁"


def test_a_reset_survives_a_restart(tmp_path):
    """**终审 F2**：改密成功到底有没有落盘，此前**没有任何锁**。

    终审实测：把 `reset_password` 末尾那句 `self._flush()` 删掉，`test_auth.py` 与
    `test_auth_endpoints.py` 共 110 条全绿。后果是实的——`AuthStore` 是内存表，进程一重启
    就以 users.json 为准，于是那份旧 `pw_hash` 与旧 `tokens` 赢回来：新密码登不进、
    已经作废的每台设备又全部复活，而界面已经告诉过用户"其他设备需要重新登录一次"。
    那是这条自救路径上唯一一处"界面说了、磁盘没做"的形状。

    判据按 `test_flush_survives_reload` 的同族写法：换一个新的 `AuthStore` 实例去读同一个
    文件（另起进程的样子），四件事都得成立。第四件（轮换过的答案在重加载后仍生效）顺带
    钉住"answer_hashes 的轮换没落盘"——那一半坏起来和口令那一半一模一样：人以为换了答案，
    重启之后旧答案又开门了。
    """
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path)
    principal, token = store.register(username="重启后的人", password=PW, security_answers=ANS)
    rotated = ["沙北", "hehai2025", "李建国"]
    store.reset_password(username="重启后的人", answers=ANS, new_password=PW2,
                         new_answers=rotated)

    reloaded = AuthStore(path=path)
    with pytest.raises(AuthError):
        reloaded.login("重启后的人", PW)
    assert reloaded.login("重启后的人", PW2)[0].user_id == principal.user_id, \
        "新口令没落盘：重启之后赢回来的还是旧那道门"
    assert reloaded.resolve(token) is None, \
        "令牌作废没落盘：重启之后每台旧设备又都登录着"
    with pytest.raises(AuthError):
        reloaded.reset_password(username="重启后的人", answers=ANS, new_password=PW)
    reloaded.reset_password(username="重启后的人", answers=rotated, new_password=PW)
    assert reloaded.login("重启后的人", PW)[0].user_id == principal.user_id, \
        "答案轮换没落盘：重加载之后旧答案又换得动别人的密码"


def test_partial_new_answers_are_rejected_without_touching_anything(store):
    principal, token = store.register(username="戊", password="correct-horse-battery", security_answers=ANS)
    with pytest.raises(AuthError):
        store.reset_password(username="戊", answers=ANS, new_password="another-correct-horse",
                             new_answers=["只有一条"])
    assert store.resolve(token) is not None, "被拒的重置不该已经动过令牌或口令"
    assert store.login("戊", "correct-horse-battery")[0].user_id == principal.user_id


@pytest.mark.parametrize("new_answers", [ANS[:2], ANS + ["第四条"]])
def test_a_malformed_answer_rotation_pays_no_bcrypt_at_all(store, monkeypatch,
                                                           new_answers):
    """**新增锁（与上面那条条数闸门对称）**：轮换答案的条数也在比对之前就被拦住。

    answers 侧的零次判据已有，new_answers 侧此前只从"被拒之后什么都没动"那一侧看着
    （上一条）——那半句测不到顺序：条数闸门若挪到比对之后，被拒的重置照样不改库，
    却已经把三题真跑了一遍 bcrypt。顺序在这条路径上从来不只是性能：跑完才拒，"这一
    次回得慢"就是"三题猜对了"的信号，免凭据端点于是多了一比特可问的东西。
    """
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(pw, hashed):
        calls.append(1)
        return real(pw, hashed)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    principal, token = store.register(username="壬", password=PW, security_answers=ANS)
    calls.clear()
    with pytest.raises(AuthError) as e:
        store.reset_password(username="壬", answers=ANS, new_password=PW2,
                             new_answers=new_answers)
    assert str(e.value) != RESET_FAIL, \
        "条数不对的轮换被判成了『答案不正确』——它走到了比对，话术也就和猜错混成一句"
    assert calls == [], f"{len(new_answers)} 条轮换答案却跑了 {len(calls)} 次 bcrypt：闸门在比对之后"
    assert store.resolve(token) is not None, "被拒的轮换不许已经作废令牌"
    assert store.login("壬", PW)[0].user_id == principal.user_id, "口令必须还是旧的"


def test_reset_revokes_every_token_and_keeps_a_disabled_user_disabled(store):
    """这条只断两件事：改密作废名下全部令牌；被停用的人来改密只听到 RESET_FAIL。

    原名里的 never_un_disables 已经搬去一条结构性锁
    （test_reset_password_source_never_writes_the_disabled_flag）。理由：被停用的
    账号在 reset 上必然走 RESET_FAIL，"改密成功后 disabled 仍为 True"这个状态从
    公开接口根本到不了，断"成功之后它还是 False"只是在复读 enable_user 的结果。

    这里能真断的是反方向：一次**被拒**的改密不许顺手把人从停用堆里捞出来，
    否则"被停 → 自己改个密码 → 又能用了"就成了绕过管理员的自助恢复通道（login
    那条自助路已经在 test_disabled_user_cannot_log_in_even_with_the_right_password
    里被堵死，这是它的另一半）。
    """
    principal, first = _reg(store, username="己")
    _, second = store.login("己", PW)
    store.disable_user(principal.user_id)

    with pytest.raises(AuthError) as e:
        store.reset_password(username="己", answers=ANS, new_password=PW2)
    assert str(e.value) == RESET_FAIL, "停用不许从文案上被认出来，只能和答案错同一句话"
    assert [u for u in store.list_users()
            if u["user_id"] == principal.user_id][0]["disabled"] is True, \
        "被拒的改密不许把人放出来：那等于停用闸门只对 login 生效"

    store.enable_user(principal.user_id)
    # 人在此刻是开着的，所以 login(PW2) 失败只剩一个理由：那次被拒的改密确实没动口令
    with pytest.raises(AuthError):
        store.login("己", PW2)
    store.reset_password(username="己", answers=ANS, new_password=PW2)
    assert store.resolve(first) is None, "改密的理由之一就是让别的设备掉线"
    assert store.resolve(second) is None, "每一台设备都得掉线，包括偷到令牌那台"
    assert [u for u in store.list_users()
            if u["user_id"] == principal.user_id][0]["disabled"] is False, \
        "改密也不许反过来把人关回去：他刚从停用里恢复，口令是他自己的"


# 三条正则**认得**的三种写 disabled 的写法——注意这不等于"写 disabled 的一切写法"：
# `record.setdefault("disabled", False)` 与 `record.pop("disabled", None)` 都认不出，
# 换一个 helper 写也同样漏（task-1-report 里记为"结构锁的固有边界"）。这把锁挡的是
# "顺手往成功路径里加一行"，不挡"成心换一种写法"——文本锁都这样，test_web_pwa.py 读
# JS 文本同此。原理性限制本轮不动，只是别把它写成"堵死一切写法"。
# 读（record.get("disabled")）不在其列——停用这一支本来就得判，那条判断由耗时测试与
# 措辞测试分别看着。
_DISABLED_WRITES = (
    re.compile(r"""\[\s*["']disabled["']\s*\]\s*=(?!=)"""),          # record["disabled"] = ...
    re.compile(r"""\bdel\b[^=\n]*\[\s*["']disabled["']\s*\]"""),     # del record["disabled"]
    re.compile(r"""\.update\([^)]*["']disabled["']"""),              # record.update({"disabled": ...})
)


def _code_lines(func) -> list:
    """能拿结构锁去匹配的行：真代码，不含 docstring 与整行注释。

    两个边角都是这一轮评审点出来的：
    1. **注释咬人**。这条锁要挡的是"以后有人在成功路径里加一行赋值"，而解释为什么
       不加的那句注释里完全可能出现 `record["disabled"] = False` 这个字样——那时改
       一句文档就红，红得没有道理，而一条会误报的锁最后被人删掉。docstring 整段切
       掉；注释整行跳过，挂在代码后面的那半截也先切掉（`return x  # ...` 只留
       `return x` 参与匹配）。判据与被切的字样见
       test_the_structural_ruler_ignores_trailing_comments。
    2. **打包环境**。本仓要打进 EXE（PyInstaller），`inspect.getsource` 在拿不到
       源文件时抛 OSError。一个读不到源码的自检应当明说"我没跑成"，而不是把整套
       测试判红——红在这里等于告诉别人"改密路径坏了"，而坏的是测试的尺子。
    """
    try:
        source = inspect.getsource(func)
    except OSError as e:
        pytest.skip(f"读不到 {func.__qualname__} 的源码，结构性锁无从谈起：{e}")
    doc = func.__doc__
    if doc:                       # -O / -OO 运行时 docstring 已被剥掉，这里跟着退化
        source = source.replace(doc, "", 1)
    lines = []
    for line in source.splitlines():
        code = line.split("#")[0]     # 行尾注释先切掉：解释"为什么不许写"的那句里完全
        if code.strip():              # 可能出现被禁字样；切完还剩东西才算这一行有代码
            lines.append(code)
    return lines


def _offenders(lines) -> list:
    """把三条被禁写法在一批行上过一遍，返回咬到的行。"""
    return [line.strip() for pattern in _DISABLED_WRITES
            for line in lines if pattern.search(line)]


def test_the_structural_ruler_ignores_trailing_comments():
    """**新增锁（修复轮 1 / F4）**：尺子只认真代码——行尾注释不误伤，真赋值仍咬得到。

    这把锁最该容忍的情形，恰恰是"以后有人在成功路径旁边加一句解释性行尾注释，里面提到
    被禁的写法"：一句文档改动把锁判红，红得没有道理，而一条会误报的锁最后被人删掉。
    上一轮只跳过了整行注释，`return x  # record["disabled"] = False` 这种行尾挂着代码的
    照旧参与匹配——实测就是误红。反向那半一样要成立：真写了一行赋值必须还咬得到，
    否则"修卫生"就是"把牙磨掉"。
    """
    def a_prose_comment():
        record = {"disabled": True}
        return record  # record["disabled"] = False 这种写法永远不许出现在这条路径上

    assert _offenders(_code_lines(a_prose_comment)) == [], \
        "行尾注释被当成了代码：改一句解释就把锁判红"

    def a_real_write():
        record = {"disabled": True}
        record["disabled"] = False
        return record

    offenders = _offenders(_code_lines(a_real_write))
    assert offenders == ['record["disabled"] = False'], \
        f"真赋值没被咬到（{offenders}）：这把锁已经没有牙了"


def test_reset_password_source_never_writes_the_disabled_flag():
    """结构性锁：停用是管理员的动作，改密这条用户自助路没有资格碰它。

    行为上测不到（见 test_reset_revokes_every_token_and_keeps_a_disabled_user_disabled
    的说明），所以只剩这一条读源码的锁：它挡的是"以后有人往成功路径里加一行
    `record["disabled"] = False`，顺手把『改密』当成一条自助解封通道"。本仓库有
    先例——test_web_pwa.py 通篇读 JS 文本当锁，理由一样：那条约束在可观测行为上
    不成立，只在代码形状上成立。
    """
    lines = _code_lines(AuthStore.reset_password)
    for pattern in _DISABLED_WRITES:
        offenders = [line.strip() for line in lines if pattern.search(line)]
        assert not offenders, \
            f"reset_password 里出现了写 disabled 的代码（{pattern.pattern}）：{offenders}"


# ---------- 上面那六条之外的另一半：措辞与落盘形状 ----------


def test_the_three_answers_are_stored_as_slow_hashes_only(store):
    """答案熵比密码还低（往往是能猜的地名），只存 sha256 等于把库给人拿去离线猜。"""
    principal, _ = _reg(store)
    raw = open(store.path, encoding="utf-8").read()
    record = json.loads(raw)[principal.user_id]
    assert [h.startswith("bcrypt:") for h in record["answer_hashes"]] == [True, True, True]
    for answer in ANS:
        assert answer not in raw, "明文答案绝不能落盘"


def test_every_wrong_answer_says_the_very_same_thing(store):
    """答案错、这个人没设找回答案、查无此人、账号被停用——四句必须一字不差。

    四条路都在免凭据端点上，多说一个字就是"哪个用户名真存在"的免费查询。
    四句还必须**都**带着 charge=True 出来：HTTP 层靠这个标记决定要不要往找回的猜错账上
    记一格（终审 F4 之前它判的是 reason 等于哪句文案，换措辞就静默不计费）。少一条带
    标记的出口，就等于给那四种原因之一开了一条免费猜测的路。
    """
    principal, _ = _reg(store, username="开着的")
    store.register(username="没开的", password=PW)
    store.disable_user(principal.user_id)

    reasons = {}
    for label, args in (("答案错", ("开着的", ["肯定不对", "肯定也不对", "第三个也不对"])),
                        ("没设找回", ("没开的", ANS)),
                        ("查无此人", ("没这个人", ANS)),
                        ("已停用", ("开着的", ANS))):
        with pytest.raises(AuthError) as e:
            store.reset_password(username=args[0], answers=args[1], new_password=PW2)
        reasons[label] = str(e.value)
        assert e.value.charge is True, \
            f"{label} 这一支没带 charge：HTTP 层不会为它计费，那种原因就成了免费猜测"
    assert len(set(reasons.values())) == 1, f"四种失败说出了不同的话：{reasons}"


def test_a_bad_new_password_is_rejected_before_the_answers_are_consulted(store):
    """先问答案再看新密码合不合格，等于给攻击者一个"答案对了"的信号。

    顺序反过来之后，"密码太短"这句只描述调用方填的新密码，与答案对不对无关。
    """
    _reg(store, username="按顺序的")
    for answers in (["肯定不对的答案", "肯定也不对", "第三个也不对"], ANS):
        with pytest.raises(AuthError) as e:
            store.reset_password(username="按顺序的", answers=answers, new_password="短")
        assert "密码" in str(e.value), f"新密码形状没先查：{e.value}"


def test_reset_swaps_the_password_and_kills_every_session_token(store):
    """改密的正确后果：旧密码登不进、新密码能登、之前发出去的令牌全部作废。

    只改哈希不清令牌的话，一个偷到旧令牌的人在新密码生效之后还能继续用——
    "我改了密码，因为手机丢了"这条最常见的自救动作就成了假的。
    """
    principal, first = _reg(store, username="丢了手机")
    _, second = store.login("丢了手机", PW)
    assert store.resolve(first) and store.resolve(second)

    store.reset_password(username="丢了手机", answers=ANS, new_password=PW2)

    assert store.resolve(first) is None, "改密之后旧令牌必须立刻解不出来"
    assert store.resolve(second) is None, "每一台设备都得掉线，包括偷到令牌那台"
    with pytest.raises(AuthError):
        store.login("丢了手机", PW)
    fresh_user, fresh_token = store.login("丢了手机", PW2)
    assert fresh_user.user_id == principal.user_id
    assert store.resolve(fresh_token)


def test_the_answer_match_ignores_case_and_surrounding_spaces(store):
    """人会输入 "Hehai University " 而不是精确串：归一化只碰大小写与首尾空白。"""
    _reg(store, username="归一化", answers=["Hehai University", "hehai2024", "李建国"])
    store.reset_password(username="归一化", answers=["  hehai university  ", "HEHAI2024", "李建国"],
                         new_password=PW2)
    assert store.login("归一化", PW2)


@pytest.mark.parametrize("answers", [
    [], ["新市场小学"], ANS[:2], ANS + ["第四条"],
    ["", "hehai2024", "李建国"], ["  ", "hehai2024", "李建国"],
    ["答" * 200, "hehai2024", "李建国"], ["x", "yy", "zzz"],
    "新市场小学",
])
def test_absurd_recovery_answers_are_rejected(store, answers):
    """注册时三条答案都得是个能用的东西：空的、太短的、超长的、条数不对的一律 422 那一类。"""
    with pytest.raises(AuthError):
        store.register(username="不合格", password=PW, security_answers=answers)


def test_a_rejected_register_leaves_no_record(store):
    """半套找回凭据比没有更糟：界面会以为能自助，走到第二步才发现答不上来。

    被拒的那次注册必须在写盘之前就抛出去，否则库里躺着一堆解不开的残骸。
    """
    for answers in ([], ANS[:2], ["", "x2", "x3"]):
        with pytest.raises(AuthError):
            store.register(username="半个", password=PW, security_answers=answers)
    assert store.list_users() == [], "被拒的注册不该留下半成品记录"
