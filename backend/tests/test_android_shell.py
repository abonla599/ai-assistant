"""壳（android/）里那些只能在真机上暴露、但源码形状就能判定的坑。

本机没有模拟器也没有真机，所以这里全是"读源码"的锁，不是行为测试。它们的价值不在于
证明功能对，而在于**不让注释重新变成第二个事实来源**——下面这条就是为一次真实闪退写的。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_SRC = REPO_ROOT / "android" / "app" / "src" / "main" / "java"

# Android 14（targetSdk 34）起，Context.registerReceiver 只有在过滤器【全部】命中
# AOSP IntentFilter.SYSTEM_ONLY_ACTIONS 那张硬编码表时才能省掉导出标志，否则直接抛
# SecurityException。android.intent.action.DOWNLOAD_COMPLETE 不在那张表里。
EXPORT_FLAGS = ("RECEIVER_EXPORTED", "RECEIVER_NOT_EXPORTED")


def _call_args(src: str, open_paren: int) -> str:
    """从左括号起取到配对的右括号，跨行也算。"""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
    return src[open_paren:]


def _strip_java_comments(src: str) -> str:
    """去掉 // 与 /* */ 两种注释，但【尊重字符串字面量】。

    不尊重字符串的话就毁在自家门口：LATEST_URL 的值里那句
    "https://api.github.com/..." 会被当成注释从 `//` 起整段切掉，
    于是"全仓只有一处提到 api.github.com"这条锁会因为读不到而永远绿着。
    注释里的符号也不该算数：把一行注掉不等于把那处调用删了。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            quote = c
            out.append(c)
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                out.append(src[i])
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")            # 保住两侧的换行以外的分词边界
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _code(path: Path) -> str:
    return _strip_java_comments(path.read_text(encoding="utf-8"))


