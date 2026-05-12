import subprocess
import tempfile
import os
import time

class SandboxManager:
    # 语言配置：镜像名、执行命令、文件后缀
    LANGUAGE_CONFIG = {
        "python": {
            "image": "ai-sandbox:latest",
            "command": ["python", "/tmp/code.py"],
            "suffix": ".py"
        },
        "javascript": {
            "image": "ai-sandbox-node:latest",
            "command": ["node", "/tmp/code.js"],
            "suffix": ".js"
        }
    }
    # 类级别统计（所有SandboxManager共享）
    _total_runs = 0
    _daily_runs = 0
    _last_reset_day = None
    def __init__(self):
        self.default_mem_limit = "64m"
        self.default_timeout = 10
        self._check_reset_daily()
    def _check_reset_daily(self):
        from datetime import date
        today = date.today()
        if SandboxManager._last_reset_day != today:
            SandboxManager._daily_runs = 0
            SandboxManager._last_reset_day = today
    def run_code(self, code: str, language: str = "python", timeout: int = None) -> dict:
        self._check_reset_daily()
        if SandboxManager._daily_runs >= 1000:
            return {
                "stdout": "",
                "stderr": "",
                "error": "达到每日执行上限（1000次），请明天再试"
            }
        # 1. 检查语言是不是支持
        if language not in self.LANGUAGE_CONFIG:
            return {"error": f"不支持的语言: {language}。当前支持: {list(self.LANGUAGE_CONFIG.keys())}"}

        if timeout is None:
            timeout = self.default_timeout

        config = self.LANGUAGE_CONFIG[language]
        suffix = config["suffix"]

        # 2. 把代码写进临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        start_time = time.time()  # 开始计时

        try:
            # 3. 拼装 docker run 命令
            cmd = [
                "docker", "run",
                "--rm",                                     # 跑完就删
                "--memory", self.default_mem_limit,          # 内存限制
                "--cpus", "0.5",                             # CPU限制
                "--network", "none",                         # 禁止联网
                "--user", "sandbox",                         # 非 root 用户
                "-v", f"{tmp_path}:/tmp/code{suffix}:ro",    # 只读挂载代码文件
                "--tmpfs", "/home/sandbox/tmp:size=32m",     # 提供32MB的临时可写空间
                "--read-only",
                "--security-opt", "no-new-privileges:true",
                "--cap-drop", "ALL",                               # 【关键】根文件系统设为只读
                config["image"],                             # 用对应的镜像
            ]
            # 拼接执行命令和代码文件路径
            cmd.extend(config["command"])

            # 4. 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace"
            )

            execution_time = time.time() - start_time  # 计算耗时

            # 打印日志，方便调试
            print(f"[Sandbox] 语言: {language}, 耗时: {execution_time:.2f}s, 代码长度: {len(code)}")

            SandboxManager._total_runs += 1
            SandboxManager._daily_runs += 1
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": None,
                "execution_time": execution_time,
                "total_runs": SandboxManager._total_runs,
                "daily_runs_remaining": 1000 - SandboxManager._daily_runs
            }

        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": "",
                "error": f"代码执行超时（>{timeout}秒），进程已被杀死",
                "total_runs": SandboxManager._total_runs,
                "daily_runs_remaining": 1000 - SandboxManager._daily_runs
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": "",
                "error": str(e)
            }
        finally:
            # 5. 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)