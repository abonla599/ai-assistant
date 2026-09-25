"""PWA 前端托管：让手机浏览器直接访问后端即可使用，无需单独的前端服务。

静态资源与 API 同源，因此前端一律使用相对路径调用 /v1/*，不再需要把
服务器地址硬编码进客户端——这正是此前 127.0.0.1 写法让手机端无法使用的根因。

缓存策略（2026-09-22 改）：外壳一次开门要发 11 个请求，每一个此前都必须完整回源
一趟。量过的数是源站 2~18ms、走隧道单趟 ttfb 300~430ms——慢的不是应用，是趟数。
所以带版本号的资源给一年 immutable，不带版本号的仍旧 no-cache。
"""
import os
import re
import sys
import threading
import time
import zlib
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse as StarletteFileResponse

from app.core import releases
from app.core.auth_router import _client_ip

# APK 的 MIME 只写这一次；浏览器认的是它 + Content-Disposition，两样缺一就变成
# "下载已完成，但点开后系统问这是什么文件"。
APK_MEDIA_TYPE = "application/vnd.android.package-archive"


def _static_dir() -> str:
    """定位静态目录，兼容 PyInstaller 打包后的解包路径。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "app", "web", "static")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


STATIC_DIR = _static_dir()


def _admin_dir() -> str:
    """管理员页的位置。与 PWA 同层但分目录：它不属于聊天前端，
    不该被 /app 那份 service worker 的作用域覆盖。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "app", "web", "admin")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin")


ADMIN_DIR = _admin_dir()


# 一年 immutable。它之所以敢用，靠的是下面那个水印：文件一变水印就变、URL 就变。
IMMUTABLE = "public, max-age=31536000, immutable"

# HTML 只允许晚 30 秒。这是"重复打开"剩下最后那一趟回源的价钱：30 秒内可能拿到旧
# 页面配新脚本，所以页面自己带构建号去对一次、对不上就重载（见 static/index.html 里
# 的 window.__ASSETS__ 与 app.js 的 staleBuild）。测试里按这个数字钉，谁把它调大就红。
PAGE_CACHE = "public, max-age=30"

# 哪些引用要跟着水印走：按扩展名认，不另列清单——加一个新资源不需要想起来改第二处。
_REF = re.compile(r'\b(?:src|href)="[^"?]*\.(?:css|js|png|jpe?g|svg|webmanifest)"')

_TOKEN = ""


def _newest_mtime() -> float:
    newest = 0.0
    for root in (STATIC_DIR, ADMIN_DIR, SITE_DIR):
        for base, _, files in os.walk(root):
            for name in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, name)))
                except OSError:      # 正好有人在替换文件：少看一个不丢正确性
                    continue
    return newest


def asset_token() -> str:
    """当下这一版静态资源的水印 = 三份静态目录里最新的修改时间。

    算一次就够：这些文件只随构建变化。每次请求重扫一遍目录，等于把拖慢首屏的那个瓶颈
    换个地方再放一遍。测试要换水印时改 `_TOKEN`——判据都从这里取，不存在第二份。
    """
    global _TOKEN
    if not _TOKEN:
        _TOKEN = f"{_newest_mtime():.0f}"
    return _TOKEN


def _query_token(scope) -> str:
    for pair in (scope.get("query_string") or b"").split(b"&"):
        if pair.startswith(b"v="):
            return pair[2:].decode("utf-8", "replace")
    return ""


def stamp_assets(html: str) -> str:
    """把 HTML 里对本地资源的引用换成带水印的那一个 URL。

    `sw.js` 刻意跳过：service worker 的注册地址是写死的那一个，给它换 URL 等于每次
    注册一个新 worker，而旧的那个永远等不到更新。
    已经带 `?` 的引用不匹配，所以这个替换是幂等的——重复盖不会叠出 `?v=1?v=2`。
    """
    token = asset_token()

    def one(match):
        whole = match.group(0)
        attr, _, value = whole.partition('="')
        path = value[:-1]
        if path.rsplit("/", 1)[-1] == "sw.js":
            return whole
        return f'{attr}="{path}?v={token}"'

    return _REF.sub(one, html)


