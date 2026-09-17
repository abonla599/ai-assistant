"""给"身份层之前"就存在的记录补 owner —— 会话与附件两份存储共用的那一份实现。

这段逻辑原先在 session_store 与 uploads 里各抄了一遍（37 行和 32 行，连备份名
的格式串都逐字符相同）。抄两遍的代价不是行数，是"修一处、漏一处"：回填判据只要
在一边改掉，另一边就会带着半迁移的库对外服务，而那时归属校验是**静默放行**的。

flush 由调用方把绑定方法传进来，而不是在这里自己写盘。两个原因：会话那边的 _flush
要多一句 makedirs，两边本就不是同一件事；更重要的是 test_isolation 里有两条契约
靠 monkeypatch **类上的** _flush 来证明"重复加载一次都不写盘"，在这里自己写盘
会让那两条锁当场失明。
"""
import shutil
from datetime import datetime


def backfill_owner(records: dict, path: str, *, entity: str, legacy_owner: str, flush):
    """就地给缺 owner 的记录补上 legacy_owner；一条都不缺就一个字节也不写。

    entity 只影响日志与报错里的称呼（"会话" / "附件"）。备份或写回任何一步失败
    都向上抛：两个存储都在导入期构造，异常让进程起不来正是我们要的失败方式——
    带着半迁移的库对外服务比拒绝启动更糟。
    """
    # 畸形记录要先报错给人看。直接 `record["owner"] = ...` 抛的是
    # "TypeError: 'str' object does not support item assignment"，冻结成 EXE 之后
    # 既不写文件名也不说哪条记录，用户根本没法自助修复。
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError(
                f"{path} 中的记录 {key!r} 不是对象（实际是 {type(record).__name__}），"
                f"无法给{entity}补 owner。请修复或还原该文件：宁可拒绝启动，"
                "也不带着认不出归属的库对外服务。")
    # 判据是 not record.get("owner")，不是 "owner" not in record：手工写成
    # {"owner": null} 也算没迁完。放过去会被归属校验当成"已有 owner"跳过，
    # 此后它与任何 user_id 都不相等，这条记录就永久隐身了。
    missing = [r for r in records.values() if not r.get("owner")]
    if not missing:
        return
    backup = f"{path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(path, backup)
    for record in missing:
        record["owner"] = legacy_owner
    flush()
    # 数据文件的绝对路径打进日志：这次动的是不是用户真实的 data/，看一眼输出的
    # 第一秒就知道，不用等事后去比对 md5。
    print(f"🧭 已为 {len(missing)} 条历史{entity}补 owner={legacy_owner}"
          f"（数据文件 {path}），原件备份于 {backup}")
