"""一次「东西齐不齐」的自检：把只有出事那天才看得见的东西，摆到 /health 上。

`/health` 一直只回 `{"status": "healthy"}`，量的是「进程还在」。这一轮新加的每样东西
偏偏都能在这个状态下坏：密钥库文件读不动（聊天全 401）、用量账本写不进去（钱白花）、
嵌入后端降级成全零伪嵌入（语义检索再也不可信，但不报错）、新写的限流账没登记进
`_LEDGERS`（于是永不修剪，内存单向涨）。这些都是"活着但不齐"，所以逐项问一遍。

两件刻意不做的事：

① **不因为哪一项坏了就返回非 200。** 那会让"配错了密钥"变成"看门狗每分钟重启一次
   后端"——重启修不好任何配置问题，只会把正在进行的对话一起带走。判据交给日志与
   运维，不交给进程管理器。
② **不回显绝对路径、不读任何用户内容。** `/health` 是免鉴权端点（见
   `backend/tests/test_route_auth_contract.py` 的 EXEMPT_PATHS），公开进程目录结构
   与用户名对探针没有用处，只多一个泄露面。所以这里只有状态与一句原因。
"""

import json
import os

OK = "ok"
DEGRADED = "degraded"
BROKEN = "broken"

_SEVERITY = {OK: 0, DEGRADED: 1, BROKEN: 2}


def _check_provider_config() -> tuple:
    """有没有一个真能拿去调用的模型配置。resolve() 就是聊天时会走的那条判断。"""
    from app.core.providers import ProviderError, store as provider_store

    try:
        provider = provider_store.resolve()
    except ProviderError as e:
        # 没配 / 配了但没密钥 / 占位符——这些是同一类"服务其实不能用"，说清楚是哪一种
        return BROKEN, f"没有可用的模型服务：{e}"
    except Exception as e:
        return BROKEN, f"读取模型服务配置时出错：{type(e).__name__}"
    return OK, f"{provider.get('label') or provider.get('id')} / {provider.get('model')}"


def _check_key_vault() -> tuple:
    """密钥库文件与内存里那份对不对得上。

    只比条数，不比内容：内容对不上是"哪条密钥被掉了"，条数对不上是"有一边在悄悄丢"。
    后者才是这里要抓的形状——记录搬走了密钥、密钥文件却没落盘，界面上一切正常。
    """
    from app.core.providers import store as provider_store

    records = provider_store.all()
    in_memory = sum(1 for p in records if p.get("api_key"))
    path = provider_store.keys_path
    if not os.path.isfile(path):
        if in_memory:
            return BROKEN, f"{in_memory} 条配置自报带密钥，密钥库文件却不在"
        return DEGRADED, "还没配过任何密钥"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return BROKEN, "密钥库文件读不动或不是合法 JSON"
    if not isinstance(data, dict):
        return BROKEN, "密钥库形状不是 dict"
    on_disk = sum(1 for v in data.values() if v)
    if on_disk != in_memory:
        return BROKEN, f"密钥库 {on_disk} 条、配置里 {in_memory} 条，有一边在丢密钥"
    return OK, f"{on_disk} 条"


def _check_usage_ledger() -> tuple:
    """账本读得动吗。写坏了的话这一轮的钱就不在任何人的账上了。"""
    from app.core import usage

    rows = usage.snapshot()
    days = usage.days()
    if not isinstance(rows, list) or not isinstance(days, list):
        return BROKEN, "账本接口返回了意外的形状"
    return OK, f"已记 {len(days)} 天"


def _check_memory_backend() -> tuple:
    """嵌入后端是哪一种。伪嵌入必须在这里露出来。

    这是本文件唯一一条 degraded 而不是 broken 的形状：全零向量下 add/query 都不报错，
    只是检索结果完全不可信——它不影响"能不能聊"，影响的是"记不记得住"，
    所以报 degrade、不报断。
    """
    from app.memory.memory_router import memory_init_error, memory_manager

    if memory_manager is None:
        return BROKEN, f"记忆后端没起来：{memory_init_error or '未知原因'}"
    if hasattr(memory_manager, "memories"):
        # 测试/CI 的内存替身：本来就不做嵌入，这不是降级，是这套后端的设计。
        # 不认出来的话 CI 每次启动都白报一条⚠️，而习惯了自己说谎的告警等于没有告警。
        return OK, "内存替身（测试/CI）"
    if getattr(memory_manager, "_dummy_embed", False):
        return DEGRADED, "伪嵌入（全零向量），语义检索不可信"
    if getattr(memory_manager, "embed_model", None) == "injected":
        return OK, "测试注入的确定性向量"
    if getattr(memory_manager, "use_local_embed", False):
        return OK, "本地嵌入"
    if getattr(memory_manager, "client", None) is not None:
        return OK, "云端嵌入"
    return DEGRADED, "说不清用的是哪种嵌入后端"


