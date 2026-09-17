"""沙箱本身的场景锁：能跑、死循环会被掐断、出不去网。

这三条原先只存在于 backend/ 根下三个手工脚本里（test_sandbox / test_sandbox_basic
/test_sandbox_multi），全是 print 没有断言，而且 CI 只跑 backend/tests/——也就是说
"沙箱有覆盖"这个印象从来没有对应的回归。现在把它们真正要验的三件事写成断言，
需要容器的部分由 conftest 的 sandbox_language 统一决定跑还是 skip。

没有从 test_sandbox_multi 带过来的一条：JavaScript 的联网检查。它原来写的是
`try { http.get(...) } catch { ... }`，而 Node 的 http 错误走 'error' 事件、
不会被 try/catch 抓到，那条 print 无论通不通网都只会打印成功分支之外的空结果。
与其搬一个测不出东西的断言，不如留在这里说明为什么没搬。
"""
import time

import pytest

from app.sandbox.sandbox_manager import SandboxManager


@pytest.fixture(scope="module")
def sm():
    return SandboxManager()


def test_python_code_runs_and_returns_stdout(sm, sandbox_language):
    sandbox_language("python")
    res = sm.run_code("print('hello from sandbox')", "python")
    assert res.get("error") is None, f"沙箱执行失败: {res.get('error')}"
    assert "hello from sandbox" in res["stdout"]


def test_python_dead_loop_is_cut_off_not_left_running(sm, sandbox_language):
    """死循环必须在超时后被杀掉：这条锁的是"宿主不被用户代码挂住"。

    断的是墙上时间而不是错误文案，因为各平台 docker SDK 的 wait(timeout=) 抛什么
    并不一致；但无论走哪条分支，都不允许这次调用一直不返回。
    """
    sandbox_language("python")
    started = time.monotonic()
    res = sm.run_code("while True: pass", "python", timeout=3)
    elapsed = time.monotonic() - started
    assert res.get("error"), "死循环竟然正常返回了，说明超时没生效"
    assert elapsed < 30, f"超时未生效，调用耗了 {elapsed:.1f}s"


def test_python_cannot_reach_the_network(sm, sandbox_language):
    """沙箱必须出不去网（run_code 里是 network_disabled=True）。

    这是沙箱存在的理由之一：用户代码能连外网，就等于在宿主上开了个出站代理。
    """
    sandbox_language("python")
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('EGRESS-OK')\n"
        "except Exception as e:\n"
        "    print('EGRESS-BLOCKED', type(e).__name__)\n"
    )
    res = sm.run_code(code, "python", timeout=10)
    assert res.get("error") is None, f"容器没跑起来，这条断言什么都没测: {res.get('error')}"
    assert "EGRESS-BLOCKED" in res["stdout"]
    assert "EGRESS-OK" not in res["stdout"], "沙箱内的代码连上了外网"


def test_javascript_code_runs(sm, sandbox_language):
    sandbox_language("javascript")
    res = sm.run_code("console.log('hi from js');", "javascript")
    assert res.get("error") is None, f"JS 沙箱执行失败: {res.get('error')}"
    assert "hi from js" in res["stdout"]
