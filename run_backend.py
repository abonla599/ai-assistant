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

_LOG_SINK = None


def _route_logs_to_file() -> None:
    """冻结版把 stdout/stderr 接到 data/backend.log。

    这个 exe 是以隐藏窗口启动的，日志原本落在一条没人看的管道上，而开机自启那条
    快捷方式也不带重定向。那次"模型全挂、界面只说 Connection error."因此全程没有
    服务端证据，只能靠界面文案反推。

    两件事在这里一起办（2026-09-22 安全审查派单）：
    - 轮转：超过 1MB 往后滚一份，最多留 3 份历史，无人清理时磁盘占用有上界；
    - 脱敏：整个流被 RedactingStream 包住，服务商名/模型 id/上游 host 在
      write 时就换掉，落盘即无明文——auth.log 那种"探测输出重定向留原件"的
      泄露不再可能经由这条管道发生。
    """
    global _LOG_SINK
    from app.core.logsanitizer import RedactingStream
    from app.core.paths import data_file

    path = data_file("BACKEND_LOG_PATH", "backend.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            for i in range(3, 1, -1):           # .2→.3, .1→.2，然后当前份升 .1
                src = f"{path}.{i - 1}"
                if os.path.exists(src):
                    os.replace(src, f"{path}.{i}")
            os.replace(path, f"{path}.1")
        _LOG_SINK = RedactingStream(open(path, "a", buffering=1, encoding="utf-8"))
    except OSError as e:
        print(f"日志文件打不开，本次只往控制台输出：{e}")
        return
    sys.stdout = sys.stderr = _LOG_SINK
    print(f"[启动] 日志落在 {path}（已启用落盘脱敏与多代轮转）")


if __name__ == "__main__":
    # 确保工作目录正确（处理 PyInstaller 打包后的路径问题）
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 EXE，切换到 EXE 所在目录
        os.chdir(os.path.dirname(sys.executable))
        _route_logs_to_file()

    print("🚀 AI智能助手后端启动中...")
    print("📍 服务地址: http://127.0.0.1:8000")
    print("⏳ 请稍候，正在初始化服务...\n")

    uvicorn.run(app, host="127.0.0.1", port=8000)
