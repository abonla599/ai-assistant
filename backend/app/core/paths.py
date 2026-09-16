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


def load_project_env() -> None:
    """加载项目根的 .env，让桌面版与源码版读到同一份密钥。

    入口 run_backend.py 在冻结模式会 chdir 到 EXE 目录，若沿用隐式的
    load_dotenv() 就会去找 dist/run_backend/.env —— 该文件随每次构建被删除，
    结果是桌面版一个模型都播种不出来。显式钉在项目根，且已存在的变量不覆盖。
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(data_root(), ".env"), override=False)
