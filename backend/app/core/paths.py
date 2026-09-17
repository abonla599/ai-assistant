"""可变运行时数据的落盘位置。

此前打包版把 data/ 与 chroma_db/ 写在 exe 同级目录，而 dist/run_backend 每次
PyInstaller 重建都会被整体删掉——等于一次构建抹光长期记忆、会话和已配好的
模型服务。这里改为向上定位项目根，让桌面版与源码版共用同一份数据。
"""
import os
import sys

# 项目根的特征文件：只有仓库根目录下才存在这个路径
_PROJECT_MARKER = os.path.join("backend", "app", "main.py")


def _walk_up_to_project_root(start: str):
    current = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(current, _PROJECT_MARKER)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _source_repo_root() -> str:
    """本文件位于 backend/app/core/paths.py，向上三层即仓库根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def data_root() -> str:
    """会话、长期记忆、附件、模型服务配置共同挂在它下面。"""
    if getattr(sys, "frozen", False):
        # exe 一般在 dist/run_backend/ 或绿色版解压目录，先向上找项目根；
        # 真独立分发（找不到项目根）时退回 exe 同级，保证仍可整目录拷走。
        exe_dir = os.path.dirname(sys.executable)
        return _walk_up_to_project_root(exe_dir) or exe_dir
    return _walk_up_to_project_root(os.getcwd()) or _source_repo_root()


def data_file(env_var: str, filename: str) -> str:
    """一份可变运行时数据文件的落点：$env_var > <项目根>/data/<filename> > <项目根>/<filename>。

    反馈（feedback.json）与偏好摘要（preference.txt）此前是硬拼在 `data_root()` 下的
    裸文件名：既没有其它存储都有的环境变量口子（USERS_DB_PATH / SESSION_DB_PATH /
    UPLOAD_DIR / INVITES_DB_PATH 各自都能指走，唯独这两条指不走），也不在 `data/`
    那棵树下。第二点的后果是版本控制层面的：`.gitignore` 里写的是 `feedback.json`、
    `preference.txt` 这两个**精确文件名**，而按人分账后偏好摘要叫
    `preference-<uid>.txt`——换个名字就漏进仓库。本仓已经因运行数据被跟踪付出过一次
    代价（chroma_db/ 与 .env 曾入库，API Key 公开了五个月），所以"漏一个名字就裸奔"
    这个形状本身就是要拆掉的东西。

    第三档（项目根那份）只为兼容而留：本机管理员多年的 feedback.json/preference.txt
    就写在项目根，新的那一份还不存在时必须继续读老的，否则历史反馈与偏好摘要凭空
    蒸发。它不参与写入决策——一旦 data/ 下出现同名文件就以它为准，两处并存不会有
    "读 A 写 B"的错位。
    """
    override = os.getenv(env_var, "").strip()
    if override:
        return override
    root = data_root()
    fresh = os.path.join(root, "data", filename)
    if os.path.exists(fresh):
        return fresh
    legacy = os.path.join(root, filename)
    if os.path.exists(legacy):
        return legacy
    return fresh


def ensure_parent(path: str) -> str:
    """写盘前把父目录建出来，返回原路径。

    与 auth/providers/session_store 在各自 `_save` 里的做法同一套：目录不在就在第一次
    写入前建，而不是在导入期顺手往仓库里 mkdir——那会让任何一次 `import` 都有副作用。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


# 每一份可变数据的"环境变量名 → 中文标签"。这张表同时是启动日志的目录与
# 测试断言覆盖面所用的清单：加一份数据只改这里一处。
DATA_PATH_ENV_VARS = {
    "会话": "SESSION_DB_PATH",
    "身份库": "USERS_DB_PATH",
    "邀请码": "INVITES_DB_PATH",
    "模型服务配置": "PROVIDERS_DB_PATH",
    "附件": "UPLOAD_DIR",
    "长期记忆向量库": "CHROMA_DB_PATH",
    "反馈原文": "FEEDBACK_FILE",
    "偏好摘要": "PREFERENCE_FILE",
}


