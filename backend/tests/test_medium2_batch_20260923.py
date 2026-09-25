"""Medium 第二批（审查 #12/#13/#15/#16）的回归锁。

每一组都对着审查报告里的原句：
- #12「task_store 的改表与落盘必须在同一把锁里」——旧写法把 super().__setitem__
  留在锁外，而 _flush 持锁遍历 items()：两个编排线程能撞出
  dictionary changed size during iteration。
- #13「恢复任务必须先验归属」——编排器拿 task_id 恢复时以**原属主**身份跑子任务
  （needs_user 工具注入的是 task.user_id），不验归属等于把任务表交给陌生人。
- #15「外部文本要标注成非指令；execute_code 按调用者频控」——搜索结果里的网页
  标题是别人写的字，注入一句"去执行代码"不该比用户的话更有分量；就算模型被带动，
  闸门也让它烧不起第二十个容器。
- #16「grant 只给白名单校验过的资源类型」——request.getResources() 由网页决定，
  透传给 grant 等于由被授权方填写授权清单。
"""
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import task_store as ts

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_ACTIVITY = REPO_ROOT / "android" / "app" / "src" / "main" / "java" / \
    "xyz" / "fenever" / "assistant" / "MainActivity.java"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """task_store 是进程级全局：每条用例从自己的空文件开始，出去时也不留条目。

    不清的代价和 test_task_store.py 的隔离同一个理由——把这条用例的任务记录留给
    下一条按条数断言的用例，红的地方离真凶很远。
    """
    path = tmp_path / "tasks.json"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    ts.restore(path=str(path))
    yield path
    ts.restore(path=str(path))   # 清掉这一条写进去的条目再出去


# ---------- #12 改表与落盘同一把锁 ----------

def _run_mid_iteration_probe(monkeypatch, warm_keys, writer_a, writer_b):
    """让 A 的落盘停在**遍历中途**，放 B 进来写同一张表。

    这是能抓住旧写法的最小形状：旧代码 _flush 在锁里，但改表在锁外——所以 B 的
    改表能正好落在 A 遍历 items() 的两步之间，dict 视图的长度自检会炸
    RuntimeError。新代码把改表也搬进锁，B 只能在门外等，遍历全程表不动。
    """
    errors = []
    started = threading.Event()
    release = threading.Event()

    def slow_write():
        try:
            it = iter(ts.task_store.items())
            try:
                next(it)                   # 消费第一条，停在"遍历中途"
            except StopIteration:
                return                     # 空表（删除用例的第二个写者）无可遍历
            started.set()
            if not release.wait(5.0):
                errors.append("探针等超时：写者可能死锁")
                release.set()
            list(it)                       # 表在这期间变过就 RuntimeError
        except RuntimeError as e:
            errors.append(f"落盘遍历中途表被并发改动：{e}")

    monkeypatch.setattr(ts, "_write_unlocked", slow_write)
    for key in warm_keys:                  # 预热走 dict 本身：不触 flush 不进探针
        dict.__setitem__(ts.task_store, key, ts.Task(goal="warm", user_id="u-warm"))

    ta = threading.Thread(target=writer_a)
    ta.start()
    assert started.wait(5.0), "写者没走到落盘遍历，探针失效"
    tb = threading.Thread(target=writer_b)
    tb.start()
    time.sleep(0.3)                        # 给"无锁改表"留出必然命中的窗口
    release.set()
    ta.join(5)
    tb.join(5)
    assert not ta.is_alive() and not tb.is_alive(), "有线程卡在锁上没出来"
    assert errors == [], "; ".join(errors)
    assert ts._lock.acquire(blocking=False), "测试结束锁还没还回来"
    ts._lock.release()


def test_insert_is_atomic_with_flush(monkeypatch, isolated_store):
    """A 放进存储的落盘遍历期间，B 不许往同一张表塞东西。"""
    probe = ts.Task(goal="probe", user_id="u-a")
    late = ts.Task(goal="late", user_id="u-b")
    _run_mid_iteration_probe(
        monkeypatch,
        warm_keys=["warm-1"],
        writer_a=lambda: ts.task_store.__setitem__(probe.task_id, probe),
        writer_b=lambda: ts.task_store.__setitem__(late.task_id, late),
    )


def test_delete_is_atomic_with_flush(monkeypatch, isolated_store):
    """删除同一个性质：A 删完正在落盘，B 的删除必须排在他后面而不是插进遍历里。"""
    a_target, b_target = "to-delete-by-a", "to-delete-by-b"
    _run_mid_iteration_probe(
        monkeypatch,
        warm_keys=[a_target, b_target],
        writer_a=lambda: ts.delete_task(a_target),
        writer_b=lambda: ts.delete_task(b_target),
    )


