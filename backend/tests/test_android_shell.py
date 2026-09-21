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
SHELL_BRIDGE = SHELL_SRC / "xyz" / "fenever" / "assistant" / "ShellBridge.java"
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


def _invocations(src: str, name: str) -> list:
    """这个方法被【调用】的位置：`name(` 与 `::name` 两种写法都算，定义那一行不算。"""
    hits = []
    for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"\s*(?:\(|$)", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        head = src[line_start:m.start()]
        if re.search(r"\b(void|public|private|protected)\b", head):
            continue                                   # 这是定义，不是调用
        hits.append(m.start())
    for m in re.finditer(r"::\s*" + re.escape(name) + r"\b", src):
        hits.append(m.start())
    return sorted(hits)


def test_nothing_checks_for_an_update_unless_the_user_asks():
    """「不点检查更新就拿不到最新安装包」这条承诺的下半段：没有任何自动触发。

    能点燃这条链路的入口恰好三个——长按图标的快捷方式、桌面组件那颗按钮、设置里那一行
    （走桥）——每一个都是"用户自己点的"。今天多一处"顺手在 onResume 里查一下"，这句话
    就变成谎，而且没人会报错：GitHub 那个接口按来源 IP 限 60 次/小时，自动化的第一个代价
    是把配额自己烧光。

    判据是"可数"而不是"看着像"：startUpdateCheck 只容许一个调用者，
    那个调用者（requestUpdateCheck）只容许两处引用，且每处都必须贴着它自己的用户动作判据。
    """
    main = _code(MAIN_ACTIVITY)
    bridge = _code(SHELL_BRIDGE)

    starts = _invocations(main, "startUpdateCheck")
    assert len(starts) == 1, (
        f"startUpdateCheck 只容许一个调用者（现在 {len(starts)} 个）："
        "多出来的那一处就是「不点也会检查更新」")
    assert "void requestUpdateCheck()" in main, "唯一那个调用者必须在 requestUpdateCheck 里"
    body = main[main.index("void requestUpdateCheck()"):]
    assert starts[0] > main.index("void requestUpdateCheck()") and \
        starts[0] < main.index("void requestUpdateCheck()") + body.index("\n    }") + 1, \
        "startUpdateCheck 的调用点跑出了 requestUpdateCheck 的方法体"

    from_main = _invocations(main, "requestUpdateCheck")
    from_bridge = _invocations(bridge, "requestUpdateCheck")
    assert len(from_main) == 1 and len(from_bridge) == 1, (
        f"requestUpdateCheck 的引用数变了：壳内 {len(from_main)}、桥里 {len(from_bridge)}"
        "（每多一处就要在这里说清它是哪个用户动作）")

    line = main[:from_main[0]].count("\n") + 1
    window = "\n".join(main.splitlines()[max(0, line - 9):line - 1])
    assert "isCheckUpdateLaunch" in window, (
        f"MainActivity.java:{line} 的 requestUpdateCheck 不在用户动作的判据后面")
    b_line = bridge[:from_bridge[0]].count("\n") + 1
    b_window = "\n".join(bridge.splitlines()[max(0, b_line - 12):b_line - 1])
    assert "@JavascriptInterface" in b_window and "checkUpdate" in b_window, (
        f"ShellBridge.java:{b_line} 的 requestUpdateCheck 不是从桥方法里来的——"
        "桥方法是页面里【人点出来的】，绕开它就没有自动触发")

    others = [p for p in SHELL_SRC.rglob("*.java")
              if p not in (MAIN_ACTIVITY, SHELL_BRIDGE)]
    assert len(others) >= 8, "没扫到别的壳源码文件，这条锁在空转"
    for path in others:
        body = _code(path)
        assert "startUpdateCheck" not in body and "requestUpdateCheck" not in body, (
            f"{path.name} 里出现了触发更新的符号：它会在没人点的时候跑")