def resolve_all_data_paths() -> list:
    """每一份可变运行时数据的**实际**落点：[(名字, 绝对路径), ...]。

    为什么需要它：今天"数据到底放在哪"只写在文档里，而真实答案其实是个条件——
    `data_file()` 在 `<项目根>/data/` 下没有同名文件时会退回项目根那份历史文件
    （见它的第三档），于是"是不是所有可变数据都在 data/ 底下"取决于本机有没有一个
    老 `preference.txt` 或 `feedback.json`。这类事猜不准也没法查：唯一诚实的办法是
    把它打印出来。会话与附件的迁移日志已经在打各自那一份的绝对路径了，这里补齐剩下的。

    兄弟模块一律在函数内导入：paths 被它们每一个人 import，反向依赖会成环。
    """
    from app.core.auth import _default_invites_path, _default_users_path
    from app.core.providers import _default_path as providers_path
    from app.core.uploads import _default_dir as uploads_dir
    from app.feedback_storage import FEEDBACK_FILE
    from app.memory.memory_manager import _default_persist_dir as chroma_dir
    from app.preference_analyzer import PREFERENCE_FILE
    from app.session.session_store import _default_path as sessions_path

    resolved = {
        "会话": sessions_path(),
        "身份库": _default_users_path(),
        "邀请码": _default_invites_path(),
        "模型服务配置": providers_path(),
        "附件": uploads_dir(),
        "长期记忆向量库": chroma_dir(),
        "反馈原文": FEEDBACK_FILE,
        "偏好摘要": PREFERENCE_FILE,
    }
    # 按 DATA_PATH_ENV_VARS 的顺序出：日志行序稳定，两份日志才比得出差别
    return [(label, resolved[label]) for label in DATA_PATH_ENV_VARS]


def _display_width(text: str) -> int:
    """中日韩字符在终端里占两列，按 len() 对齐会歪。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def log_data_locations() -> None:
    """启动时把八份数据的绝对路径打一遍；不在 data/ 下的说明它为什么不在。

    只读不改：这条不建目录、不碰文件，因此对 `import` 没有任何副作用，测试里跑也安全。
    先解析再打印，是因为解析会触发兄弟模块导入（其中 providers 会自己打一行日志），
    混在中间就把这个块切断了。
    """
    rows = resolve_all_data_paths()
    root = os.path.abspath(data_root())
    data_dir = os.path.join(root, "data")
    width = max(_display_width(label) for label, _ in rows)
    print(f"📁 可变运行时数据（项目根 {root}）：")
    for label, path in rows:
        absolute = os.path.abspath(str(path))
        # 前缀比较按分隔符对齐：/data 本身算在里面，/databak 不算
        inside = absolute == data_dir or absolute.startswith(data_dir + os.sep)
        env_var = DATA_PATH_ENV_VARS[label]
        redirected = bool(os.getenv(env_var, "").strip())
        if inside:
            flag, why = "  ", ""
        elif redirected:
            flag, why = "↳", f"   ← 由环境变量 {env_var} 指走"
        else:
            # 这一行才是运维真正会踩的：他以为数据都在 data/ 底下
            flag, why = "⚠️", (f"   不在 data/ 之下：读的是项目根那份历史数据"
                              f"（用 {env_var} 可指走）")
        print(f"   {flag} {label}{' ' * (width - _display_width(label))} {absolute}{why}")
    print("   ⚠️ 一个数据根目录只允许一个服务进程：这些存储用进程内锁串行化，"
          "两个进程同写一份会互相覆盖")


def load_project_env() -> None:
    """加载项目根的 .env，让桌面版与源码版读到同一份密钥。

    入口 run_backend.py 在冻结模式会 chdir 到 EXE 目录，若沿用隐式的
    load_dotenv() 就会去找 dist/run_backend/.env —— 该文件随每次构建被删除，
    结果是桌面版一个模型都播种不出来。显式钉在项目根，且已存在的变量不覆盖。
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(data_root(), ".env"), override=False)
