"""身份存储：用户、密码校验、可撤销会话令牌。

刻意不 import FastAPI——身份规则必须能离线测，也不该被 web 框架绑住。

2026-09-17 凭据模型换过一次：注册不再需要邀请码，改成用户名 + 自设密码。
密码**只用来换一枚会话令牌**，运行时鉴权走的仍然是令牌，密码不作为每请求
凭据——否则它会出现在每一次请求头、代理与访问日志里。

令牌只存 SHA-256：本文件的数据与 sessions.json 同目录，而本仓库有过 .env
被跟踪导致密钥泄露 5 个月的前科，明文存令牌等于把所有人的访问权一起放在
一个随时可能被误提交的文件里。令牌本身是 256 位随机值，故 sha256 足够，
不需要慢哈希。密码反过来必须用慢哈希（bcrypt），它是人会自己编的东西。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import bcrypt

from app.core.paths import data_root

RESERVED_NAMES = {"admin", "default_user"}
USERNAME_MAX = 24
PASSWORD_MIN = 8
PASSWORD_MAX = 512          # 上限只为挡"贴进来一本書"，真正的长度问题由预哈希解决

# 一个人同时在几台设备上用是正当需求（手机 + 桌面 + 浏览器），所以登录是"追加
# 一枚会话令牌"而不是"顶掉旧的"。但令牌表不能只进不出：一个脚本反复登录就能把
# 这个人的记录撑大，所以设上限，超了就丢最老的那一枚。
MAX_SESSION_TOKENS = 8

# bootstrap 管理员口令的 env 名：authz 与 main 共用这一个，别再各写一份字面量
BOOTSTRAP_TOKEN_ENV = "ACCESS_TOKEN"

# last_used_at 只是运维参考信息，不值地为每一次鉴权重写文件：磁盘满、
# 或 Windows 上文件被编辑器/杀软/同步盘锁住时，热路径上的写会把一枚有效令牌
# 变成 500。内存里照常刷新，落盘按这个阈值降频。
LAST_USED_FLUSH_AFTER = timedelta(hours=1)

_LOGIN_FAIL = "用户名或密码不正确"
# 找回流程的两句话是安全边界，不是文案：
#   NO_RECOVERY 同时用于"查无此人"与"这个人没开找回"——两者同形，这个免凭据
#     端点才不会顺手报出"哪些用户名真实存在"；
#   RESET_FAIL 同时用于"答案错"、"没开找回"、"查无此人"与"账号被停用"。
NO_RECOVERY = "这个用户名没有设置密码找回"
RESET_FAIL = "答案不正确"

QUESTION_MAX = 60
ANSWER_MIN = 2
ANSWER_MAX = 64


def _now_dt() -> datetime:
    return datetime.now()


def _now() -> str:
    return _now_dt().isoformat()


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prehash(password: str) -> bytes:
    """bcrypt 只吃前 72 字节，长密码会被静默截断成同一个哈希。

    先 SHA-256 再 base64（44 字节，恒小于 72），于是 "正确但很长" 的密码不会
    在若干年后变成另一个人的密码。
    """
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    # 带算法前缀，和令牌那边的 "sha256:" 同一个理由：以后换哈希方案时，
    # 旧记录还认得自己是用什么散的，校验分支不必靠猜。
    return "bcrypt:" + bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("ascii")


def _check_password(password: str, stored) -> bool:
    if not isinstance(stored, str) or not stored.startswith("bcrypt:"):
        return False        # 没有密码的旧记录：一律登不进，而不是抛异常
    try:
        return bcrypt.checkpw(_prehash(password), stored[7:].encode("ascii"))
    except ValueError:
        # 摘要被人手改坏（非法 base64 等）→ "这枚密码不对"，不是 500
        return False


# "用户名不存在"与"密码错"必须连时间都一样，否则响应快慢本身就是一份用户名名单。
# 这个固定摘要只在 import 时算一次，让"查无此人"那一支也付一次 bcrypt 的代价。
# 找回密码用的答案走同一枚摘要：答案错、没开找回、查无此人三件事同样不许有快慢差。
_DUMMY_PW_HASH = "bcrypt:" + bcrypt.hashpw(_prehash("timing-equalizer"),
                                           bcrypt.gensalt()).decode("ascii")


def _answer_key(answer: str) -> str:
    """比对用的宽松归一化：人会输入 "Hehai University " 而不是精确串。

    这里刻意**不**做长度校验——"答案太短"这种话一旦出现在找回流程里，
    它就成了一条新的、可区分的失败措辞。严格校验只属于注册（_check_answer_shape）。
    """
    return (answer or "").strip().casefold()


def _check_answer_shape(answer: str) -> str:
    key = _answer_key(answer)
    if len(key) < ANSWER_MIN:
        raise AuthError(f"答案至少 {ANSWER_MIN} 个字符")
    if len(key) > ANSWER_MAX:
        raise AuthError(f"答案最长 {ANSWER_MAX} 个字符")
    return key


def _check_question_shape(question: str) -> str:
    cleaned = (question or "").strip()
    if not cleaned:
        raise AuthError("找回问题不能为空")
    if len(cleaned) > QUESTION_MAX:
        raise AuthError(f"找回问题最长 {QUESTION_MAX} 个字符")
    if re.search(r"[\x00-\x1f\x7f]", cleaned):
        raise AuthError("找回问题含不可见字符")
    return cleaned


def _as_hash_bytes(value) -> bytes:
    # 令牌摘要本恒为 ASCII，但 users.json 和 sessions.json 同目录、是可被人手
    # 改的文件：hmac.compare_digest 遇到非 ASCII str 会抛 TypeError，而非 str
    # 会抛别的。鉴权读到坏数据的正确表现是"这枚令牌解不出来"，不是 500。
    if not isinstance(value, str):
        return b"\x00not-a-token-hash"
    return value.encode("utf-8", "surrogatepass")


def _token_matches(stored, digest: str) -> bool:
    return hmac.compare_digest(_as_hash_bytes(stored), _as_hash_bytes(digest))


def _stale_for_flush(stored, moment: datetime) -> bool:
    """时间戳缺失或被人改坏时按"该落盘"处理：宁可多写一次，不可丢记录。"""
    try:
        return moment - datetime.fromisoformat(stored) >= LAST_USED_FLUSH_AFTER
    except (TypeError, ValueError):
        return True


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str      # "user" | "admin"


class AuthError(Exception):
    """注册/登录/找回失败。

    reason 是面向用户的那句话。登录失败不分"没这个用户"还是"密码错"——两句
    必须逐字节相同（test_auth.py 钉着），否则免凭据的登录端点就是用户名探测器。
    注册端的"该用户名已存在"是有意保留的实话（改名是用户自己能解决的事），
    但它前面没有闸门，所以那份预算改由 HTTP 层按真实 IP 计费（AuthError.taken）。

    taken 必须显式带着：HTTP 层拿它决定"这句要不要进限流账本"。靠 reason 里
    有没有某个字来判，等于把一条安全预算挂在文案上——改文案的那天计费静默失效。
    """

    def __init__(self, reason: str, *, taken: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.taken = taken


def _quarantine(path: str, why: str) -> None:
    """把读不懂的身份文件先挪走，再允许从空库开始。

    "就地留着、以空库启动"是不够的：下一次 register/login/disable 就会
    用内存里那份空表把它覆盖掉，被删掉的人连找回的原始材料都没有。先改名成
    .corrupt，坏数据至少还在磁盘上、也还在人眼里（会话与附件两个兄弟存储同一套
    做法，见 session_store._load、uploads._load）。
    """
    backup = path + ".corrupt"
    try:
        os.replace(path, backup)
        print(f"⚠️ {why}，已备份为 {backup}")
    except OSError as e:
        print(f"⚠️ {why}，但备份失败（{e}）：{path} 未被挪走，请先手工备份再重启")


def _default_users_path() -> str:
    # env 覆盖是硬需求：conftest 必须把它指向临时目录，否则测试会写进用户
    # 真实的 data/users.json——那是越出本次改动范围的副作用。
    env_path = os.getenv("USERS_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "users.json")


class AuthStore:
    def __init__(self, path: str = None):
        self.path = os.path.abspath(path or _default_users_path())
        self._lock = threading.Lock()
        self._users = {}
        self._load(self._users, self.path)

    @staticmethod
    def _load(target: dict, path: str):
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            # 身份库坏了绝不能"静默当空库继续跑"——那会让已发令牌全部失效，
            # 并可能在下一次写入时覆盖掉真实数据。备份后从空开始并显式告警。
            _quarantine(path, f"身份文件损坏（{e}）")
            return
        if isinstance(data, dict):
            target.update(data)
            return
        # 同一句话也管这一支：能 parse、但顶层不是对象（整份被写成了一个列表、
        # 一个字符串、甚至手工写成了 `[]`）。原先这里什么都不做，于是库以空表
        # 启动、下一次 register/disable 把 users.json 整个覆盖掉，
        # 坏数据连一个字都不剩——上面那条不变量就是这么被绕过去的。
        _quarantine(path, f"身份文件形状不对（{path} 顶层是 "
                          f"{type(data).__name__}，应为对象）")

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._users, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---------- 用户 ----------

    @staticmethod
    def _normalize_username(username: str) -> str:
        cleaned = (username or "").strip()
        if not cleaned:
            raise AuthError("用户名不能为空")
        if len(cleaned) > USERNAME_MAX:
            raise AuthError(f"用户名最长 {USERNAME_MAX} 个字符")
        if re.search(r"[\x00-\x1f\x7f]", cleaned):
            raise AuthError("用户名含不可见字符")
        if cleaned.casefold() in RESERVED_NAMES:
            raise AuthError("该用户名为系统保留字")
        return cleaned

    @staticmethod
    def _check_password_shape(password: str) -> str:
        pw = password or ""
        if not pw.strip():
            raise AuthError("密码不能为空")
        if len(pw) < PASSWORD_MIN:
            raise AuthError(f"密码至少 {PASSWORD_MIN} 位")
        if len(pw) > PASSWORD_MAX:
            raise AuthError(f"密码最长 {PASSWORD_MAX} 位")
        return pw

    def list_users(self) -> list:
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def _new_user_id(self) -> str:
        # 取 id 那处必须重取：两个线程撞上同一个 id 时，
        # `self._users[user_id] = record` 会把已有那个人整条记录覆盖掉——
        # 他的令牌当场失效，而且没有任何报错。32 bit 撞上的概率极低，
        # 但"极低"不是"检查只要一行就别省"的理由。
        user_id = "u_" + secrets.token_hex(4)
        while user_id in self._users:
            user_id = "u_" + secrets.token_hex(4)
        return user_id

    def _issue_token(self, record: dict) -> str:
        """给这个账号追加一枚会话令牌，超出上限就丢最老的那一枚。"""
        token = secrets.token_urlsafe(32)
        tokens = record.setdefault("tokens", [])
        tokens.append(hash_token(token))
        if len(tokens) > MAX_SESSION_TOKENS:
            del tokens[:len(tokens) - MAX_SESSION_TOKENS]
        return token

    def register(self, username: str, password: str, security_question: str = None,
                 security_answer: str = None):
        """用户名 + 自设密码换一个可撤销的会话令牌。注册即登录。

        找回问题与答案在这里是**可选**的，因为真实库里就有它们之前注册的账号；
        强制"新注册必须填"的是 HTTP 层的 RegisterRequest（见 auth_router），
        那里少一个字段就是 422。半套凭据在存储层同样被拒：界面会以为能自助，
        走到第二步才发现答不上来。
        """
        with self._lock:
            cleaned = self._normalize_username(username)
            pw = self._check_password_shape(password)
            carrying = [security_question is not None, security_answer is not None]
            if any(carrying) and not all(carrying):
                raise AuthError("找回问题与答案要一起填")
            question = _check_question_shape(security_question) if all(carrying) else None
            answer = _check_answer_shape(security_answer) if all(carrying) else None

            lc = cleaned.casefold()
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已存在", taken=True)

            user_id = self._new_user_id()
            record = {
                "user_id": user_id,
                "username": cleaned,
                "username_lc": lc,
                "pw_hash": hash_password(pw),
                "tokens": [],
                "role": "user",
                "disabled": False,
                "created_at": _now(),
                "last_used_at": _now(),
            }
            if question is not None:
                record["security_question"] = question
                # 答案与密码同一个慢哈希：它往往是个能猜的地名，熵比密码还低。
                record["answer_hash"] = hash_password(answer)
            self._users[user_id] = record
            token = self._issue_token(record)
            self._flush()
        return Principal(user_id=user_id, username=cleaned, role="user"), token

    def login(self, username: str, password: str):
        """校验用户名与密码，成功则追加一枚会话令牌。

        查无此人与密码错走的是同一条出口：同一句 reason、同一个状态码，而且
        查无此人也要跑一次 bcrypt（_DUMMY_PW_HASH），否则"立刻返回"的快慢差
        就把这个端点变成用户名探测器——时序与文案都得一样。
        """
        want = (username or "").strip().casefold()
        with self._lock:
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            # 旧模型留下的账号没有 pw_hash，也要走同一份假摘要：否则"有这个人但
            # 没密码"会比"有这个人且密码错"快一截，时序又漏了信息。
            stored = (record or {}).get("pw_hash") or _DUMMY_PW_HASH
            if not _check_password(password, stored):
                raise AuthError(_LOGIN_FAIL)
            if record.get("disabled"):
                # 停用与密码错也说同一句话：告诉调用方"这个账号被停用了"等于
                # 让任何人确认账号存在、并知道该去找谁求情。
                raise AuthError(_LOGIN_FAIL)
            token = self._issue_token(record)
            record["last_used_at"] = _now()
            self._flush()
        return Principal(user_id=record["user_id"], username=record["username"],
                         role=record.get("role", "user")), token

    def recovery_question(self, username: str) -> str:
        """报出这个人的找回问题；没得报的两种情况说同一句话。

        返回问题文本本身就是泄露面（"这个用户名开了找回"），所以这里只保留那一
        比特：查无此人与开了账号但没设找回问题的人，拿到逐字节相同的 NO_RECOVERY。
        HTTP 层再按真实 IP 给这个端点计失败预算（见 auth_router）。
        """
        want = (username or "").strip().casefold()
        with self._lock:
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None) or {}
            if record.get("disabled"):
                # 停用的人不该在这里被认出来：那等于告诉别人"这个号存在且被停了"。
                return NO_RECOVERY
            return record.get("security_question") or NO_RECOVERY

    def reset_password(self, username: str, answer: str, new_password: str):
        """答对找回问题就换密码，并把这个人名下所有会话令牌一起作废。

        两处顺序是安全属性，不是风格：
        1. 新密码的形状先查，答案后验。反过来时"密码太短"会变成"答案猜对了"的
           确认信号，一个免凭据端点就多了一比特可问的东西；
        2. 改密必须清令牌。"我改了密码，因为手机丢了"是这条路径存在的理由，
           旧令牌还活着的话它就是个假动作。
        """
        pw = self._check_password_shape(new_password)
        want = (username or "").strip().casefold()
        with self._lock:
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            stored = (record or {}).get("answer_hash") or _DUMMY_PW_HASH
            if not _check_password(_answer_key(answer), stored):
                # 答案错、没开找回、查无此人：三条走同一句、同一份 bcrypt 代价
                raise AuthError(RESET_FAIL)
            if record.get("disabled"):
                raise AuthError(RESET_FAIL)
            record["pw_hash"] = hash_password(pw)
            record["tokens"] = []
            record["last_used_at"] = _now()
            self._flush()

    def resolve(self, token: str):
        if not token:
            return None
        digest = hash_token(token)
        moment = _now_dt()
        with self._lock:
            for record in self._users.values():
                if record.get("disabled"):
                    continue
                if not any(_token_matches(stored, digest)
                           for stored in (record.get("tokens") or [])):
                    continue
                # 热路径：先判断该不该落盘，再改内存——顺序反了阈值就永远不满。
                stale = _stale_for_flush(record.get("last_used_at"), moment)
                record["last_used_at"] = moment.isoformat()
                if stale:
                    self._flush()
                return Principal(user_id=record["user_id"],
                                 username=record["username"],
                                 role=record.get("role", "user"))
        return None

    def disable_user(self, user_id: str) -> bool:
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                return False
            record["disabled"] = True
            self._flush()
            return True

    def enable_user(self, user_id: str) -> bool:
        # disable 的对称动作。rotate_token 刻意不再顺手清掉 disabled（那等于把
        # 撤销抵消掉），所以"恢复访问"这条路径必须显式存在，否则运维只能删号重建。
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                return False
            record["disabled"] = False
            self._flush()
            return True

    def rotate_token(self, user_id: str) -> str:
        """强制全端重登：清掉所有旧会话令牌，只留新发的这一枚。

        有了多设备并存之后，"撤销"必须是清空整张令牌表——只换掉其中一枚等于什么
        都没撤销，别人手机上的那枚还能继续用。这里刻意不碰 disabled：换令牌是
        凭证动作，不是重新启用账号。
        """
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                raise AuthError("用户不存在")
            record["tokens"] = []
            token = self._issue_token(record)
            self._flush()
            return token

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._flush()
            return True


auth_store = AuthStore()