def rewritten_html(source: StarletteFileResponse, token: str,
                   if_none_match: str = "") -> Response:
    """HTML（以及要盖水印的 sw.js）出门前的那一次改写，连带把条件请求保住。

    HTML 允许缓存 `PAGE_CACHE` 这么久：它是"重复打开"这条路上剩下的最后那一趟回源。
    代价要说明白——这几十秒里可能拿到旧 HTML 配新 JS，界面会缺元素、点不动。所以同
    一次改写把 `__ASSET_TOKEN__` 也填进页面（`window.__ASSETS__`），app.js 拿它跟服务
    端当下那一版对一次，对不上就自己重载一次（只重载一次，见 app.js 的 `staleBuild`）：
    "改版后有一小会儿是坏的"从等人刷新变成自己好回来。
    `sw.js` 继续 no-cache：它是注册的入口，晚一步发现新版就等于再等一次开页面。
    自己算 ETag 是为了条件请求：整页 HTML 三十多 KB，丢了它每次导航都全量重发。
    """
    with open(source.path, "rb") as fh:
        raw = fh.read().decode("utf-8")
    is_page = source.path.endswith(".html")
    body = stamp_assets(raw).replace("__ASSET_TOKEN__", token).encode("utf-8")
    etag = f'"st-{zlib.crc32(body):08x}-{len(body):x}"'
    cache = PAGE_CACHE if is_page else "no-cache"
    if etag in if_none_match:
        return site_headers(Response(status_code=304, headers={"ETag": etag}), cache=cache)
    return site_headers(Response(content=body, media_type=source.media_type,
                                 headers={"ETag": etag}), cache=cache)


def site_headers(response, cache: str = "no-cache"):
    """全站响应共用的一组头，字面量只这一份：/app、/admin、/site 的静态资源走
    RevalidatingStaticFiles，官网 index.html 走 FileResponse，两头都收口在这里。

    不抽出来的话就有第二份缓存字面量，改一份漏一份——和 _PROTECTED_PREFIXES
    在测试里"不抄第二份清单"是同一个道理。

    调用方传进来的 `cache` 只有三种取值，每一种对应一笔说得出名字的取舍：
    - `IMMUTABLE`：URL 里已经写明是哪一版的资源（带当下水印）；
    - `PAGE_CACHE`：HTML，允许晚 30 秒，且页面带着自己的水印去对账、不对就自己重载；
    - `"no-cache"`：裸地址与旧水印的资源、以及 `sw.js`。宁可多问一趟也不把人钉在一份
      旧文件上——旧 URL 一旦被允许长缓存，就直接复刻 2026-09-17 那次"改了没生效"。
    """
    response.headers["Cache-Control"] = cache
    # 全站零 iframe（2026-09-19 grep 确认），所以这条不会碰坏任何东西；
    # 它挡的是"WebView 里 @JavascriptInterface 会挂到每个 frame"这条路。
    response.headers["Content-Security-Policy"] = "frame-src 'none'; object-src 'none'"
    return response


