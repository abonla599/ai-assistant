"""按信号存记忆（app/memory/signals.py + pipeline.save_interaction）。

原先的形状是"每轮都存、存前 200 字"，库里因此堆满"用户问：帮我把这句话改顺一点;
AI答：当然可以…"这种一次性内容。这里守的正是它的反面：**多数轮次什么都不存**，
而存下来的那条要带着"它是什么"（kind）和"它会不会腐烂"（scope）。

用真 MemoryManager + 注入的确定性向量，不用假存储：假存储对 metadata 是原样塞、
原样吐，验不到 chroma 真的把 kind 存住了这条链——跨用户泄露那次已经教训过，
"假存储天然通过"的测试等于没测。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.memory import signals
from app.memory.memory_manager import MemoryManager
from app.pipeline import ChatPipeline

VECTORS = {
    "记住，以后回答都要短": [1.0, 0.0, 0.0, 0.0],
    "我叫费丙乾": [0.0, 1.0, 0.0, 0.0],
    "我今天背完了 80 个单词": [0.0, 0.0, 1.0, 0.0],
    "不对，我是说这个学生": [0.0, 0.0, 0.0, 1.0],
}


def _embed(text):
    return VECTORS.get(text, [0.5, 0.5, 0.5, 0.5])


@pytest.fixture
def mm(tmp_path):
    return MemoryManager(persist_dir=str(tmp_path / "chroma"), embedding_fn=_embed)


@pytest.fixture
def pipe(mm):
    p = ChatPipeline("u_signal")
    p.memory = mm
    return p


# ------------------------------------------------------------------ 判据 --

WORTH_STORING = [
    ("instruction", "durable", "记住，以后回答都要短"),
    ("correction", "durable", "不对，我是说这个学生"),
    ("profile", "durable", "我叫费丙乾"),
    ("progress", "time-bound", "我今天背完了 80 个单词"),
]


@pytest.mark.parametrize("kind,scope,sentence", WORTH_STORING,
                         ids=[w[2] for w in WORTH_STORING])
def test_a_signal_carries_its_kind_and_whether_it_ages(kind, scope, sentence):
    """四种形状各埋一条：少一种判据，那一类记忆就再也不进库，而且没人报错。

    scope 不是一样的：进度会腐烂（"今天背完了"三个月后还是噪声），名字与指令不会。
    把它们混成一锅，检索侧就没有"只取不腐烂的"这个抓手了。
    """
    got = signals.classify(sentence)
    assert got is not None, f"{sentence} 被判成不值得记"
    assert got.kind == kind and got.scope == scope, got
    assert got.quote == sentence, "存的是原文里那一句，不是整段对话"


@pytest.mark.parametrize("sentence", [
    "帮我把这句话改顺一点",
    "这个函数有什么问题",
    "再说一遍刚才那个例子",
    "今天天气怎么样",
    "翻译一下：hello world",
    "你好",
])
def test_an_ordinary_turn_is_not_memory(sentence):
    """这条锁住的就是这次改动的全部意义：一次性请求不进长期记忆。

    反过来（把判据写宽）不会报错，只会让库慢慢变成垃圾场——所以每一句都得是
    "看起来像会被误存"的那种。
    """
    assert signals.classify(sentence) is None, signals.classify(sentence)


def test_a_signal_buried_in_a_longer_turn_still_fires():
    """判据看的是"句"，不是"整轮"。

    只在前几句找标记、或者拿整段去做相似度，都会让"我有个问题。记住，回答要短。"
    这一类被漏掉——用户的指令常常就在最后一句。
    """
    got = signals.classify("我有个别的事。记住，以后回答都要短。谢谢")
    assert got is not None and got.quote == "记住，以后回答都要短", got


def test_correction_wins_when_the_sentence_also_looks_like_a_profile():
    """规则顺序就是语义：这句话的重点是"你忘了"，不是"我叫什么"。

    判成 profile 会存进一条"我叫费丙乾"，而真正该修的是它上面那条已经存在的记忆。
    """
    got = signals.classify("又忘了，我叫费丙乾")
    assert got.kind == "correction", got


# ------------------------------------------------------------------ 判重 --

def test_the_same_instruction_said_twice_stores_once():
    """连着强调同一件事是重复入库最常见的来源，标点与大小写不该把它变成两条。"""
    assert signals.is_repeat("我叫费丙乾", ["我叫费丙乾。"])
    assert signals.is_repeat("Call Me Fei", ["call me fei"])
    assert signals.is_repeat("记住  回答要短", ["记住回答要短"])


def test_a_different_sentence_is_not_treated_as_repeat():
    """刻意**不做**语义判重，这条把这个边界钉住。

    伪嵌入（全零向量）下任意两句都"完全相似"，语义判重会把"什么都不再存"伪装成
    "去重生效"——静默停功能是本仓最难查的那类故障。宁可留两条相近的，不可悄悄不存。
    """
    assert not signals.is_repeat("我叫费丙乾", ["我的名字是费丙乾"])


# ------------------------------------------------------------------ 端到端 --

def _rows(mm):
    data = mm.collection.get()
    return list(zip(data["documents"], data["metadatas"]))


def test_a_signalled_turn_reaches_the_store_with_kind_metadata(pipe, mm):
    pipe.save_interaction("记住，以后回答都要短")
    rows = _rows(mm)
    assert [d for d, _ in rows] == ["记住，以后回答都要短"], rows
    meta = rows[0][1]
    assert meta["kind"] == "instruction" and meta["scope"] == "durable", meta
    # 归属由存储自己钉住：metadata 里没有 user_id 键，add_memory 在服务端补上。
    assert meta["user_id"] == "u_signal"


def test_an_ordinary_turn_writes_nothing(pipe, mm):
    """旧形状在这里每轮都会存一条"用户问:…；AI答:…"。少一条断言就退回那个样子。"""
    pipe.save_interaction("帮我把这句话改顺一点")
    pipe.save_interaction("这个函数有什么问题")
    assert _rows(mm) == []


def test_repeating_the_same_instruction_does_not_grow_the_store(pipe, mm):
    for _ in range(3):
        pipe.save_interaction("我叫费丙乾")
    assert len(_rows(mm)) == 1, _rows(mm)


def test_a_store_failure_does_not_break_the_reply(pipe, monkeypatch, capsys):
    """记忆是尽力而为：chroma 抛了不能把这轮回答一起带走。"""
    def boom(*a, **k):
        raise RuntimeError("库被别的进程锁住了")

    monkeypatch.setattr(pipe.memory, "add_memory", boom)
    pipe.save_interaction("我叫费丙乾")          # 不许抛
    assert "记忆保存失败" in capsys.readouterr().out


# ------------------------------------------------------------------ 两条路 --

def test_both_chat_paths_go_through_the_same_judge():
    """流式那条以前自己写了一份摘要形状，于是"改判据"只会改到一半。

    这里锁的是 main.py 只把用户那句话交给同一个函数——AI 的回答不再是入参，
    判据也不再有两份。
    """
    root = Path(__file__).resolve().parent.parent
    main_src = (root / "app" / "main.py").read_text(encoding="utf-8")
    pipeline_src = (root / "app" / "pipeline.py").read_text(encoding="utf-8")

    calls = [line.strip() for line in main_src.splitlines() if "save_interaction(" in line]
    assert calls == ["pipe.save_interaction(user_text)"], calls
    assert "用户问" not in pipeline_src and "AI答" not in pipeline_src, \
        "旧的「每轮存前 200 字」形状还在，两种判据会同时生效"