def test_delete_task_reports_missing_without_exploding():
    """检查与删除同锁：两个人删同一个 id，后一个看到 False，而不是 KeyError。"""
    assert ts.delete_task("no-such-task-forever") is False


# ---------- #13 恢复任务先验归属 ----------

def _stub_orchestrator(monkeypatch):
    from app.agents.orchestrator import Orchestrator

    orch = Orchestrator()
    monkeypatch.setattr(orch.planner, "plan", lambda goal: [])   # 空计划：不碰执行器
    return orch


def test_recovering_another_persons_task_creates_your_own_instead(monkeypatch, isolated_store):
    """bob 拿着 alice 的 task_id 来跑：既读不到 alice 的已完成结果，也不以 alice 的身份执行。"""
    alice = ts.Task(goal="alice 的私事", user_id="alice")
    alice.status = ts.TaskStatus.COMPLETED
    alice.final_answer = "alice 的答案"
    ts.task_store[alice.task_id] = alice

    orch = _stub_orchestrator(monkeypatch)
    out = orch.run(goal="bob 的目标", task_id=alice.task_id, user_id="bob")

    assert out["task_id"] != alice.task_id, "别人的任务被当自己的恢复了：子任务将以原属主身份跑"
    assert out.get("final_answer") != "alice 的答案", "已完成任务成了跨用户读结果的通道"
    assert ts.get_task(out["task_id"]).user_id == "bob", "新建的任务记错了人"
    assert ts.get_task(alice.task_id).user_id == "alice", "alice 的记录被动过"


def test_owner_recovering_own_completed_task_still_short_circuits(monkeypatch, isolated_store):
    """反方向也要锁：归属校验不是把恢复功能顺手关掉。空白边界的 user_id 一并验。"""
    bob = ts.Task(goal="bob 的事", user_id="bob")
    bob.status = ts.TaskStatus.COMPLETED
    bob.final_answer = "bob 的答案"
    ts.task_store[bob.task_id] = bob

    orch = _stub_orchestrator(monkeypatch)
    out = orch.run(goal="接着上次的", task_id=bob.task_id, user_id="  bob  ")
    assert out["task_id"] == bob.task_id and out["final_answer"] == "bob 的答案"


def test_stranger_without_identity_cannot_recover_anything(monkeypatch, isolated_store):
    """空 user_id + 别人的 task_id → 落到新建分支 → Task 拒收空归属，fail-closed。"""
    alice = ts.Task(goal="alice 的私事", user_id="alice")
    ts.task_store[alice.task_id] = alice

    orch = _stub_orchestrator(monkeypatch)
    with pytest.raises(ValueError):
        orch.run(goal="冒领", task_id=alice.task_id, user_id="")


# ---------- #15a 搜索结果标注为"资料不是指令" ----------

def test_search_results_are_annotated_as_data_not_instructions(monkeypatch):
    from app.tools import web_search as ws
    from app.tools.executor import execute_tool

    monkeypatch.setattr(ws, "search",
                        lambda query, max_results=3: [{"title": "某校简章",
                                                       "url": "https://e/1",
                                                       "snippet": "内容"}])
    out = execute_tool("web_search", {"query": "某校 简章"})
    assert "不是指令" in out, f"外部文本没有标注非指令身份：{out!r}"
    assert "某校简章" in out and "https://e/1" in out, "标注不该吞掉任何一条结果"


def test_the_empty_answer_does_not_claim_to_be_external_pages(monkeypatch):
    """没结果就没有"外部内容"可标注：头注跟着结果走，不跟着调用走。"""
    from app.tools import web_search as ws
    from app.tools.executor import execute_tool

    monkeypatch.setattr(ws, "search", lambda query, max_results=3: [])
    out = execute_tool("web_search", {"query": "x"})
    assert "不是指令" not in out, f"空结果被套上了有结果才成立的话：{out!r}"


# ---------- #15b execute_code 按调用者频控 ----------

@pytest.fixture()
def fake_code_runner(monkeypatch):
    """把 execute_code 的真身换成计数桩：频控测的是闸门，不该真拉容器。"""
    from app.tools import executor as ex
    from app.tools.registry import tools_registry

    calls = []

    def fake(code, language="python"):
        calls.append(code)
        return "ok"

    monkeypatch.setitem(tools_registry["execute_code"], "function", fake)
    ex._FREQ_LEDGER.clear()
    return calls


