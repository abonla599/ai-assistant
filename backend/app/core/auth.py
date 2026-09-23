"""身份存储：用户、密码校验、可撤销会话令牌。

刻意不 import FastAPI——身份规则必须能离线测，也不该被 web 框架绑住。

2026-09-17 凭据模型换过一次：注册不再需要邀请码，改成用户名 + 自设密码。
密码**只用来换一枚会话令牌**，运行时鉴权走的仍然是令牌，密码不作为每请求
凭据——否则它会出现在每一次请求头、代理与访问日志里。

2026-09-18 密保从"每人自设一个问题"换成全站固定的三题（RECOVERY_QUESTIONS）：
记录里只存三个答案的 bcrypt 摘要（answer_hashes，顺序与常量对齐），不再有自设
问题，也就不再有"报出问题"这条免凭据信道——问服务器要一句服务器抄出来的话，
只是白白多一条能回答"这个用户名存在吗"的路。

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
# RESET_FAIL 是同一条免凭据路径上四种失败的同一句话：答案错、这个人没留找回
# 答案、查无此人、账号被停用。多说一个字，这个端点就是一份用户名存在性名单；
# 耗时也一样，见 reset_password 里那三枚 dummy 摘要。
RESET_FAIL = "答案不正确"

# 找回问题全站固定，这一份就是唯一事实来源：界面自己渲染这三句，不必问服务器要，
# 于是"报出问题"那条能回答"这个用户名存在吗"的信道整个不存在了。列表顺序即答案
# 顺序：记录里按这个次序存三枚摘要，问题文本本身不进库。
RECOVERY_QUESTIONS: tuple[str, str, str] = ("你的手机号后四位是什么？",
                                            "你小学在哪上？",
                                            "你父母姓氏的拼音首字母各一个是什么？")
# 条数从常量取，不留第二个"三"：以后加一句问题，比对与文案自己就跟上了。
ANSWER_COUNT = len(RECOVERY_QUESTIONS)

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
# 找回密码的三条答案各走一枚这样的摘要：答案错、没留答案、查无此人三件事同样
# 不许有快慢差，而且这个"同样"是拿三条 dummy 凑出来的，不是靠运气。
_DUMMY_PW_HASH = "bcrypt:" + bcrypt.hashpw(_prehash("timing-equalizer"),
                                           bcrypt.gensalt()).decode("ascii")


def _answer_key(answer: str) -> str:
    """比对用的宽松归一化：人会输入 "Hehai University " 而不是精确串。

    这里刻意**不**做长度校验——"答案太短"这种话一旦出现在找回流程的比对环节，
    它就成了一条新的、可区分的失败措辞。严格校验属于形状闸门（_check_answer_shapes），
    它在读库之前跑完，说的只是调用方刚提交的东西。
    """
    return (answer or "").strip().casefold()


def _check_answer_shape(answer: str) -> str:
    key = _answer_key(answer)
    if len(key) < ANSWER_MIN:
        raise AuthError(f"答案至少 {ANSWER_MIN} 个字符")
    if len(key) > ANSWER_MAX:
        raise AuthError(f"答案最长 {ANSWER_MAX} 个字符")
    return key


def _check_answer_shapes(answers) -> list:
    """找回答案的形状闸门：条数必须是 ANSWER_COUNT，逐条还得是能用的东西。

    条数**先**查：少一条就抛，而不是后面比对时悄悄少跑一次 bcrypt——静默截断等于
    把"第几题没填"漏成耗时差，那正是这条路径要挡的事。这句话只描述调用方提交的
    内容，与库里任何一枚摘要无关，所以它不构成"答案猜对了"的信号。
    """
    if not isinstance(answers, list) or len(answers) != ANSWER_COUNT:
        raise AuthError(f"找回的 {ANSWER_COUNT} 题答案都要填，"
                        f"且每题至少 {ANSWER_MIN} 个字符")
    return [_check_answer_shape(a) for a in answers]


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

    charge 是同一件事在找回那一侧的写法：**这句要不要进 _RESET_FAILS**。找回端点上
    "答案错 / 没留答案 / 查无此人 / 已停用"四种原因同一句话、同一格预算，而新密码太短、
    答案条数不对那些是当事人自己改得好的手滑，422 且不计费。

    这两个标记都必须显式带着：HTTP 层靠它们决定"这句要不要进限流账本"。靠 reason 里
    有没有某个字、或等于哪句文案来判，等于把一条安全预算挂在文案上——改文案的那天计费
    静默失效。终审 F4 抓到的就是没带 charge 时的具体形状：路由写 `e.reason == RESET_FAIL`，
    第五种内部原因一旦新增就静默落到 else 那一支——不计费，还把它原话说给一个免凭据端点。
    """

    def __init__(self, reason: str, *, taken: bool = False, charge: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.taken = taken
        self.charge = charge


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
            # replace 原子不等于落盘：身份库丢一次重写就是全员锁死，必须
            # flush + fsync 之后再 replace。
            f.flush()
            os.fsync(f.fileno())
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

    def has_role(self, role: str) -> bool:
        """库里有没有这个角色的账号——fail-closed 那道门**每个请求**都要问一次。

        刻意不复制记录：list_users() 会给每人新建一份 dict(...)，而一条记录里躺着
        口令散列和全部令牌哈希，只为读一个 role 字段实在不划算。这里在同一把锁内
        就地遍历，读到的仍是当下真值——没有缓存，也就没有"停用/删号之后还认旧身份"
        那一类失效 bug（那正是 list_users 那份拷贝换来的东西，不能省成表外记忆）。
        """
        with self._lock:
            return any(u.get("role") == role for u in self._users.values())

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

    def register(self, username: str, password: str, security_answers: list = None):
        """用户名 + 自设密码换一个可撤销的会话令牌。注册即登录。

        三条找回答案在这里是**可选**的，因为真实库里就有它们之前注册的账号，
        历史数据与测试都靠"一条不给"这条走路。但只要给，就必须给满三条：半套
        凭据比没有更糟——界面会以为能自助，走到第二步才发现答不上来。
        """
        # 形状校验与慢哈希全部挪到锁外：这把锁与鉴权中间件的 resolve/has_role
        # 共用，bcrypt 一枚要几百毫秒，锁内计算等于把整个事件循环（连同所有
        # SSE 流）冻住那么久。哈希结果与"用户名是否已存在"无关，提前算不泄漏
        # 任何新信道——失败分支只会更慢，不会更快。
        cleaned = self._normalize_username(username)
        pw = self._check_password_shape(password)
        keys = None
        if security_answers is not None:
            keys = _check_answer_shapes(security_answers)
        pw_hash = hash_password(pw)
        # 答案与密码同一个慢哈希：它往往是个能猜的地名，熵比密码还低。
        answer_hashes = [hash_password(k) for k in keys] if keys is not None else None

        lc = cleaned.casefold()
        with self._lock:
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已存在", taken=True)

            record = {
                "user_id": self._new_user_id(),
                "username": cleaned,
                "username_lc": lc,
                "pw_hash": pw_hash,
                "tokens": [],
                "role": "user",
                "disabled": False,
                "created_at": _now(),
                "last_used_at": _now(),
            }
            if answer_hashes is not None:
                # 问题本身不进库——它是全站那三句常量。
                record["answer_hashes"] = answer_hashes
            self._users[record["user_id"]] = record
            token = self._issue_token(record)
            self._flush()
        return Principal(user_id=record["user_id"], username=cleaned, role="user"), token

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
        # bcrypt 挪出锁（与 register 同一条理由）：锁内只留两个字符串读取，慢哈希
        # 在锁外跑，鉴权中间件不再被登录请求冻在事件循环上。
        ok = _check_password(password, stored)
        if not ok:
            raise AuthError(_LOGIN_FAIL)
        with self._lock:
            # 出锁的这段时间里状态可能变了：发令牌前重读，鉴权不建立在过期快照上。
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            if record is None or record.get("disabled"):
                # 停用与密码错也说同一句话：告诉调用方"这个账号被停用了"等于
                # 让任何人确认账号存在、并知道该去找谁求情。查无此人同样走这句，
                # 而且三支都已经在锁外付过一次 bcrypt 的耗时。
                raise AuthError(_LOGIN_FAIL)
            token = self._issue_token(record)
            record["last_used_at"] = _now()
            self._flush()
        return Principal(user_id=record["user_id"], username=record["username"],
                         role=record.get("role", "user")), token

    def reset_password(self, username: str, answers: list, new_password: str,
                       new_answers: list = None):
        """三题全答对就换密码（顺手也能换答案），并作废这个人名下所有会话令牌。

        三处顺序是安全属性，不是风格：
        1. 新密码与两组答案的形状先查，再读库。反过来时"密码太短"会变成"答案
           猜对了"的确认信号，一个免凭据端点就多了一比特可问的东西；
        2. 三条比对全部算完再判，不因为某一条错了就提前返回。短路等于把"第几题
           猜对了"漏进耗时里，三题于是拆成三份互相独立的预算，整条路的强度除以三；
        3. 改密必须清令牌。"我改了密码，因为手机丢了"是这条路径存在的理由，
           旧令牌还活着的话它就是个假动作。
        """
        pw = self._check_password_shape(new_password)
        keys = _check_answer_shapes(answers)
        # 轮换：固定问题不等于固定答案，答案泄露过就该换得掉。不给就原样留着。
        replacement = _check_answer_shapes(new_answers) if new_answers is not None else None

        want = (username or "").strip().casefold()
        with self._lock:
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            user_id_at_read = (record or {}).get("user_id")
            # 查无此人与没留答案都补齐成 ANSWER_COUNT 枚 dummy：比对的条数与耗时
            # 都不随人变，否则"这一次回得快一点"本身就是一份用户名名单。
            digests = list((record or {}).get("answer_hashes") or []) \
                + [_DUMMY_PW_HASH] * ANSWER_COUNT
        # bcrypt 全部在锁外算（与 register/login 同一条理由）。显式循环，不是
        # any/all 生成式：三条 bool 必须先全算完再判。早退一处，"第几题猜对了"
        # 就漏进耗时里，三题变成三份互相独立的预算。
        results = []
        for index, key in enumerate(keys):
            results.append(_check_password(key, digests[index]))
        # 新密码/新答案的慢哈希也提前算好——成功才哈希的原写法会把 bcrypt 留在
        # 锁内；失败分支多付一次哈希只会更慢，不产生新的可区分信道。
        new_pw_hash = hash_password(pw)
        new_answer_hashes = ([hash_password(k) for k in replacement]
                             if replacement is not None else None)
        # 查无此人与没留答案也归到同一句 RESET_FAIL——挪到循环之前判断，就是给
        # 它们开一条"秒回"的快路径。all() 之后仍要判这两条：dummy 摘要理论上
        # 会被 "timing-equalizer" 这个答案撞中，只靠 all() 不够硬。
        if not all(results):
            raise AuthError(RESET_FAIL, charge=True)
        with self._lock:
            # 出锁期间账号可能被删除或以同名重建：重读并要求还是同一个人，
            # 不把答案校验的通过结果落到一条换过的记录上。
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            if (record is None or not record.get("answer_hashes")
                    or record.get("user_id") != user_id_at_read):
                raise AuthError(RESET_FAIL, charge=True)
            if record.get("disabled"):
                # 停用与答案错也说同一句话：告诉调用方"这个账号被停用了"等于让
                # 任何人确认账号存在、并知道该去找谁求情。
                raise AuthError(RESET_FAIL, charge=True)
            record["pw_hash"] = new_pw_hash
            if new_answer_hashes is not None:
                record["answer_hashes"] = new_answer_hashes
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

    def revoke(self, token: str) -> None:
        """摘掉这一枚会话令牌——"退出这台机器"的全部含义。

        刻意不清整张表：那是 `rotate_token` 的语义（怀疑口令泄露，把所有设备一起
        踢掉）。"退出"如果顺手把别人设备也踢下线，它就是一个能跨设备使坏的动作，
        没人敢点。

        认不出这枚令牌时不抛、也不返回"在不在"：返回值上的任何差别都是一个
        oracle，外面可以拿它试探某枚令牌是否曾经有效过。
        """
        if not token:
            return
        digest = hash_token(token)
        with self._lock:
            for record in self._users.values():
                stored = record.get("tokens") or []
                kept = [s for s in stored if not _token_matches(s, digest)]
                if len(kept) == len(stored):
                    continue
                record["tokens"] = kept
                self._flush()       # 不落盘的退出等于没退出：重启就又登得回来
                return

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
