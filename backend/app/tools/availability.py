"""工具的外部依赖通不通，是测出来的，不是写在名单里的。

写死"这台机器没有 Docker / 连不上搜索源"，在它被修好的第二天就变成一句新的谎，
而且没人会回来删它。所以这里只回答一个问题——"现在通不通"——工具清单跟着它长缩。

2026-09-22 换搜索源时改过一次口径，改的理由比改动本身值得记：原来探的是"套接字能不能
连上源站域名的 443"——那是**比"工具能用"更弱**的一件事：连得上不等于源站肯给结果，
更不等于我们解析得出来。以前两者恰好同向（那几个域名连 DNS 都拿不到正确答案，所以
连也连不上），缺陷没暴露；换成爬搜索结果页之后必然分叉：连接永远绿，而页面结构一改
就静默变空，模型就会看见一个每次回它空话的工具。所以现在探测直接调工具用的那个函数。

探测放后台线程而不是压在请求路径上：一次真查询要几百毫秒到几秒，放在用户发第一条
消息的时候才做，等于让所有人陪着一个运维检查等。
"""
import threading
import time

from app.tools import web_search

PROBE_QUERY = "北京 天气"       # 一个永远有结果、又不含任何用户内容的查询
REFRESH_SECONDS = 900            # 一次探测 = 一次真实抓取，别把它当免费的心跳

_lock = threading.Lock()
# value=None 表示"还没测出来"。此时按"通"处理：宁可多给模型一个工具，
# 也不要因为探测还没来得及跑就悄悄藏掉一个能用的功能。
_state = {"value": None, "checked_at": 0.0}
_thread = None


def _fresh() -> bool:
    return _state["value"] is True and (time.monotonic() - _state["checked_at"]) < REFRESH_SECONDS


def _refresh() -> None:
    with _lock:
        if _fresh():
            return                              # 刚有真人搜成功过，不用再敲源站
    try:
        value = bool(web_search.search(PROBE_QUERY, 1))
    except Exception as e:  # 探测代码自己出问题（依赖没装、解析器抛错…）
        print(f"⚠️ 搜索源探测异常，按可用处理：{e}")
        value = True
    with _lock:
        changed = _state["value"] != value
        _state["value"] = value
        _state["checked_at"] = time.monotonic()
    if changed:
        # 只在翻转时打印。这事的唯一用户可见症状是"web_search 从清单里消失了"，
        # 而清单是动态裁的，不看这行日志就只能靠猜。
        print(f"🔎 搜索源{'可用' if value else '不可用'}，"
              f"web_search {'给得出' if value else '不再递给模型'}")


def _loop() -> None:
    while True:
        _refresh()
        time.sleep(REFRESH_SECONDS)


def _ensure_probe_running() -> None:
    global _thread
    with _lock:
        alive = _thread is not None and _thread.is_alive()
    if not alive:
        _thread = threading.Thread(target=_loop, daemon=True, name="search-source-probe")
        _thread.start()


def search_reachable() -> bool:
    """当前是否够得着搜索源（缓存值，后台定期真跑一次查询）。"""
    _ensure_probe_running()
    with _lock:
        if _state["value"] is None:
            return True
        return bool(_state["value"])


def note_search_ok() -> None:
    """真人搜成功一次，就把这件事当成最新结论。

    两个理由：省掉对源站的无谓抓取（这台机器的出口 IP 是和朋友们共用的），以及让
    "用户刚刚还在用"这个最强的证据不被下一次定时探测盖掉。
    """
    with _lock:
        _state["value"] = True
        _state["checked_at"] = time.monotonic()


def start_probe() -> None:
    """后端一起来就把线程拉起，别等第一个用户请求。

    第一轮探测比以前那种 TCP 探测慢得多（一次真查询，最坏走到超时上限），这段时间
    `search_reachable()` 按"通"处理——早一分钟开始测，这段误开窗口就短一分钟。
    """
    _ensure_probe_running()

# Docker 那一维不在这里：`sandbox` 实例已经由 builtin_tools 在导入期建好并持有
# client，再 new 一个 SandboxManager 等于每次检查都重连一遍 docker 守护进程，
# 还会把"沙箱停用"那行警告重复打印。可用性一律读那个唯一实例。
