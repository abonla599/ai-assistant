"""密钥形态扫描器（tools/secret_scan.py）的锁。

这个文件守的东西有点特殊：它既是一道**闸**（第一条用例扫真仓，CI 里红就是不许合），
又是一件**可能坏掉的工具**（正则写坏了，闸就变成永远绿灯的装饰品）。所以除了真仓那
一条，其余全是在给扫描器做正向对照：每种真形状各埋一颗、必须被抓到，每种占位符与
测试短语各埋一颗、必须不被抓到。这个仓已经吃过一次"只断言没有异常"的假绿，同一类
形状的错误在安全闸上代价更高。
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("secret_scan", REPO_ROOT / "tools" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_scan)


def _plant(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ 真仓闸门 --

def test_the_repository_itself_scans_clean():
    """这条就是 CI 上会红的那一条。

    红法有两种，都该红：新数据/凭据文件被 `git add -f` 塞进索引（路径规则），或者
    某个正常文件里躺了一颗真 key（形状规则）。`.gitignore` 只挡得住前者的一半，
    而"记得别提交"挡不住任何人。
    """
    findings = secret_scan.active(secret_scan.scan(REPO_ROOT, use_git=True))
    assert not findings, "版本库里出现了凭据形状的东西：\n" + "\n".join(str(f) for f in findings)


def test_scanner_does_not_flag_itself():
    """扫描器自己的源码里满篇都是 key 的形状（那是规则本体）。

    它必须匹配不出自己，否则这道闸连"我在扫什么"都说不清。不依赖 git 索引，
    所以这条在文件还没被 add 的时候就已经在跑。
    """
    assert secret_scan.active(secret_scan.scan_file(REPO_ROOT, "tools/secret_scan.py")) == []


def test_env_example_is_not_flagged():
    """`.env.example` 是**该**入库的那份，而且天生长得像凭据文件。

    它一旦被路径规则抓到，下一个动作就是有人把它从版本库里删掉——那比泄露更糟。
    """
    rel = ".env.example"
    assert (REPO_ROOT / rel).exists(), "模板文件不见了，这条锁就空转了"
    assert secret_scan.active(secret_scan.scan_file(REPO_ROOT, rel)) == []


# ------------------------------------------------------------------ 正向对照 --

# 每种形状一颗"真的长什么样"。故意不与真仓里那些值重复：这些只喂给临时目录。
REAL_SHAPES = [
    ("openai-style-key", 'api_key = "sk-9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d"\n'),
    ("anthropic-key", 'ANTHROPIC_KEY = "sk-ant-abc123def456abc123def456abc123def"\n'),
    ("aws-access-key-id", 'aws = "AKIAIOSFODNN7EXAMPLE"\n'),
    ("github-token", 'gh = "ghp_16C4eFz8Yq0sWm3Ql7Rt2Vu9Xk4bNc5Pd6Qf"\n'),
    ("google-api-key", 'key: AIzaSyD1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p\n'),
    ("slack-token", 'hook = "xoxb-1234567890-abcdef"\n'),
    ("jwt", 'bearer: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQFk2sJx9LmNpRt3vWz\n'),
    ("private-key-block", "-----BEGIN RSA PRIVATE KEY-----\nMIIBOAIBAAJ\n"),
    ("opaque-assignment", 'ACCESS_TOKEN="0f1e2d3c4b5a69788796a5b4c3d2e1f0"\n'),
]


@pytest.mark.parametrize("rule,body", REAL_SHAPES, ids=[s[0] for s in REAL_SHAPES])
def test_each_secret_shape_is_caught(tmp_path, rule, body):
    """规则写坏 = 闸变成装饰品。每种形状必须各自被抓到，且被抓到的是**它这一条**。

    少了这九颗，把某条正则改成永远不匹配是完全没有代价的——真仓那条照样绿。
    """
    _plant(tmp_path, "config.py", body)
    hits = secret_scan.active(secret_scan.scan(tmp_path, use_git=False))
    rules = {h.rule for h in hits}
    # `sk-ant-…` 同时是 `sk-…` 的形状，被两条各抓一次是好事（重叠不等于遗漏），
    # 所以这里只要求「它自己这一条确实抓到了」——把某条正则改成永不匹配才是必须红的。
    assert rule in rules, f"{rule} 没被抓到：{[str(h) for h in hits]}"


# 一眼是占位符或人写的短语。这些被抓到的话，闸就开始误报，而误报的结局是被人绕过。
NOT_SECRETS = [
    'api_key="your_apiyi_api_key_here"\n',
    'API_KEY=<把密钥贴在这里>\n',
    'API_KEY=${DEEPSEEK_API_KEY}\n',
    'api_key = REDACTED  # «密钥已隐去»\n',
    'password="isolation-pw-123"\n',          # 测试口令：带分隔符的人写短语
    'secret_key = "my_test_value_9999"\n',
    'token = "xxxxxxxxxx"\n',
    'api_key: "sk-" + os.environ["K"]\n',
    '# 这行注释里提到 sk- 前缀但没有 key\n',
]


@pytest.mark.parametrize("body", NOT_SECRETS, ids=range(len(NOT_SECRETS)))
def test_placeholders_and_prose_are_not_flagged(tmp_path, body):
    _plant(tmp_path, "settings.py", body)
    hits = secret_scan.active(secret_scan.scan(tmp_path, use_git=False))
    assert not hits, f"误报会让人整条绕过这道闸：{[str(h) for h in hits]}"


# ------------------------------------------------------------------ 路径形状 --

DATA_PATHS = [
    "data/sessions.json",
    "data/users.json",
    "data/provider_keys.json",
    "data/uploads/abc.png",
    "chroma_db/ef/data.json",
    ".env",
    ".env.production",
    ".env.txt",
    "config/service.pem",
    "deploy/id_ed25519",
    "local.properties",
]


@pytest.mark.parametrize("rel", DATA_PATHS, ids=DATA_PATHS)
def test_a_data_path_is_flagged_by_its_name_alone(tmp_path, rel):
    """空文件也抓。这些路径进了版本库就是事故，跟里面此刻有没有内容无关。

    真出过一次：`data/` 那类运行数据被跟踪，其中 `.env` 里那把 key 公开了五个月。
    """
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    hits = [h for h in secret_scan.active(secret_scan.scan(tmp_path, use_git=False))
            if h.path == rel]
    assert hits, f"{rel} 没被抓到"


# ------------------------------------------------------------------ 放过机制 --

FAKE = 'STORED_KEY = "sk-adminkey-99887766554433"  # secret-scan:allow 测试里造的假密钥\n'
FAKE_NO_MARK = 'STORED_KEY = "sk-adminkey-99887766554433"\n'


def test_the_allow_marker_works_only_inside_the_test_tree(tmp_path):
    """行内标记是唯一的逃生口，所以它必须**有边界**。

    没边界的话，在 .env.example 末尾加一句注释就能把整闸静音。测试树之外一律不认，
    而测试树正是那些"故意写得像真的"假密钥唯一合理的住处。
    """
    tests_dir = tmp_path / "backend" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_fake.py").write_text(FAKE, encoding="utf-8")
    (tmp_path / "app_settings.py").write_text(FAKE, encoding="utf-8")
    (tmp_path / "test_lookalike.py").write_text(FAKE_NO_MARK, encoding="utf-8")

    hits = secret_scan.active(secret_scan.scan(tmp_path, use_git=False))
    assert [h.path for h in hits] == ["app_settings.py", "test_lookalike.py"], \
        f"放过机制的边界不对：{[str(h) for h in hits]}"


def test_suppressed_matches_stay_visible_in_the_report():
    """放过 ≠ 隐身。报告末尾要数出来，否则三年后没人知道这里放过几颗。"""
    hits = secret_scan.active(secret_scan.scan_file(REPO_ROOT, "backend/tests/test_providers.py"))
    assert not hits
    every = secret_scan.scan_file(REPO_ROOT, "backend/tests/test_providers.py")
    text = secret_scan.report(every)
    assert "按行内" in text and str(len(every)) in text


# ------------------------------------------------------------------ 不回显 --

def test_the_report_never_echoes_the_secret(tmp_path):
    """扫描器自己不能变成新的泄露面：日志、CI 产物、截图里贴的报告都算出口。

    只许出现长度与末四位——和 app/core/providers.py::mask_key 同一个口径。
    """
    secret = "sk-9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d"
    _plant(tmp_path, "config.py", f'api_key = "{secret}"\n')
    findings = secret_scan.scan(tmp_path, use_git=False)
    assert findings, "先确认它真的抓到了"
    text = secret_scan.report(findings)
    assert secret not in text
    assert secret[-4:] in text          # 末四位可对照，够定位是哪一颗
    assert str(len(secret)) in text


def test_scanning_a_directory_that_is_not_a_repo_errors_out(tmp_path, capsys):
    """工具坏了不能报"干净"。退出码 2 与 0 的差别就是这条闸有没有在工作。"""
    missing = tmp_path / "nope"
    assert secret_scan.main(["--dir", str(missing)]) == secret_scan.EXIT_ERROR
    assert "干净" not in capsys.readouterr().out


def test_ci_runs_this_gate():
    """锁没写进 CI 的回归就不是锁（这条在本仓已经教训过一次）。

    tests.yml 按目录跑 `pytest backend/tests/`，不点名文件——所以这里验的是"它没有
    退回点名清单"，而不是"我的文件在清单上"。
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    step = [line for line in workflow.splitlines() if "pytest" in line and "run:" in line]
    assert len(step) == 1, f"CI 里的 pytest 步骤形状变了：{step}"
    assert "backend/tests/" in step[0], "CI 必须按目录跑，新增测试文件才自动被覆盖"
    assert "test_secret_scan" not in workflow, "别退回点名清单：那会让新写的锁一条都不执行"


def test_git_add_f_mode_still_lands_in_the_scanned_set(tmp_path):
    """扫的是**索引**，不是 HEAD——`git add -f` 之后、提交之前就得红。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "users.json").write_text('{"x": 1}', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "data/users.json"],
                   check=True, timeout=60)

    listed = secret_scan.tracked_files(tmp_path)
    assert "data/users.json" in listed, "刚被 force-add 的文件不在扫描清单上，这道闸就慢了一步"
    assert secret_scan.active(secret_scan.scan(tmp_path, use_git=True))
