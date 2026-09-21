"""这套部署自己是哪个版本：读构建时留下的戳，不写死在代码里。

为什么要有它：「设置 → 关于 → 版本」这一行原先只有一个来源——安卓壳通过桥报上来的
versionName。浏览器里没有壳，于是那一行只能显示"网页版"，谁都不知道**服务端**这份
是第几版；而每次换包之后他第一个想问的恰恰就是这个。

判据必须是构建时生成的文件，不是源码里的常量：`android/app/build.gradle` 是壳版本的
唯一来源，把 "v0.16" 再抄一份到 Python 或 JS 里，下次升版必然有一处是旧的，而且它
显示得理直气壮（本仓已经为这类"第二个事实来源"付过不止一次钱）。所以构建时跑一次
`git describe --tags --abbrev=0 > version.txt`，这份文件不进版本库、随包一起打进去。

取不到就返回空字符串——宁可不显示，不许显示一个猜出来的号。
"""

import os
from functools import lru_cache

from app.core.paths import data_root

VERSION_FILE = "version.txt"


@lru_cache(maxsize=1)
def build_version() -> str:
    """构建戳，形如 "v0.16"；没有就返回 ""。

    找两处：项目根（源码跑与打包版向上找到的同一个根），以及 EXE 自己旁边
    （把 dist\\run_backend 整目录拷走时，version.txt 会跟着一起走）。
    """
    candidates = []
    try:
        candidates.append(os.path.join(data_root(), VERSION_FILE))
    except Exception:
        pass
    try:
        import sys
        # 打包版：spec 把 version.txt 列进 datas，落地位置是 _internal\ 根下（= _MEIPASS），
        # 跟着 EXE 一起被拷走时也还在 exe 同级。两处都找一下，少一处就少一种"拷走了就没版本"。
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, VERSION_FILE))
        if getattr(sys, "frozen", False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), VERSION_FILE))
    except Exception:
        pass

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
        except OSError:
            continue
        if value:
            return value
    return ""
