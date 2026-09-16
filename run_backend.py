# run_backend.py - PyInstaller 入口文件
import os
import sys

# 包名必须与 backend/app 内部 `from app.xxx import xxx` 的写法一致。
# 若写成 `from backend.app.main import app`，冻结后模块图里只有 backend.app.*，
# 所有 app.* 导入都会失败，而 main.py 里的 try/except ImportError 会把它们
# 逐个静默吞掉——结果是 EXE 能启动但记忆/工具/智能体全部失效。
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

import uvicorn
from app.main import app

if __name__ == "__main__":
    # 确保工作目录正确（处理 PyInstaller 打包后的路径问题）
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 EXE，切换到 EXE 所在目录
        os.chdir(os.path.dirname(sys.executable))

    print("🚀 AI智能助手后端启动中...")
    print("📍 服务地址: http://127.0.0.1:8000")
    print("⏳ 请稍候，正在初始化服务...\n")

    uvicorn.run(app, host="127.0.0.1", port=8000)
