"""工具的外部依赖通不通，是测出来的，不是写在名单里的。

写死"这台机器没有 Docker / 连不上 DuckDuckGo"，在它被修好的第二天就变成一句新的谎，
而且没人会回来删它。所以这里只回答一个问题——"现在通不通"——工具清单跟着它长缩。

搜索源用后台线程定期探测而不是在请求路径上探：一次 TCP 探测 1.5 秒，放在用户发第一
条消息的时候才做，等于让所有人陪着一个运维检查等。
"""
import socket
import threading
import time

# 三个都试：任一连通就算通。只钉一个域名的话，探测本身会变成新的单点。
SEARCH_PROBE_ENDPOINTS = (
    ("html.duckduckgo.com", 443),
    ("duckduckgo.com", 443),
    ("lite.duckduckgo.com", 443),
)
PROBE_TIMEOUT = 1.5
REFRESH_SECONDS = 300

_lock = threading.Lock()
# value=None 表示"还没测出来"。此时按"通"处理：宁可多给模型一个工具，
# 也不要因为探测还没来得及跑就悄悄藏掉一个能用的功能。
_state = {"value": None, "checked_at": 0.0}
_thread = None


def _probe_once() -> bool:
    for host, port in SEARCH_PROBE_ENDPOINTS:
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
                return True
        except OSError:
            continue
    return False


def _refresh() -> None:
    try:
        value = _probe_once()
    except Exception as e:  # 探测代码自己出问题（DNS 库、socket 权限…）
        print(f"⚠️ 搜索源探测异常，按可用处理：{e}")
        value = True
    with _lock:
        changed = _state["value"] != value
        _state["value"] = value
        _state["checked_at"] = time.monotonic()
    if changed:
        # 只在翻转时打印。这事的唯一用户可见症状是"web_search 从清单里消失了"，
        # 而清单是动态裁的，不看这行日志就只能靠猜。
        print(f"🔎 搜索源{'可达' if value else '不可达'}，"
              f"web_search {'给得出' if value else '不再递给模型'}")


def _loop() -> None:
    while True:
        _refresh()
        time.sleep(REFRESH_SECONDS)


def _ensure_probe_running() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="search-source-probe")
    _thread.start()


def search_reachable() -> bool:
    """当前是否够得着搜索源（缓存值，后台每 5 分钟刷一次）。"""
    _ensure_probe_running()
    with _lock:
        if _state["value"] is None:
            return True
        return bool(_state["value"])


def start_probe() -> None:
    """后端一起来就把线程拉起，别等第一个用户请求。

    第一轮探测比想象的慢得多：`socket.create_connection` 的 timeout 只管 connect()，
    前面的 getaddrinfo 不受它约束，DNS 被黑洞时单个域名就能挂十几秒（实测 7 秒还没出
    结果）。这段时间 `search_reachable()` 按"通"处理，也就是模型会被递一个大概率失败
    的 web_search——早一分钟开始测，这段误开窗口就短一分钟。
    """
    _ensure_probe_running()

# Docker 那一维不在这里：`sandbox` 实例已经由 builtin_tools 在导入期建好并持有
# client，再 new 一个 SandboxManager 等于每次检查都重连一遍 docker 守护进程，
# 还会把"沙箱停用"那行警告重复打印。可用性一律读那个唯一实例。
