"""身份存储：用户、邀请码、可撤销令牌。

刻意不 import FastAPI——身份规则必须能离线测，也不该被 web 框架绑住。
令牌只存 SHA-256：本文件的数据与 sessions.json 同目录，而本仓库有过 .env
被跟踪导致密钥泄露 5 个月的前科，明文存令牌等于把所有人的访问权一起放在
一个随时可能被误提交的文件里。令牌本身是 256 位随机值，故 sha256 足够，
不需要慢哈希。
"""
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.paths import data_root

# 排除易混字符：邀请码要在手机上手输
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RESERVED_NAMES = {"admin", "default_user"}
USERNAME_MAX = 24

# bootstrap 管理员口令的 env 名：authz 与 main 共用这一个，别再各写一份字面量
BOOTSTRAP_TOKEN_ENV = "ACCESS_TOKEN"

# last_used_at 只是运维参考信息，不值地为每一次鉴权重写两个文件：磁盘满、
# 或 Windows 上文件被编辑器/杀软/同步盘锁住时，热路径上的写会把一枚有效令牌
# 变成 500。内存里照常刷新，落盘按这个阈值降频。
LAST_USED_FLUSH_AFTER = timedelta(hours=1)


def _now_dt() -> datetime:
    return datetime.now()


def _now() -> str:
    return _now_dt().isoformat()


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _new_code() -> str:
    left = "".join(secrets.choice(ALPHABET) for _ in range(4))
    right = "".join(secrets.choice(ALPHABET) for _ in range(4))
    return f"{left}-{right}"


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str      # "user" | "admin"


class AuthError(Exception):
    """注册/鉴权失败。reason 面向用户；用户听到的那句不分"码不存在"与"码已用尽"。

    code_was_spent 是**只在进程内**用的记号，给 HTTP 层分账用（见
    auth_router.register：重复提交一枚已经花掉的码是正当重试，不是猜码，
    不该烧限流预算）。它不进任何响应体——对外两句 reason 完全相同这件事由
    test_auth_endpoints.py 的 ..._look_identical 钉着，别把它写成 detail。
    """

    def __init__(self, reason: str, *, code_was_spent: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.code_was_spent = code_was_spent


def _quarantine(path: str, why: str) -> None:
    """把读不懂的身份文件先挪走，再允许从空库开始。

    "就地留着、以空库启动"是不够的：下一次 create_invite/register/disable 就会
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


def _default_invites_path() -> str:
    env_path = os.getenv("INVITES_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "invites.json")


class AuthStore:
    def __init__(self, path: str = None, invites_path: str = None):
        self.path = os.path.abspath(path or _default_users_path())
        self.invites_path = os.path.abspath(invites_path or _default_invites_path())
        self._lock = threading.Lock()
        self._users = {}
        self._invites = {}
        self._load(self._users, self.path)
        self._load(self._invites, self.invites_path)

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
        # 启动、下一次 create_invite/register/disable 把 users.json 整个覆盖掉，
        # 坏数据连一个字都不剩——上面那条不变量就是这么被绕过去的。
        _quarantine(path, f"身份文件形状不对（{path} 顶层是 "
                          f"{type(data).__name__}，应为对象）")

    def _flush(self):
        for path, payload in ((self.path, self._users), (self.invites_path, self._invites)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

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
    def _normalize_code(code: str) -> str:
        # 邀请码要在手机上手输、从聊天里粘贴，大小写和首尾空格不该改变它指向
        # 哪条记录——register 与 revoke_invite 必须共用这一份规则。
        return (code or "").strip().upper()

    def list_users(self) -> list:
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def register(self, code: str, username: str):
        """邀请码换身份。检查顺序本身是安全属性，不是风格问题：码先、名字后。

        注册端点免凭据。若先查重名，那么一个邀请码都没有的人也能问出
        "这个名字被占了吗"，而且两个方向都得到真话——端点就成了用户名枚举
        预言机（用户名可被人拿去撞别的服务）。先验码之后，没码的人无论填什么
        用户名都只听到同一句"邀请码无效"；只有握着有效未用码的人才配知道
        "这名字撞了"，而他本来就有注册权限，这句实话不再增加攻击面。
        """
        with self._lock:
            invite = self._invites.get(self._normalize_code(code))
            if invite is None:
                raise AuthError("邀请码无效或已用完")
            if self._invite_spent(invite):
                # 与上面那一句一字不差——差别只在进程内的记号：这枚码真的存在过，
                # 重复提交它的人就是当初拿到它的人（手机上双击的正是这一支）。
                raise AuthError("邀请码无效或已用完", code_was_spent=True)

            # 用户名的形状与占用都排在码之后、也排在消耗码之前：打错字或撞名
            # 都不该烧掉一枚邀请码（否则用户只能回去找管理员重新要码）。
            cleaned = self._normalize_username(username)
            lc = cleaned.casefold()
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已被占用")

            user_id = "u_" + secrets.token_hex(4)
            token = secrets.token_urlsafe(32)
            record = {
                "user_id": user_id,
                "username": cleaned,
                "username_lc": lc,
                "token_hash": hash_token(token),
                "role": "user",
                "disabled": False,
                "invite_code": invite["code"],
                "created_at": _now(),
                "last_used_at": _now(),
            }
            self._users[user_id] = record
            invite["used_by"] = (invite.get("used_by") or []) + [user_id]
            self._flush()
        return Principal(user_id=user_id, username=cleaned, role="user"), token

    def resolve(self, token: str):
        if not token:
            return None
        digest = hash_token(token)
        moment = _now_dt()
        with self._lock:
            for record in self._users.values():
                if record.get("disabled"):
                    continue
                if not _token_matches(record.get("token_hash"), digest):
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
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                raise AuthError("用户不存在")
            token = secrets.token_urlsafe(32)
            record["token_hash"] = hash_token(token)
            # 这里刻意不碰 disabled：换令牌是凭证动作，不是重新启用账号。
            # 顺手清掉停用标记会把本任务存在的意义——撤销——抵消掉。
            self._flush()
            return token

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._flush()
            return True

    # ---------- 邀请码 ----------

    @staticmethod
    def _invite_spent(invite: dict) -> bool:
        if invite.get("expires_at") and invite["expires_at"] < _now():
            return True
        return len(invite.get("used_by") or []) >= int(invite.get("max_uses", 1))

    def create_invite(self, created_by: str, max_uses: int = 1) -> str:
        with self._lock:
            # 取码与占码必须在同一个临界区内：锁外查重时两个线程可以挑中同一个
            # 码，后写者把前者的记录覆盖掉——丢的那个邀请码没有任何报错。
            code = _new_code()
            while code in self._invites:
                code = _new_code()
            self._invites[code] = {
                "code": code,
                "max_uses": max(1, int(max_uses)),
                "used_by": [],
                "created_at": _now(),
                "created_by": created_by,
                "expires_at": None,
            }
            self._flush()
        return code

    def list_invites(self) -> list:
        with self._lock:
            # 必须深拷贝：dict(i) 交出去的是 used_by 这个列表的活引用，调用方
            # append 一下就直接改了库——包括"这个码已经被谁用过"这条审计记录。
            return [copy.deepcopy(i) for i in self._invites.values()]

    def revoke_invite(self, code: str) -> bool:
        with self._lock:
            normalized = self._normalize_code(code)
            if normalized not in self._invites:
                return False
            del self._invites[normalized]
            self._flush()
            return True


auth_store = AuthStore()
