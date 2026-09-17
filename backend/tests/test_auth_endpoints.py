"""注册、登录与管理端点的 HTTP 契约。conftest 已把请求当本机管理员。

需要"真实身份"的断言（401/403、me 解析到谁）一律走 enforced fixture：
全局 client 是 AUTH_MODE=disabled，在那里人人都是本机管理员，凭据根本不被解析。

2026-09-17 邀请码退役、注册改为开放之后，这个文件有三处语义换了位置，不是删掉就算：
1. "撞名"从前被邀请码挡在外面（没码的人只会听到"邀请码无效"），现在它是免凭据的
   用户名枚举信道。实话保留（改名是用户自己能解决的事），但每问一次要扣一格预算。
2. 来源键从 request.client.host（经隧道后恒为 127.0.0.1，全网共用一桶）换成
   Cloudflare 写的 CF-Connecting-IP。这条既是功能也是修 bug，见下面两条锁。
3. 多了一个公开端点 /v1/auth/login，它必须对"查无此人 / 密码错 / 已停用"给出
   逐字节相同的回答，且失败按真实来源计费。

2026-09-18 密保换成全站固定三题之后，/v1/auth/reset 也是免凭据端点了，而它成功一次
的代价比登录更大（名下每台设备都掉线）。于是多出一本**成功**账：同一来源一天最多
重置 3 次。同一天的修复轮又把猜找回答案的**失败**拆成第四本账（_RESET_FAILS）：原本
三本账共用 _FAILS 一个键，而 register 与 login 成功时都 `_FAILS.pop(ip)`——同一 IP 猜错
四次、登进自己的号（或再注册一个小号）账就清零，无限续命。找回这本现在谁的成功都不清，
"猜错 9 次 + 猜对 1 次"不再是每 10 分钟一次的免死金牌。下面那几条改密预算的锁就是钉
这两本的。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.core.auth import RESET_FAIL, auth_store
from app.main import app

client = TestClient(app)

# enforced fixture 把 bootstrap 口令设成这个值，管理端点在它之下才有意义
BOOT = {"Authorization": "Bearer boot-token"}

PW = "correct-horse-battery"
PW2 = "another-correct-horse"
# 每条用例默认用同一个 TEST-NET 地址当来源；需要"两个互不相干的访客"时各自换一个。
HOME = "192.0.2.10"

# 限流账本的清账 autouse fixture 在 conftest.py（整个套件共用，别再复制一份）。


def _budget_used() -> int:
    """账本里已记了多少次失败。_throttled() 读的时候会往 defaultdict 塞一个空
    列表，所以"有没有键"说明不了问题，只能数时间戳。"""
    from app.core.auth_router import _FAILS

    return sum(len(v) for v in _FAILS.values())


def _guessing_budget_used() -> int:
    """找回流程那本**独立**的失败账记了几次猜错（修复轮 1 / F2）。同一个理由：
    只数时间戳。两条判据都得摆出来，因为"猜口令"与"猜找回答案"现在分家了——
    只数 _FAILS 的那半看不见找回这本，反之亦然。"""
    from app.core.auth_router import _RESET_FAILS

    return sum(len(v) for v in _RESET_FAILS.values())


from tests.conftest import RECOVERY_FIELDS as RECOVERY

def _reg(username, password=PW, ip=HOME, **extra):
    return client.post("/v1/auth/register",
                       json={"username": username, "password": password, **RECOVERY, **extra},
                       headers={"CF-Connecting-IP": ip})


def _login(username, password=PW, ip=HOME):
    return client.post("/v1/auth/login",
                       json={"username": username, "password": password},
                       headers={"CF-Connecting-IP": ip})


# 三条都错的答案：与 RECOVERY 那三条对得上题数，但一枚都对不上。
WRONG = ["肯定不对", "肯定也不对", "第三个也不对"]


def _reset(username, answers=None, new_password=PW2, ip=HOME):
    """自助改密那一步：默认是"三题全对 + 换个新密码"，即正当用户的样子。

    要演"猜"的用例必须显式传 WRONG——默认值设成正确答案，才不会出现"某条用例本来
    就在猜、作者以为它在走正当路径"那种读不出来的断言。
    """
    return client.post("/v1/auth/reset",
                       json={"username": username,
                             "answers": RECOVERY["security_answers"]
                                        if answers is None else answers,
                             "new_password": new_password},
                       headers={"CF-Connecting-IP": ip})


def _register(username="张三", ip=HOME):
    res = _reg(username, ip=ip)
    assert res.status_code == 200, res.text
    return res.json()


def _uid_of(c, username):
    """在 enforced 库里按用户名找到 user_id（该库里注册的用户只能这样拿到 id）。"""
    users = c.get("/v1/admin/users", headers=BOOT).json()["users"]
    return next(u["user_id"] for u in users if u["username"] == username)


def _outcome(res):
    return (res.status_code, res.json()["detail"])


# ---------- 注册 ----------


def test_register_returns_token_once():
    body = _register("李四")
    assert body["token"] and body["username"] == "李四" and body["role"] == "user"
    assert body["user_id"].startswith("u_")


def test_register_never_echoes_the_password_back():
    """全局约束：响应与日志里不得出现凭据明文。注册请求体里带着密码，
    响应只要顺手回显任何一个字段，密码就进了访问日志与浏览器历史。"""
    res = _reg("不回显", password="a-very-secret-password")
    assert res.status_code == 200, res.text
    assert "a-very-secret-password" not in res.text


def test_duplicate_username_is_409_and_is_metered():
    """开放注册之后，"这名字被占了"是免凭据的枚举信道，所以它必须计费。

    实话保留：改名是用户自己能解决的事，让他以为系统坏了只会去缠管理员。但每问
    一次扣一格——真人改一次就过了，脚本则要每 10 次换一枚真实访客 IP。
    """
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    _register("独占者", ip="192.0.2.41")
    assert _budget_used() == 0, "前提：成功注册不留账"
    for i in range(MAX_FAILURES_PER_WINDOW):
        res = _reg("独占者")
        assert res.status_code == 409, f"第 {i} 次撞名应当仍是 409"
    assert _budget_used() == MAX_FAILURES_PER_WINDOW, "撞名必须计费，否则枚举用户名免费"
    assert _reg("全新的人").status_code == 429, "预算用尽后连正当注册也该被挡住"


def test_a_malformed_username_is_not_charged():
    """格式错（超长、保留字、空）是当事人自己能改好的手滑，一格都不该记。

    真实部署里一个 NAT 后面是一群人（校园网、移动网络），把打字错误算进预算，
    后果就是一个人连错三次用户名把整栋楼锁在注册页外，而收益为零：格式错的
    请求本来就建不出账号。
    """
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    for i in range(MAX_FAILURES_PER_WINDOW + 2):
        for bad in ("x" * 25, "admin", ""):
            res = _reg(bad)
            assert res.status_code == 422, f"第 {i} 轮 {bad!r} -> {res.status_code}"
            assert "占用" not in res.json()["detail"]
    assert _budget_used() == 0, "用户名不合格不是攻击，不该进限流账本"
    assert _reg("手滑之后的人").status_code == 200, "预算完好：正当注册不该被手滑挡住"


def test_a_weak_password_is_rejected_sayably():
    res = _reg("短密码", password="123")
    assert res.status_code == 422, res.text
    assert "密码" in res.json()["detail"], "把密码太短说成用户名问题，用户会去改名字"


def test_missing_fields_are_422():
    for body in ({"username": "缺密码"}, {"password": PW}, {}):
        res = client.post("/v1/auth/register", json=body)
        assert res.status_code == 422, body


def test_registration_budget_is_per_real_source_ip():
    """修掉"全网共用一个桶"的那条锁。

    以前来源键取 request.client.host，经 cloudflared 之后恒为 127.0.0.1：一个人
    手滑就能把所有人挡在门外。现在认 CF-Connecting-IP，两个互不相干的访客必须有
    各自独立的预算。
    """
    from app.core.auth_router import MAX_REGISTRATIONS_PER_SOURCE as CAP

    for i in range(CAP):
        assert _reg(f"甲屋{i}", ip="192.0.2.1").status_code == 200
    full = _reg("甲屋超额", ip="192.0.2.1")
    assert full.status_code == 429, "同一来源注册满就该限"
    assert full.headers.get("retry-after")
    assert _reg("乙屋的人", ip="192.0.2.2").status_code == 200, \
        "甲屋注册满了不该把乙屋一起锁掉——那正是旧的全局桶"


def test_x_forwarded_for_cannot_buy_a_fresh_budget():
    """只信 CF-Connecting-IP。XFF 是一条可被追加的链，取首项等于取攻击者写的假话。"""
    from app.core.auth_router import MAX_REGISTRATIONS_PER_SOURCE as CAP

    # 不带 CF 头时来源退回对端地址（TestClient 里是一个固定值），先把那个桶灌满
    for i in range(CAP):
        res = client.post("/v1/auth/register", json={"username": f"丙屋{i}", "password": PW, **RECOVERY})
        assert res.status_code == 200, res.text
    assert client.post("/v1/auth/register",
                       json={"username": "丙屋超额", "password": PW, **RECOVERY}).status_code == 429
    spoofed = client.post("/v1/auth/register",
                          json={"username": "伪造来源", "password": PW, **RECOVERY},
                          headers={"X-Forwarded-For": "198.51.100.77"})
    assert spoofed.status_code == 429, "伪造 XFF 换不来一个新桶"


def test_a_success_resets_the_failure_budget():
    """一次成功说明这来源确实是正当用户，别让他之前的手滑继续记账。"""
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    _register("独占预算", ip="192.0.2.42")
    for i in range(MAX_FAILURES_PER_WINDOW - 1):
        assert _reg("独占预算").status_code == 409
    assert _budget_used() == MAX_FAILURES_PER_WINDOW - 1, "前提：撞名确实被记了下来"
    assert _reg("重置者").status_code == 200
    assert _budget_used() == 0, "成功后这来源的失败计数必须清零"


def test_the_throttle_forgets_once_the_window_passes(monkeypatch):
    """限流必须自己松开：永不过期的计数器本身就是"让所有人注册不了"的 DoS 靶子。"""
    from app.core import auth_router
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    moment = [0.0]
    monkeypatch.setattr(auth_router, "_now", lambda: moment[0])
    _register("被撞的名字", ip="192.0.2.43")

    for i in range(MAX_FAILURES_PER_WINDOW):
        assert _reg("被撞的名字").status_code == 409, f"第 {i} 次应当仍是 409"
    assert _reg("窗口内的新人").status_code == 429

    moment[0] = float(auth_router.FAILURE_WINDOW_SECONDS) + 1
    assert _reg("窗口后的新人").status_code == 200, "窗口过了必须自己松开"


# 原先这里还有一条 test_trailing_slash_is_not_a_credential_free_door：它断的是
# authz 中间件"精确匹配 PUBLIC_PATHS"，把 auth_router 整个摘掉它照样绿，测的从来
# 不是注册端点。已挪到 tests/test_route_auth_contract.py，与公开面钉在一起；
# 留在下面的这条才是注册端点自己的事（斜杠变体烧的是同一份限流预算）。


def test_trailing_slash_cannot_dodge_the_throttle():
    """带斜杠不是第二条免计数的入口：它烧的是同一个来源的同一份预算。"""
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW, _FAILS

    _register("被斜杠撞的人", ip="192.0.2.44")
    # 斜杠会经 307 归一化到真实端点，TestClient 跟随重定向，失败照常计数
    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register/",
                          json={"username": "被斜杠撞的人", "password": PW, **RECOVERY},
                          headers={"CF-Connecting-IP": HOME})
        assert res.status_code == 409, f"第 {i} 次斜杠尝试 -> {res.status_code}"
    assert _reg("斜杠后来者").status_code == 429, "斜杠攒下的失败必须作用到无斜杠路径上"

    # 反向同理：无斜杠用光预算后，斜杠变体也别想拿到一次没被限流的注册。
    # 这一句必须在测试中间清账，autouse fixture 只保证进出用例时是空的。
    _FAILS.clear()
    for i in range(MAX_FAILURES_PER_WINDOW):
        assert _reg("被斜杠撞的人").status_code == 409
    slashed = client.post("/v1/auth/register/",
                          json={"username": "斜杠正当用户", "password": PW, **RECOVERY},
                          headers={"CF-Connecting-IP": HOME})
    assert slashed.status_code == 429, "斜杠变体必须与真实端点共用同一个限流窗口"


# ---------- 登录 ----------


def test_login_issues_a_working_token(client, enforced):
    """必须在 enforced 下验：disabled 模式中间件不解析凭据，me 只会回本机管理员。"""
    res = client.post("/v1/auth/register",
                      json={"username": "登录的人", "password": PW, **RECOVERY},
                      headers={"CF-Connecting-IP": "192.0.2.50"})
    assert res.status_code == 200, res.text
    login = client.post("/v1/auth/login", json={"username": "登录的人", "password": PW, **RECOVERY},
                        headers={"CF-Connecting-IP": "192.0.2.50"})
    assert login.status_code == 200, login.text
    hdrs = {"Authorization": "Bearer " + login.json()["token"]}
    assert client.get("/v1/auth/me", headers=hdrs).json()["username"] == "登录的人"


def test_login_failure_is_the_same_sentence_for_every_cause():
    """查无此人 / 密码错 / 已停用，三句必须逐字节相同，整个响应体也一样。

    注册全开放之后用户名本身就是可猜的公开信息；登录端点若在这三种情况上换了
    措辞，它就是一份"谁在这里有账号"的名单，而"这个号被停了"还额外告诉别人该找
    谁求情。整份 body 进断言：多回一个字段就是探测器。
    """
    created = _register("在册登录者")
    auth_store.disable_user(created["user_id"])
    outcomes = [_outcome(_login("从没注册过", PW)),
                _outcome(_login("在册登录者", PW2)),
                _outcome(_login("在册登录者", PW))]
    assert len(set(outcomes)) == 1, f"三种失败说出了不同的话：{outcomes}"
    assert outcomes[0][0] == 401


def test_login_failures_are_charged_per_source_and_not_globally():
    from app.core.auth_router import FAILURE_WINDOW_SECONDS, MAX_FAILURES_PER_WINDOW

    _register("被猜的人")
    for i in range(MAX_FAILURES_PER_WINDOW):
        assert _login("被猜的人", "肯定不对的密码", ip="192.0.2.9").status_code == 401
    locked = _login("被猜的人", PW, ip="192.0.2.9")
    assert locked.status_code == 429, "猜密码必须有代价"
    assert locked.headers.get("retry-after") == str(FAILURE_WINDOW_SECONDS)
    assert _login("被猜的人", PW, ip="192.0.2.8").status_code == 200, \
        "锁的是这个来源，不是这个账号——否则停用别人账号只要拿他的用户名撞十次"


def test_a_correct_login_resets_the_failure_budget():
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    _register("手滑的人")
    for i in range(MAX_FAILURES_PER_WINDOW - 1):
        assert _login("手滑的人", "不对", ip="192.0.2.11").status_code == 401
    assert _login("手滑的人", PW, ip="192.0.2.11").status_code == 200
    assert _budget_used() == 0, "登进来就说明这来源是本人，别让他之前的手滑继续记账"


# ---------- 自助改密 ----------


def test_reset_through_http_replaces_the_password_and_kills_the_sessions():
    """改密请求体刚换过形状（一条答案 → 三条答案 + 可选轮换），而全套件里只有这条
    真的从 /v1/auth/reset 拿到 200。少了它，参数顺序写反（把新密码当答案传）也照样
    全绿——那正是这条自救路径唯一没人看着的一段。
    """
    created = _register("改密的人")
    assert auth_store.resolve(created["token"]) is not None, "前提：注册即登录"

    res = client.post("/v1/auth/reset",
                      json={"username": "改密的人", "answers": RECOVERY["security_answers"],
                            "new_password": PW2},
                      headers={"CF-Connecting-IP": HOME})
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "password_reset"}
    # 这里不能用 /v1/auth/me 判令牌：本文件的 client 是 disabled 模式，中间件根本不
    # 看凭据，谁都 200。撤销是否生效只能直接问存储层。
    assert auth_store.resolve(created["token"]) is None, "改密必须作废名下所有令牌"
    assert _login("改密的人", PW).status_code == 401, "旧密码要立刻登不进"
    assert _login("改密的人", PW2).status_code == 200, "新密码要能登进来"


def test_three_successful_resets_exhaust_the_source_budget():
    """**新增锁（本轮核心）**：改密成功本身要计费，一天最多三次。

    旧实现里成功路径是 _FAILS.pop(ip)：于是"猜错 9 次、猜对 1 次"就把账清零，每 10
    分钟再来 10 次，无限续命。现在成功记进另一本账，并且第 4 次成功也不放行——连着
    答对三题三次的人不是"忘了答案的本人"，是一个已经能稳定猜中别人答案的人，而他每
    成功一次就让名下每台设备掉线。
    """
    from app.core.auth_router import MAX_RESETS_PER_SOURCE, RESET_WINDOW_SECONDS

    _register("被改密的人", ip="192.0.2.60")
    for i in range(MAX_RESETS_PER_SOURCE):
        res = _reset("被改密的人", new_password=f"new-password-{i}", ip="192.0.2.60")
        assert res.status_code == 200, f"第 {i} 次全对的重置应当放行：{res.text}"
    locked = _reset("被改密的人", new_password="new-password-3", ip="192.0.2.60")
    assert locked.status_code == 429, "答案全对也要挡：成功不是免费的"
    assert locked.headers.get("retry-after") == str(RESET_WINDOW_SECONDS), \
        "retry-after 必须出自那一天窗口，不是十分钟的失败窗口——两本账得分得开"
    # 邻居连坐是老全局桶的病，新这本账一样不能有
    _register("隔壁的改密者", ip="192.0.2.61")
    assert _reset("隔壁的改密者", ip="192.0.2.61").status_code == 200, \
        "192.0.2.60 用光三次不该把 192.0.2.61 一起锁在自救路径外"


def test_the_reset_budget_is_keyed_on_the_same_trusted_source():
    """第三本账的来源键与前两本同一个：只认 CF-Connecting-IP，XFF 一律不信。

    注册侧那条 XFF 锁看不见这本新账，而"给新账本换一个来源键"（按用户名记、或者
    顺手信起 XFF 的链首项来）恰好是能把三次配额变成无限次的那一改法。这里全程不带
    CF 头，让来源退回 uvicorn 看到的对端地址，再拿伪造的 XFF 去要第四次。
    """
    from app.core.auth_router import MAX_RESETS_PER_SOURCE

    def reset_without_cf(**extra):
        return client.post("/v1/auth/reset",
                           json={"username": "换头的人",
                                 "answers": RECOVERY["security_answers"],
                                 "new_password": PW2},
                           **extra)

    res = client.post("/v1/auth/register",
                      json={"username": "换头的人", "password": PW, **RECOVERY})
    assert res.status_code == 200, res.text
    for i in range(MAX_RESETS_PER_SOURCE):
        assert reset_without_cf().status_code == 200, f"第 {i} 次在配额内"
    assert reset_without_cf(headers={"X-Forwarded-For": "198.51.100.77"}).status_code == 429, \
        "伪造 X-Forwarded-For 换不来第四格改密配额"


def test_a_successful_reset_leaves_the_guessing_ledger_alone():
    """**重述后的锁（修复轮 1 / F2）**：改密成功不许顺手抹掉这个来源已花掉的猜错格。

    五格猜错 → 猜中一次 → 再猜五格：找回那本独立账应当攒到十条，于是下一格是 429。
    旧行为里猜中那一下把账清成零，第二次猜错五格照样 401——攻击者只要每 10 次里蒙中
    一次就永远不会被限。判据直接数 _RESET_FAILS 的条数而不是只数状态码：状态码在
    "清账 vs 不清账"这两支上要到第 11 次才分得开，账上那一格当场就分得开。
    另断 _FAILS 全程为零：猜找回答案记的是找回那本账，不是登录那本（两本分家）。
    """
    from app.core.auth_router import FAILURE_WINDOW_SECONDS, MAX_FAILURES_PER_WINDOW

    _register("被猜答案的人", ip="192.0.2.62")
    for i in range(5):
        assert _reset("被猜答案的人", WRONG, ip="192.0.2.62").status_code == 401, \
            f"第 {i} 次猜错应当还在预算内被答 401"
    assert _reset("被猜答案的人", ip="192.0.2.62").status_code == 200
    assert _guessing_budget_used() == 5, "猜中一次就把找回那本账清零 = 奖励猜中者"
    assert _budget_used() == 0, "猜答案不该烧掉登录预算：那是另一个端点的账"
    for i in range(5):
        assert _reset("被猜答案的人", WRONG, ip="192.0.2.62").status_code == 401, \
            f"第 {i} 次仍在 10 格之内"
    assert _guessing_budget_used() == MAX_FAILURES_PER_WINDOW, \
        "两段失败必须累计，中间那次成功不算清账"
    locked = _reset("被猜答案的人", WRONG, ip="192.0.2.62")
    assert locked.status_code == 429, "旧行为在这里回 401：那人还能再猜十次"
    assert locked.headers.get("retry-after") == str(FAILURE_WINDOW_SECONDS), \
        "挡下他的是那本十分钟的猜错账，不是 24 小时的改密成功账——他今天只用了一次成功"


@pytest.mark.parametrize("success_path", ("login", "register"))
def test_no_other_success_pays_off_the_guessing_ledger(success_path):
    """**新增锁（F2 主体）**：猜找回答案的失败预算独立成册，别的端点成功洗不掉它。

    上一轮三本账（失败 / 注册成功 / 改密成功）里只有那本失败账是安全预算，而它只按来源
    IP 一个键、被三个端点共用：register 与 login 成功时都执行 `_FAILS.pop(ip, None)`。
    评审实测过那条洗白路径——同一 IP 猜错 4 次（budget=4）→ 用**自己的号**登录成功一次
    → budget=0 → 继续 401 无限续命；再注册第二个号同样清零。本轮把 reset 变成免凭据
    端点之后这条第一次变得致命：猜中一次口令、或随手建一个新号，都是伸手就有的"成功"。
    判据照旧数账，并且两支都跑到 429：只断状态码的话，差一位置会藏住清账。
    """
    from app.core.auth_router import FAILURE_WINDOW_SECONDS, MAX_FAILURES_PER_WINDOW

    ip = "192.0.2.71" if success_path == "login" else "192.0.2.72"
    # 参数化两支与上面那条登录用例共用同一份 users.json，名字必须各自唯一
    victim = f"被猜的人{success_path}"
    _register(victim, ip=ip)
    for i in range(4):
        assert _reset(victim, WRONG, ip=ip).status_code == 401, f"第 {i} 次猜错"
    assert _guessing_budget_used() == 4, "前提：猜错确实进了找回那本账"

    if success_path == "login":
        assert _login(victim, PW, ip=ip).status_code == 200
    else:
        assert _reg("换个号的人", ip=ip).status_code == 200
    assert _guessing_budget_used() == 4, \
        f"{success_path} 成功替找回清了账：预算又还给猜中者了"
    assert _budget_used() == 0, "登录/注册那本账按既有契约被清干净了——但找回这本不能跟着清"

    for i in range(MAX_FAILURES_PER_WINDOW - 4):
        assert _reset(victim, WRONG, ip=ip).status_code == 401, \
            f"累到第 {i} 次仍应在预算内"
    assert _guessing_budget_used() == MAX_FAILURES_PER_WINDOW, "两段猜错必须累计"
    locked = _reset(victim, WRONG, ip=ip)
    assert locked.status_code == 429, "旧行为在这里回 401：那人每成功一次就把预算回满"
    assert locked.headers.get("retry-after") == str(FAILURE_WINDOW_SECONDS)


def test_a_partially_correct_answer_set_says_what_a_wrong_one_says():
    """猜对第 1 题与三题全错，对外一个字都不许差（R3 的 HTTP 侧）。

    存储层已经保证同一句 RESET_FAIL，这里钉的是 HTTP 这一层别再加东西：多回一个字段、
    换个状态码、或把"第几题不对"写进文案，这个端点就成了一份逐题 oracle，三题被拆成
    三份互相独立的预算。
    """
    _register("逐题被猜的人", ip="192.0.2.65")
    partial = _reset("逐题被猜的人", [RECOVERY["security_answers"][0]] + WRONG[1:],
                     ip="192.0.2.65")
    all_wrong = _reset("逐题被猜的人", WRONG, ip="192.0.2.65")
    assert (partial.status_code, all_wrong.status_code) == (401, 401)
    assert partial.headers.get("content-type") == all_wrong.headers.get("content-type")
    assert partial.text == all_wrong.text, \
        f"两种失败说得不一样：{partial.text!r} / {all_wrong.text!r}"
    assert partial.json()["detail"] == RESET_FAIL, "同形不等于说错了话：还得是那句"


def test_a_missing_answer_at_register_is_a_free_typo():
    """三格只填两格是手滑：422，而且一格预算都不记。

    这条在实现上已经成立，写在这里是要有人看着它——真实部署里一个 NAT 后面是一群人，
    把手滑算进预算，代价就是一个人填漏一格把整栋楼锁在注册页外，而收益为零。
    """
    res = _reg("少填一格", security_answers=RECOVERY["security_answers"][:2],
               ip="192.0.2.63")
    assert res.status_code == 422, res.text
    assert _budget_used() == 0, "填漏一格不是攻击"
    assert _reg("少填一格", ip="192.0.2.63").status_code == 200, \
        "补上第三格就该注册成功：既没被计费，也没留下半成品占住名字"


@pytest.mark.parametrize("answers", [WRONG[:2], WRONG + ["第四条"]])
def test_a_wrong_answer_count_is_a_free_typo(answers):
    """**降级后的锁（修复轮 1 / F1）**：数错格子仍是一句人话的 422，而且一格都不记。

    上一轮在这里加过一层 pydantic 的 `Field(min_length=3, max_length=3)`，评审裁定撤掉：
    撤掉之后状态码、bcrypt 次数、计费、库里留没留痕迹四个维度实测全部等价，唯一区别
    是错误形状，而那个区别是成本——detail 变成结构化列表、会把提交的答案原样回显给
    调用方，且前端 `api.js` 是 `new Error(detail)`，数组到浏览器里就是 [object Object]；
    判据还会绑在 pydantic 的错误布局（`detail[0]["loc"]`）上，升级即碎。
    所以"哪一层拦下来的"不再是被钉的东西，"拦下来时说的是不是人话"才是：必须是存储层
    那句指着题数的中文，不许与"答案不正确"混成一句。原判据一条没丢：422、不计费、
    answer_hashes 逐字节未变、旧口令仍可用、新口令没被装上。
    """
    # 参数化两支共用同一份 users.json，名字必须各不相同，否则第二支是撞名不是测条数
    name = f"条数的人{len(answers)}"
    created = _register(name, ip="192.0.2.66")

    def digests():
        # 必须是 list(...) 拷贝：dict(u) 是浅拷贝，抄一份才比得出"摘要被就地改过"
        row = next(u for u in auth_store.list_users()
                   if u["user_id"] == created["user_id"])
        return list(row["answer_hashes"])

    before = digests()
    res = _reset(name, answers, ip="192.0.2.66")
    assert res.status_code == 422, f"{len(answers)} 条答案 -> {res.status_code} {res.text}"
    detail = res.json()["detail"]
    assert isinstance(detail, str), \
        f"422 吐回了结构化列表（前端 new Error(detail) 会显示 [object Object]）：{detail!r}"
    assert "3 题" in detail and "答案" in detail, \
        f"{len(answers)} 条答案说的是别的话：{detail!r}"
    assert detail != RESET_FAIL, "数错格子被说成了『答案不正确』：手滑和猜混成了一句"
    assert _budget_used() == 0, "自己填错条数不计费"
    assert _guessing_budget_used() == 0, \
        "F2 之后'不计费'要两本账都断：数错格子若被记进找回那本，只数 _FAILS 就漏了"
    assert digests() == before, "answer_hashes 不许被动过"
    assert _login(name, PW, ip="192.0.2.66").status_code == 200, "旧口令必须还在用"
    # 下面这一格是这条用例自己猜的一次口令，本来就该计费——所以它必须在"不计费"
    # 那条断言之后才发生，别把自己探测出来的账算到闸门头上。
    assert _login(name, PW2, ip="192.0.2.66").status_code == 401


def test_the_prune_sweeps_all_four_ledgers(monkeypatch):
    """内存压力清账必须覆盖四本账：没被扫到的那本就是一本只胀不收的表。

    触发条件（来源数超过 4096）今天这台机器碰不到，所以这条挡的不是现行故障，而是
    "以后加第五本账时忘了把它加进那个元组"——那种遗漏不会让任何对外行为变错（读数
    另有 _recent 按窗口过滤，过期条目挡不住合法请求），只会让表在常年不重启的进程里
    悄悄长出去，而这台机器是开机自启的。反向也要成立：还热着的条目不许被顺手清掉，
    否则这条锁在"无条件清空"那种改法面前就是空的。
    """
    from app.core import auth_router
    from app.core.auth_router import _FAILS, _REGISTERS, _RESET_FAILS, _RESETS

    monkeypatch.setattr(auth_router, "MAX_TRACKED_SOURCES", 0)
    moment = auth_router._now() + auth_router.FAILURE_WINDOW_SECONDS * 2
    # 时间戳取"比最宽那本窗口还老"，这条才只管覆盖度、不管阈值——阈值归下面那条锁
    long_overdue = moment - auth_router.RESET_WINDOW_SECONDS - 1
    ledgers = (_FAILS, _REGISTERS, _RESETS, _RESET_FAILS)
    for ledger in ledgers:
        ledger.clear()
        for i in range(5):
            ledger[f"192.0.2.{i}"] = [long_overdue]   # 整桶在哪本账里都过期了
        ledger["198.51.100.7"] = [moment]             # 还热着
    auth_router._prune(moment)
    for ledger in ledgers:
        assert set(ledger) == {"198.51.100.7"}, \
            f"{ledger} 没被扫到（过期桶还留着），或热桶被一起清了"


def test_prune_uses_each_ledgers_own_window(monkeypatch):
    """**新增锁（修复轮 1 / F3）**：清内存要用每本账自己的窗口，不能一把尺子量四本。

    _prune 由**内存**上限触发，不是安全窗口。上一轮那把统一的最短窗口（十分钟）去量 24
    小时的两本就是 fail-open：来源数一旦真超过 4096，"这个人今天注册过/改过密"的记录会
    被提前丢掉，两本配额当场悄悄放宽。反向同样错：量 _RESET_FAILS 时十分钟是对的，可要是
    哪天有人图省事把阈值改成最宽的那本，猜答案的预算就从十分钟变成二十四小时——把一个
    安全闸门改成了另一个。所以两边都要断：长窗口那两本留住"只在 24 小时内"的那一格，
    短窗口那两本把它扫掉。
    """
    from app.core import auth_router
    from app.core.auth_router import _FAILS, _REGISTERS, _RESET_FAILS, _RESETS

    monkeypatch.setattr(auth_router, "MAX_TRACKED_SOURCES", 0)
    moment = auth_router._now()
    in_long_window_only = moment - auth_router.FAILURE_WINDOW_SECONDS - 1
    overdue_everywhere = moment - auth_router.RESET_WINDOW_SECONDS - 1
    for ledger in (_FAILS, _REGISTERS, _RESET_FAILS, _RESETS):
        ledger.clear()
        ledger["in-long-window"] = [in_long_window_only]
        ledger["overdue-everywhere"] = [overdue_everywhere]

    auth_router._prune(moment)
    for ledger in (_REGISTERS, _RESETS):
        assert set(ledger) == {"in-long-window"}, \
            "24 小时那两本被拿十分钟的窗口去过了：记录被提前清掉 = 配额悄悄放宽"
    for ledger in (_FAILS, _RESET_FAILS):
        assert set(ledger) == set(), \
            f"十分钟那两本没按自己的窗口过期：{ledger}"


# ---------- /v1/auth/me 与令牌真的能用 ----------


def test_me_reports_resolved_identity(client, enforced):
    """必须走 enforced：disabled 下中间件不看凭据，me 只会返回本机管理员。"""
    hdrs = enforced("郑九")
    res = client.get("/v1/auth/me", headers=hdrs)
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "郑九" and body["role"] == "user"
    assert body["user_id"].startswith("u_")
    assert set(body) == {"user_id", "username", "role"}


def test_registered_token_works_on_every_protected_route(client, enforced):
    """注册 → 拿令牌 → 用令牌，全程走 HTTP。

    中间件读 authz.auth_store、端点也得读同一个库，否则发出去的令牌谁都解不出来：
    这条是这层接线唯一的端到端证据。
    """
    res = client.post("/v1/auth/register",
                      json={"username": "自助注册", "password": PW, **RECOVERY},
                      headers={"CF-Connecting-IP": "192.0.2.20"})
    assert res.status_code == 200, res.text
    hdrs = {"Authorization": "Bearer " + res.json()["token"]}
    assert client.get("/v1/auth/me", headers=hdrs).json()["username"] == "自助注册"
    assert client.get("/v1/sessions", headers=hdrs).status_code == 200
    assert client.get("/v1/admin/users", headers=hdrs).status_code == 403


# ---------- 管理端：谁能用 ----------


def test_admin_endpoints_reject_non_admin(client, enforced):
    # 必须走 enforced 模式：全局 conftest 是 disabled，人人都是管理员，403 无从发生
    hdrs = enforced("王五")
    assert client.get("/v1/admin/users", headers=hdrs).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/v1/admin/users"),
    ("post", "/v1/admin/users/u_some/disable"),
    ("post", "/v1/admin/users/u_some/enable"),
    ("post", "/v1/admin/users/u_some/rotate-token"),
    ("delete", "/v1/admin/users/u_some"),
])
def test_no_admin_route_leaks_past_the_role_check(client, enforced, method, path):
    """管理面每一条都要过 require_admin，漏一条等于把整套身份体系作废。"""
    hdrs = enforced("普通用户")
    kwargs = {"headers": hdrs}
    if method == "post":
        kwargs["json"] = {}
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 403, f"{method.upper()} {path} -> {res.status_code}"


# 原先这里还有一条 test_admin_endpoints_are_not_reachable_without_credentials：
# 401 是中间件在路由之前发的，把 auth_router 摘掉它仍然绿，所以它守的是"凭据先于
# 路由"这条中间件性质，不是管理端点。已挪到 tests/test_route_auth_contract.py。
# 邀请码那五条（撤销、大小写归一化、不回显、列表字段、max_uses）随功能一起删除。


# ---------- 管理端：用户 ----------


def test_user_list_never_leaks_credentials():
    created = _register("孙六")
    res = client.get("/v1/admin/users")
    assert res.status_code == 200
    text = res.text
    assert created["token"] not in text, "明文令牌绝不能出现在管理列表里"
    from app.core.auth import hash_token
    assert hash_token(created["token"]) not in text, "换个键名把摘要放出去同样是泄露"
    for word in ("pw_hash", "token_hash", '"tokens"', "bcrypt"):
        assert word not in text, f"用户列表把内部字段 {word} 交了出去"
    row = [u for u in res.json()["users"] if u["user_id"] == created["user_id"]][0]
    assert set(row) == {"user_id", "username", "role", "disabled",
                        "created_at", "last_seen", "sessions"}


def test_the_session_count_is_reported_without_the_tokens():
    """管理员要能看出"这个人有几台设备在线"才谈得上撤销，但摘要本身是凭据。"""
    created = _register("多端的人")
    _login("多端的人")
    _login("多端的人")
    row = [u for u in client.get("/v1/admin/users").json()["users"]
           if u["user_id"] == created["user_id"]][0]
    assert row["sessions"] == 3, "注册 + 两次登录应当有三枚会话令牌"


def test_the_activity_field_is_named_last_seen_because_it_is_coarse():
    """存储层为省热路径写盘把落盘节流到一小时，它不是"最后活动时间"。
    键名叫 last_used_at 会诱导前端把它当在线状态用。"""
    created = _register("观察")
    row = [u for u in client.get("/v1/admin/users").json()["users"]
           if u["user_id"] == created["user_id"]][0]
    assert "last_used_at" not in row and row["last_seen"]


def test_disable_takes_effect_immediately():
    created = _register("周七")
    assert client.post(f"/v1/admin/users/{created['user_id']}/disable").status_code == 200
    # disabled 模式下中间件不看凭据，故这里用真实 resolve 断言撤销已生效
    assert auth_store.resolve(created["token"]) is None


def test_enable_undoes_disable_because_rotate_no_longer_does():
    created = _register("被误停者")
    uid = created["user_id"]
    client.post(f"/v1/admin/users/{uid}/disable")
    assert auth_store.resolve(created["token"]) is None
    res = client.post(f"/v1/admin/users/{uid}/enable")
    assert res.status_code == 200
    assert res.json()["status"] == "enabled"
    assert auth_store.resolve(created["token"]) is not None, "重新启用必须让原令牌复活"
    assert client.post("/v1/admin/users/u_deadbeef/enable").status_code == 404


def test_disable_and_enable_change_real_access(client, enforced):
    """在真正会解析凭据的模式下走一遍停用→401→启用→200。"""
    hdrs = enforced("被停的人")
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 200
    uid = _uid_of(client, "被停的人")
    assert client.post(f"/v1/admin/users/{uid}/disable", headers=BOOT).status_code == 200
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 401, "停用必须立刻生效"
    assert client.post(f"/v1/admin/users/{uid}/enable", headers=BOOT).status_code == 200
    assert client.get("/v1/auth/me", headers=hdrs).status_code == 200


def test_rotate_token_invalidates_every_device():
    created = _register("吴八")
    second = _login("吴八").json()["token"]
    _login("吴八")                       # 第三台设备
    res = client.post(f"/v1/admin/users/{created['user_id']}/rotate-token")
    assert res.status_code == 200
    new = res.json()["token"]
    assert new != created["token"]
    assert auth_store.resolve(created["token"]) is None, "只换一枚等于什么都没撤销"
    assert auth_store.resolve(second) is None, "其它设备也必须一起断线"
    assert auth_store.resolve(new) is not None


def test_rotate_keeps_a_disabled_user_disabled():
    """换令牌是凭证动作，不是重新启用账号——否则它把本任务的存在意义抵消掉。"""
    created = _register("停用的轮换者")
    uid = created["user_id"]
    client.post(f"/v1/admin/users/{uid}/disable")
    fresh = client.post(f"/v1/admin/users/{uid}/rotate-token").json()["token"]
    assert auth_store.resolve(fresh) is None
    row = [u for u in client.get("/v1/admin/users").json()["users"] if u["user_id"] == uid][0]
    assert row["disabled"] is True


def test_unknown_user_is_404_because_it_is_absent_not_because_the_route_is_gone():
    """404 只在"同一条路对真用户回 200"的前提下才有意义。

    原先这里只断四个 404：把 auth_router 整个摘掉，每条路由都改成"路由不存在"的
    404，测试照样全绿——那不是断言，是巧合。所以先用一个真用户把这四条路走活，
    再要求不存在的 id 得到同一个 404（而不是 403/500，那才是要防的泄露）。
    """
    created = _register("四条路都走一遍")
    uid = created["user_id"]
    assert client.post(f"/v1/admin/users/{uid}/disable").status_code == 200
    assert client.post(f"/v1/admin/users/{uid}/enable").status_code == 200
    assert client.post(f"/v1/admin/users/{uid}/rotate-token").status_code == 200
    assert client.delete(f"/v1/admin/users/{uid}").status_code == 200

    for path in ("/v1/admin/users/u_none/disable", "/v1/admin/users/u_none/enable",
                 "/v1/admin/users/u_none/rotate-token"):
        assert client.post(path).status_code == 404, path
    assert client.delete("/v1/admin/users/u_none").status_code == 404


def test_delete_user_removes_it():
    created = _register("待删者")
    uid = created["user_id"]
    assert client.delete(f"/v1/admin/users/{uid}").json()["status"] == "deleted"
    assert auth_store.resolve(created["token"]) is None
    assert uid not in [u["user_id"] for u in client.get("/v1/admin/users").json()["users"]]


def test_admin_cannot_delete_the_identity_it_is_signed_in_as():
    """删掉自己正在用的身份 = 把自己关在门外，这种操作不能靠手滑发生。"""
    res = client.delete("/v1/admin/users/default_user")
    assert res.status_code == 400
    assert "自己" in res.json()["detail"]


def test_no_endpoint_can_grant_the_admin_role():
    """提权必须是 API 之外的动作（只有 ACCESS_TOKEN 是管理员）。
    一旦请求体能写 role，注册端点就成了给任何人发管理员的公开课。"""
    import inspect

    from pydantic import BaseModel

    from app.core import auth_router

    fields = set()
    for _, obj in inspect.getmembers(auth_router, inspect.isclass):
        if issubclass(obj, BaseModel) and getattr(obj, "__module__", "") == auth_router.__name__:
            fields.update(obj.model_fields)
    assert "role" not in fields, "请求体模型里出现 role 字段，就是提权入口"

    res = client.post("/v1/auth/register",
                      json={"username": "想当管理员", "password": PW, "role": "admin", **RECOVERY},
                      headers={"CF-Connecting-IP": "192.0.2.30"})
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "user", "多塞的 role 必须被忽略，不能被采纳"

    for path in ("/v1/admin/users/x/role", "/v1/admin/promote"):
        assert client.post(path, json={"role": "admin"}).status_code == 404, path
