"""运行时数据落盘位置测试。

守护两条不变量：
1. EXE 放在 dist/run_backend/ 下（PyInstaller 每次重建都会整体删除该目录）时，
   可变数据仍必须写到项目根，否则一次构建就抹光长期记忆与会话。
2. 每一份可变数据都要能被环境变量指走，且默认落在 `data/` 那棵树下——写在项目根
   的裸文件名靠 `.gitignore` 里逐个记名兜底，改个名字（preference-<uid>.txt）就会
   被提交进公开仓库。规则收在 paths.data_file，这里按优先级逐档钉住。
"""
import os
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

    这张表是"少列一个存储 = 少一层保护"的那种清单，所以身份库（users.json）尤其
    不能缺席：它是本分支最新、也最敏感的一份数据——密码摘要、令牌摘要、用户名全
    在里面。它一旦跟着 exe 落在 dist/run_backend/ 下，一次 PyInstaller 重建就不
    只是"丢了几个人的账号"，而是把已发令牌整批清零（所有人立刻 401，只能逐个
    重发），而那台机器的历史 users.json 同时被抹掉、无从恢复。
    """
    from app.core.auth import _default_users_path
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


# ---------- 启动时把"可变数据到底在哪"打出来（散文换成可观测） ----------
# 一份数据是不是在 data/ 底下，取决于本机有没有一份同名的历史文件（见 data_file
# 的第三档），这件事光读文档猜不准，所以把它打印出来。下面两条钉的是：这张表
# 必须**全**（漏一份就等于那份永远不出现在日志里），以及它必须说真话。

def test_resolve_all_data_paths_covers_every_store(monkeypatch, tmp_path):
    from app.core.paths import DATA_PATH_ENV_VARS, resolve_all_data_paths

    # 每份存储的默认文件名。以前这一处、上面那张表、还有断言里的标签映射各抄了一份，
    # 加一份存储要改三处，漏一处就是这条锁自己过时。现在只有两份：环境变量名来自
    # DATA_PATH_ENV_VARS（产品代码里的那一份），文件名来自下面这一份，两边必须一一对齐。
    FILENAMES = {"会话": "sessions.json", "身份库": "users.json",
                 "模型服务配置": "providers.json", "附件": "uploads",
                 "长期记忆向量库": "chroma_db", "任务清单": "tasks.json",
                 "用量账本": "usage.json", "日程": "schedule.json"}
    # 反馈与偏好是导入期算好的模块常量，指不走环境变量，所以不在 FILENAMES 里
    assert set(FILENAMES) | {"反馈原文", "偏好摘要"} == set(DATA_PATH_ENV_VARS), \
        f"存储清单与文件名表对不上：{set(FILENAMES) ^ (set(DATA_PATH_ENV_VARS) - {'反馈原文', '偏好摘要'})}"

    redirected = {label: tmp_path / "x" / name for label, name in FILENAMES.items()}
    for label, value in redirected.items():
        monkeypatch.setenv(DATA_PATH_ENV_VARS[label], str(value))

    got = dict(resolve_all_data_paths())
    assert set(got) == set(DATA_PATH_ENV_VARS), \
        f"这张表漏了存储：{set(DATA_PATH_ENV_VARS) ^ set(got)}"
    for label, value in redirected.items():
        assert got[label] == os.path.abspath(str(value)), f"{label} 没跟着 {DATA_PATH_ENV_VARS[label]} 走"
    # 反馈与偏好的常量在 conftest 里被指到临时目录，这里只要求它们出现在表里并且
    # 是绝对路径——它们进的是同一份日志。
    for label in ("反馈原文", "偏好摘要"):
        assert os.path.isabs(str(got[label])), f"{label} 不是绝对路径"


def test_log_data_locations_tells_the_two_reasons_apart(capsys, tmp_path, monkeypatch):
    """这条日志的价值全在"说清为什么"：被指走 ≠ 读的是历史数据。

    两者对运维的意思完全相反——前者是"我故意的"，后者是"你以为在 data/ 底下，
    其实这台机器上还躺着一份项目根的老文件"。混成一句 ⚠️ 就等于没说。
    """
    from app.core.paths import DATA_PATH_ENV_VARS, log_data_locations

    log_data_locations()
    out = capsys.readouterr().out
    for label in DATA_PATH_ENV_VARS:
        assert label in out, f"启动日志里没有 {label}"
    assert "只允许一个服务进程" in out, "单写者规则必须跟日志一起说，否则没人知道它在防什么"
    assert "项目根" in out
    # conftest 把会话库用 SESSION_DB_PATH 指到了临时目录：必须说是"被指走"
    assert "由环境变量 SESSION_DB_PATH 指走" in out, "被环境变量指走的存储不该被报成历史兼容位"

    # 没有任何 env 变量、却又落在 data/ 之外——这才是历史兼容位那种情况
    import app.feedback_storage as fs
    import app.preference_analyzer as pa
    was_fs, was_pa = fs.FEEDBACK_FILE, pa.FEEDBACK_FILE
    monkeypatch.delenv("FEEDBACK_FILE", raising=False)
    elsewhere = str(tmp_path / "legacy" / "feedback.json")
    fs.FEEDBACK_FILE = pa.FEEDBACK_FILE = elsewhere
    try:
        log_data_locations()
        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if "反馈原文" in l]
        assert len(lines) == 1
        assert elsewhere in lines[0]
        assert "不在 data/ 之下" in lines[0], "读历史兼容位必须点名"
        assert "FEEDBACK_FILE" in lines[0], "还要说出用哪个变量能把它收进 data/"
        assert "由环境变量 FEEDBACK_FILE 指走" not in lines[0]
    finally:
        fs.FEEDBACK_FILE, pa.FEEDBACK_FILE = was_fs, was_pa

# 刻意**没有**"再断言 feedback_storage.FEEDBACK_FILE / preference_analyzer.PREFERENCE_FILE
# 必须等于上面这条规则"的那一条：conftest 的 autouse 夹具会把这两个常量 patch 到临时
# 目录，那种断言区分不了真实值与 patch 值，永远绿（写过，已删过一次）。规则本身由上面
# 几条钉住，两个模块各用一行 data_file(...) 接上，接线点就在模块开头第三行。
