"""注册、登录与管理端点。

这里是身份存储（app/core/auth.py）与 HTTP 之间唯一的一层，规则三条：
1. 对外说话保守。登录失败只有一句"用户名或密码不正确"——区分"没这个用户"和
   "密码错"，就把这个免凭据端点变成了用户名探测器；存储层内部也刻意不分开
   （AuthError 只带那一句，见 auth.login）。注册端的"该用户名已存在"是有意
   保留的实话（改名是用户自己能解决的事），但邀请码退役之后它前面再没有闸门，
   所以这句改由**按真实 IP 计费**来限制——见 _note_failure 的口径。
2. 响应按字段白名单出。存储层的记录带着 pw_hash、tokens 和内建的 username_lc，
   顺手 return record 等于把口令摘要与会话令牌摘要交给前端与日志。
3. 凭据明文只在"必须被看见"的那一次出现：令牌见于注册与登录的响应，以及管理员
   轮换的响应。任何端点都不许复述调用方刚提交的密码或令牌。
"""
import re
import time
from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import authz, usage
# 只 import AuthError：找回那一支要不要计费看的是 e.charge 这个显式标记，
# 不再需要把 RESET_FAIL 那句文案搬进路由层（终审 F4）。
from app.core.auth import AuthError
from app.core.authz import CurrentPrincipal, Principal, RequireAdmin

router = APIRouter(tags=["身份"])

# 猜密码的代价：同一来源在窗口内失败太多次就拒一拒。进程内计数即可——
# 重启即清零是可接受的，因为真正的凭据是 bcrypt 校验与长密码。
# 账本只记失败，不记格式错（用户名打错字、密码太短）：那是当事人自己能改好的事，
# 把它算进预算只会让唯一的登录入口被自己的手滑锁死。
# **任何成功都不还回预算**（终审 F3 删掉了 register/login 成功路径上的两处
# `_FAILS.pop(ip)`）。旧契约"密码对了就说明来路正当"实测是假的：一台机器、一枚来源 IP，
# 只要穿插"自己的号成功一次"，撞名枚举能跑到 45 次 409 / 0 次 429，口令猜测能跑到
# 36 次错 / 0 次 429——因为"成功"本身就是这个免凭据端点上随手可得的东西（猜对自己的
# 口令、再注册一个小号）。代价如实认：同一个十分钟窗口里连续打错十次的真人要等窗口过去，
# 话术已经是"尝试次数过多，请稍后再试"。判据是两条反转过的锁
# （test_a_correct_login_does_not_pay_back_the_failure_budget 与
# test_a_successful_register_does_not_pay_back_the_failure_budget）。
# day 参数只认这一种写法；放宽=任何垃圾都换回一份 200 空表
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FAILURE_WINDOW_SECONDS = 600
MAX_FAILURES_PER_WINDOW = 10

# 注册开放之后，"能建多少个号"是唯一的成本闸门：按真实来源限成功数。
# 记成功而不是记失败，因为失败（撞名）本来就是零成本，而一个脚本可以无限撞名
# 却一个号也建不出来；能真正花钱的是"注册成功 + 拿去对话"。
REGISTER_WINDOW_SECONDS = 86400
MAX_REGISTRATIONS_PER_SOURCE = 3

# 自助改密走的是同一套口径，而且比注册更需要它：三题是全站公开的常量，"答对"不再
# 说明对面是本人，只说明他猜中了。连着猜中三次的人是另一个人，而每一次成功都把
# 名下所有会话令牌作废——对被猜的人而言这是真实的伤害（每台设备都掉线）。
# 上限 3 = 本人忘密改一次 + 反悔改回来 + 再出一次意外，之后只能找管理员。
RESET_WINDOW_SECONDS = 86400
MAX_RESETS_PER_SOURCE = 3