def test_bridge_advertises_the_update_capability_the_page_depends_on():
    """capabilities() 必须报 update 与 version，网页那一行全靠这两个键决定画法。

    少一个键不会让任何东西崩：app.js 只认不到 caps.update，于是所有手机上的这一行
    永远退化成"去下载页"，而 v0.16 明明能真的查——这是一条会静默生效的回归。
    """
    bridge = _code(SHELL_BRIDGE)
    block = bridge[bridge.index("public String capabilities()"):]
    block = block[:block.index("\n    }")]
    assert '"update"' in block and "1L" in block, f"capabilities 没报 update：{block}"
    assert '"version"' in block and "BuildConfig.VERSION_NAME" in block, \
        "capabilities 没报 version：设置那一行就没地方显示装的是哪一版"


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


def test_released_apks_are_signed_with_one_pinned_key():
    """发出去的每个包必须签在**同一把**密钥上——这条为一次真实的"更新失败"而写。

    Android 判能不能覆盖安装看的是签名，不看版本号。发布流之前跑的是 assembleDebug，
    而每台 GitHub runner 都会现造一把 debug keystore：实测 v0.15 与 v0.16 两个 Release
    的证书 SHA-256 指纹不同（60f1ded9… / 8de2fbe3…），所以用户点"检查更新"永远装不上，
    只能卸载重装。debug 签名还带 android:debuggable=true——任何能连 adb 的人都能读出
    壳里 localStorage 存的会话令牌。

    所以这里钉三件：① 发布流不许再出 debug 包；② 密钥从 Secrets 来、从环境变量读，
    仓库里不许躺任何密钥文件；③ 缺 secret 时必须**失败**，不许悄悄退回现造一把新钥匙
    ——那等于再给用户制造一次"必须卸载重装"，而这件事一旦发生就收不回来。
    """
    raw_workflow = (REPO_ROOT / ".github" / "workflows" / "release-apk.yml").read_text(encoding="utf-8")
    # 判 YAML 的正文，不判注释：这条锁要禁的字符串（assembleDebug）正是注释里
    # 解释"为什么禁它"时用到的那个词——拿原文去 grep，锁会因为它自己说的话而红。
    workflow = "\n".join(ln for ln in raw_workflow.splitlines() if not ln.lstrip().startswith("#"))
    gradle = (REPO_ROOT / "android" / "app" / "build.gradle").read_text(encoding="utf-8")

    for forbidden in ("assembleDebug", "apk/debug/"):
        assert forbidden not in workflow, f"发布流又回到 {forbidden}：每次一把新 debug 钥匙，老用户永远装不上"
    assert "assembleRelease" in workflow, "发布流不再出 release 包了"
    assert "secrets.APK_KEYSTORE_BASE64" in workflow, "签名密钥不再来自 Secrets"
    assert 'if [ -z "${KEY_B64:-}" ]' in workflow and "exit 1" in workflow, (
        "缺 secret 时没有停下来：宁可发布失败，也不该发一把新钥匙签的包")
    assert "keytool" in workflow, "不再把签进包里的指纹打进日志——下次查签名问题又要靠回忆"

    assert 'System.getenv("APK_KEYSTORE")' in gradle, "gradle 不再从环境变量读密钥路径"
    assert "APK_KEYSTORE_PASSWORD" in gradle and "APK_KEY_ALIAS" in gradle, "签名四项缺项：配了路径没配口令"
    for leak in ("storeFile file(\"release.jks\")", "keyPassword \""):
        assert leak not in gradle, f"gradle 里出现了写死的密钥材料：{leak}"


def _gradle_block(text: str, header: str) -> str:
    """取 `header {` 到与之配对的那个 `}` 之间的正文（按花括号深度配对，跨行算）。

    判"某一行在不在文件里"永远看不出**它写在哪儿**，而 Groovy DSL 恰恰是按位置解释的：
    同一句 storeFile 放进 signingConfigs 是对的，放进 buildTypes 是配置期就炸。
    """
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    raise AssertionError(f"{header} 这块没有闭合，gradle 文件本身就不对")


