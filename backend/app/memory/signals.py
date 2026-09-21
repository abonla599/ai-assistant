"""哪些话值得长期记住：判据只写在这一个地方。

原来的形状是"每轮都存，存前 200 字"（`f"用户问: {前100}；AI答: {前100}"`）。向量库
因此堆满了一次性内容——"用户问：帮我把这句话改顺一点；AI答：当然可以，…"。两个代价
都很实在：真正该被想起来的（"我叫…""回答要短"）被这些噪声挤出 top_k；以及每一轮都
花一次嵌入钱，去存一条以后再也不会被召回的记录。

判据换成按信号存：一句话里出现「明确要求记住 / 纠正助手 / 自述稳定事实 / 报了进度」
这四类形状之一才进库。用正则而不是再调一次模型判"这句话值不值得记"——这一步在每轮
的必经之路上，为筛选用一次付费调用再加几百毫秒延迟，不值。

规则顺序就是语义：先命中的算。"不对，我是说这个学生"该判 correction 而不是 profile，
所以纠正排在自述前面。
"""

from typing import NamedTuple
import re

# kind：这条记忆是什么。scope：它会不会腐烂——progress 会，其余三条不会。
# 两个字段都写进 chroma 的 metadata，于是 /v1/memory/list 里当场看得见，将来
# 检索侧要"只取不腐烂的"或"按 kind 衰减"也不用回头改写入形状。
KINDS = ("instruction", "correction", "profile", "progress")


class Signal(NamedTuple):
    kind: str
    scope: str
    quote: str


SIGNALS: list[tuple[str, str, re.Pattern]] = [
    # 用户亲口下的指令。下一轮不记得就是失职。
    ("instruction", "durable", re.compile(
        r"(记住|记一下|记好|别忘了|别忘记|以后.{0,6}都|每次.{0,4}都|please remember|"
        r"remember that|call me|称呼我)", re.I)),
    # 纠正：这一类不记下来，同一个错每轮重犯。
    ("correction", "durable", re.compile(
        r"(不对|不是这样|说错|搞错|弄错|其实是|应该是|我说过|又忘了|别再|重申一遍|"
        r"wrong|I told you)", re.I)),
    # 自述的稳定事实：名字、身份、所在地、长期偏好。
    ("profile", "durable", re.compile(
        r"(我叫|我的名字|我名字|我是.{0,8}(学生|老师|工程师|程序员|大[一二三四五]|考研|备考)"
        r"|我在.{0,10}(上学|读大|工作|住|备考)|我(喜欢|不喜欢|讨厌|习惯|偏好|平时|一般)"
        r"|我的生日|请注意我)", re.I)),
    # 进度与计划：会腐烂，所以单独标成 time-bound。
    ("progress", "time-bound", re.compile(
        r"(今天(完成|做|学|背|写|刷|练)了?|明天(要|打算|得|想|准备)|这周|下周|周末"
        r"|我的计划|我的目标|还差|没(完成|做完|背)|进度)", re.I)),
]

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n\r]+")
_NOTHING_BUT_NOISE = re.compile(
    r"[\s。．.，,、：:；;！!？?…·—~_\-「」『』“”\"‘’'()（）【】\[\]《》<>]+"
)


def classify(text: str) -> Signal | None:
    """这句话值不值得记。值得就返回 (kind, scope, 原文里的那一句)，否则 None。"""
    if not text or not text.strip():
        return None
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        for kind, scope, pattern in SIGNALS:
            if pattern.search(sentence):
                return Signal(kind=kind, scope=scope, quote=sentence)
    return None


def normalize(content: str) -> str:
    """比"是不是同一句"用的形状：去掉空白与中英标点，统一大小写。

    标点与空格不该让"我叫费丙乾"和"我叫费丙乾。"变成两条记忆——那正是重复入库的
    最常见来源（用户连着强调同一件事）。
    """
    return _NOTHING_BUT_NOISE.sub("", content).lower()


def is_repeat(quote: str, stored_docs: list[str]) -> bool:
    """库里已经有一模一样的一句就别再存。

    刻意**不**做语义相似度判重：嵌入后端可能是全零伪嵌入（本机没装 sentence-transformers
    且没配 key 时的降级路径），那时任意两条的距离都是 0，语义判重会把"什么都不再存"
    伪装成"去重生效"——静默停功能是本仓最难查的那类故障，不去踩。
    """
    target = normalize(quote)
    return bool(target) and any(normalize(doc) == target for doc in stored_docs)
