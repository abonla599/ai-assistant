#!/usr/bin/env python3
"""密钥形态扫描：把「别把密钥提交上来」从一句叮嘱变成一条会红的规则。

为什么不是「记得检查」：本仓已经因运行数据入库付过一次真金白银的代价——`.env` 与
`chroma_db/` 曾经被跟踪，一把上游 API Key 因此在公开仓库里挂了五个月（见
[[ai-assistant-public-repo-secret-hygiene]]）。而 `.gitignore` 只是「不加 -f 就没事」，
`git add -f data/users.json` 一句就绕过它，绕过之后没有任何东西会报错。所以这道闸
必须**看内容形状**，而不是只看有没有人记得。

扫什么：默认扫 `git ls-files` 列出的那一份，也就是**即将进版本库的工作树内容**——
force-add 进来的文件在提交前就已经在这张清单上了。CI 里跑同一份清单，于是
「本地红」和「CI 红」是同一个判据，不存在只在某台机器上生效的锁。

两类规则：
  路径形状  —— 文件名本身就是凭据（`.env`、`data/`、`provider_keys.json`、密钥库）。
  内容形状  —— 文件名无辜、里面躺着真凭据（`sk-…`、`AKIA…`、`ghp_…`、JWT、PEM 头，
              以及 `xxx_key = <足够长且含数字的不透明串>` 这种通用赋值）。

误报比漏报更贵：一条天天误报的闸，最后一定被人 `--no-verify` 掉。所以通用赋值那条
带占位符过滤（`your_xxx`、`<贴在这里>`、`REDACTED`、连续 `xxxx` 都不算），而且
`backend/tests/test_secret_scan.py` 里两头都验——每种真形状各埋一颗必须被抓到
（正向对照，防止规则写坏了还「全绿」），每种占位符必须不被抓到。

测试里那些**故意写得像真的**假密钥（验脱敏、验落盘用）走行内标记放过：
    STORED_KEY = "sk-<这里是一颗假的>"   # secret-scan:allow 这台机器上没有这颗 key
两点限制：① 只有内容规则能放过，路径规则不能——`data/` 进了索引就是进了，注释救不了；
② 放过的那几处照样打印在报告末尾并计数，「静默通过」正是这道闸要消灭的东西。

只依赖标准库。退出码：0 干净 / 1 有命中 / 2 工具自身出错（不在仓库里、路径不存在）。

用法：
    python tools/secret_scan.py                # 扫当前仓库将入库的那一份
    python tools/secret_scan.py --dir some/dir # 扫目录下所有文件（测试用的正向对照）
    python tools/secret_scan.py --json         # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_ERROR = 2

# 超过这个大小就不看内容：真凭据不会只出现在超大文件里，而扫描器一旦在 CI 上
# 因为某个产物文件慢到超时，下一个动作就是把它整条注释掉。
MAX_FILE_BYTES = 2 * 1024 * 1024

# ---------------------------------------------------------------- 路径形状 --

# (正则, 说明)。相对仓库根、用 / 分隔。**用 search 不用 match**：`config/service.pem`
# 的尾部才是判据，match 只从位置 0 试，于是这类规则会安静地永远不命中——测试里
# 每条路径各埋一颗就是为了让这种写法当场红。
PATH_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)(^|/)\.env$"), "真实环境变量文件入库了。该入库的那份叫 .env.example"),
    (re.compile(r"(?i)(^|/)\.env\.(?!example$)[a-z0-9_-]+$"), "带环境后缀的 .env 同样是真凭据"),
    (re.compile(r"(?i)(^|/)\.env\.txt$"), "这是 .env 的抄本（历史遗留），内容就是凭据"),
    (re.compile(r"(?i)^data/"), "运行数据整棵目录都在 data/ 下：会话、身份库、密钥库、附件"),
    (re.compile(r"(?i)(^|/)chroma_db(/|$)"), "长期记忆向量库：里面是所有人的对话内容"),
    (re.compile(r"(?i)(^|/)provider_keys\.json$"), "密钥库单列文件，整仓最敏感的一份"),
    (re.compile(r"(?i)(^|/)(users|sessions|tasks|usage)\.json$"),
     "按人分账后的运行时数据文件：密码摘要、会话、任务、用量"),
    (re.compile(r"(?i)(^|/)(feedback\.json|preference[^/]*\.txt)$"),
     "反馈原文与按人分的偏好摘要，都含用户内容"),
    (re.compile(r"(?i)\.(pem|p12|pfx|jks|keystore)$"), "密钥库/证书私钥文件"),
    (re.compile(r"(?i)(^|/)(id_[a-z0-9]+|\.git-credentials)$"), "私钥或 git 凭据文件"),
    (re.compile(r"(?i)(^|/)local\.properties$"), "Android 本地配置，历来装着签名口令"),
]

# ---------------------------------------------------------------- 内容形状 --

# (规则名, 正则)。正则的 group(1) 若存在则视为「密钥本体」，否则整段匹配即密钥。
SHAPE_RULES: list[tuple[str, re.Pattern]] = [
    # OpenAI 兼容口径：DeepSeek、以及本仓嵌入口用过的那类中转站，全是这个形状。
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("stripe-key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----")),
    # 通用赋值：键名一看就是凭据，值是一串足够长、字母数字混排的乱码。
    ("opaque-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|access[_-]?key|password|passwd)\b\s*[=:]\s*[\"']?"
        r"([^\s\"',;)]{16,})")),
]

# 一眼就是占位符的值。放在 opaque-assignment 这一条上：它最宽，不筛就必然误报，
# 而误报的代价是这条闸整个被人绕过。
_PLACEHOLDER = re.compile(
    r"(?i)(your[_-]|_here|_goes|example|sample|placeholder|dummy|fake|changeme|"
    r"replace|redacted|xxx+|\*{3,}|<|\$\{|\{\{|密钥|贴|填|TODO|null|none|true|false|"
    r"^[\"'])"
)

# 行内放过：见模块开头「两点限制」。
ALLOW_MARKER = "secret-scan:allow"


def _looks_opaque(value: str) -> bool:
    """通用赋值那条的收紧判据：像真凭据，而不像说明文字。

    三个条件各自挡一类误报：占位符形状（`your_xxx`、`<贴在这里>`）、纯字母数字以外
    的短语、以及**人写的带分隔符短语**（`isolation-pw-123` 这种测试口令）。最后一条
    是有边界的取舍：真正的不透明 token 由服务端随机生成，里面不会有 `-` 或 `_`；
    而带 `sk-` 那类前缀的键另有 openai-style 专条覆盖，不受这里影响。
    """
    if _PLACEHOLDER.search(value):
        return False
    if "_" in value or "-" in value:
        return False
    # 真密钥一定混着数字：`api_key=mysecretvalue` 这类描述性写法不会。
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def mask(value: str) -> str:
    """报告里只出现长度与末四位——和 app/core/providers.py::mask_key 同一口径。

    扫描器自己的输出如果回显密钥，它就变成新的泄露面：日志、CI 产物、粘到聊天里的
    报告片段，全都算。test_secret_scan 里有一条专门守这个。
    """
    tail = value[-4:] if len(value) >= 8 else ""
    return f"{len(value)}位，末四位 {tail}" if tail else f"{len(value)}位"


class Finding:
    __slots__ = ("path", "line", "rule", "detail", "suppressed")

    def __init__(self, path: str, line: int, rule: str, detail: str, suppressed: bool = False):
        self.path = path
        self.line = line
        self.rule = rule
        self.detail = detail
        self.suppressed = suppressed

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "rule": self.rule,
                "detail": self.detail, "suppressed": self.suppressed}

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line > 0 else self.path
        return f"  [{self.rule}] {where} — {self.detail}"


def active(findings: list[Finding]) -> list[Finding]:
    """真正会红的那些：行内放过的那几处不算。"""
    return [f for f in findings if not f.suppressed]


def tracked_files(root: Path) -> list[str] | None:
    """`git ls-files` = 索引里的那一份，含刚被 `git add -f` 塞进来、还没提交的文件。

    返回 None 表示这里不是个能问 git 的仓库（比如 --dir 指向的普通目录），调用方改用
    全量走盘。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p.decode("utf-8", "replace") for p in out.stdout.split(b"\0") if p]