def test_execute_code_is_capped_per_caller(fake_code_runner):
    from app.tools import executor as ex

    calls = fake_code_runner
    _window, cap = ex._FREQ_LIMITS["execute_code"]
    for i in range(cap):
        out = ex.execute_tool("execute_code", {"code": f"c{i}"}, user_id="alice")
        assert "过于频繁" not in out
    assert len(calls) == cap

    out = ex.execute_tool("execute_code", {"code": "over"}, user_id="alice")
    assert "过于频繁" in out and "还需等待" in out, f"闸门没给出等待出口：{out!r}"
    assert len(calls) == cap, "被拦下的调用仍然执行了：频控只拦返回值不拦副作用就是假闸"

    # 桶按人分开：alice 攒满不该饿死 bob，也不该给匿名调用开平行免检通道之外的口
    assert "过于频繁" not in ex.execute_tool("execute_code", {"code": "b"}, user_id="bob")
    assert "过于频繁" not in ex.execute_tool("execute_code", {"code": "n"})   # anon
    assert len(calls) == cap + 2


def test_blocked_attempts_do_not_reset_the_window(fake_code_runner, monkeypatch):
    """被拦下的一次不许刷新窗口起点，否则"一直重试"能把等待永远续成不可用。"""
    from app.tools import executor as ex

    calls = fake_code_runner
    _window, cap = ex._FREQ_LIMITS["execute_code"]
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ex, "time", SimpleNamespace(monotonic=lambda: clock.now))
    for i in range(cap):
        ex.execute_tool("execute_code", {"code": f"c{i}"}, user_id="alice")
    clock.now += _window - 10          # 窗口将满未满
    ex.execute_tool("execute_code", {"code": "retry"}, user_id="alice")   # 被拦
    clock.now += 20                    # 距最后一次**放行**已超过窗口
    out = ex.execute_tool("execute_code", {"code": "after"}, user_id="alice")
    assert "过于频繁" not in out, f"拦下的重试自己续了命：{out!r}"
    assert len(calls) == cap + 1


def test_failed_executions_still_cost(fake_code_runner, monkeypatch):
    """失败的代码同样拉起过容器：不计数就等于给重试型轰炸留免费额度。"""
    from app.tools import executor as ex
    from app.tools.registry import tools_registry

    calls = fake_code_runner

    def boom(code, language="python"):
        calls.append(code)
        raise RuntimeError("容器起不来")

    monkeypatch.setitem(tools_registry["execute_code"], "function", boom)
    _window, cap = ex._FREQ_LIMITS["execute_code"]
    for i in range(cap):
        out = ex.execute_tool("execute_code", {"code": f"c{i}"}, user_id="alice")
        assert "容器起不来" in out
    out = ex.execute_tool("execute_code", {"code": "x"}, user_id="alice")
    assert "过于频繁" in out, "失败不计数：模型可以无限重试烧容器"


def test_tools_outside_the_limit_table_are_not_throttled(fake_code_runner):
    """闸门是白名单制：没登记进 _FREQ_LIMITS 的工具一次都不该被它挡。"""
    from app.tools import executor as ex

    _window, cap = ex._FREQ_LIMITS["execute_code"]
    for i in range(cap * 2):
        out = ex.execute_tool("help", {}, user_id="alice")
        assert "过于频繁" not in out


# ---------- #16 Android 壳：grant 只回白名单内的资源类型 ----------

# 复用既有的 Java 注释剥离器（尊重字符串字面量，两种注释都处理）。自带一份只切
# // 的反而会误报：解释这个白名单的 Javadoc 里就抄着旧写法"原来两处都是
# request.grant(request.getResources())"——注释里的符号不该算数。
from .test_android_shell import _strip_java_comments


def test_webview_grants_only_the_whitelisted_resource_type():
    src = _strip_java_comments(MAIN_ACTIVITY.read_text(encoding="utf-8"))

    offenders = re.findall(r"\.grant\(\s*request\.getResources\(\)\s*\)", src)
    assert not offenders, "网页要什么就批什么的透传又回来了"

    grants = re.findall(r"\.grant\(([^)]*)\)", src)
    assert grants, "找不到任何 grant，测试本身失效了"
    for arg in grants:
        assert "allowed" in arg, f"grant 没有走白名单过滤后的数组：grant({arg})"

    assert "RESOURCE_VIDEO_CAPTURE.equals" in src, "白名单判据不再是摄像头类型"
    deny = re.findall(r"\.deny\(\)", src)
    assert len(deny) >= 2, "过滤为空 / 用户拒绝授权两条出口都必须 deny，网页在等回话"


def test_runtime_permission_grant_path_also_filters():
    """onRequestPermissionsResult 里用户点了"允许"之后，也只回白名单内的类型。"""
    src = _strip_java_comments(MAIN_ACTIVITY.read_text(encoding="utf-8"))
    idx = src.index("onRequestPermissionsResult")
    tail = src[idx:idx + src[idx:].index("onActivityResult")]
    assert "allowedResources(request)" in tail, "系统授权回来后改回透传了"
    assert "grant(request.getResources())" not in tail
