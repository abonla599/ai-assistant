"""日程存储与它那两条端点、两条工具的契约。

这一份是新长出来的东西，所以钉的顺序就是它可能坏掉的顺序：

1. **归属**：本项目已经为"跨用户读到别人的东西"付过一次账（跨用户记忆泄露那次）。
   日程比记忆更敏感——它写的是"这个人什么时候要做什么"，所以归属是唯一入口，
   而唯一入口意味着客户端与模型都没有那个口子。
2. **上限**：`data/` 没有任何清理机制。一天 40 条、一条 200 字是"磁盘不会被单个人打满"
   这一件事的边界，不是产品判断。
3. **格式**：坏日期与坏时间回 400/失败，而不是安静地给一份空清单——后者与"那天确实
   没安排"长得一模一样，会被渲染成一句假话。
4. **落盘**：原子替换 + 读不懂改名 `.corrupt`，与 usage/auth 同一套；静默当空库会把
   "文件坏了"变成"我的日程凭空消失了"。
"""
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import schedule
from app.main import app

# isolated_schedule 夹具在 conftest：流式那条路（test_stream_tools）也要验同一份存储，
# 两处各自定义就是两份口径。


# ---------- 存储层 ----------

def test_every_plan_needs_an_owner(isolated_schedule):
    for bad in ("", None, "   "):
        with pytest.raises(schedule.ScheduleError):
            schedule.plan(bad)
        with pytest.raises(schedule.ScheduleError):
            schedule.add_item(bad, "交周报")


def test_two_people_do_not_see_each_others_schedule(isolated_schedule):
    """正向对照 + 反例：A 写的那条既不出现在 B 的清单里，也不出现在 B 的那些天里。"""
    # "别的哪一天"必须避开运行当天：A 那条落在 today，字面量撞上 today 时
    # "换天串不串人"的两条断言查的就是同一天（2026-09-25 当天 CI 就是这么红的）。
    other_day = "2026-09-25" if schedule.today() != "2026-09-25" else "2027-01-01"
    schedule.add_item("u-A", "交周报", at="15:00")
    schedule.add_item("u-B", "取快递", day=other_day)

    assert [i["text"] for i in schedule.plan("u-A")] == ["交周报"]
    assert [i["text"] for i in schedule.plan("u-B", other_day)] == ["取快递"]
    assert schedule.days_for("u-A") == [schedule.today()]
    assert schedule.days_for("u-B") == [other_day]
    assert schedule.plan("u-A", other_day) == [], "换天也串不了人才算数"
    assert schedule.plan("u-B") == [], "B 今天没有安排，A 的那条不该出现在他这儿"


def test_returned_rows_are_copies(isolated_schedule):
    """改拿回来的那份不该改到存储：否则"读一下再顺手改"会变成第二条写路径。"""
    schedule.add_item("u-1", "交周报")
    rows = schedule.plan("u-1")
    rows[0]["text"] = "被改掉的字"
    rows.append({"text": "多出来的一条"})
    assert schedule.plan("u-1")[0]["text"] == "交周报"
    assert len(schedule.plan("u-1")) == 1


def test_text_and_day_and_time_are_all_bounded(isolated_schedule):
    """三条边界都用**字面量**判，不写 `MAX_* + 1`。

    写常量的那条测试自我实现：把上限改成 20 万字，它照样绿——而"一条事项 200 字"
    是写进工具描述里给模型看的话，改了就是界面在说假话。
    """
    assert schedule.MAX_TEXT_CHARS == 200 and schedule.MAX_ITEMS_PER_DAY == 40
    with pytest.raises(schedule.ScheduleError):
        schedule.add_item("u-1", "   ")
    with pytest.raises(schedule.ScheduleError):
        schedule.add_item("u-1", "字" * 201)
    assert len(schedule.add_item("u-1", "字" * 200)["text"]) == 200
    for bad_time in ("15:00:01", "24:00", "9:00", "3点"):
        with pytest.raises(schedule.ScheduleError):
            schedule.add_item("u-1", "开会", at=bad_time)
    for bad_day in ("昨天", "2026-9-1", "2026-02-30", "2026-13-01"):
        with pytest.raises(schedule.ScheduleError):
            schedule.add_item("u-1", "开会", day=bad_day)
    # 留空是合法的另一种意思："今天找时间做"
    assert schedule.add_item("u-1", "不定点", at="")["at"] == ""


def test_one_day_holds_a_bound_not_the_whole_life(isolated_schedule):
    """上限存在的理由写在模块注释里：data/ 不清理，所以每条存储都得会自己喊停。"""
    for i in range(schedule.MAX_ITEMS_PER_DAY):
        schedule.add_item("u-1", f"第{i}条")
    with pytest.raises(schedule.ScheduleError):
        schedule.add_item("u-1", "挤不进去的一条")
    assert len(schedule.plan("u-1")) == schedule.MAX_ITEMS_PER_DAY