def test_every_runtime_receiver_registration_names_an_export_flag():
    """v0.13 崩在 MainActivity.onCreate 里的原因：registerReceiver 少给一个标志。

    那条注释当时写着"属系统广播，因此不必传 RECEIVER_EXPORTED"——这个豁免不成立，
    于是 Android 14+ 的设备上点图标闪一下回桌面，而服务端一条请求都收不到。

    带标志的重载是 API 33 才有的，minSdk 23 必须按版本分岔，所以"没带标志"本身不是罪：
    只有当它**同时**不在 SDK_INT 判据的保护下才红。这是源码形状的近似（往回看 12 行），
    不是精确的控制流分析——它守的是"别再靠注释宣称豁免"，不是编译器。
    """
    offenders = []
    sites = 0
    for path in sorted(SHELL_SRC.rglob("*.java")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<!un)\bregisterReceiver\s*\(", src):
            sites += 1
            args = _call_args(src, m.end() - 1)
            if any(flag in args for flag in EXPORT_FLAGS):
                continue
            line = src[:m.start()].count("\n") + 1
            guarded = "SDK_INT" in "\n".join(src.splitlines()[max(0, line - 13):line - 1])
            if not guarded:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")

    # 正向对照：路径写错时 rglob 一个文件都找不到，offenders 空着也"通过"——那这条锁
    # 就只是在空转，而它绿得和真的守住时一模一样。CI 里跑的正是这条命令，所以这里必须钉。
    assert sites >= 1, f"在 {SHELL_SRC} 下一个 registerReceiver 调用都没扫到：要么路径错了，要么这条锁没用了"

    assert not offenders, (
        "这些 registerReceiver 既没给导出标志、也不在 SDK_INT 保护下，"
        "Android 14+ 上会抛 SecurityException：" + "、".join(offenders)
    )


# ---------- 「检查更新」这条新链路：边界与"三处必须一起改" ----------

SHELL_RES = REPO_ROOT / "android" / "app" / "src" / "main" / "res"
MAIN_ACTIVITY = SHELL_SRC / "xyz" / "fenever" / "assistant" / "MainActivity.java"
SHELL_EVENTS = SHELL_SRC / "xyz" / "fenever" / "assistant" / "core" / "ShellEvents.java"
SHORTCUT_PLAN = SHELL_SRC / "xyz" / "fenever" / "assistant" / "core" / "ShortcutPlan.java"
SHORTCUTS_XML = SHELL_RES / "xml" / "shortcuts.xml"
WIDGET_LAYOUT = SHELL_RES / "layout" / "widget_assistant.xml"
WIDGET_JAVA = SHELL_SRC / "xyz" / "fenever" / "assistant" / "AssistantWidget.java"
MANIFEST = REPO_ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml"


def _const_values(path: Path) -> dict:
    """把 `public static final String OPEN_CAMERA = "camera";` 读成 {OPEN_CAMERA: camera}。"""
    body = path.read_text(encoding="utf-8")
    return dict(re.findall(r'public\s+static\s+final\s+String\s+(\w+)\s*=\s*"([^"]*)"\s*;', body))


def _wanted_entries() -> list:
    """ShortcutPlan.WANTED 里引的那几个 ShellEvents 常量，折成它们的字面值。"""
    plan = _code(SHORTCUT_PLAN)
    block = re.search(r"WANTED\s*=\s*Collections\.unmodifiableList\((.*?)\)\;", plan, re.S)
    assert block, "读不到 ShortcutPlan.WANTED：这条锁的名字跟着源码变了"
    names = re.findall(r"ShellEvents\.(OPEN_\w+)", block.group(1))
    assert names, "ShortcutPlan.WANTED 是空的，那这条锁在空转"
    values = _const_values(SHELL_EVENTS)
    missing = [n for n in names if n not in values]
    assert not missing, f"WANTED 引用了没定义的常量：{missing}"
    return [values[n] for n in names]


def test_nothing_checks_for_an_update_unless_the_user_asks():
    """「不点检查更新就拿不到最新安装包」这条承诺的下半段：没有任何自动触发。

    唯一的调用点必须在 isCheckUpdateLaunch 的判据后面。今天多一处"顺手在 onResume 里查一下"，
    这句话就变成谎，而且没人会报错——GitHub 那个接口是按来源 IP 限 60 次/小时的，
    自动化的第一个代价是把配额自己烧光。
    """
    src = _code(MAIN_ACTIVITY)
    assert len(re.findall(r"void\s+startUpdateCheck\s*\(", src)) == 1, "定义本身应当只有一处"
    # 只数【真调用】（后面紧跟分号）：注释里那句 {@link #startUpdateCheck()} 不是调用点，
    # 把它算进来会让这条锁在"谁都没多写一处"的情况下天天红。
    invocations = [m.start() for m in re.finditer(r"\bstartUpdateCheck\s*\(\s*\)", src)
                   if src[m.end():].lstrip().startswith(";")]
    assert len(invocations) == 1, (
        f"startUpdateCheck 应当只有【一个】调用点（现在 {len(invocations)} 个），"
        "多出来的那一处就是「不点也会检查更新」")
    line = src[:invocations[0]].count("\n") + 1
    window = "\n".join(src.splitlines()[max(0, line - 9):line - 1])
    assert "isCheckUpdateLaunch" in window, (
        f"MainActivity.java:{line} 的 startUpdateCheck 不在用户动作的判据后面")

    # 其它任何文件都不许触发它（开机广播、到点通知、组件刷新是最容易"顺手"加的地方）。
    # 只锁 startUpdateCheck 这一个符号：core/ 里出现 ReleasePlan 是它自己的类名，不是触发点。
    others = [p for p in SHELL_SRC.rglob("*.java") if p != MAIN_ACTIVITY]
    assert len(others) >= 8, "没扫到别的壳源码文件，这条锁在空转"
    for path in others:
        assert "startUpdateCheck" not in _code(path), (
            f"{path.name} 里出现了 startUpdateCheck：它会在没人点的时候检查更新")


def test_the_release_endpoint_is_named_exactly_once():
    """全仓只有一处提到 api.github.com，而且就在 ReleasePlan.LATEST_URL。

    两处地址等于两个事实来源：改一处的时候另一处会安静地继续打老地方。
    """
    hits = sorted(p.relative_to(REPO_ROOT).as_posix()
                  for p in (SHELL_SRC.rglob("*.java"))
                  if "api.github.com" in _code(p))
    assert hits == ["android/app/src/main/java/xyz/fenever/assistant/core/ReleasePlan.java"], \
        f"api.github.com 出现在这些地方：{hits}"


def test_install_intent_and_its_permission_arrive_together():
    """起安装页与 REQUEST_INSTALL_PACKAGES 必须同批存在。

    只改一边各有两种坏法：有 Intent 没权限 → 点了下载永远装不上（还会被 catch 成一句
    "去设置"，看起来像 ROM 的问题）；有权限没 Intent → 白要一个吓人的授权，
    而"这个应用想装别的软件"是会被用户记住的那一类声明。
    """
    src = _code(MAIN_ACTIVITY)
    manifest = MANIFEST.read_text(encoding="utf-8")
    fires = "package-archive" in src
    declared = "REQUEST_INSTALL_PACKAGES" in manifest
    assert fires and declared, (
        "安装链路断了一头：" + ("清单没声明 REQUEST_INSTALL_PACKAGES" if not declared
                                else "代码里没有起安装页的那一句"))


def test_every_launcher_entry_exists_in_all_three_places():
    """长按菜单、桌面组件、以及"点进来以后认不认"这三处必须列同一批入口。

    漏在哪一处的表现都是"那颗按钮点了没反应"或"桌面上干脆少一颗"，而它不会让任何
    Java 编译失败——正是本仓反复栽的那一类。
    """
    wanted = set(_wanted_entries())
    assert wanted, "WANTED 折出来是空的，这条锁在空转"

    xml = SHORTCUTS_XML.read_text(encoding="utf-8")
    in_shortcuts = set(re.findall(r'assistant://open/([A-Za-z0-9_]+)', xml))
    assert in_shortcuts == wanted, (
        f"shortcuts.xml 与 ShortcutPlan.WANTED 对不上：只在 XML {sorted(in_shortcuts - wanted)}、"
        f"只在 WANTED {sorted(wanted - in_shortcuts)}（对不上的那条会被冷启动自检补成重复项）")

    layout = WIDGET_LAYOUT.read_text(encoding="utf-8")
    drawn = set(re.findall(r'android:id="@\+id/(btn\w+)"', layout))
    wired = set(re.findall(r"R\.id\.(btn\w+)", _code(WIDGET_JAVA)))
    assert drawn == wired and drawn, (
        f"组件布局里的按钮 {sorted(drawn)} 与代码挂上事件的 {sorted(wired)} 不是同一批——"
        "画出来但没挂 PendingIntent 的那颗就是死按钮")