# 来源键取自 CF-Connecting-IP：域名必经 Cloudflare，而它会把真实访客 IP 写在
# 这个头上；后端只监听 127.0.0.1:8000、外部唯一入口就是 cloudflared，没有旁路
# 可以伪造这个头。取不到该头时退回 uvicorn 看到的对端地址（本机直连与测试）。
# 以前只用 request.client.host，经过隧道后恒为 127.0.0.1——全网共用一个桶，
# 一个人手滑就能把所有人挡在门外，所以这既是功能也是修 bug。
MAX_TRACKED_SOURCES = 4096
_FAILS = defaultdict(list)
_REGISTERS = defaultdict(list)
_RESETS = defaultdict(list)
# 猜找回答案的失败单独一册（窗口与上限沿用 FAILURE_WINDOW_SECONDS /
# MAX_FAILURES_PER_WINDOW，换的只是账本）。
# 拆开之后仍然站得住的两条理由：一，猜口令与猜三题是两种不同的猜测面，共用一格预算时
# 前者会把后者顶满，一个人猜错九次之后连自己的密码都不许再试一次；二，429 落在哪本账上
# 要看得出来，否则运维没法从 Retry-After 区分"刚才有人在枚举用户名"还是"有人在猜找回答案"。
# 找回这本账任何成功路径都不许清，改密成功也不行（判据：
# test_no_other_success_pays_off_the_guessing_ledger）。
# 当初拆账的**原始**理由是"_FAILS 会被登录或注册成功清空，猜答案的人随手就能拿到一次
# 那样的成功"——终审 F3 把那两处 pop 删掉之后，那半边理由已经不成立了，留着的是上面这两条。
# 别把这段再读成"那本会被成功清、这本不会"。
_RESET_FAILS = defaultdict(list)

# 每本账配自己的窗口：_prune 是内存闸门，拿十分钟那把尺子去过 24 小时那两本，就是
# 在来源数超过 4096 时把整桶有效记录提前丢掉——配额被悄悄放宽，是一条 fail-open。
# 这份元组同时是"账本有哪几本"的唯一清单：新加一本忘了加进来，就是只胀不收。
# 第五本：聊天节流。键是 `IP + 登录身份`，不是单独 IP——同一家共用一个出口的人
# 不该互相挡；而"换个身份就能重来一轮"是这个选择自带的口子，写在这行是为了别把
# 它当成密不透风的防线（真正花钱的那本在 app/core/usage.py，两件事互补）。
CHAT_WINDOW_SECONDS = 60
MAX_CHAT_CALLS_PER_WINDOW = 20
_CHATS = defaultdict(list)


def _chat_key(ip: str, user_id: str) -> str:
    return f"{ip}|{user_id or 'anon'}"


_LEDGERS = ((_FAILS, FAILURE_WINDOW_SECONDS),
            (_REGISTERS, REGISTER_WINDOW_SECONDS),
            (_RESETS, RESET_WINDOW_SECONDS),
            (_RESET_FAILS, FAILURE_WINDOW_SECONDS),
            (_CHATS, CHAT_WINDOW_SECONDS))


def _store():
    """身份库要从 authz 现取，不能在 import 时绑死。

    中间件读的是 authz.auth_store（测试换库也只换它）。端点若绑住 app.core.auth
    里那个单例，enforced 模式下就会出现"中间件认 A 库、注册端点写 B 库"，
    发出去的令牌连它自己都解不出来——而这恰是路由层最容易悄悄错位的地方。
    """
    return authz.auth_store


def _now() -> float:
    """单调时钟，独立成函数只为让"窗口过期"这一条测得到。"""
    return time.monotonic()


def _recent(ledger, ip: str, window: float, moment: float) -> list:
    recent = [t for t in ledger[ip] if moment - t < window]
    ledger[ip] = recent
    return recent


def _prune(moment: float) -> None:
    """来源数超出上限时丢掉"整桶都已过期"的那些键——四本账都要扫，且各按自己的窗口过。

    触发条件是**内存**上限，不是安全窗口，所以两件事都不能凑：漏掉一本就是只胀不收，
    拿统一的最短窗口去过 24 小时的那两本则会在来源数超过 4096 时把整桶有效记录提前丢掉
    （注册与改密的配额被悄悄放宽，那是一条 fail-open）。窗口就在 _LEDGERS 里跟账本绑在
    一起，判据见 test_prune_uses_each_ledgers_own_window。
    """
    for ledger, window in _LEDGERS:
        if len(ledger) <= MAX_TRACKED_SOURCES:
            continue
        for key in [k for k, v in ledger.items()
                    if not any(moment - t < window for t in v)]:
            ledger.pop(key, None)


def _throttled(ip: str) -> bool:
    """登录与注册共用的那本失败账（猜口令、撞名）。找回流程不看这本，见下。"""
    moment = _now()
    _prune(moment)
    return len(_recent(_FAILS, ip, FAILURE_WINDOW_SECONDS, moment)) >= MAX_FAILURES_PER_WINDOW


