"""2026-09-23 Medium 批次的回归锁（审查报告 #6/#8/#9/#10/#14）。

每条锁都按"改坏哪一处就红哪一条"来写：判据钉的是修复后的行为契约，
不是实现细节。跑之前记得对照——把修复逐条回退，这里应当逐条变红。
"""
import threading
import time

from tests.conftest import peer_client


# ---------- #6 找回的账号维度失败锁定 ----------


def _bad_reset(client, username, tag):
    return client.post("/v1/auth/reset", json={
        "username": username,
        "answers": [f"错答案{tag}甲", f"错答案{tag}乙", f"错答案{tag}丙"],
        "new_password": "Correct-Horse-Battery-9",
    })


def test_reset_locks_the_account_not_just_the_ip():
    """三题是全站固定的低熵常量，换 IP 的人对同一账号可以无限猜。
    按来源的十格预算用完后，换一个来源打**同一个账号**也必须吃 429。
    """
    a = peer_client(("203.0.113.1", 11111))
    for i in range(10):
        res = _bad_reset(a, "被猜的人", f"n{i}")
        assert res.status_code == 401, (i, res.status_code)
    b = peer_client(("203.0.113.2", 22222))          # 换了来源，账号没换
    res = _bad_reset(b, "被猜的人", "x")
    assert res.status_code == 429, "账号维度没锁住：换 IP 就能续猜"


def test_reset_account_lock_is_per_account_and_shows_no_difference_for_ghosts():
    """两件事一起钉：锁不串号（别的账号照旧 401）；查无此人同样攒格，
    于是"这个用户名存不存在"不会从锁的形状里泄露出来。"""
    a = peer_client(("203.0.113.3", 33333))
    for i in range(10):
        assert _bad_reset(a, "锁不死的幽灵", f"g{i}").status_code == 401
    # 幽灵账号自己被锁住——与真实账号同一形状，不给枚举留信道
    assert _bad_reset(peer_client(("203.0.113.4", 44444)),
                      "锁不死的幽灵", "y").status_code == 429
    # 但锁不到邻居：别的账号第一次照常 401，不是 429
    assert _bad_reset(peer_client(("203.0.113.4", 44444)),
                      "隔壁老王", "z").status_code == 401


def test_reset_account_key_normalizes_case_and_length():
    """大小写/首尾空白是同一个账号；超长裸输入不许原样进字典当键。"""
    from app.core.auth_router import _reset_account_key
    assert _reset_account_key("  Bob ") == _reset_account_key("bob")
    assert len(_reset_account_key("x" * 500)) == 64


# ---------- #8 APK 代取的 IP 节流 + 单飞行槽位 ----------


def _apk_env(monkeypatch, plan=None, data=b"APK"):
    from app.core import releases
    if plan is None:
        plan = ({"url": "https://github.com/o/r/releases/download/v0.19/ai-assistant-native-0.19.apk",
                 "name": "ai-assistant-native-0.19.apk", "size": 3, "version": "0.19"}, "")
    monkeypatch.setattr(releases, "download_plan", lambda: plan)
    monkeypatch.setattr(releases, "fetch_asset", lambda url: (data, ""))


def test_apk_fetch_is_throttled_per_source(monkeypatch):
    """一小时 3 次：够真人换机重下，脚本每 3 次得换一枚真实访客 IP。"""
    _apk_env(monkeypatch)
    c = peer_client(("203.0.113.7", 7777))
    for i in range(3):
        assert c.get("/site/android.apk").status_code == 200, i
    res = c.get("/site/android.apk")
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) > 0
    # 节流按来源：另一个来源照旧能下
    other = peer_client(("203.0.113.8", 8888))
    assert other.get("/site/android.apk").status_code == 200


