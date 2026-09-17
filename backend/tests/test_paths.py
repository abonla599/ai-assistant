"""运行时数据落盘位置测试。

守护两条不变量：
1. EXE 放在 dist/run_backend/ 下（PyInstaller 每次重建都会整体删除该目录）时，
   可变数据仍必须写到项目根，否则一次构建就抹光长期记忆与会话。
2. 每一份可变数据都要能被环境变量指走，且默认落在 `data/` 那棵树下——写在项目根
   的裸文件名靠 `.gitignore` 里逐个记名兜底，改个名字（preference-<uid>.txt）就会
   被提交进公开仓库。规则收在 paths.data_file，这里按优先级逐档钉住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

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
    """每一份可变存储都必须同源，否则桌面版看到的记忆和手机版不是同一份。

    这张表是"少列一个存储 = 少一层保护"的那种清单，所以身份库（users.json /
    invites.json）尤其不能缺席：它们是本分支最新、也最敏感的两份数据——令牌摘要、
    用户名、邀请码全在里面。它俩一旦跟着 exe 落在 dist/run_backend/ 下，一次
    PyInstaller 重建就不只是"丢了几个人的账号"，而是把已发令牌整批清零（所有人
    立刻 401，只能逐个重发），而那台机器的历史 users.json 同时被抹掉、无从恢复。
    """
    from app.core.auth import _default_invites_path, _default_users_path
    from app.core.providers import _default_path as providers_path
    from app.core.uploads import _default_dir as uploads_dir
    from app.memory.memory_manager import _default_persist_dir as chroma_dir
    from app.session.session_store import _default_path as sessions_path

    # (环境变量, 默认路径提供者)：两列一起加，才不会又漏掉一个"能被指走"的存储
    stores = [
        ("UPLOAD_DIR", uploads_dir),
        ("PROVIDERS_DB_PATH", providers_path),
        ("SESSION_DB_PATH", sessions_path),
        ("CHROMA_DB_PATH", chroma_dir),
        ("USERS_DB_PATH", _default_users_path),
        ("INVITES_DB_PATH", _default_invites_path),
    ]
    for var, _ in stores:
        monkeypatch.delenv(var, raising=False)
    _as_frozen(monkeypatch, str(REPO_ROOT / "dist" / "run_backend" / "run_backend.exe"))

    root = Path(data_root()).resolve()
    assert root == REPO_ROOT, "项目根都认错了，下面这些断言就全成了空话"
    for var, resolve in stores:
        resolved = Path(resolve()).resolve()
        assert resolved.is_relative_to(root), \
            f"{var} 解析到了 {resolved}，不在项目根 {root} 之下：一次重建就会抹光它"


# ---------- 反馈与偏好摘要的落点（feedback.json / preference.txt） ----------
# 这两份曾经只有 `os.path.join(data_root(), "<裸文件名>")` 一句话：既没有别的存储
# 都有的环境变量口子，也不在 data/ 那棵树下，于是 .gitignore 里那两个精确文件名
# 一遇到改名（按人分账后的 preference-<uid>.txt）就漏。规则本身收在 paths.data_file，
# 下面按三条优先级各钉一次。


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """把项目根换成空目录：这三档优先级只有"两处都还没有文件"时才分得清。"""
    from app.core import paths

    root = tmp_path / "root"
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(paths, "data_root", lambda: str(root))
    return root


def test_data_file_env_override_wins(fake_root, monkeypatch):
    """$FEEDBACK_FILE 必须能指走：测试隔离、把数据放到别的盘、CI 都靠它。"""
    from app.core.paths import data_file

    elsewhere = str(fake_root / "elsewhere" / "feedback.json")
    monkeypatch.setenv("FEEDBACK_FILE", elsewhere)
    # 两处候选文件都造出来，证明"存在"敌不过显式指定的路径
    (fake_root / "data").mkdir()
    (fake_root / "data" / "feedback.json").write_text("[]", encoding="utf-8")
    (fake_root / "feedback.json").write_text("[]", encoding="utf-8")
    assert data_file("FEEDBACK_FILE", "feedback.json") == elsewhere


def test_data_file_defaults_into_the_data_tree(fake_root):
    """全新部署（两处都还没有文件）必须落在 data/ 下，与别的运行时数据同一棵树。"""
    from app.core.paths import data_file

    assert data_file("FEEDBACK_FILE", "feedback.json") == str(fake_root / "data" / "feedback.json")
    assert data_file("PREFERENCE_FILE", "preference.txt") == str(fake_root / "data" / "preference.txt")


def test_data_file_still_reads_the_legacy_root_file(fake_root):
    """项目根那一份是历史数据，data/ 下还没有时必须继续读它，否则多年反馈凭空蒸发。"""
    from app.core.paths import data_file

    legacy = fake_root / "feedback.json"
    legacy.write_text("[]", encoding="utf-8")
    assert data_file("FEEDBACK_FILE", "feedback.json") == str(legacy)


def test_data_file_prefers_data_tree_once_moved(fake_root):
    """两处并存时以 data/ 为准：一次进程"读老写新"会把反馈分裂成两半，谁都不全。"""
    from app.core.paths import data_file

    (fake_root / "feedback.json").write_text("[]", encoding="utf-8")
    (fake_root / "data").mkdir()
    fresh = fake_root / "data" / "feedback.json"
    fresh.write_text("[]", encoding="utf-8")
    assert data_file("FEEDBACK_FILE", "feedback.json") == str(fresh)


def test_ensure_parent_creates_the_missing_directory(tmp_path):
    """data/ 在全新检出时不存在，而第一次写它的人就是 feedback/preference。"""
    from app.core.paths import ensure_parent

    target = str(tmp_path / "data" / "preference.txt")
    assert ensure_parent(target) == target        # 返回值就是入参，能直接串进调用点
    assert (tmp_path / "data").is_dir()
    ensure_parent("bare-name.txt")                # 没有父目录的裸名不该炸

# 刻意**没有**"再断言 feedback_storage.FEEDBACK_FILE / preference_analyzer.PREFERENCE_FILE
# 必须等于上面这条规则"的那一条：conftest 的 autouse 夹具会把这两个常量 patch 到临时
# 目录，那种断言区分不了真实值与 patch 值，永远绿（写过，已删过一次）。规则本身由上面
# 几条钉住，两个模块各用一行 data_file(...) 接上，接线点就在模块开头第三行。
