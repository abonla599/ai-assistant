"""`/v1/update/info`：壳「检查更新」的透传端点与它那份校验值。

这条端点存在的理由是 2026-09-23 那次真实事故：壳直连 api.github.com 下载，被 ROM
里的下载器（迅雷通道）劫持成"未命名"残包，人拿着半截文件去装，报安装失败。修法是
把出网这一段收进这台服务器——所以这里钉的是：

1. 透传不改写：壳里的 `ReleasePlan` 拿到的必须还是那份 GitHub 形状的 JSON，
   判断（三态/资产名/URL 白名单）只许有壳里那一份真相；
2. 校验值的取用规矩：正文里那行 `APK-SHA256:` 是唯一来源，形状不对等于没有；
3. 拉不到时不许装出"一切正常"：502 带理由，绝不回一份能让壳误判"已是最新"的 200；
4. 免鉴权的代价与缓存：它照样替调用方出网，所以与卡片端点共用同一份 10 分钟快照。
"""
import urllib.error

import pytest

from app.core import releases
from tests.test_release_probe import GOOD, _Urlopen

DIGEST = "a" * 63 + "f"          # 64 位小写十六进制，一眼能认出是哪一行


def _release(body_text):
    rel = dict(GOOD)
    rel["body"] = body_text
    return rel


@pytest.fixture(autouse=True)
def cold_cache():
    releases.reset_for_tests()
    yield
    releases.reset_for_tests()


# ---------- 透传 + 校验值 ----------

def test_info_passes_the_release_json_through_with_the_digest(client, enforced, monkeypatch):
    fake = _Urlopen(_release(f"本版修了点东西。\n\n---\n\nAPK-SHA256: {DIGEST}\n"))
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    out = client.get("/v1/update/info")
    assert out.status_code == 200, out.text
    body = out.json()
    # 原样透传：壳里 ReleasePlan 认的字段一个都不能被服务端顺手改名
    assert body["tag_name"] == "v0.18"
    assert body["assets"][0]["browser_download_url"].startswith("https://objects.example/")
    assert body["apk_sha256"] == DIGEST


def test_a_release_without_the_marker_reports_no_digest_not_a_guess(client, enforced, monkeypatch):
    fake = _Urlopen(_release("旧版发布，正文里还没有那行校验值"))
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    out = client.get("/v1/update/info")
    assert out.status_code == 200
    assert out.json()["apk_sha256"] == "", "没有校验值时必须给空串，壳据此拒绝下载"


@pytest.mark.parametrize("junk", [
    f"APK-SHA256: {'A' * 64}",                 # 大写：发布流程写的是小写，不认混着来的
    f"APK-SHA256: {'a' * 63}",                 # 短一位
    f"  APK-SHA256: {DIGEST}",                 # 行首缩进（引用块里的样子货）
    f"APK-SHA256:{DIGEST}",                    # 冒号后没空格
])
def test_a_malformed_marker_line_is_not_a_digest(client, enforced, monkeypatch, junk):
    # 宁可壳拒装，不许半对的值被当成可信摘要。
    releases.reset_for_tests()
    monkeypatch.setattr(releases.urllib.request, "urlopen", _Urlopen(_release(junk)))
    out = client.get("/v1/update/info")
    assert out.status_code == 200
    assert out.json()["apk_sha256"] == "", f"这行不该被认成校验值：{junk!r}"


# ---------- 拉不到时的表态 ----------

def test_unreachable_github_is_a_502_with_a_reason_not_a_silent_ok(client, enforced, monkeypatch):
    monkeypatch.setattr(releases.urllib.request, "urlopen",
                        _Urlopen(urllib.error.URLError("no route")))
    out = client.get("/v1/update/info")
    assert out.status_code == 502, f"拉不到却回了 {out.status_code}：壳会把『问不到』读成别的什么"
    assert "问不到发布信息" in out.json()["detail"]


# ---------- 免鉴权与缓存 ----------

def test_the_door_is_open_without_credentials(client, enforced, monkeypatch):
    """没登录的人点了「检查更新」也得能问——这条在 PUBLIC_PATHS 里点了名。

    判据用真请求且必须在 enforced 下：disabled 里人人放行，那条断言等于没测。
    名单与中间件是两处代码（名单写没写是一回事，放不放行是另一回事），
    精确名单的锁在 test_route_auth_contract，这里补的是"门真的开着"。
    """
    monkeypatch.setattr(releases.urllib.request, "urlopen", _Urlopen(_release("x")))
    out = client.get("/v1/update/info")
    assert out.status_code not in (401, 403), "免鉴权名单改了却没改这条，门就悄悄关了"


def test_twenty_opens_still_mean_one_trip_to_github(client, enforced, monkeypatch):
    """与卡片端点共用同一份快照：二十次点开只许出网一次。

    这条是免鉴权换来的放大面的全部防线——多一处出网路径不多缓存，等于把
    GitHub 的 60 次/小时限流挂在每个访客身上。
    """
    fake = _Urlopen(_release(f"APK-SHA256: {DIGEST}"))
    monkeypatch.setattr(releases.urllib.request, "urlopen", fake)
    for _ in range(20):
        assert client.get("/v1/update/info").status_code == 200
    assert len(fake.calls) == 1, f"出网 {len(fake.calls)} 次：缓存没接住这条新端点"


def test_the_endpoint_is_a_sync_def_so_the_loop_stays_free():
    """与 /v1/release/latest 同一条课：会出网的端点写成 async 就是冻住所有人。

    test_event_loop_not_blocked 的名单里也点了这条的名字；这一处再钉一遍是因为
    那份名单丢一条时那边只会红在参数化里，看不出是"新端点没登记"。
    """
    import inspect

    from app import main
    assert not inspect.iscoroutinefunction(main.update_info)


# ---------- 校验值这条契约横跨三个语言，形状只能有一份 ----------

def test_the_digest_contract_is_the_same_shape_in_the_workflow_the_backend_and_the_shell():
    """`APK-SHA256: <64 位小写十六进制>` 由发布流水线写、后端解析、壳核对。

    三边各认各的形状时，漂移的表现不是报错而是【永远拒装】：流水线哪天写成大写，
    后端正则认不出→ apk_sha256 恒为空→ 每一台壳都拒绝下载每一版，且每一环都"正常工作"。
    所以这里把三份形状并排钉成同一个：都是"APK-SHA256 前缀 + 64 + 小写十六进制"。
    """
    from pathlib import Path

    repo = Path(releases.__file__).resolve().parents[3]
    wf = (repo / ".github" / "workflows" / "release-apk.yml").read_text(encoding="utf-8")
    assert 'APK-SHA256: %s' in wf and 'sha256sum "$file"' in wf, \
        "发布流不再写（或不再算）那行校验值了"
    assert r"[0-9a-f]\{64\}" in wf, \
        "发布流自检的 grep 形状不再是 64 位小写十六进制"

    assert releases.APK_SHA256_RE.pattern == r"^APK-SHA256: ([0-9a-f]{64})$", \
        "后端解析的形状漂了，要和上面工作流写的那一份一起改"

    plan = (repo / "android" / "app" / "src" / "main" / "java" / "xyz" / "fenever"
            / "assistant" / "core" / "ReleasePlan.java").read_text(encoding="utf-8")
    assert '"apk_sha256"' in plan, "壳不再从透传 JSON 读这个字段了"
    assert "length() != 64" in plan, "壳认的长度不再是 64？"
    assert "c < 'a' || c > 'f'" in plan, "壳认的字符集不再是小写十六进制？"
