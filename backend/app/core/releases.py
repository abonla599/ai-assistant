"""发布页探针：网页上那张「发现版本更新」卡片的数据来源。

为什么在服务端拉 GitHub，而不是让页面自己 fetch：

1. 前端有一条硬锁不许出现绝对 URL（同源纪律），把 `api.github.com` 写进 app.js 就是破它；
2. 大陆直连 GitHub 时好时坏，让**一台机器**去扛比每台手机各自扛好；
3. 一次拉取全员共享（缓存 10 分钟），不吃 GitHub 匿名 API 按 IP 的 60 次/小时限流。

代价说清楚：服务端多一条出站依赖。GitHub 挂了就是**不弹**——不弹"已是最新"，
也不在界面上转圈报错，因为这张卡片唯一的责任是"确实有新版时提一句"。

一条与安全有关的取舍：回来的 `url` 会被点给系统去下载/安装，所以这条请求走
`core/tls.py` 那一份**系统证书库**的上下文，而不是 `ssl` 的默认证书包——本机装了
带 HTTPS 扫描的杀软时，默认包会验不过（524 那次的同源根因），而"验不过就退回不验"
等于把发布地址交给一个能中间人的人。验不过就报错、就不弹。
"""
import json
import threading
import time
import urllib.request

from app.core.tls import system_ssl_context

# 全后端唯一一处 GitHub 地址（test_release_probe.py 数着它）。
LATEST_URL = "https://api.github.com/repos/abonla599/ai-assistant/releases/latest"
USER_AGENT = "ai-assistant-release-probe"
TIMEOUT_SECONDS = 5.0
CACHE_SECONDS = 600
MAX_BYTES = 256 * 1024

_lock = threading.Lock()
_payload = None                 # 上一次**成功**拉到的那份
_fetched_at = 0.0               # monotonic；失败也推进它，见 _refresh 的注释


def _numeric(segments):
    """把 "0.16" 拆成 [0, 16]。任何一段不是纯数字就返回 None（调用方判不认识）。"""
    out = []
    for part in segments.split("."):
        part = part.strip()
        if not part.isdigit():
            return None
        out.append(int(part))
    return out


def compare(a: str, b: str) -> int:
    """与壳里 `ReleasePlan.compare` 同一套规则：按点分段比数值，短的那边补 0。

    两份实现跨语言，漂移的判据在 `test_release_probe.py`：那张用例表同时喂给 Java 的
    单测和这里，两边算得不一样就红。
    """
    left, right = _numeric(a), _numeric(b)
    if left is None or right is None:
        raise ValueError(f"版本形状不认识：{a!r} / {b!r}")
    n = max(len(left), len(right))
    for i in range(n):
        l = left[i] if i < len(left) else 0
        r = right[i] if i < len(right) else 0
        if l != r:
            return -1 if l < r else 1
    return 0


def normalize_version(text) -> str:
    """`v0.16` 与 `0.16` 是同一个版本：剥掉前导的 v，两端空白不算。"""
    value = str(text or "").strip()
    return value[1:] if value[:1] in ("v", "V") else value


def _pick_asset(body: dict, version: str):
    """只取名字**精确等于** `ai-assistant-<version>.apk` 的那一个资产。

    与壳里 `ReleasePlan.pickAsset` 同一个理由：一次发布可以同时挂着 mapping.txt、
    别的平台的产物或上一次误传的文件，而这里挑中的东西是要弹给人去安装的。
    名字对上版本号顺带钉住了"这个包就是这一版"。
    """
    want = f"ai-assistant-{version}.apk"
    for asset in (body.get("assets") or []):
        if isinstance(asset, dict) and asset.get("name") == want:
            return asset
    return None


def _fetch():
    """拉一次发布页，返回 (payload | None, reason)。这个函数**不抛**。"""
    request = urllib.request.Request(
        LATEST_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                    context=system_ssl_context()) as response:
            body = json.loads(response.read(MAX_BYTES).decode("utf-8"))
    except Exception as e:                      # 超时/DNS/TLS/非 2xx/不是 JSON，全是一条 reason
        return None, f"拉取发布页失败：{type(e).__name__}"
    if not isinstance(body, dict):
        return None, "发布页回来的不是对象"
    version = normalize_version(body.get("tag_name"))
    if not version:
        return None, "最新那条发布没有 tag_name"
    asset = _pick_asset(body, version)
    return {
        "version": version,
        "url": str(body.get("html_url") or ""),
        "asset_name": (asset or {}).get("name") or "",
        "asset_url": (asset or {}).get("browser_download_url") or "",
        "size": int((asset or {}).get("size") or 0),
    }, ""


def probe(have: str = None) -> dict:
    """这张卡片要问的全部：最新是哪版、比手上这版新吗、去哪儿下。"""
    global _payload, _fetched_at
    now = time.monotonic()
    with _lock:
        stale = (now - _fetched_at) >= CACHE_SECONDS
    reason = ""
    if stale:
        payload, why = _fetch()
        with _lock:
            # 失败也把时间推进到下一轮：不然每个打开 App 的人都替 GitHub 挡一次枪。
            _fetched_at = time.monotonic()
            if payload is not None:
                _payload = payload
        if why:
            reason = why
    with _lock:
        snapshot = dict(_payload) if _payload else None

    out = {"ok": bool(snapshot), "latest": (snapshot or {}).get("version", ""),
           "url": (snapshot or {}).get("url", ""),
           "asset_name": (snapshot or {}).get("asset_name", ""),
           "size": (snapshot or {}).get("size", 0),
           "have": normalize_version(have), "has_update": None, "reason": reason}
    if not snapshot:
        out["reason"] = reason or "还没有一次成功过的发布页读取"
        return out
    if not out["have"]:
        out["reason"] = "没告诉我现在装的是哪版，无法判断有没有更新"
        return out
    try:
        out["has_update"] = compare(out["have"], snapshot["version"]) < 0
    except ValueError as e:
        out["reason"] = str(e)
    return out


def reset_for_tests() -> None:
    """把缓存清空——测试用它等价于"换一台刚起来的机器"。"""
    global _payload, _fetched_at
    with _lock:
        _payload = None
        _fetched_at = 0.0
