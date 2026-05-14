import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.tools import Tool
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.tools import DuckDuckGoSearchRun

load_dotenv()

# 1. 创建 LLM 实例（脑子）
llm = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0,
    api_key=os.getenv("OPENAI_API_KEY")
)

# 2. 准备工具列表：这里只给一个搜索工具
search = DuckDuckGoSearchRun()
tools = [
    Tool(
        name="web_search",
        func=search.run,
        description="当你需要查找实时信息或事实时使用，输入搜索查询"
    )
]

# 3. 编写提示词模板，告诉模型它可以调用工具
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个有工具的智能助手。如果需要查找信息，使用工具。"),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")  # 必须占位符，用来存中间过程
])

# 4. 创建 Agent
agent = create_openai_functions_agent(llm, tools, prompt)

# 5. 创建 Agent 执行器
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,   # 打印思考过程，方便学习
    handle_parsing_errors=True
)

# 6. 提出一个需要实时信息的任务
result = agent_executor.invoke({
    "input": "2026年世界杯冠军是谁？顺便告诉我今天的日期。"
})

print("\n最终答案：", result["output"])