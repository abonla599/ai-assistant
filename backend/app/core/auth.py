"""身份存储：用户、邀请码、可撤销令牌。

刻意不 import FastAPI——身份规则必须能离线测，也不该被 web 框架绑住。
令牌只存 SHA-256：本文件的数据与 sessions.json 同目录，而本仓库有过 .env
被跟踪导致密钥泄露 5 个月的前科，明文存令牌等于把所有人的访问权一起放在
一个随时可能被误提交的文件里。令牌本身是 256 位随机值，故 sha256 足够，
不需要慢哈希。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime

from app.core.paths import data_root

# 排除易混字符：邀请码要在手机上手输
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RESERVED_NAMES = {"admin", "default_user"}
USERNAME_MAX = 24


def _now() -> str:
    return datetime.now().isoformat()


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    """注册/鉴权失败。reason 面向用户，不外泄"码存在但已用尽"之类的区别。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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
            backup = path + ".corrupt"
            try:
                os.replace(path, backup)
                print(f"⚠️ 身份文件损坏（{e}），已备份为 {backup}")
            except OSError:
                print(f"⚠️ 身份文件损坏且无法备份（{e}）")
            return
        if isinstance(data, dict):
            target.update(data)

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

    def list_users(self) -> list:
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def register(self, code: str, username: str):
        cleaned = self._normalize_username(username)
        lc = cleaned.casefold()
        with self._lock:
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已被占用")

            invite = self._invites.get((code or "").strip().upper())
            if invite is None or self._invite_spent(invite):
                raise AuthError("邀请码无效或已用完")

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
        now = _now()
        with self._lock:
            for record in self._users.values():
                if record.get("disabled"):
                    continue
                if hmac.compare_digest(record.get("token_hash", ""), digest):
                    record["last_used_at"] = now
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

    def rotate_token(self, user_id: str) -> str:
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                raise AuthError("用户不存在")
            token = secrets.token_urlsafe(32)
            record["token_hash"] = hash_token(token)
            record["disabled"] = False
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
        code = _new_code()
        while code in self._invites:
            code = _new_code()
        with self._lock:
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
            return [dict(i) for i in self._invites.values()]

    def revoke_invite(self, code: str) -> bool:
        with self._lock:
            if code not in self._invites:
                return False
            del self._invites[code]
            self._flush()
            return True


auth_store = AuthStore()