def candidates(root: Path, use_git: bool) -> list[str]:
    """待扫文件（相对 root）。"""
    if use_git:
        listed = tracked_files(root)
        if listed is not None:
            return listed
    return [
        p.relative_to(root).as_posix()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    ]


def scan_file(root: Path, rel: str) -> list[Finding]:
    findings: list[Finding] = []

    for pattern, why in PATH_RULES:
        if pattern.search(rel):
            findings.append(Finding(rel, 0, "path", why))

    path = root / rel
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        # 路径规则已经记过了，读不到内容不算漏扫：文件名本身就是那件事。
        return findings

    if len(raw) > MAX_FILE_BYTES or b"\0" in raw[:4096]:
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    lines = text.splitlines()
    # 行内标记只在测试树里有效。理由是这道闸的形状：真出事的是产品代码与配置，
    # 那里冒出一颗 `sk-…` 一律该红；而测试要造像真的假密钥，只有它有正当理由。
    # 少了这条限制，往 .env.example 末尾加一句注释就能把整闸静音。
    may_suppress = rel.startswith("backend/tests/") or "/test_" in f"/{rel}"

    for name, pattern in SHAPE_RULES:
        for match in pattern.finditer(text):
            secret = match.group(1) if match.groups() else match.group(0)
            if name == "opaque-assignment" and not _looks_opaque(secret):
                continue
            start_line = text.count("\n", 0, match.start())
            line_text = lines[start_line] if start_line < len(lines) else ""
            findings.append(Finding(
                rel, start_line + 1, name, f"命中 {name}，{mask(secret)}",
                suppressed=(may_suppress and ALLOW_MARKER in line_text),
            ))
    return findings


