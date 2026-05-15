import docker
import os
import tempfile
import time
import requests
from datetime import date

class SandboxManager:
    # ===== 类级别统计变量 =====
    _total_runs = 0
    _daily_runs = 0
    _last_reset_day = None

    @classmethod
    def _check_reset_daily(cls):
        """跨天自动重置每日计数"""
        today = date.today()
        if cls._last_reset_day != today:
            cls._daily_runs = 0
            cls._last_reset_day = today

    @classmethod
    def get_stats(cls) -> dict:
        """返回当前使用统计"""
        cls._check_reset_daily()
        return {
            "total_runs": cls._total_runs,
            "daily_runs": cls._daily_runs,
            "last_reset_day": str(cls._last_reset_day) if cls._last_reset_day else None
        }
    # ============================

    LANGUAGE_IMAGES = {
        "python": "ai-sandbox:latest",
        "javascript": "ai-sandbox-node:latest"
    }
    LANGUAGE_COMMANDS = {
        "python": ["python", "/tmp/code.py"],
        "javascript": ["node", "/tmp/code.js"]
    }
    FILE_EXTENSIONS = {
        "python": ".py",
        "javascript": ".js"
    }

    def __init__(self):
        self.client = docker.from_env()
        self.default_mem_limit = "64m"
        self.default_timeout = 10
        SandboxManager._check_reset_daily()

    def run_code(self, code: str, language: str = "python", timeout: int = None) -> dict:
        """安全执行代码，含限流、统计和安全加固"""
        if language not in self.LANGUAGE_IMAGES:
            supported = list(self.LANGUAGE_IMAGES.keys())
            return {"error": f"不支持的语言: {language}。当前支持: {supported}"}

        # 限流检查：每天最多 1000 次
        SandboxManager._check_reset_daily()
        if SandboxManager._daily_runs > 1000:
            return {"error": "达到每日执行上限（1000次），请明日再试"}

        if timeout is None:
            timeout = self.default_timeout

        ext = self.FILE_EXTENSIONS[language]
        with tempfile.NamedTemporaryFile(mode='w', suffix=ext, delete=False, encoding='utf-8') as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        start_time = time.time()
        container = None
        try:
            container = self.client.containers.create(
                image=self.LANGUAGE_IMAGES[language],
                command=self.LANGUAGE_COMMANDS[language],
                volumes={tmp_path: {"bind": f"/tmp/code{ext}", "mode": "ro"}},
                tmpfs={"/home/sandbox/tmp": "size=32m"},
                mem_limit=self.default_mem_limit,
                cpu_period=100000,
                cpu_quota=50000,
                network_disabled=True,
                detach=True,
                user="sandbox",
                # ===== 安全加固 =====
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                read_only=True,
                # ====================
            )
            container.start()
            container.wait(timeout=timeout)

            logs = container.logs(stdout=True, stderr=True)
            stdout = logs.decode() if logs else ""
            execution_time = time.time() - start_time

            # 更新统计
            SandboxManager._total_runs += 1
            SandboxManager._daily_runs += 1

            print(f"[Sandbox] 语言: {language}, 耗时: {execution_time:.2f}s, 代码长度: {len(code)}")

            return {
                "stdout": stdout,
                "stderr": "",
                "error": None,
                "execution_time": execution_time,
                "stats": SandboxManager.get_stats()
            }

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            if container:
                container.kill()
            execution_time = time.time() - start_time
            print(f"[Sandbox] 超时终止, 耗时: {execution_time:.2f}s")
            return {
                "stdout": "",
                "stderr": "",
                "error": f"代码执行超时（{timeout}秒）",
                "execution_time": execution_time
            }
        except docker.errors.ContainerError as e:
            execution_time = time.time() - start_time
            return {
                "stdout": "",
                "stderr": e.stderr.decode() if e.stderr else "",
                "error": str(e),
                "execution_time": execution_time
            }
        except Exception as e:
            execution_time = time.time() - start_time
            return {
                "stdout": "",
                "stderr": "",
                "error": str(e),
                "execution_time": execution_time
            }
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass