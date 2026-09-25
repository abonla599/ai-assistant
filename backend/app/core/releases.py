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
import re
import threading
import time
import urllib.parse
import urllib.request

import httpx

from app.core.tls import system_ssl_context

# 全后端唯一两处 GitHub 地址（`api.` 那条由 test_release_probe.py 数着）：
# 一条问"最新是哪一版"，一条是问不到之后的退路——点了按钮的人总得拿到文件。
LATEST_URL = "https://api.github.com/repos/abonla599/ai-assistant/releases/latest"
RELEASES_PAGE = "https://github.com/abonla599/ai-assistant/releases/latest"
USER_AGENT = "ai-assistant-release-probe"
TIMEOUT_SECONDS = 5.0
CACHE_SECONDS = 600
MAX_BYTES = 256 * 1024

# ---------- 官网那颗「安卓版」按钮要代取的字节 ----------
# 白名单是两条，不是一条，而且这条是量出来的不是记住的：`browser_download_url` 在
# github.com 上，它 302 去的是 **release-assets.githubusercontent.com**（2026-09-22 真跑
# 代取时第一版只放了 objects.*，结果每一次都卡在"目标主机不在白名单里"、静默退回
# 发布页——功能上线即失效）。objects.* 留着是因为别的资产形状确实会走它；
# 每一跳都重新过这个判断（fetch_asset 里 follow_redirects 是关着的），否则
# "第一跳合法、第二跳随你"，那正是这条代理存在的理由所反对的事。
DOWNLOAD_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com"})
ASSET_TIMEOUT_SECONDS = 20.0
APK_MAX_BYTES = 16 * 1024 * 1024
MAX_HOPS = 3
# 这个形状同时保证它放进 Content-Disposition 是安全的：没有 CR/LF、没有引号、没有分号。
_ASSET_NAME_RE = re.compile(r"^ai-assistant-[0-9][0-9A-Za-z.\-]*\.apk$")

_lock = threading.Lock()
_payload = None                 # 上一次**成功**拉到的那份
# monotonic 读数；None = 这一世还没试过。**这里不能用 0.0 当"该拉了"的哨兵**：
# monotonic 是从开机算的，刚起来的机器上 now 本身就小于 CACHE_SECONDS，
# 于是 `now - 0.0 >= 600` 为假——"该拉一次"被判成"缓存还新"，而 `_payload` 是空的。
# 症状是每次重启后的头 10 分钟里那张卡片永远不弹，且一句错都不报。
# （不是假想：CI 就是这么抓到的，runner 是一台刚开的虚拟机。）
_fetched_at = None


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
        # 原样的那条发布 JSON。壳的「检查更新」走 /v1/update/info 透传它——判断逻辑
        # （三态、资产名、URL 白名单）整个活在壳里且被 JVM 台架钉着，服务端只做
        # "一台机器出网 + 缓存"这一段，不另起一份判断的第二真相。
        "raw": body,
    }, ""


# 发版工作流写在 Release 正文末尾的那一行：`APK-SHA256: <64 位小写十六进制>`。
# 摘要在构建机上、签完包之后算——所以它验的是"装进手机的那串字节就是发布的那一串"，
# 服务端与下载通道都只是过手的人。锚定整行、大小写敏感：正文里的散文不许凑巧长成
# 一条校验值。
APK_SHA256_RE = re.compile(r"^APK-SHA256: ([0-9a-f]{64})$", re.M)


def apk_sha256(body_text) -> str:
    """从 Release 正文里取那行校验值；没有（旧版发布、手改正文）就是空串。"""
    if not isinstance(body_text, str):
        return ""
    m = APK_SHA256_RE.search(body_text)
    return m.group(1) if m else ""


def latest_release_manifest():
    """壳「检查更新」要的那份 JSON：原样透传 + 顶层多一枚 `apk_sha256`。

    返回 (dict, reason)。拉不到时 (None, 理由)——调用方必须把这句理由原样带给人，
    而不是回一份"看起来没有更新"的空 JSON：那条三态纪律（读不出来 ≠ 已是最新）
    从壳里一路管到服务端这一层。
    """
    snapshot, reason = _snapshot()
    if not snapshot:
        return None, reason or "还没有一次成功过的发布页读取"
    raw = snapshot.get("raw")
    if not isinstance(raw, dict):
        return None, "发布快照里没有原样 JSON（内部状态坏了）"
    manifest = dict(raw)
    manifest["apk_sha256"] = apk_sha256(raw.get("body"))
    return manifest, ""


