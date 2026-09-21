"""`/v1/release/latest` 与它背后那条出站请求。

这张卡片只有一个责任："确实有新版时提一句"。所以这里钉的全是它**不该**做的事：
不该每次有人打开 App 就真去一趟 GitHub（缓存）、不该在读不到时说"你已是最新"
（三态）、不该让阻塞网络待在事件循环里（同步 def）、不该让一个能中间人的人
替我们决定用户去下哪个 apk（系统证书库 + 资产名必须对上版本号）。

版本比较在这里是第二份实现（第一份是壳里的 `ReleasePlan.compare`）。两份跨语言的
实现一定漂，所以那张用例表由 Java 的单测与本文件**共用同一份判据**：从 Java 测试源码里
把用例读出来，逐条喂给 Python 的那一份。
"""
import json
import re
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.core import releases
from app.main import app

GOOD = {"tag_name": "v0.18", "html_url": "https://github.com/o/r/releases/tag/v0.18",
        "assets": [{"name": "ai-assistant-0.18.apk", "size": 98304,
                    "browser_download_url": "https://objects.example/ai-assistant-0.18.apk"}]}


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, _limit):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Urlopen:
    """假 urlopen：按脚本依次返回结果，并记下每一次调用。

    脚本用完还被打第二次就直接失败——"多了一次出站请求"正是缓存那条锁要抓的事，
    把它咽下来等于让锁闭着眼通过。
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request, timeout=None, context=None):
        self.calls.append({"request": request, "timeout": timeout, "context": context})
        if not self.script:
            raise AssertionError(f"第 {len(self.calls)} 次出站超出脚本，缓存或重试不对")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)


@pytest.fixture(autouse=True)
def cold_cache():
    releases.reset_for_tests()
    yield
    releases.reset_for_tests()


# ---------- 三态：有 / 没有 / 不知道 ----------

def test_it_says_update_exists_only_when_the_installed_one_is_older(monkeypatch):
    fake = _Urlopen(GOOD, GOOD, GOOD)      # 三次 probe 各一次出站（每次都 reset_for_tests）
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)

    out = releases.probe(have="0.16")
    assert out["ok"] and out["latest"] == "0.18" and out["has_update"] is True
    assert out["size"] == 98304 and out["asset_name"] == "ai-assistant-0.18.apk"

    releases.reset_for_tests()
    same = releases.probe(have="v0.18")          # 带不带 v 是同一个版本
    assert same["has_update"] is False

    releases.reset_for_tests()
    ahead = releases.probe(have="0.19")
    assert ahead["has_update"] is False


def test_not_being_able_to_read_is_not_reported_as_up_to_date(monkeypatch):
    """`ok:false` 与 `has_update:false` 必须分得开：前者是"我不知道"，后者才是"你已是最新"。

    把这两件混成一件的界面会做出最坏的行为——GitHub 抽风的那天，所有人都会被告知
    "你没有更新"，而这句话听起来完全可信。
    """
    fake = _Urlopen(urllib.error.URLError("no route to host"))
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)

    out = releases.probe(have="0.16")
    assert out["ok"] is False and out["has_update"] is None
    assert "URLError" in out["reason"]


def test_a_response_without_a_version_is_a_failure_not_an_empty_one(monkeypatch):
    fake = _Urlopen({"tag_name": "", "assets": []})
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    out = releases.probe(have="0.16")
    assert out["ok"] is False and out["has_update"] is None, "读不到版本号却回了一份'最新是空'"


def test_asking_without_saying_what_is_installed_is_its_own_answer(monkeypatch):
    fake = _Urlopen(GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    out = releases.probe()
    assert out["ok"] and out["latest"] == "0.18"
    assert out["has_update"] is None and "哪版" in out["reason"]


# ---------- 缓存与出站 ----------

def test_twenty_opens_still_mean_one_trip_to_github(monkeypatch):
    """免鉴权 + 会出网 = 必须自己挡住放大。10 分钟一拉，与来多少请求无关。"""
    fake = _Urlopen(GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    for _ in range(20):
        releases.probe(have="0.16")
    assert len(fake.calls) == 1, f"20 次问出了 {len(fake.calls)} 次出站，缓存没生效"


def test_a_failed_refresh_keeps_the_last_good_answer_but_retries_later(monkeypatch):
    """GitHub 挂了这 10 分钟里，不再逐个请求去替它挡枪，而是继续用上一次的结果。"""
    import time

    fake = _Urlopen(GOOD, urllib.error.URLError("boom"), GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)

    assert releases.probe(have="0.16")["latest"] == "0.18"
    releases._fetched_at = time.monotonic() - releases.CACHE_SECONDS   # 假装缓存到期
    stale = releases.probe(have="0.16")
    assert stale["ok"] and stale["latest"] == "0.18", "读不到就清空，等于把用户已有的答案弄丢"
    assert "URLError" in stale["reason"], "用了旧数据就该说清楚这次没读到"
    releases._fetched_at = time.monotonic() - releases.CACHE_SECONDS
    assert releases.probe(have="0.16")["latest"] == "0.18"
    assert len(fake.calls) == 3


def test_a_machine_that_just_booted_still_probes(monkeypatch):
    """刚开机的机器上 monotonic 只有几秒——缓存哨兵不能拿 0.0 当"该拉了"。

    这不是假想出来的边界，是 CI 抓的（runner 是一台刚开的虚拟机，monotonic 远小于
    CACHE_SECONDS）：`now - 0.0 >= 600` 在那台机器上为假，于是"从没拉过"被判成
    "缓存还新"，而 `_payload` 是空的。九条用例当场全红，而线上对应的症状是
    **每次重启后的头 10 分钟里那张卡片永远不弹**——不弹、不报错、没人知道。
    """
    fake = _Urlopen(GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    monkeypatch.setattr(releases.time, "monotonic", lambda: 5.0)        # 开机 5 秒

    out = releases.probe(have="0.16")
    assert len(fake.calls) == 1, "刚起来的机器一次都不去拉：那这 10 分钟里没人能看到卡片"
    assert out["ok"] and out["has_update"] is True, out


def test_a_fresh_process_with_a_small_clock_probes():
    """上一条测的是"reset 之后的模块"，这条测的是**刚 import 进来的模块**那一行初值。

    为什么要开子进程：autouse 的 cold_cache 夹具会把模块状态重写成"刚起来"的样子，
    于是在主进程里把 `_fetched_at = None` 改回 `0.0`，上面那条照样绿——而生产要跑的
    恰恰是那一行初值（进程起来之后没人替它 reset）。判据只能在新解释器里读它。
    """
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(releases.__file__).resolve().parents[1].parent
    code = (
        "import app.core.releases as r\n"
        "r.time.monotonic = lambda: 5.0\n"                 # 一台刚开的机器
        "r._fetch = lambda: ({'version': '0.18', 'url': 'u', 'asset_name': 'a',\n"
        "                     'asset_url': 'd', 'size': 1}, '')\n"
        "print('PROBED' if r.probe(have='0.16')['ok'] else 'SILENT')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(backend), timeout=120)
    assert "PROBED" in out.stdout, f"新进程 + 小钟 = 不去拉：{out.stdout}{out.stderr}"


def test_the_url_we_hand_back_is_fetched_over_the_system_trust_store(monkeypatch):
    """回来的 url 是要点给人去下载/安装的：能被中间人改掉，就等于能推任意安装包。

    所以这条请求必须走 `core/tls.py` 那份系统证书库上下文。写成"验不过就退回不验"
    是这个仓库明确拒绝过的那种修法（524 那次同一节课）。
    """
    fake = _Urlopen(GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    seen = {}

    def fake_context():
        seen["called"] = True
        return object()

    monkeypatch.setattr(releases, "system_ssl_context", fake_context)
    releases.probe(have="0.16")
    assert seen.get("called"), "没走系统证书库"
    assert fake.calls[0]["context"] is not None, "urlopen 没收到 context"
    assert fake.calls[0]["timeout"] == releases.TIMEOUT_SECONDS, "没有超时的出站请求会拖死线程池"


def test_the_github_host_is_written_in_exactly_one_place():
    """`api.github.com` 在后端只许出现一次：多一处就多一个能漂白的地址。"""
    from pathlib import Path

    root = Path(releases.__file__).resolve().parent.parent      # backend/app
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        if "api.github.com" in body:
            hits.append(path.name)
    assert hits == ["releases.py"], f"GitHub 地址出现在多处：{hits}"


def test_the_asset_must_be_named_after_the_version(monkeypatch):
    """挂着别的 apk 不算：一次发布可能同时有 mapping.txt、别的平台的产物或误传的旧包。"""
    payload = {"tag_name": "v0.18", "html_url": "u",
               "assets": [{"name": "ai-assistant-0.17.apk", "size": 1,
                           "browser_download_url": "https://objects.example/old.apk"}]}
    fake = _Urlopen(payload)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    out = releases.probe(have="0.16")
    assert out["ok"] and out["asset_name"] == "", "把名字对不上的包当成这一版可安装的那一个"
    assert out["has_update"] is True, "发布本身还在，卡片仍该提一句（去向是发布页不是那个包）"


# ---------- 路由 ----------

def test_the_endpoint_is_public_read_only_and_answers_200(monkeypatch):
    """没登录也要答：这张卡片出现在人还没输口令的时候。"""
    from app.core.authz import PUBLIC_PATHS

    assert "/v1/release/latest" in PUBLIC_PATHS
    fake = _Urlopen(GOOD)
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    res = TestClient(app).get("/v1/release/latest", params={"have": "0.16"})
    assert res.status_code == 200, res.text
    assert res.json()["has_update"] is True


def test_it_is_a_sync_endpoint_because_it_blocks(monkeypatch):
    """`def` 而不是 `async def`：FastAPI 会把同步路由丢进线程池。

    写成 async 的话，那 5 秒超时是**整个事件循环**在等——正在流式回答的人一起卡住，
    而这个仓库为同一件事付过一次 524 的账。
    """
    import inspect

    from app.main import release_latest
    assert not inspect.iscoroutinefunction(release_latest), "async 路由会把出站等待搬回事件循环"


# ---------- 跨语言那份比较规则不许漂 ----------

def _java_compare_cases():
    """从壳的 JUnit 里把 compare 的用例读出来（同一张表，两边各跑一遍）。"""
    from pathlib import Path

    path = (Path(releases.__file__).resolve().parents[3]
            / "android" / "app" / "src" / "test" / "java" / "xyz" / "fenever"
            / "assistant" / "core" / "ReleasePlanTest.java")
    text = path.read_text(encoding="utf-8")
    cases = []
    for a, b, op in re.findall(r'compare\("([\d.]+)",\s*"([\d.]+)"\)\s*([<>])\s*0', text):
        cases.append((a, b, -1 if op == "<" else 1))
    for a, b in re.findall(r'assertEquals\(0,\s*ReleasePlan\.compare\("([\d.]+)",\s*"([\d.]+)"\)\)', text):
        cases.append((a, b, 0))
    return path.name, cases


def test_python_and_the_shell_agree_on_every_case_the_shell_tests():
    """Java 那份测过的用例，Python 这份必须给同样的答案。

    判据从 Java 测试源码里读，不是在这里手抄一份：手抄的那份会在下一个人往 Java
    里加一条用例的那天悄悄失效，而两边不一致的表现是"壳说有新版本、卡片说不该弹"。
    """
    source, cases = _java_compare_cases()
    assert len(cases) >= 5, f"从 {source} 里只读到 {len(cases)} 条用例，这条锁快空转了"
    for a, b, expected in cases:
        got = releases.compare(a, b)
        assert (got > 0) - (got < 0) == expected, f"compare({a!r}, {b!r}) 两边给的答案不同：{got} vs {expected}"
