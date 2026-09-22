"""每个请求留一行：什么时候到的、花了多久。

起因是 2026-09-22 那次「网页版快一分钟才进去」。当场能量到的只有"现在不慢"，而事后
翻 `data/backend.log` 连"那 60 秒里有没有请求到达"都问不出来——uvicorn 的访问日志
不带时间戳也不带耗时。这一行就是为了让下一次故障可定位：慢在哪一段，先要能分清
"请求根本没到"和"到了，是这里在等"。

刻意写成裸 ASGI 中间件而不是 `@app.middleware("http")`：后者是 BaseHTTPMiddleware，
会给每个响应加一层线程队列，而这个应用有 SSE 流式对话。它也不改任何响应，只做观察。
"""
import time
from datetime import datetime

# 超过这个毫秒数就单独打一个「慢」字。1 秒的根据：本机量过的静态外壳是 6~30ms，
# 走隧道是 0.4~0.6s，取两者之上、又远低于"人会以为坏了"的那个区间。
SLOW_MS = 1000

# 这两个端点的时长由对面决定：模型慢慢出字、从 GitHub 代取十几 MB 的安装包。
# 用同一个阈值标它们只会让警告天天响，最后没人看它——所以照记时长，但不挂「慢」。
LONG_LIVED = ("/v1/chat/stream", "/site/android.apk")


def describe(method: str, path: str, status: int, ms: float, when: datetime) -> str:
    """拼那一行。单独成函数是为了让格式本身可测（测试里按整行正则对）。

    超阈值把「⚠慢」挂在**末尾**而不是开头：行首留给时刻，`grep 慢` 与按时刻排序两种
    用法都要能用，而开头插一个字会让所有行错位。
    查询串一律不进：令牌一旦被写进 URL，这行就会把它抄进一个可能被贴出去的日志文件。
    """
    mark = " ⚠慢" if ms >= SLOW_MS and not path.startswith(LONG_LIVED) else ""
    return f"[{when:%H:%M:%S}] {method} {path} {status or '—'} {ms:.0f}ms{mark}"


class RequestTiming:
    """包在最外层的观察器：不改响应、不吞异常，只负责在请求走完时印一行。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":       # lifespan 与 websocket 原样放过
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = {"code": 0}              # 0 = 一个字节都没发出去（连接半路断了）

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # finally 而不是成功分支：崩掉与断连的那几次才是最该留下证据的。
            print(describe(scope.get("method", "?"), scope.get("path", "?"),
                           status["code"], (time.perf_counter() - started) * 1000,
                           datetime.now()))
