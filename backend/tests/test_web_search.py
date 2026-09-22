"""搜索源层：把一页搜索结果 HTML 变成模型能读的三件套。

这一层的存在理由是"可测"。CI 没有公网、也不该有（搜索结果每天都在变，拿真公网
当回归锁等于一条永远会莫名变红的锁——原来那条 @pytest.mark.skip 就是这么来的），
所以解析必须能拿一份**从真页面裁下来的** HTML 离线跑。

夹具刻意保留了 `<li class="b_algo">` 里那串 `<link rel=stylesheet>` 与
`<div elementtiming=…>` 垃圾：如果把夹具洗成干净的 `<li><h2>…` ，那么一条
"假设 h2 紧跟在 li 后面"的实现也能全绿，而它上线第一天就会失效。
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.web_search import (USER_AGENT, parse_results,  # noqa: E402
                                  search)

# 2026-09-22 从 cn.bing.com/search 的真实响应里手工裁剪（三条块，含广告块与
# 结构化答案块各一，用来钉住"只有 b_algo 才算结果"）。URL 里的跟踪参数已删。
SAMPLE = """<html><body><ol id="b_results">
<li class="b_ans">某条结构化答案，不该被当成有机结果</li>
<li class="b_algo" data-id iid=SERP.5331><link rel="stylesheet" href="/rp/a.css" type="text/css"/><link rel="stylesheet" href="/rp/b.css" type="text/css"/><h2 class=""><a target="_blank" target="_blank" href="https://baike.baidu.com/item/%E8%A5%BF%E5%AE%89%E5%B8%82/1002501" h="ID=SERP,5127.2"><strong>西安</strong>市_百度百科</a></h2><div class="b_caption"><p class="b_lineclamp2" data-rslinkclamp-iid="">截至2025年末，西安市常住人口1323.63万人。 西安市有3100多年的建城史和1100多年的国都史&hellip;</p></div><div elementtiming=frp.MiddleOfPage aria-hidden="true" style="pointer-events:none"> &nbsp; </div></li>
<li class="b_ad"><h2><a href="https://ad.example/promo">这是投放的广告</a></h2><p>广告摘要</p></li>
<li class="b_algo"><h2><a href="https://zhuanlan.zhihu.com/p/693928008">强烈推荐20个去<strong>西安</strong>必打卡的景点</a></h2><div class="b_caption"><p class="b_lineclamp2">2024年4月26日&ensp;&#0183;&ensp;原来，西安说的&ldquo;给我一天，还你千年&rdquo;，实非诳语。</p></div></li>
</ol></body></html>"""


def test_parse_results_takes_title_url_and_snippet_from_each_organic_block():
    """一个 `b_algo` 块 → 一条 {title, url, snippet}，顺序保持页面的顺序。

    标题里的 `<strong>` 是 Bing 给命中词加的标记，不是文字的一部分；摘要是实体
    引用（`&hellip;` `&ensp;` `&#0183;`）与标签混在一层里，必须还原成能读的句子。
    """
    rows = parse_results(SAMPLE)
    assert len(rows) == 2, f"应该只认出 2 条有机结果，实际 {len(rows)}：{rows}"
    assert rows[0]["url"] == "https://baike.baidu.com/item/%E8%A5%BF%E5%AE%89%E5%B8%82/1002501"
    assert rows[0]["title"] == "西安市_百度百科", rows[0]
    assert rows[0]["snippet"].startswith("截至2025年末")
    assert "…" in rows[0]["snippet"] and "b_lineclamp" not in rows[0]["snippet"]
    assert rows[1]["title"] == "强烈推荐20个去西安必打卡的景点", rows[1]
    assert "&" not in rows[1]["snippet"] and "·" in rows[1]["snippet"], \
        f"实体没还原或把正文吃掉了：{rows[1]['snippet']!r}"


def _page(n: int) -> str:
    """造一页有 n 条有机结果的页面（形状照真实响应，含块内的那串 link 垃圾）。"""
    block = ('<li class="b_algo"><link rel="stylesheet" href="/rp/a.css"/>'
             '<h2><a href="https://ex.example/{i}">标题 {i}</a></h2>'
             '<div class="b_caption"><p>摘要 {i}</p></div></li>')
    return "<html><body><ol>" + "".join(block.format(i=i) for i in range(n)) + "</ol></body></html>"


class _ClientSpy:
    """替 httpx.Client：记下 prod 真的传了哪些 kwargs，同时让请求照原样走 MockTransport。

    为什么非要记 kwargs：超时、跟不跟跳转、验不验证证书——这三件事全在构造参数上，
    不出现在请求里，用 MockTransport 的 handler 永远看不到。而它们正是本仓为 524 和
    "验不过就退回不验" 各付过一次账的地方。
    """

    def __init__(self, handler):
        self.handler = handler
        self.kwargs = {}
        self._real = httpx.Client

    def __call__(self, **kwargs):
        self.kwargs.update(kwargs)
        inner = dict(kwargs)
        inner["transport"] = httpx.MockTransport(self.handler)
        return self._real(**inner)


def _install(monkeypatch, handler):
    spy = _ClientSpy(handler)
    monkeypatch.setattr(httpx, "Client", spy)
    return spy


def test_search_sends_the_query_as_a_parameter_and_returns_parsed_rows(monkeypatch):
    """查询必须走参数，不能拼进 URL——拼字符串迟早把某个人的一句话变成另一个路径段。"""
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        seen["q"] = httpx.QueryParams(request.url.query).get("q")
        seen["path"] = request.url.path
        return httpx.Response(200, text=SAMPLE)

    _install(monkeypatch, handler)
    rows = search("西安市 人口", max_results=2)
    assert seen["q"] == "西安市 人口", seen
    assert seen["host"] == "cn.bing.com", f"源不该由调用方决定：{seen}"
    assert [r["title"] for r in rows] == ["西安市_百度百科", "强烈推荐20个去西安必打卡的景点"]


def test_search_keeps_the_egress_rules_the_rest_of_the_repo_already_follows(monkeypatch):
    """出站四件套：有界超时、不跟跳转、系统信任锚且必须验主机名、UA 固定。

    - 不跟跳转：302 的去向是源站决定的，跟着跳等于把"打哪儿"交给对面；
    - verify 必须是 CERT_REQUIRED + check_hostname：写成"验不过就退回不验"是失败开放；
    - 超时必须有界：这条调用现在只会在 worker 线程里跑，但无界超时一样能挂满 5 个线程。
    """
    import ssl

    spy = _install(monkeypatch, lambda request: httpx.Response(200, text=SAMPLE))
    search("任意查询")
    assert spy.kwargs.get("follow_redirects") is False, spy.kwargs
    timeout = spy.kwargs.get("timeout")
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 8, f"超时没界住：{timeout}"
    ctx = spy.kwargs.get("verify")
    assert isinstance(ctx, ssl.SSLContext), f"没走系统信任锚：{type(ctx)}"
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True, \
        "证书校验被放宽了：那是失败开放"
    assert spy.kwargs.get("headers", {}).get("User-Agent") == USER_AGENT


def test_search_returns_no_rows_when_the_source_answers_badly(monkeypatch):
    """源站回 5xx/4xx：结果是空，不是异常。空会让可用性判定把它摘掉；异常只会把
    一句栈信息当成工具结果喂给模型。

    夹具是一页**解析得出来结果**的 503：如果实现忘了看状态码，它照样能解析出三条，
    这条才有牙。（拿一段乱码当 503 的正文，两种实现都回空，断言就是空的。）
    """
    _install(monkeypatch, lambda request: httpx.Response(503, text=_page(3)))
    assert search("任意查询") == []


def test_search_returns_no_rows_when_the_transport_dies(monkeypatch):
    """连接层面失败（这台机器的常态：DNS 会给出错的 IP）同样只回空列表。"""
    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install(monkeypatch, boom)
    assert search("任意查询") == []


def test_search_never_hands_the_model_more_rows_than_it_asked_for(monkeypatch):
    """`max_results` 是模型给的参数，必须夹住：它要 99 条我们就真去解析 99 条，
    然后一路塞进上下文预算。夹在源层这一个地方，比在每个调用方各记一遍强。"""
    _install(monkeypatch, lambda request: httpx.Response(200, text=_page(8)))
    assert len(search("q", max_results=99)) == 5, "上限没夹住"
    assert len(search("q", max_results=2)) == 2
    assert len(search("q", max_results=0)) == 1, "夹到 0 等于告诉模型「没结果」"


def test_search_refuses_an_oversized_page(monkeypatch):
    """响应体有上限，而且是**边读边判**：一页结果本来几百 KB，对面哪天回 200MB
    就该当失败，而不是先把它读完再判（那时内存已经花掉了）。

    夹具刻意是一页"能解析出结果"的超大页面：如果实现只是忘了上限、把整页交给解析器，
    结果照样非空，这条就会红。用一堆无意义字符撑大小的话，解析器返回空也"通过"，
    那条断言就成了空的。
    """
    big = _page(8) + "x" * 6_000_000
    _install(monkeypatch, lambda request: httpx.Response(200, text=big))
    assert search("任意查询") == [], "超大响应被照常解析了：上限没在读取路径上"
