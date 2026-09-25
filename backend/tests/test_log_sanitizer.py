"""运行期日志脱敏与轮转的判据（派单②，验收＝"新日志样本无明文服务商名"）。

泄露面不是仓库而是运行期：部署版把 stdout/stderr 整体接进 data/backend.log，
09-22 的安全审查在 auth.log / named.log 里抓到了服务商名与后端域名明文。
本文件把三件事各自钉死：
1. redact 对登记词与 `model=` 查询串的行为（含最长优先、短词不误伤）；
2. RedactingStream 与 uvicorn logger 过滤器的落盘效果——用真实临时文件当
   "新日志样本"，逐字节断言不含明文；
3. run_backend 的轮转：超限滚动 .1/.2/.3，有上界、不排队等人工清理。

回退必红自查：把 RedactingStream.write 改回直写、或把 install_std_filters 的
挂接删掉，本文件第 2 组断言立刻红；把轮转上限从 3 改小/滚序颠倒，第 3 组红。
"""
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import logsanitizer
from app.core.logsanitizer import MASK, RedactingStream, redact, register


@pytest.fixture(autouse=True)
def _isolated_terms():
    """登记表是进程级全局。带着别的用例的词进断言，等价于拿运气当判据。

    还原而不是清空：测试进程在 import app.main 时已经登记了 conftest 那份
    fake provider 的词（example.invalid 等），那是运行期就该存在的常态，
    还不清的话后面的用例会在一个比线上更"干净"的世界里测。
    """
    before = logsanitizer.registered_terms()
    logsanitizer.clear_terms_for_tests()
    yield
    logsanitizer.clear_terms_for_tests()
    for term in before:
        logsanitizer.register(term)


# ------------------------------------------------------------------ 词表与打码 --

def test_registered_terms_are_masked_longest_first():
    register("deepseek")
    register("deepseek-chat")
    # 长词先替换：先替 "deepseek" 会把 "deepseek-chat" 劈成 MASK+"-chat"，
    # 留下一个能被反推的残根——这正是要藏的信息本身。
    assert redact("model=deepseek-chat ok") == f"model={MASK} ok"
    assert redact("provider deepseek says hi") == f"provider {MASK} says hi"


def test_short_terms_are_not_registered():
    """4 个字符以下的词不收：短词进正文的概率太高，打码会变成毁日志。"""
    register("gpt")
    assert "gpt" not in logsanitizer.registered_terms()
    assert redact("gpt-4 换药方") == "gpt-4 换药方"


def test_registration_is_idempotent():
    register("acme-llm-host.invalid")
    n = len(logsanitizer.registered_terms())
    register("acme-llm-host.invalid")
    assert len(logsanitizer.registered_terms()) == n


def test_model_query_param_is_masked_even_when_unregistered():
    """访问日志里 `?model=xxx` 的形状先于清单：新加服务商、没来得及登记的那天，
    查询串这条路已经堵住——真实泄露样本（data/backend.log:7046）就是这个形状。"""
    out = redact('GET /v1/chat/stream?model=somebrand-v9&x=1 HTTP/1.1" 200')
    assert "somebrand-v9" not in out
    assert f"model={MASK}" in out
    assert "x=1" in out, "只该掩 model 的值，把整条查询串吞了日志就没法看了"


# ---------------------------------------------------------------- 落盘即无明文 --

def test_redacting_stream_writes_nothing_in_plaintext(tmp_path):
    """验收样本本身：经过壳的流写进真实文件，文件里找不到登记词。"""
    register("deepseek-chat")
    path = tmp_path / "backend.log"
    with open(path, "a", encoding="utf-8") as raw:
        sink = RedactingStream(raw)
        sink.write("upstream model=deepseek-chat responded\n")
        sink.write("plain line without secrets\n")
    body = path.read_text(encoding="utf-8")
    assert "deepseek-chat" not in body
    assert MASK in body
    assert "plain line without secrets" in body


def test_redacting_stream_returns_original_length():
    """print 拿 write 的返回值判断是否补换行。返回打码后的长度会把行拼歪。"""
    register("some-provider-name")
    import io

    buf = io.StringIO()
    sink = RedactingStream(buf)
    s = "some-provider-name is down"
    assert sink.write(s) == len(s)


def test_uvicorn_access_logger_passes_through_the_filter(caplog):
    """源码直跑 + 手工重定向没有 RedactingStream，兜底是 logger filter。

    这条演的是 auth.log 那次的形状：uvicorn.access 把整条请求行（含查询串）
    写进日志。install_std_filters 挂上后，同一行落笔时必须已经打码。
    """
    register("secretmodel-x1")
    logsanitizer.install_std_filters()
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logging.getLogger("uvicorn.access").info(
            'GET /v1/chat?model=secretmodel-x1 200')
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "secretmodel-x1" not in text
    assert MASK in text


