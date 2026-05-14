import os
from dotenv import load_dotenv
from openai import OpenAI
import chromadb

load_dotenv()

# 初始化 OpenAI 客户端（用来做 embedding）
client = OpenAI(api_key=os.getenv("api_key"),base_url="https://api.apiyi.com/v1")


# 初始化 Chroma 客户端（数据存在本地文件夹 ./chroma_db）
chroma_client = chromadb.PersistentClient(path="./chroma_db")

# 创建一个集合（类似数据库里的表）
collection = chroma_client.get_or_create_collection(name="my_memories")

# 准备一些“记忆”句子
sentences = [
    "我喜欢吃意大利面。",
    "我的猫叫小白。",
    "我最喜欢的编程语言是 Python。"
]

# 为每个句子生成 embedding（向量）
embeddings = []
for text in sentences:
    response = client.embeddings.create(
        model="text-embedding-3-small",   # 性价比最高的嵌入模型
        input=[text]
    )
    vec = response.data[0].embedding
    embeddings.append(vec)
    print(f"文字：「{text}」 => 向量长度：{len(vec)}（前5个值：{vec[:5]}）")

# 将记忆存入 Chroma
collection.add(
    documents=sentences,
    embeddings=embeddings,
    ids=["mem1", "mem2", "mem3"]   # 每条记忆的唯一ID
)

print("\n记忆已存入 Chroma，数量：", collection.count())
# 模拟用户查询：“你养了什么宠物？”
query = "你养了什么宠物？"
query_embedding = client.embeddings.create(
    model="text-embedding-3-small",
    input=[query]
).data[0].embedding

# 用查询向量去搜索最相关的记忆
results = collection.query(
    query_embeddings=[query_embedding],
    n_results=2   # 返回最相关的 2 条
)

print("\n查询：「", query, "」")
print("最相关的记忆：")
for doc, distance in zip(results['documents'][0], results['distances'][0]):
    print(f"   - {doc} （距离：{distance:.4f}）")