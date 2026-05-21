# run_backend.py - PyInstaller 入口文件
import os
import sys
import uvicorn
from backend.app.main import app

if __name__ == "__main__":
    # 确保工作目录正确（处理 PyInstaller 打包后的路径问题）
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 EXE，切换到 EXE 所在目录
        os.chdir(os.path.dirname(sys.executable))
    
    print("🚀 AI智能助手后端启动中...")
    print("📍 服务地址: http://127.0.0.1:8000")
    print("⏳ 请稍候，正在初始化服务...\n")
    
    uvicorn.run(app, host="127.0.0.1", port=8000)
