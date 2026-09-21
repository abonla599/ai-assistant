# 3.12，不是 3.11：CI（tests.yml）、本机跑测试的 venv、以及打 EXE 用的全局解释器
# 今天全是 3.12，而 requirements 里钉死的 chromadb 对上限敏感。容器这条路径此前
# 悄悄用另一个解释器装另一套依赖，等于"备用部署"和"被验过的部署"不是同一个东西。
FROM python:3.12-slim

WORKDIR /app

# 安装 curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 依赖只有一份事实来源：backend/requirements.txt（CI 装的就是它，逐条带"为什么需要"的注释）。
# 仓库根原先还躺着一份 118 行的 pip freeze 转储（UTF-16、零注释、numpy/onnxruntime 等
# 版本号与这份对不上），而这里 COPY 的正是那一份——于是 docker build 出来的容器
# 从来不是 CI 验过的那套依赖。那份转储已删；要重新生成随时 pip freeze 可得，留着它
# 只会让人以为自己是清单。
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制源码
COPY backend/ ./backend/

EXPOSE 8000

# 从 backend/ 里以 app.main 起，和 run_backend.py、CI 同一个导入根：
# 代码里全是 `from app.core...`，写成 backend.app.main 会在收集期就 ImportError。
WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]