def test_set_plan_replaces_the_whole_day_idempotently(isolated_schedule):
    """整份替换是幂等的：同一份请求重放两次，盘上还是那一份。"""
    first = schedule.set_plan("u-1", [{"text": "甲", "at": "09:00"}, {"text": "乙"}])
    again = schedule.set_plan("u-1", [{"text": "甲", "at": "09:00"}, {"text": "乙"}])
    assert len(first) == len(again) == 2
    assert [r["text"] for r in schedule.plan("u-1")] == ["甲", "乙"]
    schedule.set_plan("u-1", [{"text": "只剩这一条", "done": True}])
    rows = schedule.plan("u-1")
    assert len(rows) == 1 and rows[0]["done"] is True
    schedule.set_plan("u-1", [])
    assert schedule.plan("u-1") == []


def test_set_plan_caps_the_day_it_writes(isolated_schedule):
    """`add_item` 的上限管不到 `set_plan`：一次 PUT 就能写进来一百条。

    前端与工具是两条写入口，各自都要会喊停——只守一条就是"绕开那条路"的洞。
    """
    schedule.set_plan("u-1", [{"text": "留着的一条"}])
    with pytest.raises(schedule.ScheduleError):
        schedule.set_plan("u-1", [{"text": f"第{i}条"} for i in range(schedule.MAX_ITEMS_PER_DAY + 1)])
    assert [r["text"] for r in schedule.plan("u-1")] == ["留着的一条"], "被拒的那一次不该把原来那份清掉"


def test_set_plan_caps_the_day_it_writes(isolated_schedule):
    """整份替换那条路也有同一个上限：`add_item` 挡不住一次 PUT 塞进来四百条。"""
    with pytest.raises(schedule.ScheduleError):
        schedule.set_plan("u-1", [{"text": f"第{i}条"} for i in range(schedule.MAX_ITEMS_PER_DAY + 1)])
    assert schedule.plan("u-1") == [], "被拒的那一次不该留下半份清单"


def test_rejected_rows_never_reach_the_disk(isolated_schedule):
    """校验发生在写盘之前：一次带坏数据的 set_plan 不该把原来那份清掉。"""
    schedule.set_plan("u-1", [{"text": "留着的一条"}])
    with pytest.raises(schedule.ScheduleError):
        schedule.set_plan("u-1", [{"text": "还行"}, {"text": ""}])
    assert [r["text"] for r in schedule.plan("u-1")] == ["留着的一条"]


def test_the_file_survives_a_restart(isolated_schedule):
    path = isolated_schedule
    schedule.add_item("u-1", "重启之后还要在", at="08:30")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "plans" in on_disk and on_disk["plans"]["u-1"], "盘上形状与读取端对不上"

    schedule.restore(path=str(path))
    rows = schedule.plan("u-1")
    assert [r["text"] for r in rows] == ["重启之后还要在"]
    assert rows[0]["at"] == "08:30" and rows[0]["id"]


def test_an_unreadable_file_is_kept_not_forgotten(isolated_schedule, tmp_path):
    """读不懂的日程改名 .corrupt 留着，本轮从空清单开始——与 usage 同一套。"""
    path = tmp_path / "broken-schedule.json"
    path.write_text("{这不是 JSON", encoding="utf-8")
    assert schedule.restore(path=str(path)) == 0
    assert not path.exists(), "原件没被改名：下一次写盘就会把它盖掉"
    backup = Path(str(path) + ".corrupt")
    assert backup.read_text(encoding="utf-8") == "{这不是 JSON", "留下的那份得能人工读回来"
    assert schedule.plan("u-1") == []