class RevalidatingStaticFiles(StaticFiles):
    """带当下水印的资源 → 一年 immutable；HTML → 30 秒；裸地址、旧水印、sw.js → no-cache。

    HTML 与 sw.js 出门前还要盖一次水印（见 `stamp_assets`），所以"页面引用的资源是
    上一版"这件事不会悄悄发生：引用和它指向的文件由同一个水印绑在一起，而页面带着
    自己的水印去对账（`window.__ASSETS__`）。

    这一套换掉的是一条更贵的旧规则：源站原先对 /app 与 /admin 完全不表态，缓存策略由
    别人代填（Cloudflare 给 .css/.js 注入 max-age=14400），2026-09-17 重建重启之后
    公网拿到的仍是 80 分钟前那份 `style.css`（`cf-cache-status: HIT`），界面改版在
    用户那边成了"改了没生效"。当天的解法是一律 no-cache——正确，但一次开门那 11 个
    请求每一个都要完整回源一趟，实测单趟 ttfb 300~430ms，代价最后落在首屏上。
    """

    async def get_response(self, path: str, scope):
        # 这一趟请求的东西一律走局部变量：一个 StaticFiles 实例服务所有并发请求，
        # 把 If-None-Match 存在 self 上就是"下一个请求看见上一个请求的头"。
        token = _query_token(scope)
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers") or ()
                         if k == b"if-none-match"), "")
        response = await super().get_response(path, scope)
        target = getattr(response, "path", "") or ""
        if isinstance(response, StarletteFileResponse) and target.endswith((".html", "sw.js")):
            return rewritten_html(response, token or asset_token(), incoming)
        return site_headers(response, cache=IMMUTABLE if token == asset_token() else "no-cache")


def mount_pwa(app: FastAPI) -> None:
    """把 PWA 挂到 /app。

    必须直接 mount 到 app 上：include_router 无法携带子 Mount 路由。
    html=True 使 /app/ 直接返回 index.html；service worker 与页面同目录，
    作用域自然收敛在 /app 下，不会拦截 /v1/* 接口请求。
    """
    app.mount("/app", RevalidatingStaticFiles(directory=STATIC_DIR, html=True), name="pwa")


def mount_admin(app: FastAPI) -> None:
    """把管理员页挂到 /admin。

    这个地址是公开的——有意为之：页面是个不含任何数据的空壳，用户数据只能
    经 /v1/admin/* 那套 require_admin 接口取到。鉴权中间件的保护前缀只有
    /v1/、/docs 等，所以这里不需要动 PUBLIC_PATHS。
    """
    app.mount("/admin", RevalidatingStaticFiles(directory=ADMIN_DIR, html=True), name="admin")


