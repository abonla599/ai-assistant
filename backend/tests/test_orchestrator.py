"""编排器的结构锁。

这个文件的存在理由很窄但很硬：`Orchestrator` 类里曾经同时定义了 `run(self, goal, task_id)`
与 `run(self, goal)`，Python 静默采用后一个，于是 `/v1/agent/orchestrate` 每次带 `task_id=`
调用都 TypeError，而前一个里那些 `task.cancelled` 检查全成了死代码——取消是假的。

编译器不会报，运行时只在调用点炸，且调用点是唯一那个传 `task_id=` 的地方。所以这里锁的不是
行为细节，是"同名方法只允许定义一次"这个性质：它能一次性挡住整类静默覆盖。
"""
import ast
import inspect

from app.agents.orchestrator import Orchestrator

MODULE = ast.parse(inspect.getsource(Orchestrator))


def _method_names(tree, class_name: str) -> list:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef):
                    names.append(stmt.name)
    return names


def test_no_method_is_defined_twice_in_orchestrator():
    """同名方法定义两次 = 前一份静默变成死代码。这是本文件的头号锁。"""
    names = _method_names(MODULE, "Orchestrator")
    assert names, "没在模块里找到 Orchestrator 类，测试本身失效了"
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"Orchestrator 里这些方法被定义了不止一次：{dupes}"


def test_run_accepts_task_id():
    """活着的 run 必须收得下 main.py 里那个 `orchestrator.run(goal=..., task_id=...)`。"""
    params = inspect.signature(Orchestrator.run).parameters
    assert "task_id" in params, "run() 不接受 task_id，/v1/agent/orchestrate 必然 TypeError"


def test_task_records_its_owner():
    """F-1a 的占位符转正。

    这一格原先是 `xfail`，断言对象还是 `type("T", (), {})()` 现造的鸭子——它测的
    不是产品，是"永远没有任务的代码路径"，所以连 xfail 都算不上证据。
    Task 现在必填 user_id（test_task_store.py 锁了拒收空归属），这里锁的是
    归属要能跟着任务过一遍 to_dict/from_dict：落盘再读回来时"属于谁"不能丢，
    丢了的话重启后 tasks_of 就会把所有人的任务都筛没。
    """
    from app.agents.task_store import Task

    task = Task(goal="排一下今天的背诵", user_id="alice")
    assert task.user_id == "alice"
    assert Task.from_dict(task.to_dict()).user_id == "alice"
