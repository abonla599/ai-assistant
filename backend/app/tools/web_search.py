"""联网搜索的源层：只这一处知道搜索结果页长什么样。

为什么是爬而不是官方 API：拍定于 2026-09-22，不注册任何账号、不产生按人计费的那本账。
代价写在下面，别让下一个读这段的人以为它免费又好：
- 源站自己的 `robots.txt` 在 `User-agent: *` 段里写着 `Disallow: /search`，这条用法
  是它明确不欢迎的；
- 页面结构一改，解析就静默变空。所以 tools/availability.py 的探测必须走**同一个
  search()**（探一次真查询），否则"工具在清单里"和"工具能给出结果"就变成两件事；
- 所有用户共用这台机器的一个出口 IP，量大首先被限流的是它。

只用标准库解析：`bs4` 本机没装，`lxml` 装着但没登记在 requirements.txt 里，
import 它会让 test_dependencies 那把锁失去意义（那条锁的存在理由就是"看着能用其实没登记"）。
"""
import html.parser
import re

import httpx

from app.core.tls import system_ssl_context

SEARCH_URL = "https://cn.bing.com/search"
# www.bing.com 会 302 到 cn.bing.com（实测），所以直接打后者，省一次跳转也少一个
# 允许被跳去的地方——follow_redirects 保持关着。
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT_SECONDS = 6.0
MAX_BYTES = 2 * 1024 * 1024        # 一页结果是几百 KB 量级；超了就当失败（照 releases.py 的口径）
MAX_RESULTS = 5                    # max_results 是模型给的参数，源层这一道夹住它
DEFAULT_RESULTS = 3

# 页面里 &ensp; &#0183; &nbsp; 这类排版空格会被原样还原成 U+2002 等字符，模型读着
# 占位、写进上下文浪费预算，一律并成普通空格。
_SPACES = re.compile("[\\s\\u00a0\\u2000-\\u200b\\u202f\\u205f\\u3000]+")


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


class _BlockParser(html.parser.HTMLParser):
    """从 `<li class="b_algo">` 块里取第一组 (h2 里的链接, b_caption 里的段落)。

    写成状态机而不是正则：块里真实存在的样子是 `<li …>` 后面先跟十几条
    `<link rel=stylesheet>`、中间还夹着 `<div elementtiming …>`，任何"h2 紧跟在 li 后面"
    这类假设都会在上线第一天失效（夹具里刻意留着这些垃圾就是为这条）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._in_block = False           # 是否在某个 b_algo 块内（块里没有嵌套 li）
        self._row = None
        self._in_h2 = self._in_a = self._in_p = False
        self._href = None
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            if "b_algo" in dict(attrs).get("class", "").split():
                self._in_block, self._row = True, {"title": "", "url": "", "snippet": ""}
            return
        if not self._in_block:
            return
        if tag == "h2" and not self._row["title"]:
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and not self._row["url"]:
            self._in_a, self._href = True, dict(attrs).get("href", "")
            self._parts = []
        elif tag == "p" and not self._row["snippet"]:
            self._in_p, self._parts = True, []

    def handle_endtag(self, tag):
        if not self._in_block:
            return
        if tag == "a" and self._in_a:
            self._in_a = False
            title = _clean("".join(self._parts))
            if title and self._href.startswith("http"):
                self._row["title"], self._row["url"] = title, self._href
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p" and self._in_p:
            self._in_p = False
            snippet = _clean("".join(self._parts))
            if snippet:
                self._row["snippet"] = snippet
        elif tag == "li":
            if self._row["title"]:
                self.results.append(self._row)
            self._in_block, self._row = False, None

    def handle_data(self, data):
        if self._in_a or self._in_p:
            self._parts.append(data)


def parse_results(html: str) -> list:
    """取出一页里的有机结果：[{title, url, snippet}]，保持页面顺序。

    认不出任何一条就返回空列表——**不猜**。空列表是 tools/availability.py 判定
    "这个源现在不好用"的唯一信号，替它编一条出来就会把一个坏源一直挂在清单里。
    """
    parser = _BlockParser()
    parser.feed(html)
    parser.close()
    return parser.results


def _clamp(max_results) -> int:
    """把模型传来的 max_results 夹进 [1, MAX_RESULTS]。

    模型张嘴要 50 条是常态；不夹的话一条回复能把上下文预算吃光（executor 那道 4000
    字是出口兜底，不该拿它当配额用）。要 0 或负数也不给空——"没结果"和"你不许有结果"
    是两件事，后者会把可用性判定骗成"源坏了"。
    """
    try:
        want = int(max_results)
    except (TypeError, ValueError):
        want = DEFAULT_RESULTS
    return min(max(want, 1), MAX_RESULTS)


def search(query: str, max_results: int = DEFAULT_RESULTS) -> list:
    """问一次搜索源。永远返回列表：非 200、超时、TLS 失败、页面太大、解析不出——全是 []。

    失败回空而不是抛：这条返回值一路喂给模型，抛出去的就是 `tools/executor.py` 拼的
    一句错误，模型会拿那句错误当"事实"继续编。空则由 availability 把工具摘掉。
    出站四件套（有界超时 / 不跟跳转 / 系统信任锚且验主机名 / 上限）的判据在
    tests/test_web_search.py——这一处是全站第二条自己发 HTTP 的地方，第一条是 releases.py。
    """
    want = _clamp(max_results)
    if not str(query or "").strip():
        return []
    try:
        with httpx.Client(verify=system_ssl_context(), timeout=TIMEOUT_SECONDS,
                          follow_redirects=False,
                          headers={"User-Agent": USER_AGENT,
                                   "Accept-Language": "zh-CN,zh;q=0.9"}) as client:
            with client.stream("GET", SEARCH_URL, params={"q": query}) as response:
                if response.status_code != 200:
                    return []
                chunks, total = [], 0
                for part in response.iter_bytes():
                    total += len(part)
                    if total > MAX_BYTES:
                        return []
                    chunks.append(part)
    except Exception:
        return []
    return parse_results(b"".join(chunks).decode("utf-8", "replace"))[:want]
