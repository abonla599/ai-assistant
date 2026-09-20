#!/usr/bin/env python3
"""在没有 Gradle、也没有 Android SDK 的机器上跑一遍壳的 core/ 单测。

为什么要有这个文件：CI 那一条（.github/workflows/android-tests.yml）跑的是
`gradle testDebugUnitTest`，从推上去到看见红要一两分钟，而且本机根本没有 Gradle
——以前每次改完 core/ 里那点纯逻辑，唯一的反馈来源就是"推上去赌一把"。这个台架
把那段等待换成三秒钟的本地判据，代价是它只跑 core/：那里的类不 import android.*，
所以真能在普通 JVM 上跑（CI 注释里"纯 JVM 真跑、不引 Robolectric"说的就是同一件事）。

它不替代 CI，只替代"改一行等两分钟"。R.*、清单合并、资源引用这些还得靠 gradle 出包。

用法
----
  python tools/shell_jvm_tests.py            # 跑全部
  python tools/shell_jvm_tests.py Release    # 只跑类名里含 Release 的
  python tools/shell_jvm_tests.py --keep     # 编译产物留在原地不清，方便 javap

退出码有讲究，别只看"非零"：
  0 = 真的跑过了且全绿
  1 = 跑起来了，有用例失败
  2 = 根本没跑成（缺工具链 / core/ 里出现了 android 依赖）
把 2 当成"没问题"是最容易养出来的坏习惯——台架缺件时它什么也不说，和全绿长得一样。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN_SRC = REPO / "android" / "app" / "src" / "main" / "java"
TEST_SRC = REPO / "android" / "app" / "src" / "test" / "java"
CORE_PKG_DIR = MAIN_SRC / "xyz" / "fenever" / "assistant" / "core"
TEST_PKG_DIR = TEST_SRC / "xyz" / "fenever" / "assistant" / "core"
RELEASE = "17"          # 与 android/app/build.gradle 的 sourceCompatibility 同值

IS_WINDOWS = os.name == "nt"


def say(msg=""):
    print(msg, flush=True)


def fail_to_run(reason, fix):
    say("")
    say(f"没能跑起来：{reason}")
    say(f"怎么办：{fix}")
    sys.exit(2)


# --------------------------------------------------------------- 工具链发现
def find_tool(name):
    """先认 JAVA_HOME/bin，再退到 PATH。

    只查 PATH 的话，装了多个 JDK 的机器上很可能撞上另一个版本的 javac，
    而 --release 17 在旧 javac 上是"未知选项"——那种失败长得很像代码写错了。
    """
    home = os.environ.get("JAVA_HOME")
    if home:
        cand = Path(home) / "bin" / (name + (".exe" if IS_WINDOWS else ""))
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def android_jar_candidates():
    """按"最可能在 → 最后兜底"排。多个 SDK 平台时取编号最大的那个。"""
    roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk")
    roots.append(Path.home() / ".qoder-cn" / "tmp" / "android-platform")

    found = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(root.glob("platforms/android-*/android.jar"))
        found.extend(root.glob("android-*/android.jar"))
    return sorted(set(found), key=lambda p: _api_of(p), reverse=True)


def _api_of(path):
    m = re.search(r"android-(\d+)", str(path.parent))
    return int(m.group(1)) if m else 0


def jar_candidates(patterns):
    """patterns: [(目录, glob), ...]，返回第一个存在的。"""
    out = []
    for base, glob in patterns:
        root = Path(os.path.expanduser(base)) if base.startswith("~") else Path(base)
        if root.is_dir():
            out.extend(sorted(root.glob(glob), reverse=True))
    return out


def find_junit():
    tmp = str(Path("~/.qoder-cn/tmp/junitlib").expanduser())
    m2 = str(Path.home() / ".m2" / "repository")
    jars = jar_candidates([
        (tmp, "junit-4*.jar"),
        (m2, "junit/junit/4*/junit-4*.jar"),
    ])
    hamcrest = jar_candidates([
        (tmp, "hamcrest-core-1.3.jar"),
        (m2, "org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar"),
    ])
    return jars[:1], hamcrest[:1]


# --------------------------------------------------------------- 契约检查
def core_is_pure_jvm():
    """core/ 一旦 import android.*，这个台架就失去意义（跑起来只会 "Stub!"）。

    与其让它在运行时抛一个看不懂的 "Stub!"，不如在这里把话说清楚：
    那一层要验证得走 CI 的 gradle，或者引 Robolectric。
    """
    offenders = []
    for path in sorted(CORE_PKG_DIR.glob("*.java")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*import\s+(android|androidx)\.", line):
                offenders.append(f"{path.name}:{n} {line.strip()}")
    return offenders


def test_class_names(only):
    names = []
    for path in sorted(TEST_PKG_DIR.glob("*Test.java")):
        cls = f"xyz.fenever.assistant.core.{path.stem}"
        if not only or only.lower() in path.stem.lower():
            names.append(cls)
    return names


# ------------------------------------------------------------------ 主流程
def main(argv):
    # 控制台默认按 GBK 解释字节（Windows 中文区），中文输出会糊成一团。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    keep = "--keep" in argv
    only = next((a for a in argv if not a.startswith("-")), "")

    if not CORE_PKG_DIR.is_dir() or not TEST_PKG_DIR.is_dir():
        fail_to_run(f"找不到源码目录 {CORE_PKG_DIR if not CORE_PKG_DIR.is_dir() else TEST_PKG_DIR}",
                    "这个脚本要跟着仓库走：cd 到 ai-assistant 目录再跑，别单独拷走它")

    javac, java = find_tool("javac"), find_tool("java")
    if not (javac and java):
        fail_to_run("PATH/JAVA_HOME 里没有 javac 或 java",
                    "装 JDK 17 及以上，或设 JAVA_HOME 指向它的安装目录")

    jars = android_jar_candidates()
    if not jars:
        fail_to_run("找不到 android.jar",
                    "装 Android SDK（%LOCALAPPDATA%\\Android\\Sdk\\platforms\\android-34\\android.jar），"
                    "或把平台包放到 ~/.qoder-cn/tmp/android-platform/ 下")

    junit_jars, hamcrest_jars = find_junit()
    if not junit_jars or not hamcrest_jars:
        fail_to_run("找不到 junit-4*.jar 或 hamcrest-core-1.3.jar",
                    "两个 jar 放 ~/.qoder-cn/tmp/junitlib/，或者用已有的 ~/.m2 仓库")
    android_jar = jars[0]

    offenders = core_is_pure_jvm()
    if offenders:
        fail_to_run("core/ 里出现了 android 依赖，这个台架跑不了它：\n      " + "\n      ".join(offenders),
                    "那一层只能靠 CI 的 gradle 或 Robolectric 验证，别把这里当通过")

    tests = test_class_names(only)
    if not tests:
        fail_to_run(f"{'过滤词 ' + only + ' 没匹配到' if only else '一个 *Test.java 都没找到'}"
                    f"（{TEST_PKG_DIR}）",
                    "确认路径没改名——宁可报「没跑到」，也不要安静地返回 0")

    say("台架：")
    say(f"  javac      {javac}")
    say(f"  android.jar {android_jar}")
    say(f"  junit      {junit_jars[0]}")
    say(f"  hamcrest   {hamcrest_jars[0]}")
    say(f"  测试类     {len(tests)} 个" + (f"（过滤：{only}）" if only else ""))
    say("")

    out_dir = Path(tempfile.mkdtemp(prefix="shell-jvm-tests-"))
    try:
        cp = os.pathsep.join([str(android_jar), str(junit_jars[0]), str(hamcrest_jars[0])])
        sources = sorted(str(p) for p in CORE_PKG_DIR.glob("*.java"))
        sources += sorted(str(p) for p in TEST_PKG_DIR.glob("*.java"))

        compiled = subprocess.run(
            [javac, "--release", RELEASE, "-encoding", "UTF-8", "-d", str(out_dir),
             "-classpath", cp, *sources],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if compiled.returncode != 0:
            say("编译就没过：")
            say((compiled.stdout + compiled.stderr).strip())
            sys.exit(1)

        ran = subprocess.run(
            [java, "-cp", os.pathsep.join([str(out_dir), cp]),
             "org.junit.runner.JUnitCore", *tests],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        report = (ran.stdout + ran.stderr).strip()
        say(report)
        if ran.returncode != 0:
            say("")
            say("有用例失败（上面是 JUnit 的原始输出）。")
            sys.exit(1)
        n = re.search(r"OK \((\d+) tests?\)", report)
        say("")
        say(f"全绿：{n.group(1) if n else '?'} 个用例（这是 JUnit 自己报的数，不是脚本数的）")
    finally:
        if keep:
            say(f"编译产物留在 {out_dir}")
        else:
            shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
