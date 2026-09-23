import json
import os
import threading

from app.core.paths import data_file, ensure_parent

# 必须是绝对路径：裸相对名会随进程工作目录漂移。打包版入口 chdir 到 EXE 目录，
# 而该目录每次重建都被清空，等于把反馈数据写进一个注定消失的地方。
# 落点规则（环境变量优先、默认进 data/、兼容项目根那份历史数据）见 paths.data_file。
FEEDBACK_FILE = data_file("FEEDBACK_FILE", "feedback.json")

# 反馈是**整份列表读出来、append 一条、再整份写回去**的，所以"读—改—写"必须
# 是一个整体。锁放在模块级而不是实例级：这个模块只有一个函数、没有对象，
# 与 session_store / auth / uploads 各拿一把 threading.Lock 的形态一致。
# 注意它只在单进程内串行——一个数据根目录只允许跑一个服务进程。
_LOCK = threading.Lock()


def _read_all(path: str) -> list:
    """读出全部反馈；文件还不存在时是空列表，读不懂则原样抛出去。

    "读不懂"绝不能当成"没有反馈"然后继续写：那等于一次磁盘抖动就把多年攒下的
    真实反馈清零。让异常冒到接口上，运维才会看见并去修文件。
    """
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"反馈文件 {path} 顶层不是列表（实际是 {type(data).__name__}），"
            "拒绝在修好之前写入，以免整表被覆盖。")
    return data


def _write_all(path: str, rows: list) -> None:
    """临时文件 + os.replace 原子替换，中途失败也要保住原有那一份。

    直接 `open(path, "w")` 的问题在于**截断发生在打开的那一刻**：进程被杀、磁盘
    写满、或 json.dump 中途抛错（Windows 上这条并不罕见），留下的都是一个截断的
    或半截的 feedback.json——原来那几百条反馈不是"这一条没记上"，是**全部没了**。
    同目录的兄弟存储早就走 tmp + os.replace，这里补齐。
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            # 原子替换不等于落盘：不 fsync 的话断电就丢最后一批反馈，replace
            # 却已经"成功"了。flush 到 OS、fsync 到磁盘，再 replace。
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 替换失败时临时文件会留在数据目录里，下次看见还以为是份数据
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# 修改函数入参，直接接收独立参数
def save_feedback(message_id: str, rating: int, comment: str, user_id: str):
    """记下一条反馈，并记下是谁给的。

    user_id 必填、无默认值：与 sessions/uploads 的 owner 同一口径。没有默认身份
    可退，才不会出现"漏传的调用点把反馈记到管理员名下"这种静默错位；而调用方
    必须先证明这条 message_id 属于他（见 main.py 的 /v1/feedback），否则一行都
    不该写进来。
    """
    feedback_data = {
        "message_id": message_id,
        "rating": rating,
        "comment": comment,
        "user_id": user_id
    }

    # data/ 在全新检出时还不存在，而这里是第一次写它的人
    ensure_parent(FEEDBACK_FILE)

    # 读—改—写整体加锁：两个人同时点 👍/👎 时，后写者手里的"旧全表"不包含
    # 先写者刚追加的那条，写回去就先那条就丢了。
    with _LOCK:
        rows = _read_all(FEEDBACK_FILE)
        rows.append(feedback_data)
        _write_all(FEEDBACK_FILE, rows)

    return True