def test_the_signing_material_sits_in_signingconfigs_not_buildtypes():
    """签名四项 + storeType 必须待在 signingConfigs 里，buildTypes 只许引用它。

    为一次真实的发版失败而写：v0.17 第一次跑 release-apk.yml，"Restore the pinned
    signing key" 过了、"Assemble release APK" 炸了。原因是那几行被写在
    `buildTypes.release { … }` 里面——`BuildType` 没有 storeFile 这个属性，Groovy 在
    配置期就报 unknown property。上一条锁（test_released_apks_are_signed_with_one_pinned_key）
    当时是全绿的，因为它只问"这些名字在文件里出现过吗"，而它们确实出现过、只是位置错了。
    本机没有 AGP 也跑不了 gradle，所以位置只能这样钉。
    """
    raw = (REPO_ROOT / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    gradle = _strip_java_comments(raw)          # 注释里也出现过 storeFile 这个词
    signing = _gradle_block(gradle, "signingConfigs")
    build_types = _gradle_block(gradle, "buildTypes")

    for prop in ("storeFile", "storePassword", "keyAlias", "keyPassword", "storeType"):
        assert prop in signing, f"{prop} 不在 signingConfigs 里：密钥配不出签名"
        assert prop not in build_types, (
            f"{prop} 又回到 buildTypes 里了：BuildType 没这个属性，配置期直接报错，"
            "而 CI 之前跑 assembleDebug 时这段根本不执行，所以看不见")

    release = _gradle_block(build_types, "release")
    assert re.search(r"signingConfig\s+signingConfigs\.release", release), \
        "release 这个 buildType 没挂上 signingConfig：签出来的包与那把钉死的密钥无关"
    assert 'storeType "PKCS12"' in signing, \
        "storeType 不再写死：JDK 默认值一改，gradle 就会拿 JKS 去读一把 PKCS12"

    # 两边说的是同一件事：生成脚本也得显式写 PKCS12，不能靠 keytool 的默认值
    generator = (REPO_ROOT / "tools" / "make-apk-keystore.ps1").read_text(encoding="utf-8")
    assert "-storetype PKCS12" in generator, \
        "生成脚本靠 JDK 默认格式，而 gradle 写死了 PKCS12——两边有一边会先漂"


def test_no_signing_key_lives_in_the_repository():
    """密钥文件一旦进了这个公开仓库，就等于把"能给他的用户发更新"的能力公开送人。

    tools/secret_scan.py 已经按扩展名挡了 .jks/.keystore，那条是"提交了会红"；
    这条是"根本没打算提交"的正向确认——两件事都写下来，下一个人才不会觉得多余。
    """
    import subprocess

    listed = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO_ROOT),
                            capture_output=True, timeout=60)
    assert listed.returncode == 0, "问不到 git，这条锁就是空的"
    tracked = [p.decode() for p in listed.stdout.split(b"\0") if p]
    bad = [p for p in tracked if p.endswith((".jks", ".keystore")) or p.endswith("keystore.properties")]
    assert not bad, f"版本库里躺着签名密钥：{bad}"


def test_the_keystore_generator_refuses_to_overwrite():
    """生成脚本必须拒绝覆盖已有的密钥。

    丢了这把钥匙没有补救办法：从此每个新包都是新签名，所有装过的人都要先卸载。
    一个"顺手再跑一次"就把这件事做掉的脚本，比没有脚本更危险。
    """
    script = (REPO_ROOT / "tools" / "make-apk-keystore.ps1").read_text(encoding="utf-8")
    assert "REFUSING TO OVERWRITE" in script, "覆盖前不再拦一道"
    assert "exit 2" in script, "拦下来却不以非零退出：脚本照样被下一步当成成功"
    assert "-validity 10000" in script, "证书有效期缩短会让未来的包签不上（Android 要求签名证书有效到 2033 之后）"