def test_install_std_filters_is_idempotent():
    logsanitizer.install_std_filters()
    logsanitizer.install_std_filters()
    n = sum(1 for f in logging.getLogger("uvicorn.access").filters
            if isinstance(f, logsanitizer._RedactFilter))
    assert n == 1, "重复挂 filter 会让同一条消息被打码器过好几遍，也白白变慢"


def test_provider_store_registers_its_terms(tmp_path, monkeypatch):
    """持有敏感串的模块自己登记：新加一个服务商，不需要有人在别处记得加一行。"""
    from app.core.providers import ProviderStore

    path = tmp_path / "providers.json"
    path.write_text(json.dumps([{
        "id": "acme", "label": "Acme云", "base_url": "https://api.acme-llm.invalid/v1",
        "model": "acme-turbo-9", "is_default": True,
    }]), encoding="utf-8")

    ProviderStore(path=str(path))
    assert redact("calling acme-turbo-9 at api.acme-llm.invalid for Acme云") \
        == f"calling {MASK} at {MASK} for {MASK}"

    # 运行期新增同样登记（upsert→_flush 路径）
    store = ProviderStore(path=str(path))
    store.upsert({"id": "beta", "label": "B服务", "model": "beta-large-v2",
                  "base_url": "https://beta.svc.invalid/v1"})
    assert redact("beta-large-v2") == MASK


# ------------------------------------------------------------------ 轮转 --

@pytest.fixture
def run_backend_mod():
    """按路径加载根目录的 run_backend.py（它不在 backend/ 包内，import 不到名字）。

    顶层会 import app.main——pytest 里早已加载过，是一次 sys.modules 命中，不重付。
    """
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("run_backend", root / "run_backend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_log_rotation_rolls_three_generations_max(run_backend_mod, tmp_path,
                                                  monkeypatch, capsys):
    """超限 → .1 是旧当前份，旧 .1 滚到 .2、旧 .2 滚到 .3，最老的被挤出——
    上界 3 份，无人值守也长不出花。滚序若颠倒（先 .1→.2 再 .2→.3），GEN1 会
    被自己推走、.2 变成孤儿——那是要等到真出事要翻旧日志那天才能发现的丢法。"""
    log = tmp_path / "backend.log"
    log.write_bytes(b"CUR" + b"x" * 1_000_000)            # 逼过 1MB 阈值，带哨兵头
    (tmp_path / "backend.log.1").write_text("GEN1", encoding="utf-8")
    (tmp_path / "backend.log.2").write_text("GEN2", encoding="utf-8")
    (tmp_path / "backend.log.3").write_text("GEN3", encoding="utf-8")
    monkeypatch.setenv("BACKEND_LOG_PATH", str(log))

    saved = (sys.stdout, sys.stderr)
    try:
        run_backend_mod._route_logs_to_file()
    finally:
        sys.stdout, sys.stderr = saved
        run_backend_mod._LOG_SINK.flush()
        run_backend_mod._LOG_SINK._inner.close()

    assert (tmp_path / "backend.log.1").read_bytes().startswith(b"CUR")
    assert (tmp_path / "backend.log.2").read_text(encoding="utf-8") == "GEN1"
    assert (tmp_path / "backend.log.3").read_text(encoding="utf-8") == "GEN2"


def test_log_rotation_keeps_small_log_untouched(run_backend_mod, tmp_path,
                                                monkeypatch):
    """没超限就不许动任何历史份：轮转是压力触发的，不是每次启动都洗牌。"""
    log = tmp_path / "backend.log"
    log.write_text("today", encoding="utf-8")
    (tmp_path / "backend.log.1").write_text("yesterday", encoding="utf-8")
    monkeypatch.setenv("BACKEND_LOG_PATH", str(log))

    saved = (sys.stdout, sys.stderr)
    try:
        run_backend_mod._route_logs_to_file()
    finally:
        sys.stdout, sys.stderr = saved
        run_backend_mod._LOG_SINK.flush()
        run_backend_mod._LOG_SINK._inner.close()

    assert (tmp_path / "backend.log.1").read_text(encoding="utf-8") == "yesterday"
    assert not (tmp_path / "backend.log.2").exists()


def test_routed_logs_are_masked_end_to_end(run_backend_mod, tmp_path, monkeypatch):
    """整条管道验收：_route_logs_to_file 之后 print 一个登记词，文件里没有明文。"""
    log = tmp_path / "backend.log"
    register("brandnew-model-77")
    monkeypatch.setenv("BACKEND_LOG_PATH", str(log))

    saved = (sys.stdout, sys.stderr)
    try:
        run_backend_mod._route_logs_to_file()
        print("switched upstream to brandnew-model-77")
    finally:
        sys.stdout, sys.stderr = saved
        run_backend_mod._LOG_SINK.flush()
        run_backend_mod._LOG_SINK._inner.close()

    body = log.read_text(encoding="utf-8")
    assert "brandnew-model-77" not in body
    assert "switched upstream to" in body