def _check_limiters() -> tuple:
    """每本限流账都登记了吗——没登记的那本永远不会被修剪。

    `_prune` 只遍历 `_LEDGERS`。新写一本账忘了注册，结果是这个进程的内存随来源数
    单向上涨，而且限流本身照样生效，所以没有任何症状。这里数一下本数、并确认每本
    都带着自己的窗口长度。
    """
    from app.core import auth_router

    ledgers = auth_router._LEDGERS
    if not ledgers:
        return BROKEN, "一本限流账都没登记"
    malformed = [i for i, item in enumerate(ledgers)
                 if not (isinstance(item, tuple) and len(item) == 2
                         and isinstance(item[1], (int, float)) and item[1] > 0)]
    if malformed:
        return BROKEN, f"第 {malformed} 本账没有合法的窗口长度，_prune 会按 0 处理"
    return OK, f"{len(ledgers)} 本"


def _check_data_paths() -> tuple:
    """每份可变数据的实际落点，父目录在不在。

    这条是 PyInstaller 那次教训的形状：打包版的数据原先落在 EXE 同级目录，而 `dist/`
    每次重建都被整体删空——一次构建抹光长期记忆。落点从此由 paths 单源决定，但"到底
    落在哪、还在不在"只能问当下这个进程，所以把它打印出来。
    """
    from app.core.paths import resolve_all_data_paths

    resolved = resolve_all_data_paths()
    missing = [label for label, path in resolved
               if not os.path.isdir(os.path.dirname(os.path.abspath(path)))]
    if missing:
        return BROKEN, f"这几份数据所在目录不存在：{'、'.join(missing)}"
    return OK, f"{len(resolved)} 处可写"


CHECKS = (
    ("模型服务", _check_provider_config),
    ("密钥库", _check_key_vault),
    ("用量账本", _check_usage_ledger),
    ("记忆后端", _check_memory_backend),
    ("限流账", _check_limiters),
    ("数据落点", _check_data_paths),
)


def _scrub(detail: str) -> str:
    """出口脱敏。它自己读不动时宁可整条丢掉。

    `/health` 免鉴权，而"密钥库读不动"恰恰是本文件要报的那类故障——脱敏依赖
    密钥库，于是它可能正好在报错的那一刻坏掉。这时候把详情原样发出去是失败开放，
    把详情换成一句"说不清"是失败封闭：宁可少说，不可把别人的 key 说给路人听。
    """
    from app.core.providers import scrub_secrets

    try:
        return scrub_secrets(detail)
    except Exception:
        return "（详情无法安全呈现）"


def run() -> dict:
    """逐项自检。任何一项抛异常都折算成 broken，绝不把异常透出到 /health 上。"""
    checks = {}
    worst = OK
    for label, fn in CHECKS:
        try:
            status, detail = fn()
        except Exception as e:                       # 自检本身坏了也要看得见
            status, detail = BROKEN, f"自检项异常：{type(e).__name__}"
        checks[label] = {"status": status, "detail": _scrub(detail)}
        if _SEVERITY[status] > _SEVERITY[worst]:
            worst = status
    return {"status": worst, "checks": checks}


def log_startup_summary() -> None:
    """启动时打一行，让 server.log 里留一份"开机时齐不齐"的证据。

    只在 /health 上给结论不够：出事的时候人多半在看日志，而不是去猜当时 /health
    回过什么。
    """
    result = run()
    bad = {k: v for k, v in result["checks"].items() if v["status"] != OK}
    if not bad:
        print("✅ 自检通过：模型服务、密钥库、账本、记忆、限流账、数据落点都齐")
        return
    icon = "🚨" if result["status"] == BROKEN else "⚠️"
    print(f"{icon} 自检 {result['status']}：" +
          "；".join(f"{k}={v['status']}({v['detail']})" for k, v in bad.items()))
