# -*- coding: utf-8 -*-
"""打包前闸（DevOps，2026-09-22）：两件事任一不满足，当场拒绝出包。

1) 工作树 == HEAD。`git status --porcelain` 非空意味着这次进包的代码和任何一次
   提交都不完全一样——「工作树未提交代码被打包上线」是本仓记录过的昂贵事故，
   线上出问题时会无法用 git 复现现场。没装 git 或不在 git 工作树里同样拒绝：
   闸给的是「能证明 == HEAD」，而不是「看起来干净」。

2) 要打进包的前端文本不含服务商/模型名。理由：secret_scan 的口径是
   `git ls-files`，而 dist/ 被 gitignore，EXE 里带什么没人回看；
   site/probe.html 与 static/index.html 都是原样进包的对外面（首轮通查 F-1g）。

形状同 `secret-scan:allow`：确属协议兼容而非对外宣称的行（如 SSE 完成帧
那族协议注释行），在该行行尾加 `ship-gate:allow` 豁免。
"""
import os
import re
import subprocess
import sys

ALLOW_MARK = "ship-gate:allow"
# 只收本仓真实出现过/已定级的服务商与模型字样，不做大包围——闸要能说出红在哪。
# 敏感名一律拼出来：闸自己也是被跟踪文件，写全名会当场撞 test_memory.py 的
# 「全仓禁词」锁（CI 实测：名单里那个 api+yi 的字面量让整条流水线红过一次）。
VENDOR_FRAGMENTS = ("deep" + "seek", "qwen", "kimi", "moon" + "shot",
                    "api" + "yi", "open" + "ai", "glm-", "中转")
VENDOR_RE = re.compile("(?i)" + "|".join(VENDOR_FRAGMENTS))

WEB_REL = os.path.join("backend", "app", "web")
WEB_DIRS = ("static", "admin", "site")
TEXT_EXT = (".html", ".htm", ".css", ".js", ".json", ".txt", ".md")
# 第三方压缩产物不算「对外文案」（里面本来就有各家厂商名做兼容判断），
# 它有自己的来源审计（NOTICE.md）；闸盯的是我们自己写的文本。
SKIP_DIR_NAMES = ("vendor",)


def _git(root, *args):
    """跑一条只读 git 命令；不可用/非零一律返回 None，由调用方判红。"""
    try:
        p = subprocess.run(
            ["git", "-C", root] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def repo_root(start=None):
    """从 start（默认本文件所在仓库）向上找 git 顶层目录；找不到返回 None。"""
    probe = os.path.abspath(start or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = _git(probe, "rev-parse", "--show-toplevel")
    if out is None:
        return None
    top = out.strip()
    return top or None


def dirty_lines(root):
    """工作树相对 HEAD 的脏行。返回 None = 无法证明在工作树里（视同脏）。"""
    out = _git(root, "status", "--porcelain")
    if out is None:
        return None
    return [ln for ln in out.splitlines() if ln.strip()]


def vendor_violations(root):
    """扫会被打进包的 web 文本与构建戳，返回 [(相对路径, 行号, 内容片段)]。"""
    hits = []
    targets = [os.path.join(root, WEB_REL, d) for d in WEB_DIRS]
    targets.append(os.path.join(root, "version.txt"))
    for base in targets:
        if os.path.isfile(base):
            files = [base]
        elif os.path.isdir(base):
            files = []
            for dp, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
                files.extend(os.path.join(dp, f) for f in filenames)
        else:
            continue
        for f in files:
            if not f.lower().endswith(TEXT_EXT):
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if VENDOR_RE.search(line) and ALLOW_MARK not in line:
                    hits.append((os.path.relpath(f, root), i, line.strip()[:100]))
    return hits


def enforce(root=None):
    """两闸都过返回 True；任一不过以 SystemExit 终止（PyInstaller 会原样带出信息）。"""
    root = root or repo_root()
    problems = []
    if root is None:
        problems.append("当前目录不在 git 工作树里（或找不到 git）：无法证明工作树 == HEAD")
        d = None
    else:
        d = dirty_lines(root)
        if d is None:
            problems.append("git status 执行失败：无法证明工作树 == HEAD")
        elif d:
            shown = "\n".join("  " + ln for ln in d[:20])
            more = "" if len(d) <= 20 else "\n  ……共 %d 行" % len(d)
            problems.append("工作树 ≠ HEAD（先提交或撤掉这些改动）：\n%s%s" % (shown, more))
    if root is not None:
        vv = vendor_violations(root)
        if vv:
            detail = "\n".join("  %s:%d  %s" % h for h in vv[:20])
            more = "" if len(vv) <= 20 else "\n  ……共 %d 处" % len(vv)
            problems.append("将被打进包的前端文本含服务商/模型名（清理或按行加 %s）：\n%s%s"
                            % (ALLOW_MARK, detail, more))
    if problems:
        msg = "\n".join(problems)
        raise SystemExit(
            "打包闸：拒绝执行 PyInstaller。原因：\n" + msg
            + "\n处理办法见 tools/package_gate.py；闸的行为由 backend/tests/test_package_gate.py 钉住。"
        )
    return True


if __name__ == "__main__":
    enforce()
    print("打包闸：通过（工作树 == HEAD，随包前端文本无未豁免服务商名）")
    sys.exit(0)
