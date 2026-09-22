"""静态资源水印与缓存：一次开门为什么从 11 趟回源变成几乎不用回源。

判据全部从"服务端发出去的那份 HTML"里现读，不另列一份资源清单——列了就等于第二份
真相：加一个资源时人想起改 HTML 却忘了改清单，或者反过来，测试绿而线上漏盖章。
"""
import re

from fastapi.testclient import TestClient

from app.main import app
from app.web import web_router

client = TestClient(app)

# 与 web_router._REF 认的那一批扩展名对应。两边各写一次是有意的：这个测试要抓的正是
# "目录里冒出一个新类型，而盖章的规则不知道"。
STAMPED = (".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".webmanifest")
# 不盖章的本地文件：HTML 是被盖章那一批的出处（它自己只许缓存 30 秒），APK 是动态代取的，
# .md 是随包带出去的第三方许可说明（`static/NOTICE.md`），没有任何页面引用它。
UNSTAMPED_OK = (".html", ".apk", ".md")

PAGES = ("/app/", "/admin/", "/")


def _refs(html: str):
    return re.findall(r'\b(?:src|href)="([^"]*)"', html)


def _path_of(ref: str) -> str:
    return ref.split("?")[0].split("#")[0]


def _local_files_in(root: str):
    out = []
    for base, _, files in __import__("os").walk(root):
        out.extend(f for f in files)
    return out


def test_every_asset_the_page_points_at_carries_the_current_watermark():
    """三个页面的每一条资源引用都必须带着当下水印，一条不漏。

    放回哪种 bug 会红：`_REF` 少认一个扩展名、`stamp_assets` 整段不调用、或者
    `site_index` 绕过 `rewritten_html` 直接回 FileResponse——那时 `/site/site.css`
    会以裸地址出现，这里当场点名。
    """
    token = web_router.asset_token()
    for page in PAGES:
        refs = _refs(client.get(page).text)
        assert refs, f"{page} 里一条引用都没有，这条测试就空了"
        for ref in refs:
            path = _path_of(ref)
            if path.rsplit("/", 1)[-1] == "sw.js":
                continue                      # worker 的地址必须固定，见 web_router 注释
            if not path.endswith(STAMPED):
                continue
            assert ref == f"{path}?v={token}", f"{page} 引用没盖当下水印：{ref!r}"


def test_a_new_file_type_in_the_static_dirs_forces_a_decision():
    """静态目录里冒出 STAMPED 之外的类型时要红——它意味着有一种资源永远不会被盖章。

    这条是给"以后加字体/图标集"准备的：`_REF` 与 HTML 都不改的话，那个文件会退回
    每次回源，而这正是本文件要消灭的东西；宁可在这里挡下来让人显式表态。
    """
    for root in (web_router.STATIC_DIR, web_router.ADMIN_DIR, web_router.SITE_DIR):
        for name in _local_files_in(root):
            suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            assert suffix in STAMPED + UNSTAMPED_OK, \
                f"{root} 里出现了没被归类过的文件 {name}：要盖章就加进 _REF 与 STAMPED"


def test_versioned_assets_are_immutable_while_bare_and_stale_ones_revalidate():
    """带当下水印 → 一年 immutable；裸地址与旧水印 → no-cache。

    后一半是正确性的那一半：一个 URL 一旦被人存进书签、或被上一版 service worker
    预取过，它的地址就固定了。如果服务端对"任何带 ?v= 的请求"都回 immutable，
    旧 URL 会被钉死在一年——那正好复刻 2026-09-17 那次"改版没生效"。
    """
    token = web_router.asset_token()
    for path in ("/app/style.css", "/app/app.js", "/site/site.css", "/admin/admin.css"):
        bare = client.get(path)
        assert bare.status_code == 200, path
        assert bare.headers["cache-control"] == "no-cache", \
            f"{path} 不带水印却给了 {bare.headers['cache-control']!r}：旧 URL 会被钉住"
        assert bare.headers.get("etag"), f"{path} 没 ETag：no-cache 会退化成每次全量重传"

        fresh = client.get(path, params={"v": token})
        assert fresh.status_code == 200, path
        assert fresh.headers["cache-control"] == web_router.IMMUTABLE, \
            f"{path}?v={token} 仍要回源：首屏就还是十几个往返"

        stale = client.get(path, params={"v": "1970"})
        assert stale.headers["cache-control"] == "no-cache", \
            f"{path} 对旧水印也回 long cache：页面与资源会各停在一版"


def test_a_page_may_be_stale_for_at_most_the_agreed_window():
    """HTML 可以晚一步，但只能晚 30 秒——这个数字是拿"旧页面配新脚本"换来的。

    2026-09-22 定的那条边界：整页 HTML 是"重复打开"这条路上剩下的最后那一趟回源，
    给它正 max-age 才能省掉；代价是最坏情况下人拿到旧骨架跑新代码，症状是缺元素、
    点了没反应。窗口由页面自己兜住（下一条锁钉它在不在），而窗口大小按整串头钉：
    谁把 PAGE_CACHE 调大或者干脆改成 immutable，这里红，而不是等到线上偶发一次空白。
    """
    for page in PAGES:
        head = client.get(page).headers["cache-control"]
        m = re.fullmatch(r"public, max-age=(\d+)", head)
        assert m, f"{page} 的 HTML 缓存头不是说得出价钱的那一种: {head!r}"
        assert 0 < int(m.group(1)) <= 30, f"{page} 允许晚 {m.group(1)} 秒，超出谈定的窗口"
    # sw.js 反过来：它是"现在线上是哪一版"的那一个问处，缓存住就等于永远问不到新版。
    assert client.get("/app/sw.js").headers["cache-control"] == "no-cache", \
        "sw.js 被允许缓存：版本自愈那条路就断了"


def test_a_second_visit_to_the_page_answers_304_with_no_body():
    """过了那 30 秒还是要问一次，问价必须便宜：整页三十多 KB 全量重传比改动之前还慢。"""
    first = client.get("/app/")
    assert first.headers.get("etag"), "没有 ETag，条件请求无从谈起"
    second = client.get("/app/", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 304, f"带着 ETag 仍旧全量重发：{second.status_code}"
    assert not second.content, "304 却带了正文"
    assert second.headers["cache-control"] == web_router.PAGE_CACHE, \
        "304 上没带缓存头：客户端与边缘只能按自己的启发式猜这一页能留多久"


def test_the_page_declares_which_build_it_is():
    """页面必须自报构建号，而且要和服务端当下那一版、和资源 URL 上的水印是同一个值。

    这是"HTML 允许晚 30 秒"的那根保险销：app.js 拿 `window.__ASSETS__` 去问 sw.js
    现在是多少，对不上就自己跳一次。占位没被替换、这一行被删掉、或者填进去的是别的
    数——保险就悄悄没了，而症状只是"改版后偶尔有人点不动"。
    """
    token = web_router.asset_token()
    page = client.get("/app/").text
    assert f'window.__ASSETS__ = "{token}"' in page, \
        "页面没有自报构建号（占位没替换或那一句被删了）"
    assert "__ASSET_TOKEN__" not in page, "HTML 里的占位漏替换：填号的那条路只覆盖了 sw.js"
    assert f'src="app.js?v={token}"' in page, "构建号与资源水印不是同一个出处"


def test_the_watermark_moves_with_the_files_and_both_copies_agree(monkeypatch):
    """换一版：HTML 里的引用与 sw.js 里的常量必须一起换。

    两处不同步是本项目最贵的一类形状——worker 预取的是上一版的 URL，而页面引的是
    新 URL，于是离线回退悄悄指向旧文件（效果没了，也不报错）。
    """
    html = client.get("/app/").text
    served = client.get("/app/sw.js").text
    assert "__ASSET_TOKEN__" not in served, "sw.js 里的占位没被替换：worker 拿不到水印"
    token = web_router.asset_token()
    assert f'const V = "{token}"' in served, f"sw.js 的 V 与 HTML 用的水印不一致：{token}"

    monkeypatch.setattr(web_router, "_TOKEN", "20990101010101")
    moved_html = client.get("/app/").text
    moved_sw = client.get("/app/sw.js").text
    assert f'href="style.css?v=20990101010101"' in moved_html, "换水印后 HTML 没跟上"
    assert 'const V = "20990101010101"' in moved_sw, "换水印后 sw.js 没跟上"
    assert token not in moved_html and token not in moved_sw, "两份水印同时在线"


def test_the_worker_precaches_exactly_the_files_that_exist():
    """sw.js 预取清单里的每一条，都得真的取得到，而且带水印时回 immutable。

    名字写错一位（比如 vendor 下改了文件名）今天的表现是"离线时少一个文件"——不报错、
    在线时看不出。这里把它变成红的。
    """
    from pathlib import Path

    token = web_router.asset_token()
    body = client.get("/app/sw.js").text
    block = body.split("const ASSETS = [", 1)[1].split("]", 1)[0]
    entries = re.findall(r'"([^"]+)"', block)
    assert len(entries) >= 10, f"预取清单读出来只有 {len(entries)} 条，解析本身大概是坏了"
    for name in entries:
        assert (Path(web_router.STATIC_DIR) / name).is_file(), f"预取清单里 {name} 不存在"
        res = client.get("/app/" + name, params={"v": token})
        assert res.status_code == 200, f"/app/{name} 取不到：{res.status_code}"
        assert res.headers["cache-control"] == web_router.IMMUTABLE


def test_stamp_assets_is_idempotent_and_leaves_the_worker_alone():
    """盖章函数自己：重复盖不叠出两个 `?v=`，`sw.js` 的地址不许变。

    幂等这一条防的是"HTML 被盖章两次"的路径（比如某个中间件循环调用）；
    sw.js 那条防的是给 worker 换地址——注册 URL 一变，旧 worker 永远收不到更新。
    """
    once = web_router.stamp_assets('<script src="app.js"></script>'
                                    '<link rel="manifest" href="manifest.webmanifest">'
                                    '<a href="/app/sw.js">w</a>'
                                    '<a href="#top">锚</a>')
    twice = web_router.stamp_assets(once)
    assert once.count("?v=") == 2, once
    assert twice == once, f"重复盖章会叠出第二个查询：{twice!r}"
    assert 'href="/app/sw.js"' in twice, "sw.js 的地址被动了"
    assert 'href="#top"' in twice, "锚点被当成资源盖了章"
