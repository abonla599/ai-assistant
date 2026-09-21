"""任务存储：重启还在、且知道属于谁。

2026-09-21 的现状是 `task_store = {}`（一个进程级内存字典），文件头自己写着
"后续可升级为 Redis 或数据库"。规划书阶段一判据 2 要的是"关掉服务器再开起来，
昨天的任务还在，且属于正确的人"——所以这一步不挑 Redis、不加队列，就用与
sessions/providers 同一份写法：一个 JSON 文件 + 一把锁 + 原子替换。
"""
import json
import os

import pytest

from app.agents import task_store as ts


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """每一份测试跑在自己的文件上：真实 data/tasks.json 不是测试的靶子。"""
    path = tmp_path / "tasks.json"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    ts.restore(path=str(path))
    yield path


def test_a_task_survives_a_restart(isolated):
    task = ts.Task(goal="把这份材料排进今天", user_id="u-1")
    ts.task_store[task.task_id] = task

    ts.restore(path=str(isolated))          # 等价于重启进程
    back = ts.get_task(task.task_id)
    assert back is not None, "任务没落盘：重启后 get_task 拿到 None"
    assert back.goal == "把这份材料排进今天"
    assert back.status == ts.TaskStatus.PENDING


def test_every_task_knows_who_it_belongs_to(isolated):
    task = ts.Task(goal="g", user_id="u-1")
    ts.task_store[task.task_id] = task
    ts.restore(path=str(isolated))
    assert ts.get_task(task.task_id).user_id == "u-1"


def test_a_task_without_an_owner_is_refused(isolated):
    """没有身份就没有任务——跨用户读到别人的任务是这个项目付过一次账的形状。"""
    with pytest.raises(ValueError):
        ts.Task(goal="g", user_id="")
    with pytest.raises(ValueError):
        ts.Task(goal="g", user_id=None)


def test_listing_is_filtered_to_one_person(isolated):
    mine = ts.Task(goal="我的", user_id="u-1")
    theirs = ts.Task(goal="别人的", user_id="u-2")
    ts.task_store[mine.task_id] = mine
    ts.task_store[theirs.task_id] = theirs
    ts.restore(path=str(isolated))
    assert [t.task_id for t in ts.tasks_of("u-1")] == [mine.task_id]
    assert [t.task_id for t in ts.tasks_of("u-2")] == [theirs.task_id]
    assert ts.tasks_of("u-3") == []


def test_deleting_a_task_removes_it_from_disk(isolated):
    task = ts.Task(goal="g", user_id="u-1")
    ts.task_store[task.task_id] = task
    assert ts.delete_task(task.task_id) is True
    raw = json.loads(open(isolated, encoding="utf-8").read())
    assert task.task_id not in raw["tasks"], "内存删了、盘上还在，重启就诈尸"


def test_the_file_lives_where_the_env_says_and_starts_empty(isolated):
    """空账不该被凭空造出来：没有任务的时候没有文件。"""
    task = ts.Task(goal="g", user_id="u-1")
    ts.task_store[task.task_id] = task
    assert isolated.exists()
    assert os.path.basename(isolated) == "tasks.json"
    ts.delete_task(task.task_id)
    assert json.loads(isolated.read_text(encoding="utf-8"))["tasks"] == {}


def test_the_orchestrator_bills_the_task_to_whoever_asked_for_it(monkeypatch):
    """Task 现在要求归属人，那创建它的那条路就必须把"谁"传下来。

    空计划是让编排器跑到建任务就停的最短路径——不碰 planner 的提示词，也不碰 LLM。
    """
    from app.agents.orchestrator import Orchestrator

    orch = Orchestrator()
    monkeypatch.setattr(orch.planner, "plan", lambda goal: [])
    result = orch.run(goal="随便排一下", user_id="u-9")
    tid = result.get("task_id")
    assert tid, result
    assert ts.get_task(tid).user_id == "u-9"


def test_the_endpoint_forwards_the_caller_instead_of_dropping_it():
    """`_: Principal = RequireAdmin` 那一格把身份丢掉了——写归属时得接住。"""
    from app.main import orchestrate_task
    import inspect

    params = inspect.signature(orchestrate_task).parameters
    assert "principal" in params, f"端点没接住当前这个人：{list(params)}"
    src = inspect.getsource(orchestrate_task)
    assert "user_id=principal.user_id" in src, src
