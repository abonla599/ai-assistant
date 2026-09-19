"""发版前的闸门：版本号涨了，本版变更清单必须跟着写。

release-apk.yml 会把 docs/releases/v<版本号>.md 拼在通用安装说明前面。文件不在的时候它
**不报错**，只打一条 ::warning:: 然后发出一份没有变更清单的 Release——正是"效果没了
但不报错"那一类。所以拦的位置在这里：develop 一推送就红，而不是等 Release 发出去才发现。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_GRADLE = REPO_ROOT / "android" / "app" / "build.gradle"
RELEASE_NOTES_DIR = REPO_ROOT / "docs" / "releases"


def current_version_name() -> str:
    """versionName 的唯一来源。工作流里的 `Check tag matches versionName` 读的是同一个字段。"""
    text = BUILD_GRADLE.read_text(encoding="utf-8")
    m = re.search(r'^\s*versionName\s+"([^"]+)"', text, re.M)
    assert m, f"{BUILD_GRADLE} 里找不到 versionName——是这里读法变了，还是那一行被删了"
    return m.group(1)


def test_this_versions_release_notes_exist():
    ver = current_version_name()
    path = RELEASE_NOTES_DIR / f"v{ver}.md"
    assert path.is_file(), (
        f"versionName 已经是 {ver}，但没有 docs/releases/v{ver}.md。"
        f"发出去的 Release 会安静地少掉整份本版变更清单（工作流只 warn 不 fail）。")
    assert path.read_text(encoding="utf-8").strip(), f"{path.name} 是空文件，等于没写"


def test_release_notes_are_named_after_a_real_tag():
    """文件名要能对上 git tag。写成 0.14.md 那种（漏了 v）工作流永远找不到。"""
    if not RELEASE_NOTES_DIR.is_dir():
        pytest.skip("还没有 docs/releases/ 目录")
    bad = [p.name for p in RELEASE_NOTES_DIR.glob("*.md")
           if not re.fullmatch(r"v\d+\.\d+\.md", p.name)]
    assert not bad, f"这些文件名对不上 tag 形状 v<主>.<次>.md：{bad}"


def test_the_workflow_reads_the_notes_file_by_tag_not_by_bare_version():
    """钉住那个刚踩过的坑：${ver} 是 ${tag#v}，v 已经被剥掉了。

    写成 docs/releases/${ver}.md 时脚本去找 0.14.md，永远找不到，于是每次发版都走
    "没有清单"那条兜底分支——而那条分支是静默的。
    """
    wf = (REPO_ROOT / ".github" / "workflows" / "release-apk.yml").read_text(encoding="utf-8")
    assert 'notes="docs/releases/${tag}.md"' in wf, (
        "工作流不再按 tag 找变更清单文件了；若确实换了命名规则，这条和上面那条要一起改")
    assert 'docs/releases/${ver}' not in wf, "又用回 ${ver} 了：那会去找少一个 v 的文件名"
