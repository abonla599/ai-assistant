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


def load_project_env() -> None:
    """加载项目根的 .env，让桌面版与源码版读到同一份密钥。

    入口 run_backend.py 在冻结模式会 chdir 到 EXE 目录，若沿用隐式的
    load_dotenv() 就会去找 dist/run_backend/.env —— 该文件随每次构建被删除，
    结果是桌面版一个模型都播种不出来。显式钉在项目根，且已存在的变量不覆盖。
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(data_root(), ".env"), override=False)
