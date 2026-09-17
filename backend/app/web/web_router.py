"""PWA 前端托管：让手机浏览器直接访问后端即可使用，无需单独的前端服务。

静态资源与 API 同源，因此前端一律使用相对路径调用 /v1/*，不再需要把
服务器地址硬编码进客户端——这正是此前 127.0.0.1 写法让手机端无法使用的根因。
"""
import os
import sys

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


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


class RevalidatingStaticFiles(StaticFiles):
    """静态资源一律 no-cache：每次使用前必须回源问一次，ETag 就是那次问价。

    源站原先对 /app 与 /admin 下的文件完全不表态，于是缓存策略由别人代填：
    Cloudflare 给 .css/.js 注入 max-age=14400，浏览器再按启发式各存一份。2026-09-17
    重建并重启后，公网 /app/style.css 拿到的仍是 80 分钟前那份旧的
    （cf-cache-status: HIT）——界面改版在用户那边就成了"改了没生效"。

    no-cache 不等于不缓存：命中 ETag 时源站回 304，只有一个头；离线也不受影响，
    service worker 那份缓存不归 HTTP 缓存管。
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
