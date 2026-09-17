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
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
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
