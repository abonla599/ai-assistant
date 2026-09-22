# -*- coding: utf-8 -*-
"""打包闸的锁（tools/package_gate.py）。

CI 不跑 PyInstaller，所以和 test_website.py 对 spec 的做法同形：
闸「挂在 spec 上、拦得住脏工作树、认得出随包文本里的服务商名、也认得豁免标记」
这四件事全部钉成测试——没写进 CI 回归属性的锁等于不存在。
闸本体只用标准库，就是为了让这条测试在无 PyInstaller 的 venv/CI 里也能跑。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "package_gate", str(ROOT / "tools" / "package_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


# ---------- 1. 闸确实挂在唯一的出品入口上 ----------

def test_spec_calls_the_package_gate():
    spec_text = (ROOT / "run_backend.spec").read_text(encoding="utf-8")
    assert "package_gate" in spec_text, "spec 不再引用打包闸——闸被拆了都没人红"
    assert "_package_gate_enforce()" in spec_text, \
        "spec 引了模块却没调用：闸等于挂了个牌子"
    assert spec_text.index("_package_gate_enforce()") < spec_text.index("a = Analysis("), \
        "闸必须跑在 Analysis 之前——等 collect_all 跑完才拒绝，白烧几分钟构建"


def test_gate_is_stdlib_only():
    """闸要能在测试 venv（没有 PyInstaller）里被 import，只许依赖标准库+git。"""
    src = (ROOT / "tools" / "package_gate.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "PyInstaller" not in s, "闸引入了 PyInstaller：" + s


# ---------- 2. 随包文本扫描：拦得住、放得下豁免 ----------

def _fake_root(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_vendor_scan_fires_on_shipped_text(tmp_path):
    root = _fake_root(tmp_path, {
        os.path.join("backend/app/web/site", "probe.html"): '<input value="https://api.deepseek.com">\n',
        os.path.join("backend/app/web/static", "ok.html"): "<p>中性占位</p>\n",
    })
    hits = gate.vendor_violations(str(root))
    assert len(hits) == 1, ("服务商名没被扫出来——F-1g 那批文案就又能原样进包：%r" % (hits,))
    rel, lineno, snippet = hits[0]
    assert rel.endswith("probe.html") and lineno == 1
    assert "deepseek" in snippet.lower()


def test_vendor_scan_honours_allow_marker(tmp_path):
    root = _fake_root(tmp_path, {
        os.path.join("backend/app/web/site", "probe.html"):
            '// 兼容 ": OPENAI-COMPLETION" 注释行  # ship-gate:allow\n',
    })
    assert gate.vendor_violations(str(root)) == []


def test_vendor_scan_skips_third_party_vendor_dir(tmp_path):
    """static/vendor/ 是第三方压缩产物，有自己的 NOTICE 审计，不归这道闸管。"""
    root = _fake_root(tmp_path, {
        os.path.join("backend/app/web/static/vendor", "lib.min.js"): "openai&&qwen\n",
    })
    assert gate.vendor_violations(str(root)) == []


# ---------- 3. 工作树 == HEAD：脏则拒，非 git 也拒 ----------

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def _git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytestmark_git
def test_dirty_worktree_blocks_and_clean_passes(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "seed")
    web = tmp_path / "backend" / "app" / "web" / "site"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<p>干净</p>\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "web")

    assert gate.dirty_lines(str(tmp_path)) == []
    assert gate.enforce(str(tmp_path)) is True  # 干净 + 文本无违禁 → 放行

    (tmp_path / "uncommitted_source.py").write_text("x = 1\n", encoding="utf-8")
    assert gate.dirty_lines(str(tmp_path)), "未跟踪的源文件也算脏——它进包却不进 git"
    with pytest.raises(SystemExit) as e:
        gate.enforce(str(tmp_path))
    assert "工作树" in str(e.value)


@pytestmark_git
def test_non_git_dir_is_refused(tmp_path):
    with pytest.raises(SystemExit) as e:
        gate.enforce(str(tmp_path))
    msg = str(e.value)
    assert "HEAD" in msg or "git" in msg, \
        "无法证明 == HEAD 时必须拒绝，而不是静默放行：" + msg