def test_apk_single_flight_falls_back_without_billing_the_budget(monkeypatch):
    """槽位被占：不排队（排队=把攻击者的积压搬进线程池），直接 302 发布页；
    而且这一支没朝 GitHub 跑，不扣来客的格子。"""
    from app.web import web_router
    _apk_env(monkeypatch)
    c = peer_client(("203.0.113.9", 9999))
    assert web_router._APK_SLOTS.acquire(blocking=False)
    try:
        res = c.get("/site/android.apk", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"]
        assert not web_router._APK_DOWNLOADS.get("203.0.113.9"), "没代取却扣了格子"
    finally:
        web_router._APK_SLOTS.release()
    # 释放后恢复正常，且第一次成功只扣一格
    assert c.get("/site/android.apk").status_code == 200
    assert len(web_router._APK_DOWNLOADS["203.0.113.9"]) == 1


def test_failed_no_plan_never_touches_the_slot_or_budget(monkeypatch):
    """拿不到快照（GitHub 挂了）走最便宜的退路：不占槽、不计费。"""
    from app.web import web_router
    _apk_env(monkeypatch, plan=(None, "模拟：没有快照"))
    c = peer_client(("203.0.113.10", 10101))
    for _ in range(5):
        assert c.get("/site/android.apk", follow_redirects=False).status_code == 302
    assert not web_router._APK_DOWNLOADS.get("203.0.113.10")
    assert web_router._APK_SLOTS.acquire(blocking=False), "槽位没被还回来"
    web_router._APK_SLOTS.release()


# ---------- #9 上游客户端按配置复用 ----------


def test_build_client_reuses_one_instance_per_config():
    from app.core import providers
    p = {"id": "cache-test", "api_key": "sk-cache-test-0001",
         "base_url": "https://cache.invalid/v1", "model": "m"}
    c1 = providers.build_client(p)
    assert providers.build_client(dict(p)) is c1, "同一份配置又建了一份：fd 在漏"
    assert providers.build_client(dict(p), timeout=20.0, max_retries=0) is not c1, \
        "timeout 不同的两个用途共用了同一份客户端"
    providers.invalidate_client_cache()
    assert providers.build_client(dict(p)) is not c1


def test_any_config_write_invalidates_the_client_cache():
    """改 label 不改连接形状——不接缓存的话它会原样复用旧客户端；
    这里判的是"写路径必清表"这条纪律本身。"""
    from app.core import providers
    rec = providers.store.upsert({"label": "缓存失效测试", "base_url": "https://x.invalid/v1",
                                  "api_key": "sk-cache-test-0002", "model": "m"})
    try:
        before = providers.build_client(providers.store.get(rec["id"]))
        again = providers.store.get(rec["id"])
        again["label"] = "缓存失效测试改名字"
        providers.store.upsert(again)
        after = providers.build_client(providers.store.get(rec["id"]))
        assert after is not before, "_flush 没接 invalidate_client_cache：改配置不重连"
    finally:
        providers.store.delete(rec["id"])
        providers.invalidate_client_cache()


# ---------- #10 TaskAgent 的提示词清单按可用性过滤 ----------


def test_task_agent_prompt_hides_unavailable_tools(monkeypatch):
    """schema 一条路过滤了，文本提示另一条路没过滤——模型照旧能"看见"并用它硬编答案。"""
    from app.agents import task_agent as ta

    captured = {}

    def fake_llm(model, messages):
        captured["messages"] = messages
        return "完成了。"

    monkeypatch.setattr(ta, "get_llm_response", fake_llm)
    tools = {
        "good_tool": {"description": "现在能用的工具", "available": None},
        "broken_tool": {"description": "依赖断了的工具", "available": lambda: False},
    }
    agent = ta.TaskAgent(name="t", model="m", tools_schema=[], tools=tools, user_id="u")
    assert agent.run("做个任务") == "完成了。"
    prompt = captured["messages"][1]["content"]
    assert "good_tool" in prompt
    assert "broken_tool" not in prompt, "不可用工具还挂在提示词里递给了模型"


# ---------- #14 探测线程的创建竞态 ----------


def test_probe_thread_is_created_exactly_once_under_concurrent_first_calls(monkeypatch):
    """旧写法锁内查活、锁外创建：两个首调同时看到"没活线程"，各起一个、
    后一个覆盖 _thread——被覆盖的那个永远双开而无人报错。
    这里用一枚"start 很慢"的 Thread 替身把竞态窗口钉死成确定性的。
    """
    import types
    from app.tools import availability

    spawned = []

    class _SlowThread:
        def __init__(self, target=None, daemon=None, name=None):
            self._real = threading.Thread(target=target, daemon=daemon, name=name)
            spawned.append(self)

        def start(self):
            time.sleep(0.2)          # 慢启动：旧代码让另一个调用者在这段里完成"查活"
            self._real.start()

        def is_alive(self):
            return self._real.is_alive()

    monkeypatch.setattr(availability, "_refresh", lambda: None)
    monkeypatch.setattr(availability, "_thread", None)
    monkeypatch.setattr(availability, "threading",
                        types.SimpleNamespace(Thread=_SlowThread))

    gate = threading.Barrier(2)

    def go():
        gate.wait()
        availability._ensure_probe_running()

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert len(spawned) == 1, f"并发首调起了 {len(spawned)} 个探测线程：TOCTOU 回来了"
