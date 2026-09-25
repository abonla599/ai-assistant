"""请求耗时日志：每个请求一行，写清"什么时候到的、花了多久"。

为什么要有这一行：2026-09-22 报「网页版快一分钟才进去」。当场能量到的只有"现在不慢"
——本机 `/app/` 10ms、走隧道 0.4~0.6s、空闲四分钟后第一脚也一样。而那 60 秒究竟花在
EXE、隧道、Cloudflare 还是手机上，事后一条都取证不了：uvicorn 的访问日志既没有时间戳
也不带耗时，连"那一段时间里有没有请求到达"都问不出来。

这里补的是**取证能力**，不是修复。与 `test_event_loop_not_blocked.py` 的分工：那边测
"事件循环没被占住"，这边测"万一被占住，会不会留下一条能看出是哪个请求的证据"。
"""
import re
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import request_log
from app.core.request_log import RequestTiming

LINE_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] ([A-Z]+) (\S+) (\d{3}|—) (\d+)ms(?: ⚠慢)?$")


def _app_with(**routes):
    """一个只挂了这台架需要的那条路由的小应用。

    不直接借用 `app.main.app`：那会把鉴权、记忆、provider 全拖进来，一条日志格式的
    断言就该为它自己负责。真实的接线由最后那条测试单独验。
    """
    app = FastAPI()
    app.add_middleware(RequestTiming)
    for path, handler in routes.items():
        app.add_api_route(path, handler, methods=["GET"])
    return app


def _ok():
    return "pong"


def _slow():
    time.sleep(0.12)
    return "later"


def test_every_request_logs_when_it_arrived_and_how_long_it_took(capsys):
    """一行六个字段：时刻、方法、路径、状态、毫秒。缺任一个都算这条白写。

    只断言"输出里出现了 /ping"是不够的——那正是本次要修的缺陷的形状：uvicorn 那行
    有路径、恰好证明不了任何关于时间的事。所以时刻与 `ms` 结尾的耗时都要逐字对上。
    """
    with TestClient(_app_with(**{"/ping": _ok})) as client:
        client.get("/ping")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"一个请求应当只留一行: {lines!r}"
    m = LINE_RE.match(lines[0])
    assert m, f"这一行不是能拿去对时的形状: {lines[0]!r}"
    assert m.group(4) == "GET" and m.group(5) == "/ping" and m.group(6) == "200", lines[0]
    assert int(m.group(7)) < 5000, f"一次内存路由不该量出 {m.group(7)}ms: {lines[0]}"


def test_the_query_string_never_reaches_the_log(capsys):
    """查询串不进日志。令牌一旦被写进 URL，就会被这行原样抄进一个公开可读的文件。

    `backend.log` 不在 .gitignore 的保护范围内（它躺在 data/ 下，而 data/ 已被排除，
    但任何一次"顺手把日志贴给别人看"都会把它送出去）。路径留着，`?...` 一律不带。
    """
    with TestClient(_app_with(**{"/ping": _ok})) as client:
        client.get("/ping?token=SK-a-very-live-token")

    out = capsys.readouterr().out
    assert "SK-a-very-live-token" not in out, f"查询串漏进日志了: {out!r}"
    assert "/ping " in out, f"路径本身也没了，这条日志等于没记: {out!r}"


def test_a_request_over_the_threshold_is_called_out(capsys, monkeypatch):
    """超过阈值要打上「慢」，没超过的不许打。

    两条一起写是为了让它有牙：把阈值判据整个删掉，第一句红；反过来把「慢」无条件
    挂上，第二句红。真实阈值（1 秒）由下面那条测接线时顺带覆盖不了，所以这里把它
    压到 50ms 让用例跑得快。
    """
    monkeypatch.setattr(request_log, "SLOW_MS", 50)
    with TestClient(_app_with(**{"/slow": _slow, "/quick": _ok})) as client:
        client.get("/slow")
        client.get("/quick")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 2, lines
    assert "慢" in lines[0] and "/slow" in lines[0], f"120ms 的请求没被标出来: {lines[0]!r}"
    assert "慢" not in lines[1], f"每个请求都被标慢，这个标记就没人看了: {lines[1]!r}"


def test_a_stream_or_a_download_is_not_reported_as_slow(capsys, monkeypatch):
    """模型出字与代取 APK 的耗时由对面决定，拿同一个阈值标它们只会天天报警。

    这两个端点的时长要照记（判断"请求有没有到"时它们同样是证据），只是不挂「慢」。
    把 `LONG_LIVED` 判据删掉，这条红。
    """
    monkeypatch.setattr(request_log, "SLOW_MS", 20)
    app = FastAPI()
    app.add_middleware(RequestTiming)
    app.add_api_route("/v1/chat/stream", _slow, methods=["POST"])
    with TestClient(app) as client:
        client.post("/v1/chat/stream")

    out = capsys.readouterr().out
    assert "/v1/chat/stream" in out, f"长连接那类请求被整个跳过了: {out!r}"
    assert "慢" not in out, f"模型慢慢出字被当成故障: {out!r}"


def test_the_live_app_has_it_installed_and_the_shell_paths_are_logged(capsys):
    """真实应用必须装着它，而且 `/app` 下面那些静态外壳要留下行。

    前四条只证明这个类自己能用：`add_middleware` 没写、或者被写在 `app.mount("/app")`
    之后而漏掉子应用，这四条仍旧全绿——那正是"效果没了但不报错"。所以这里按真实路径
    走一遍：`/health` 与 `/app/app.js`（网页版外壳就是这一批请求）。
    """
    from app.main import app

    assert any(m.cls is RequestTiming for m in app.user_middleware), \
        "耗时日志没装上：下一次「快一分钟」仍然取证不了"

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/app/app.js").status_code == 200

    out = capsys.readouterr().out
    assert re.search(r"\[\d\d:\d\d:\d\d\] GET /health 200 \d+ms", out), out
    assert re.search(r"\[\d\d:\d\d:\d\d\] GET /app/app\.js 200 \d+ms", out), out