def _reset_throttled(ip: str) -> bool:
    """找回流程自己那本失败账：同一个窗口、同一个上限，只是另一本账——
    登录成功、注册成功、改密成功都清不到它（为什么必须分开，见 _RESET_FAILS 上面那段）。
    """
    moment = _now()
    _prune(moment)
    return len(_recent(_RESET_FAILS, ip, FAILURE_WINDOW_SECONDS,
                       moment)) >= MAX_FAILURES_PER_WINDOW


def _note_failure(ip: str) -> None:
    _FAILS[ip].append(_now())


def _note_reset_failure(ip: str) -> None:
    _RESET_FAILS[ip].append(_now())


def _registrations_full(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return (len(_recent(_REGISTERS, ip, REGISTER_WINDOW_SECONDS, moment))
            >= MAX_REGISTRATIONS_PER_SOURCE)


def _note_registration(ip: str) -> None:
    _REGISTERS[ip].append(_now())


def _resets_full(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return (len(_recent(_RESETS, ip, RESET_WINDOW_SECONDS, moment))
            >= MAX_RESETS_PER_SOURCE)


def _note_reset(ip: str) -> None:
    _RESETS[ip].append(_now())


def _client_ip(request: Request) -> str:
    # 只信 Cloudflare 那一个头。X-Forwarded-For 是一条可被追加的链，取首项等于
    # 取攻击者写的第一句假话；cf-connecting-ip 由边缘改写，才是可信来源。
    real = request.headers.get("cf-connecting-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"




def chat_allowed(ip: str, user_id: str) -> bool:
    """只看，不记账。记账是 note_chat()，两件事分开才能在"挡下时不花钱"上测得出来。"""
    moment = _now()
    _prune(moment)
    return (len(_recent(_CHATS, _chat_key(ip, user_id), CHAT_WINDOW_SECONDS, moment))
            < MAX_CHAT_CALLS_PER_WINDOW)


def note_chat(ip: str, user_id: str) -> None:
    """记一次尝试。调用点在**放行之后、叫模型之前**，所以失败的、模型报错的那次
    一样占额度——成功还不还预算是这四本旧账定下来的裁决，第五本沿用。"""
    _CHATS[_chat_key(ip, user_id)].append(_now())


def chat_retry_after(ip: str, user_id: str) -> int:
    key = _chat_key(ip, user_id)
    moment = _now()
    stamps = _recent(_CHATS, key, CHAT_WINDOW_SECONDS, moment)
    if not stamps:
        return CHAT_WINDOW_SECONDS
    return max(1, int(CHAT_WINDOW_SECONDS - (moment - min(stamps))) + 1)


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(status_code=429, detail="尝试次数过多，请稍后再试",
                         headers={"Retry-After": str(retry_after)})


class RegisterRequest(BaseModel):
    username: str
    password: str
    # 三条固定问题的答案，顺序与 auth.RECOVERY_QUESTIONS 对齐。这里是必填：存储层
    # 允许记录没有找回凭据（真实库里就有那之前的老账号），但经 API 新注册的人必须
    # 留下它，否则"忘记密码"这条路对新人永远走不通。少一条就是 422。
    security_answers: List[str]


class LoginRequest(BaseModel):
    username: str
    password: str


class ResetRequest(BaseModel):
    username: str
    # 条数不在这里判，和下面的 new_answers 同一层：存储层那道闸门（auth._check_answer_shapes）
    # 在读库与比对之前就会抛出，落到 HTTP 还是那句指着题数的中文 422、照样不计费（判据见
    # tests/test_auth.py 里那条零次 bcrypt 锁与 tests/test_auth_endpoints.py 里那条 free_typo 锁）。
    answers: List[str]
    new_password: str
    # 给了就连找回答案一起轮换（固定问题不等于固定答案），不给就是原样留着。
    # "不给"本身合法，所以条数只能由存储层判：它同样在读库与比对之前抛出，落到 HTTP
    # 还是那句 422、照样不计费（判据见 test_auth.py 里那条对称的零次 bcrypt 锁）。
    new_answers: Optional[List[str]] = None


@router.post("/v1/auth/register")
def register(req: RegisterRequest, request: Request):
    """开放注册：用户名 + 自设密码，成功即发一枚会话令牌（注册即登录）。

    路由挂在精确路径上——authz.PUBLIC_PATHS 也是精确匹配，带斜杠的变体在
    enforced 下先被中间件挡在凭据之外，不会成为第二个入口。
    """
    ip = _client_ip(request)
    if _throttled(ip):
        # 与登录共用同一份失败预算。少了这一句，"撞名要计费"就是空话：格子照扣、
        # 谁也不收，枚举用户名依然是免费的。
        raise _too_many(FAILURE_WINDOW_SECONDS)
    if _registrations_full(ip):
        raise _too_many(REGISTER_WINDOW_SECONDS)
    try:
        principal, token = _store().register(
            username=req.username, password=req.password,
            security_answers=req.security_answers)
    except AuthError as e:
        raise _register_error(e, ip)
    _note_registration(ip)
    # 成功**不**清失败账（终审 F3）：理由见 _FAILS 上面那段。
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


def _register_error(e: AuthError, ip: str) -> HTTPException:
    """把存储层的失败原因翻译成状态码，并给唯一那条可被滥用的信道计费。"""
    if e.taken:
        # 实话保留，但它现在是免凭据的用户名枚举信道：每问一次扣一格登录预算。
        # 真人改名一次就过了，脚本则要每 10 次换一枚真实访客 IP——**这句从终审 F3 起才算数**：
        # 那时删掉了 register/login 成功路径上的 _FAILS.pop(ip)，它再也拿不到"自己的号
        # 成功一次"来洗账。换 IP 意味着它背后真有一张分布式网络，那时限流本来也挡不住，
        # 只是把成本抬上去。
        _note_failure(ip)
        return HTTPException(status_code=409, detail=e.reason)
    # 用户名、密码、三条找回答案本身不合格：都是当事人自己能改好的，不计费也不该挡别人的路。
    return HTTPException(status_code=422, detail=e.reason)


@router.post("/v1/auth/login")
def login(req: LoginRequest, request: Request):
    """用户名 + 密码换一枚新的会话令牌；旧令牌继续有效（多设备并存）。"""
    ip = _client_ip(request)
    if _throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    try:
        principal, token = _store().login(username=req.username, password=req.password)
    except AuthError as e:
        _note_failure(ip)
        # 401 而不是 403：这里没有"身份是真的但角色不够"这一说，只有"没认出来"。
        raise HTTPException(status_code=401, detail=e.reason)
    # 成功**不**清失败账（终审 F3）：理由见 _FAILS 上面那段。
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


@router.post("/v1/auth/reset")
def reset(req: ResetRequest, request: Request):
    """三题全答对就换密码；该人名下所有会话令牌同时作废（每一台设备都掉线）。

    这里没有"先把问题念给你听"那一步：三题是全站常量，前端自己渲染，服务器不为
    一句抄来的话开一条免凭据信道。答案与新密码**一次提交**——分开验答案就等于给
    外人一个 oracle。

    这个端点只看**两本**账，顺序是先猜错、后猜中：_RESET_FAILS 挡"一直在猜"，
    _RESETS 挡"已经猜中过几次"。第三本 _FAILS（登录与注册共用的失败账）它既不看不写，
    猜错的格子也刻意不记到那本上：猜口令与猜三题是两种不同的猜测面，共用一格预算时前者
    会把后者顶满，而 429 落在哪本账上得能从 Retry-After 里读出来（为什么单独一册，见
    _RESET_FAILS 上面那一段）。
    反过来同样不通融：改密成功只往 _RESETS 记一格，**不清**任何失败账——三题的文本和
    常见答案组合本来就是公开的，猜中一次恰恰说明来路不明的那一面还没排除。
    （这一段以前还写着"因为 _FAILS 会在登录或注册成功时被清空"：终审 F3 把那两处
    `_FAILS.pop(ip)` 删了，现在两本账任何成功都不还。）
    """
    ip = _client_ip(request)
    if _reset_throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    if _resets_full(ip):
        raise _too_many(RESET_WINDOW_SECONDS)
    try:
        _store().reset_password(req.username, req.answers, req.new_password,
                                req.new_answers)
    except AuthError as e:
        if e.charge:
            # 答案错、没留找回答案、查无此人、已停用：同一句、同一格预算、同一个 401。
            # 判的是存储层带上来的显式标记，不是 e.reason 等于哪句文案——文案是会说、
            # 会换的，预算不该挂在它上面（锁：
            # test_the_reset_billing_follows_the_flag_not_the_wording）。
            _note_reset_failure(ip)
            raise HTTPException(status_code=401, detail=e.reason)
        # 新密码或轮换答案列表本身不合格（太短、太长、条数不对）：那是当事人自己能
        # 改好的，不计费
        raise HTTPException(status_code=422, detail=e.reason)
    _note_reset(ip)
    return {"status": "password_reset"}


@router.get("/v1/auth/me")
def me(principal: Principal = CurrentPrincipal):
    """前端用它确认"我到底是谁"——凭据被解析成谁，只有这里说得准。"""
    return {"user_id": principal.user_id, "username": principal.username,
            "role": principal.role}


# ---------- 管理端 ----------
# role 不在任何请求体里：管理员只来自 ACCESS_TOKEN bootstrap，或来自运维手改
# users.json。给 API 开一个写 role 的口子，等于把整套身份体系作废。

def _public_user(record: dict) -> dict:
    """用户记录的对外视图。逐字段列出，是为了让 pw_hash 与 tokens 无处可藏。"""
    return {
        "user_id": record.get("user_id"),
        "username": record.get("username"),
        "role": record.get("role"),
        "disabled": record.get("disabled"),
        "created_at": record.get("created_at"),
        # last_seen 粗粒度是设计使然：存储层为不把鉴权变成热路径写盘，把落盘
        # 节流到一小时以上，所以它读作"上次看见它至少是一小时前"，不是在线状态。
        "last_seen": record.get("last_used_at"),
        # 有几台设备在线是运维要知道的，但只报数量：令牌摘要本身是凭据。
        "sessions": len(record.get("tokens") or []),
    }


@router.post("/v1/auth/logout")
def logout(request: Request, principal: Principal = CurrentPrincipal):
    """退出这台机器：只作废**调用方这一枚**令牌。

    刻意不清这个人的整张令牌表——那是管理员 rotate 的语义（怀疑口令泄露）。
    它还要能替"本机清单里的另一个人"退出：前端直接带上他那一枚来调就行，不必
    先把他切成当前身份、把他的会话加载到屏幕上（共用设备上没这个必要）。

    取凭据复用 authz._credential：它已经管好了方案名大小写与 x-access-token
    兜底，这里再写一份 split 就是第二个事实来源——两边一漂移就会出现
    "中间件认得这枚、退出说不认识"，而表现是退出没反应。
    """
    _store().revoke(authz._credential(request))
    return {"status": "logged_out"}


def _blank_totals() -> dict:
    return {k: 0 for k in ("calls", "ok", "failed", "prompt_tokens", "completion_tokens",
                           "total_tokens", "reasoning_tokens", "cached_tokens", "unknown_usage")}


def _today() -> str:
    from datetime import datetime
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@router.get("/v1/admin/usage")
def admin_usage(day: str = None, _: Principal = RequireAdmin):
    """账本的读取面：谁、几次、多少 token、谁的钱。

    `day` 只收 `YYYY-MM-DD`：这个值会直接当键去查字典，宽松一点就是"随便传什么
    都能拿到一份 200 空表"，那既不报错也看不出自己问错了。
    """
    if day is not None and not _DAY_RE.match(day.strip()):
        raise HTTPException(status_code=400, detail="day 要写成 YYYY-MM-DD")
    target = day.strip() if day else None
    rows = usage.snapshot(target)
    totals = {"operator": _blank_totals(), "user": _blank_totals()}
    for row in rows:
        bucket = totals.setdefault(row.get("paid_by") or "operator", _blank_totals())
        for field in ("calls", "ok", "failed", "prompt_tokens", "completion_tokens",
                      "total_tokens", "reasoning_tokens", "cached_tokens", "unknown_usage"):
            bucket[field] += row.get(field, 0)
    return {"day": target or _today(), "rows": rows, "totals": totals,
            "days": usage.days()}


@router.get("/v1/admin/users")
def list_users(_: Principal = RequireAdmin):
    return {"users": [_public_user(u) for u in _store().list_users()]}


@router.post("/v1/admin/users/{user_id}/disable")
def disable_user(user_id: str, _: Principal = RequireAdmin):
    if not _store().disable_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "disabled", "user_id": user_id}


@router.post("/v1/admin/users/{user_id}/enable")
def enable_user(user_id: str, _: Principal = RequireAdmin):
    # 轮换令牌不再顺手解除停用，因此撤销必须有对称的还原动作。
    if not _store().enable_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "enabled", "user_id": user_id}


@router.post("/v1/admin/users/{user_id}/rotate-token")
def rotate_user_token(user_id: str, _: Principal = RequireAdmin):
    """强制全端重登：旧令牌全部作废，且停用状态原样保留。"""
    try:
        return {"token": _store().rotate_token(user_id)}
    except AuthError:
        raise HTTPException(status_code=404, detail="用户不存在")


@router.delete("/v1/admin/users/{user_id}")
def remove_user(user_id: str, actor: Principal = RequireAdmin):
    if user_id == actor.user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if not _store().delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "deleted", "user_id": user_id}
