"""运行时数据落盘位置测试。

守护的不变量：EXE 放在 dist/run_backend/ 下（PyInstaller 每次重建都会整体删除
该目录）时，可变数据仍必须写到项目根，否则一次构建就抹光长期记忆与会话。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.paths import data_root

# 本文件位于 backend/tests/，向上两层即仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]


def _as_frozen(monkeypatch, exe_path: str):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe_path, raising=False)


def test_source_run_points_at_repo_root(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert Path(data_root()).resolve() == REPO_ROOT


def test_frozen_exe_inside_dist_resolves_to_project_root(monkeypatch):
    _as_frozen(monkeypatch, str(REPO_ROOT / "dist" / "run_backend" / "run_backend.exe"))
    assert Path(data_root()).resolve() == REPO_ROOT


def test_frozen_exe_with_no_project_falls_back_to_its_own_dir(tmp_path, monkeypatch):
    """真正独立分发（整个目录拷到别处）时不能找不到根就崩，退回 exe 同级保持可携带。"""
    exe = tmp_path / "run_backend.exe"
    _as_frozen(monkeypatch, str(exe))
    assert Path(data_root()).resolve() == tmp_path.resolve()


def test_all_stores_share_one_root(monkeypatch):
    """四个存储必须同源，否则桌面版看到的记忆和手机版不是同一份。"""
    from app.core.uploads import _default_dir as uploads_dir
    from app.core.providers import _default_path as providers_path
    from app.session.session_store import _default_path as sessions_path
    from app.memory.memory_manager import _default_persist_dir as chroma_dir

    for var in ("UPLOAD_DIR", "PROVIDERS_DB_PATH", "SESSION_DB_PATH", "CHROMA_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    _as_frozen(monkeypatch, str(REPO_ROOT / "dist" / "run_backend" / "run_backend.exe"))

    root = Path(data_root()).resolve()
    assert Path(uploads_dir()).resolve().is_relative_to(root)
    assert Path(providers_path()).resolve().is_relative_to(root)
    assert Path(sessions_path()).resolve().is_relative_to(root)
    assert Path(chroma_dir()).resolve().is_relative_to(root)