def test_the_write_is_atomic_and_leaves_nothing_behind(isolated_schedule):
    """落盘必须是"写临时文件 + os.replace"，跑完目录里只该有那一个文件。

    只判"没有 .tmp 残留"是一条空锁：直接 `open(path,"w")` 也不留 .tmp，而它恰恰是
    要防的那种写法——进程在写一半时被掐，整份日程就成了半截 JSON。所以这里同时读源码。
    """
    import inspect

    src = inspect.getsource(schedule._write)
    assert ".tmp" in src and "os.replace" in src, "落盘不再是原子替换：中断会留下半截文件"

    schedule.add_item("u-1", "一条")
    leftovers = [p.name for p in isolated_schedule.parent.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"残留了中间文件：{leftovers}"


# ---------- 端点 ----------

def test_schedule_endpoints_require_an_identity(enforced):
    """/v1/* 一律要凭据；这一条钉的是"新面没被顺手做成免登录"。"""
    anonymous = TestClient(app)
    assert anonymous.get("/v1/schedule").status_code == 401
    assert anonymous.put("/v1/schedule", json={"items": []}).status_code == 401


def test_the_http_shape_round_trips(isolated_schedule, client):
    """disabled 模式下 client 就是本机管理员：这条只测形状，归属那条在下面两条里。"""
    body = client.put("/v1/schedule", json={"items": [{"text": "交周报", "at": "15:00"},
                                                      {"text": "不倒垃圾"}]}).json()
    assert body["count"] == 2 and body["day"] == schedule.today()
    got = client.get("/v1/schedule").json()
    assert [i["text"] for i in got["items"]] == ["交周报", "不倒垃圾"]
    assert got["days"] == [schedule.today()]
    assert client.get("/v1/schedule", params={"day": "2026-01-01"}).json()["items"] == []


def test_a_bad_day_is_a_400_not_an_empty_plan(isolated_schedule, client):
    res = client.get("/v1/schedule", params={"day": "下周三"})
    assert res.status_code == 400, "回一份空清单会被渲染成「那天没安排」，那是句假话"
    res = client.put("/v1/schedule", json={"day": "昨天", "items": [{"text": "一条"}]})
    assert res.status_code == 400


def test_the_body_cannot_choose_whose_schedule_it_is(isolated_schedule, enforced):
    """请求体里塞 user_id 不改归属：写进去的还是登录那一个人的清单。

    pydantic 默认忽略未声明字段，所以这个口子中看不存在于本端点——但它是**默认形状**
    给的错觉，将来给 ScheduleRequest 加字段的人很容易顺手加一个 user_id。这条钉的是
    今天的行为，也是那条改动的第一个路障。
    """
    as_user = enforced("日程甲")
    stranger = TestClient(app)
    res = stranger.put("/v1/schedule",
                       json={"user_id": "default_user", "items": [{"text": "栽进去的一条"}]},
                       headers=as_user)
    assert res.status_code == 200
    mine = stranger.get("/v1/schedule", headers=as_user).json()
    assert [i["text"] for i in mine["items"]] == ["栽进去的一条"]
    # 本机管理员那份（bootstrap 的 user_id 是 default_user）没有被动到
    bootstrap = {"Authorization": "Bearer boot-token"}
    assert stranger.get("/v1/schedule", headers=bootstrap).json()["items"] == []


# ---------- 工具 ----------

def test_the_tools_are_registered_as_needing_an_identity():
    from app.tools.registry import tools_registry

    for name in ("today_plan", "plan_add"):
        assert name in tools_registry, f"{name} 没注册，模型永远看不见它"
        assert tools_registry[name]["needs_user"], \
            f"{name} 不标 needs_user，归属就由模型说了算"
        # 参数表里不许出现 user_id：那等于请模型编一个归属人
        assert "user_id" not in json.dumps(tools_registry[name]["parameters"]), \
            f"{name} 的参数表把 user_id 暴露给模型了"


def test_a_model_forged_owner_is_ignored(isolated_schedule):
    """模型传来的 user_id 一律作废：写进去的是服务端算出来的那个人。

    返回的是 `ToolResponse.to_string()` 那行文本（`✓ …`），不是 JSON——这里判文本，
    真判据在存储里：victim 那份必须还是空的。
    """
    from app.tools.executor import execute_tool

    out = execute_tool("plan_add", {"text": "模型栽的一条", "user_id": "victim"},
                       user_id="attacker")
    assert out.startswith("✓"), out
    assert [i["text"] for i in schedule.plan("attacker")] == ["模型栽的一条"]
    assert schedule.plan("victim") == [], "归属被模型说动了"


def test_schedule_tools_refuse_without_an_identity(isolated_schedule):
    from app.tools.executor import execute_tool

    for name, args in (("today_plan", {}), ("plan_add", {"text": "一条"})):
        out = execute_tool(name, args, user_id=None)
        assert out.startswith("✗"), f"{name} 在没有身份时居然跑成功了：{out}"
        assert "身份" in out, out


def test_today_plan_reads_back_what_was_written(isolated_schedule):
    from app.tools.executor import execute_tool

    written = execute_tool("plan_add", {"text": "跑穿的一条", "at": "20:15"}, user_id="u-1")
    assert written.startswith("✓"), written
    assert "20:15" in written, "回执里没带时间，模型下一步没法跟用户确认"

    text = execute_tool("today_plan", {}, user_id="u-1")
    assert "跑穿的一条" in text and "20:15" in text
    # 第一行带着日期与星期：模型换算「明天」要有基准，它从提示词里拿不到今天几号
    assert re.search(r"✓ \d{4}-\d{2}-\d{2} 周[一二三四五六日]：", text), text
    assert "共 1 条" in text


def test_today_plan_says_what_it_does_not_have(isolated_schedule):
    from app.tools.executor import execute_tool

    text = execute_tool("today_plan", {}, user_id="u-empty")
    assert "没有安排" in text, "空清单要明说没有安排，而不是回一段像有内容的空话"


def test_the_store_is_registered_in_the_data_path_table():
    """新存储要进 paths.DATA_PATH_ENV_VARS，否则启动日志里永远看不见它落在哪。"""
    from app.core.paths import DATA_PATH_ENV_VARS, resolve_all_data_paths

    assert DATA_PATH_ENV_VARS.get("日程") == "SCHEDULE_DB_PATH"
    labels = dict(resolve_all_data_paths())
    assert "日程" in labels and labels["日程"].endswith("schedule.json")
    assert os.getenv("SCHEDULE_DB_PATH"), "conftest 没把它指走，测试会写到用户真实数据上"
