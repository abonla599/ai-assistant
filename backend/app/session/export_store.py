"""一次性导出票据的存储，以及会话 Markdown 的渲染。

为什么需要这个模块：Android WebView 壳收不到前端 blob: URL 的下载回调，也不会
给下载请求带上 Authorization 头，于是壳里"导出对话"存不下任何文件。唯一走得通的
形状是一条**免登录、真实、一次性**的 HTTPS 链接：链接本身就是凭据——128 位随机
票据、5 分钟过期、兑换一次即作废（接线见 main.py 的
POST /v1/sessions/{id}/export-ticket 与 GET /v1/exports/{id}）。

票据表只在进程内，不落盘：进程重启等于所有未兑换的链接失效，这与"一次性短链"的
语义一致；反过来，落盘会让旧链接跨重启复活，那才是多出来的面。过期条目靠签发时
惰性剔除，不起后台线程——为一张几行的小表养一条常驻线程，换来的是每个测试进程
里多一个关不掉的幽灵。

渲染那半边与前端 app.js 的 exportCurrent()（titleOf + safeFilename + 同一种
拼接）逐字对齐：同一份导出规则现在服务端与手机端各有一份，这个注释就是漂移时
该被翻出来的那一页。
"""
import re
import secrets
import threading
import time
from urllib.parse import quote

# 链接的形状。签发端返回的 path、中间件放行的正则、路由模板，三处都从这里派生，
# 免得"换个前缀"要跨三个文件对暗号。
EXPORT_PATH_PREFIX = "/v1/exports/"
TICKET_TTL_SECONDS = 300
TICKET_BYTES = 16  # 128 位随机：不是自增 id，也不是可枚举的短码

# 票据形状由生成器自己算出来，不手抄长度：token_urlsafe(16) 今天恰好是 22 个
# base64url 字符，哪天改了 TICKET_BYTES，中间件那道门会跟着变，而不是悄悄把
# 新形状的链接挡在门外（401 长得像"端点不存在"，最难查的那种）。
TICKET_ID_CHARS = len(secrets.token_urlsafe(TICKET_BYTES))
TICKET_ID_RE = re.compile(rf"[A-Za-z0-9_-]{{{TICKET_ID_CHARS}}}")

# 中间件放行用的完整路径正则：**整条**必须恰好是"前缀 + 一段票据形状"，
# 不是前缀匹配。/v1/exports/ 、/v1/exports/foo、两段式的 /v1/exports/x/y 都
# 不被它放行——免登录的是"兑换这一条"，不是这个目录。
TICKET_PATH_RE = re.compile(rf"^{re.escape(EXPORT_PATH_PREFIX)}{TICKET_ID_RE.pattern}$")


class ExportTicketStore:
    """进程内票据表：ticket_id -> (session_id, owner, expires_at)。

    clock 可注入，是这里唯一的测试缝："已过期"是 HTTP 测不出来的分支
    （总不能睡 300 秒），而它是这个模块的存在理由之一。用单调时钟而不是
    wall clock：改系统时间不该能续命或秒杀一张票据。
    """

    def __init__(self, ttl: float = TICKET_TTL_SECONDS, clock=time.monotonic):
        self._lock = threading.Lock()
        self._ttl = ttl
        self._clock = clock
        self._tickets = {}

    def issue(self, session_id: str, owner: str) -> str:
        """签一张新票据，顺手把已过期的剔掉（惰性清理，见模块 docstring）。"""
        ticket_id = secrets.token_urlsafe(TICKET_BYTES)
        now = self._clock()
        with self._lock:
            for stale in [t for t, (_, _, exp) in self._tickets.items() if exp <= now]:
                del self._tickets[stale]
            self._tickets[ticket_id] = (session_id, owner, now + self._ttl)
        return ticket_id

    def consume(self, ticket_id: str):
        """兑换。命中与否，条目都先离开表——一票一问答，第二次永远不会成交。

        "没这张票"与"过期了"都返回 None，调用方无从区分，路由层因此能说同一
        句话；这个不可区分本身就是防"链接是否存在过"探测的守卫。
        """
        with self._lock:
            record = self._tickets.pop(ticket_id, None)
        if record is None:
            return None
        session_id, owner, expires_at = record
        if self._clock() >= expires_at:
            return None
        return session_id, owner

    def pending_count(self) -> int:
        with self._lock:
            return len(self._tickets)


export_ticket_store = ExportTicketStore()


# ---------- Markdown 渲染（与前端 exportCurrent 逐字一致） ----------

def _title_of(text) -> str:
    """折叠空白取前 24 字；空则「新对话」。镜像 app.js 的 titleOf()。"""
    collapsed = re.sub(r"\s+", " ", (text or "").strip())
    return collapsed[:24] or "新对话"


def _safe_filename(text) -> str:
    """文件名里不能出现的字符换成空格。镜像 app.js 的 safeFilename()。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', " ", text or "").strip()
    return (cleaned or "对话")[:40]


def session_markdown(session: dict) -> str:
    """把会话渲染成前端"导出对话"同款 Markdown。

    拼接形状照抄 `[标题, ""] + 每条 "**角色**：\\n\\n内容\\n"` 再 join("\\n")，
    少一个换行两边产物就对不上。服务端存储本来就没有前端那种 transient 占位
    消息，所以这里不需要任何过滤——存着什么就导出什么。
    """
    messages = session.get("messages") or []
    first_user = next((m.get("content") for m in messages if m.get("role") == "user"),
                      None)
    blocks = ["# " + _safe_filename(_title_of(first_user)), ""]
    blocks += ["**{}**：\n\n{}\n".format(
        "我" if m.get("role") == "user" else "助手", m.get("content") or "")
        for m in messages]
    return "\n".join(blocks)


# 老下载的兜底名刻意固定为 ASCII：标题是用户可控文本，把"清洗过的标题"塞进
# 回退位等于再养一份清洗规则的第二个事实来源，而它省下的只是一眼文件名。
ASCII_FALLBACK_NAME = "chat.md"


def content_disposition(title) -> str:
    """attachment 的 Content-Disposition 值。

    标题**绝不原样进响应头**：它可能带着 CR/LF 或引号，那是要把响应头注出
    第二行的形状。中文走 RFC 5987 的 filename*=UTF-8''<百分号编码>——quote 的
    safe="" 保证产物只剩可见 ASCII，结构上就塞不进换行；老客户端的回退位用固定
    ASCII 名。现代 WebView（Chromium）按 5987 优先取 filename*，拿到真标题。
    """
    name = _safe_filename(title)
    return (f"attachment; filename=\"{ASCII_FALLBACK_NAME}\"; "
            f"filename*=UTF-8''{quote(f'{name}.md', safe='')}")