def scan(root: Path, use_git: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for rel in candidates(root, use_git):
        findings.extend(scan_file(root, rel))
    return findings


def report(findings: list[Finding]) -> str:
    hits = active(findings)
    passed = [f for f in findings if f.suppressed]
    lines: list[str] = []
    if not hits:
        lines.append("✅ 没看到密钥形状的东西。")
    else:
        lines.append(f"🚨 发现 {len(hits)} 处疑似凭据（本仓是公开仓库，这些会被任何人读到）：")
        lines += [str(f) for f in hits]
        lines.append("")
        lines.append("处理：把凭据挪回不进版本库的地方（运行时数据在 <项目根>/data/，"
                     "环境变量在 .env，模板才叫 .env.example），然后删掉这一处。")
    if passed:
        lines.append("")
        lines.append(f"（另有 {len(passed)} 处按行内 {ALLOW_MARKER} 放过，只在测试树里有效：）")
        lines += [f"  · {f.path}:{f.line}" for f in passed]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描将进版本库的文件里的凭据形状")
    parser.add_argument("--dir", help="改扫这个目录下的全部文件（不查 git 索引）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出命中")
    args = parser.parse_args(argv)

    if args.dir:
        root = Path(args.dir).resolve()
        if not root.is_dir():
            print(f"❌ 目录不存在：{root}", file=sys.stderr)
            return EXIT_ERROR
        findings = scan(root, use_git=False)
    else:
        root = Path(__file__).resolve().parents[1]
        if tracked_files(root) is None:
            print("❌ 这里问不到 git（不在仓库里？）", file=sys.stderr)
            return EXIT_ERROR
        findings = scan(root, use_git=True)

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        print(report(findings))
    return EXIT_FOUND if active(findings) else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
