"""测试进程不得碰到用户真实数据。

conftest 把会话、身份库、provider、附件目录都指进了临时目录，唯独长期记忆漏了：
`app/memory/memory_router.py` 在导入时构造的 `MemoryManager()` 会去看 `CHROMA_DB_PATH`，
而没人设它——于是本机跑测试时那 71 条真实记忆就在射程内。

这不是假想的担心。2026-09-19 有人拿临时写的 TestClient 冒烟测 `POST /v1/memory/decay`，
把 default_user 全部记忆的权重原地乘了 0.95：接口回 200，日志写着"已衰减 58 条"，
而那是一次**没有任何测试为它负责**的写入。衰减不夹逼、也没有反向还原的端点，
只能再乘 1/0.95 手工做回去。

CI 上这个问题看不见（`CI=true` 走 FakeMemoryStore），所以只有本机会中招——
而中招时测试照样全绿：改数据的那条请求是 200，没有任何一条断言为它负责。
这里钉的就是第四种信号：测试进程看见的记忆库，必须是它自己的。
"""
import os
from pathlib import Path

import pytest

from app.memory.memory_manager import _default_persist_dir

# 本文件位于 backend/tests/，向上两层即仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_real_memory_store_is_not_what_tests_open():
    """默认解析出来的记忆库路径，不能是仓库里那份真实的 chroma_db。

    判据写成"不等于真实目录"而不是"必须在某处之下"：后者会把将来合法的重定向
    （比如 data/ 下另起一份）一并判红，而真正要守的只有"别碰用户那一份"。
    """
    resolved = Path(_default_persist_dir()).resolve()
    real = (REPO_ROOT / "chroma_db").resolve()
    assert resolved != real, (
        f"测试打开的是用户真实的长期记忆库 {real}：任何一条用例调 add/delete/decay "
        "都会改到真数据上。conftest 需要设 CHROMA_DB_PATH。")


def test_env_isolation_covers_every_redirectable_store():
    """conftest 声称能指走的存储，就得真的指走了。

    清单**从 `paths.DATA_PATH_ENV_VARS` 派生**，不再手抄一份：这一处此前抄过一遍，
    而产品代码每加一份存储都要靠人记得来这儿补一行——不补就正好是这次的 bug 形状
    （CHROMA_DB_PATH 漏了指走，本机测试直接摸到用户那 71 条真实记忆）。
    派生之后，加存储而忘在 conftest 补一行，红的就是这一条。

    反馈与偏好那两份例外：它们在导入期就算成模块常量，conftest 走的是
    `monkeypatch.setattr(module, "FEEDBACK_FILE", …)`，环境变量指不走它们。
    """
    from app.core.paths import DATA_PATH_ENV_VARS

    module_constanted = {"FEEDBACK_FILE", "PREFERENCE_FILE"}
    covered = [v for v in DATA_PATH_ENV_VARS.values() if v not in module_constanted]
    assert len(covered) >= 7, f"派生出来的清单短得不像话，这条锁在空转：{covered}"
    missing = [v for v in covered if not os.getenv(v)]
    assert not missing, f"这些存储在跑测试时没有被指走：{missing}"

    inside = [v for v in covered
              if Path(os.environ[v]).resolve().is_relative_to(REPO_ROOT)]
    assert not inside, (
        f"这些变量被指进了仓库内，重建/提交都会牵连到真实数据：{inside}")


@pytest.mark.parametrize("endpoint", ["/v1/memory/list", "/v1/memory/stats"])
def test_memory_reads_do_not_create_a_real_collection(client, endpoint):
    """连"读"也要在隔离库上跑。

    真实 chroma 是 PersistentClient：一打开就可能 get_or_create_collection，
    在用户的数据目录里落下新文件。这条用例不校验业务结果，只校验跑完之后
    仓库那份 chroma_db 的文件清单没有变长。
    """
    before = _tree(REPO_ROOT / "chroma_db")
    client.get(endpoint)
    after = _tree(REPO_ROOT / "chroma_db")
    assert after == before, f"一次测试请求改动了真实记忆库目录：{after ^ before}"


def _tree(path: Path):
    if not path.exists():
        return frozenset()
    return frozenset(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


def test_decay_via_default_client_cannot_reach_the_real_store(client):
    """把 2026-09-19 那次事故的原样钉住：一条冒烟请求把真实记忆全池权重乘了 0.95。

    修好之后这条必然绿——它守的不是"衰减能工作"，而是"以后别再绿得那么侥幸"。
    哪天真数据又被碰到了，它会是第一条红的，而且红在事发的那一次调用上，
    不是红在几小时后发现"我的记忆排序怎么变了"。
    """
    import sqlite3

    real_db = REPO_ROOT / "chroma_db" / "chroma.sqlite3"
    if not real_db.exists():
        pytest.skip("本机没有真实记忆库，无从证明没被改")

    def weights():
        con = sqlite3.connect(f"file:{real_db.as_posix()}?mode=ro", uri=True)
        try:
            return sorted(con.execute(
                "select id, float_value from embedding_metadata where key='weight'"))
        finally:
            con.close()

    before = weights()
    resp = client.post("/v1/memory/decay?decay_factor=0.5")
    # 先证明这次请求真的执行了衰减：只断言"数据没变"的话，一次 503 也算通过，
    # 那条断言就成了空转（与上面"这一轮连偏好分析都没跑"同一类毛病）。
    assert resp.status_code == 200, f"请求没走到写入，这条断言就成了空转：{resp.status_code}"
    assert weights() == before, "测试客户端把衰减写进了用户真实的记忆池"
