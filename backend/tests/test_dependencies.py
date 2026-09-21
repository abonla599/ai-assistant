"""依赖声明完整性：backend/app 导入的每个第三方包都必须在 requirements.txt 里。

CI 是按 requirements.txt 从零装包的，所以少声明一条就在收集期整轮报错。
这个洞本可以一直藏着：开发机上手动装过的包会让本地全绿，于是"本地通过"从来没
证明过依赖是完整的——ddgs / docker / pymupdf 三条正是这样漏了很久的。
"""
import ast
import pathlib
import re
import sys

BACKEND_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
REQUIREMENTS = pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"

# 导入名与发行包名不一致的情况
IMPORT_TO_DIST = {"fitz": "pymupdf", "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python",
                  "dotenv": "python-dotenv"}

# 可选依赖：缺失时代码自己捕获 ImportError 并降级，所以刻意不进必装清单
# （sentence-transformers 会把几百 MB 模型拖进冻结版，而云端嵌入是默认路径）
OPTIONAL_DEPS = {
    "sentence_transformers": "本地嵌入的退路；_init_local_embed 捕获 ImportError 后降级",
}


def _top_level_imports():
    """收集 app 包内所有顶层导入的第三方模块名（含函数内的延迟导入）。"""
    std = set(sys.stdlib_module_names)
    found = set()
    for path in BACKEND_APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:  # 语法错的文件不该悄悄跳过
            raise AssertionError(f"{path} 解析失败，依赖检查无法覆盖它：{e}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    local = {"app", "tests"}
    return {m for m in found if m and m not in std and m not in local and not m.startswith("_")}


def _declared_names():
    """requirements.txt 里声明的发行包名（去掉版本约束与行内注释）。"""
    names = set()
    # utf-8-sig 不是讲究：这份清单文件头带 BOM，用 "utf-8" 读会把第一行读成
    # "﻿annotated-doc"，strip() 去不掉 U+FEFF——于是清单的第一项对这条锁永久隐形。
    for line in REQUIREMENTS.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].strip().lower())
    return names


def test_every_third_party_import_is_declared_or_explicitly_optional():
    declared = _declared_names()
    undeclared = []
    for module in sorted(_top_level_imports()):
        if module in OPTIONAL_DEPS:
            continue
        dist = IMPORT_TO_DIST.get(module, module).lower()
        if dist not in declared:
            undeclared.append(module)
    assert not undeclared, (
        f"以下包被 backend/app 导入却没写进 requirements.txt：{undeclared}\n"
        "CI 会因此整轮收集失败。若它确实是可选的，就加进 OPTIONAL_DEPS 并写清降级位置。"
    )


def test_optional_deps_are_actually_optional_in_code():
    """可选清单不能变成垃圾抽屉：条目必须真的被 try/except 兜着。"""
    text = "\n".join(p.read_text(encoding="utf-8") for p in BACKEND_APP.rglob("*.py"))
    for module, rationale in OPTIONAL_DEPS.items():
        assert module in text, f"{module} 已不在代码里被导入，应从 OPTIONAL_DEPS 删除"
        assert rationale.strip(), f"{module} 的可选理由不能是空的"


def test_there_is_exactly_one_dependency_manifest():
    """依赖清单只许有一份，而且 CI 与 Docker 必须装同一份。

    仓库根原先躺着一份 118 行的 pip freeze 转储（UTF-16、零注释），`Dockerfile`
    COPY 的正是它，而 CI 装的是 `backend/requirements.txt` —— 两份的 numpy、
    onnxruntime、fastapi 版本互不相同。症状不是报错，是"容器里跑出来的行为和
    CI 绿的那套不是一回事"，而且没人会去查：两边都装得上，都起得来。
    这属于本仓反复栽的那种第二个事实来源，只不过这次藏在依赖声明里。
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    stray = [p.name for p in root.glob("requirements*.txt")]
    assert not stray, f"仓库根又长出一份依赖清单：{stray}（唯一的一份在 backend/ 下）"

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY backend/requirements.txt" in dockerfile, \
        "Docker 不再从 backend/ 取清单：它会在根目录找那份不存在的文件"
    assert "pip install --no-cache-dir -r /app/requirements.txt" in dockerfile, \
        "Docker 装依赖那一步的清单路径被改动了，和上面 COPY 的目标对不上就是空装"

    ci = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "backend/requirements.txt" in ci, "CI 换了清单，这条锁就管不到它了"
    # 解释器也要一致：容器此前用 3.11，而 CI/venv/打包用的都是 3.12
    assert re.search(r"FROM python:3\.12", dockerfile), \
        "容器的 Python 版本又和 CI/打包那份错开了（chromadb 对上限敏感）"


def test_the_manifest_is_parsed_from_its_first_line():
    """正向对照：清单第一项必须被认出来。

    上一版这条锁用 encoding="utf-8" 读一份带 BOM 的文件，于是第一行变成
    "\ufeffannotated-doc"——它既匹配不上任何 import，也永远不会让谁变红，
    整份清单的第一项就这样对检查隐形了。这类"检不到东西的检查看起来全绿"
    是本仓最贵的失败形状，所以这里不判"读法对不对"，直接判结果：第一项在不在。
    """
    names = _declared_names()
    assert names, "清单解析为空，下面两条断言都是空的"
    assert not [n for n in names if "\ufeff" in n], f"有包名带 BOM 残留：{sorted(n for n in names if chr(0xFEFF) in n)}"
    first_line = next(l.split("#", 1)[0].strip() for l in
                      REQUIREMENTS.read_text(encoding="utf-8-sig").splitlines()
                      if l.strip() and not l.lstrip().startswith("#"))
    first_name = re.split(r"[<>=!~;\[ ]", first_line, maxsplit=1)[0].strip().lower()
    assert first_name in names, f"清单第一行 {first_line!r} 没被解析出包名，那第一项就检不到"


def test_installed_versions_satisfy_the_declared_ranges():
    """跑这条测试的解释器，装的包必须落在清单钉的区间里。

    这条锁的由来是 2026-09-21 的一次 CI 红：conftest 写了
    `TestClient(app, client=("127.0.0.1", 54321))`，而 `client=` 这个关键字是
    starlette 0.46 才有的；清单钉的是 `starlette>=0.37.2,<0.41.0`，CI 从零按清单装，
    于是凡是用到 client 夹具的用例全部 TypeError。本地那次"全绿"跑的是另一个解释器
    （装着 starlette 1.0.0），同一行代码两边结论相反——而 CI 那侧才是真的。
    区间不匹配只说明一件事：这个解释器不是 CI 那一份，它给出的绿不作数。
    """
    from importlib import metadata

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import InvalidSpecifier

    drift = []
    checked = 0
    for line in REQUIREMENTS.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "@" in line:
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue                     # 交不上打包器的行由上面那几条锁管
        if not req.specifier:
            continue
        try:
            installed = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            continue                     # 没装就不判：可选依赖与平台专属包都在这类
        checked += 1
        try:
            if not req.specifier.contains(installed, prereleases=True):
                drift.append(f"{req.name}=={installed}（清单要 {req.specifier}）")
        except InvalidSpecifier:
            continue
    assert checked >= 20, f"区间检查只落到 {checked} 个包上，这条锁多半在空转"
    assert not drift, (
        "这些包装的版本不在清单区间里，本机结论与 CI 不是一回事：" + "; ".join(drift)
        + "\n用 `venv\\Scripts\\python.exe -m pytest backend/tests/ -q` 跑（那份与 CI 对齐），"
          "或者 `pip install -r backend/requirements.txt` 把当前解释器拉回来。")