def _site_dir() -> str:
    """官网文件的位置。和 PWA 同层、分目录：它是给人看的一页,不是聊天前端,
    不该被 /app 那份 service worker 的作用域覆盖。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "app", "web", "site")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")


SITE_DIR = _site_dir()


# ---------- 「安卓版」那颗按钮的闸门（审查 #8，2026-09-23） ----------
# 免鉴权 + 一次点击最多搬 16MB 过内存、还要替来客朝 GitHub 跑一趟——原先没有任何
# 并发/频率闸门：一个人写个循环就能把出口带宽和内存按住，全站陪着他卡。
# 两把闸，各挡一种形状：
# ① 按来源 IP 限频：一小时 3 次够真人"换了手机再下一次"，脚本则要每 3 次换一枚真 IP；
#    来源取 auth_router._client_ip 那一个口径（必经 Cloudflare 时才认 CF-Connecting-IP），
#    不在此处重写第二份取 IP 的逻辑——两边一漂移，闸门就挡错人。
# ② 单飞行槽位：同一时刻最多一个人真正在代取。槽位被占**不排队**——排队等于把
#    攻击者的积压搬进线程池，那正是这笔 DoS 的另一半；拿不到槽就退回发布页，
#    与"取不到"同一个退路，最坏情况不比以前差。
# 只有真开了代取才扣格子：GitHub 挂了不该把点按钮的真人锁在门外。
APK_WINDOW_SECONDS = 3600
APK_MAX_PER_SOURCE = 3
MAX_TRACKED_APK_SOURCES = 4096
_APK_DOWNLOADS = defaultdict(list)
_APK_SLOTS = threading.Semaphore(1)


def _apk_throttled(ip: str) -> int:
    """还让不让这个来源开代取；让则返回 0，否则返回 Retry-After 秒数。"""
    now = time.monotonic()
    recent = [t for t in _APK_DOWNLOADS[ip] if now - t < APK_WINDOW_SECONDS]
    _APK_DOWNLOADS[ip] = recent
    if len(_APK_DOWNLOADS) > MAX_TRACKED_APK_SOURCES:
        for key in [k for k, v in _APK_DOWNLOADS.items() if not v]:
            _APK_DOWNLOADS.pop(key, None)
    if len(recent) >= APK_MAX_PER_SOURCE:
        return max(1, int(APK_WINDOW_SECONDS - (now - min(recent))) + 1)
    return 0


def install_site(app: FastAPI) -> None:
    """官网:`GET /` 出 index.html,静态资源挂 `/site`。

    为什么不是 app.mount("/", ...)：根 Mount 的 .path 是空串,而它会部分匹配一切
    路径,让 Starlette 的 redirect_slashes 不再运行——/health/、/docs/ 一起变 404,
    未匹配的 POST 从 404 变 405。backend/tests/test_auth_endpoints.py:296 钉的正是
    尾斜杠语义,那是安全测试,不该为一个落地页付账。
    """
    @app.get("/", include_in_schema=False)
    async def site_index():
        # 和 /app、/admin 的 HTML 同一个出口：先盖水印再出门，且这一页自己不缓存。
        return rewritten_html(FileResponse(os.path.join(SITE_DIR, "index.html")), asset_token())

    # 这条必须注册在 /site 那个 Mount **之前**：Mount 是按前缀匹配的，排在后面的
    # 精确路由永远轮不到——症状不是报错，是"点了安卓版 404"。
    @app.get("/site/android.apk", include_in_schema=False)
    def site_android_apk(request: Request):
        """官网那颗「安卓版」：服务端替访问者把这一版的 APK 取回来，一次点击直接落盘。

        为什么不 302 到 GitHub：那正是这颗按钮原本把人丢去的地方。取不到就退回发布页
        ——那是本次改动之前的行为，所以最坏情况不比以前差；但绝不回 200 空文件，
        那在手机上长成"下载完成了，点开却没反应"。

        必须是**同步 def**：这条要朝 GitHub 搬一百来 KB 的字节，写成 async 就是占着
        事件循环干活（判据在 tests/test_event_loop_not_blocked.py 的名单里）。
        reason 一律打进日志：EXE 是隐藏窗口起的，不打印就只剩人猜是哪一层坏了。

        2026-09-23（审查 #8）起带两道闸：按来源限频 + 单飞行代取，见
        _APK_DOWNLOADS 上面那段。退路不变：拿不到真字节一律 302 发布页或 429，
        绝不回 200 空文件。
        """
        plan, why = releases.download_plan()
        if not plan:
            print(f"[site] 代取 APK 失败，退回发布页：{why}", flush=True)
            return RedirectResponse(releases.RELEASES_PAGE, status_code=302)
        ip = _client_ip(request)
        if (retry_after := _apk_throttled(ip)):
            raise HTTPException(status_code=429, detail="下载尝试过于频繁，请稍后再试",
                                headers={"Retry-After": str(retry_after)})
        if not _APK_SLOTS.acquire(blocking=False):
            print("[site] 代取 APK 已有他人在途，退回发布页", flush=True)
            return RedirectResponse(releases.RELEASES_PAGE, status_code=302)
        try:
            _APK_DOWNLOADS[ip].append(time.monotonic())
            data, why = releases.fetch_asset(plan["url"])
        finally:
            _APK_SLOTS.release()
        if data is None:
            print(f"[site] 代取 APK 失败，退回发布页：{why}", flush=True)
            return RedirectResponse(releases.RELEASES_PAGE, status_code=302)
        return Response(content=data, media_type=APK_MEDIA_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{plan["name"]}"',
                                 # 代理的是"最新那一版"，而这份快照 10 分钟才换一次；
                                 # 缓存这条响应就等于让下一个人下到上一版。
                                 "Cache-Control": "no-cache",
                                 "X-Content-Type-Options": "nosniff"})

    app.mount("/site", RevalidatingStaticFiles(directory=SITE_DIR), name="site")