def _snapshot() -> tuple:
    """返回 (最新一次的发布快照 | None, 这次读取失败的理由)。

    缓存的**唯一**入口：卡片（probe）与官网代取（download_plan）都从这里读，
    所以"10 分钟内最多问 GitHub 一次"这条承诺只有一处实现，不会一边省、一边不省。
    """
    global _payload, _fetched_at
    now = time.monotonic()
    with _lock:
        stale = _fetched_at is None or (now - _fetched_at) >= CACHE_SECONDS
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
        return (dict(_payload) if _payload else None), reason


def probe(have: str = None) -> dict:
    """这张卡片要问的全部：最新是哪版、比手上这版新吗、去哪儿下。"""
    snapshot, reason = _snapshot()

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
    """把缓存清空——测试用它等价于"换一台刚起来的机器"。

    刚起来 = `_fetched_at` 是 None，不是 0.0：后者在 monotonic 还很小（真·刚开机、
    或 CI 的虚拟机）时会被判成"缓存还新"，那正是这条缓存要防的反面。
    """
    global _payload, _fetched_at
    with _lock:
        _payload = None
        _fetched_at = None


def _host_ok(url: str):
    """这一跳能不能替用户去取。返回 (可以, 理由)。"""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        return False, f"下载地址不是 https：{parts.scheme or '空'}"
    if parts.hostname not in DOWNLOAD_HOSTS:
        return False, f"目标主机不在白名单里：{parts.hostname}"
    return True, ""


def download_plan():
    """官网「安卓版」那颗按钮要的真东西：一个可以替用户去取的 APK 地址。

    返回 ({"url", "name", "size", "version"}, "") 或 (None, 一句理由)。这个返回值决定
    的是"陌生人的浏览器从我们这台服务器落下哪个字节流"，所以任何一项对不上都宁可拒：
    调用方拿不到 plan 就退回发布页，而不是硬编一个地址给人。

    资产名必须等于 `ai-assistant-<这一版>.apk`：一次发布可以同时挂着 mapping.txt、
    别的平台的产物或上一次误传的旧包（`_pick_asset` 同一个理由），而且这条正则顺带
    保证了它放进 Content-Disposition 是安全的——没有 CR/LF、没有引号、没有分号。
    """
    snapshot, reason = _snapshot()
    if not snapshot:
        return None, reason or "还没有一次成功过的发布页读取"
    version = snapshot.get("version") or ""
    name = snapshot.get("asset_name") or ""
    url = snapshot.get("asset_url") or ""
    try:
        size = int(snapshot.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if not _ASSET_NAME_RE.match(name):
        return None, f"资产名不是我们发布的那个形状：{name!r}"
    if name != f"ai-assistant-{version}.apk":
        return None, f"资产名与版本号对不上：{name!r} vs {version!r}"
    if not size or size > APK_MAX_BYTES:
        return None, f"这个包的大小不像一个 APK：{size}"
    ok, why = _host_ok(url)
    if not ok:
        return None, why
    return {"url": url, "name": name, "size": size, "version": version}, ""


def _open_asset():
    """取包用的客户端。跳转自己管（见 fetch_asset），所以这里必须关掉自动跟。"""
    return httpx.Client(verify=system_ssl_context(), timeout=ASSET_TIMEOUT_SECONDS,
                        follow_redirects=False, headers={"User-Agent": USER_AGENT})


def fetch_asset(url: str):
    """替用户把包取回来，返回 (字节 | None, 理由)。这个函数不抛。

    一次下载 = 一台机器替所有点按钮的人去 GitHub 跑一趟：出口只有这一个 IP，
    所以上面那条 10 分钟缓存省的是元数据，这里省不掉的是字节。
    """
    try:
        with _open_asset() as client:
            target = url
            for _ in range(MAX_HOPS):
                ok, why = _host_ok(target)
                if not ok:
                    return None, why
                with client.stream("GET", target) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location") or ""
                        if not location:
                            return None, "上游说要跳转却没给地方"
                        target = urllib.parse.urljoin(str(response.url), location)
                        continue
                    if response.status_code != 200:
                        return None, f"上游回 {response.status_code}"
                    chunks, total = [], 0
                    for part in response.iter_bytes():
                        total += len(part)
                        if total > APK_MAX_BYTES:
                            return None, f"下载超出上限：{total}+"
                        chunks.append(part)
                    return b"".join(chunks), ""
            return None, f"跳转次数超过 {MAX_HOPS} 跳"
    except Exception as e:
        return None, f"取包失败：{type(e).__name__}"
