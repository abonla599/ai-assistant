import docker
import os
import tempfile
import requests   # 新增：用于捕获超时异常

class SandboxManager:
    def __init__(self):
        self.client = docker.from_env()
        self.image = "ai-sandbox:latest"
        self.default_mem_limit = "64m"
        self.default_timeout = 10   # 秒

    def run_code(self, code: str, language: str = "python", timeout: int = None) -> dict:
        """
        在沙箱中执行 Python 代码，返回 stdout, stderr, error
        """
        if language != "python":
            return {"error": "目前仅支持 Python 语言"}

        if timeout is None:
            timeout = self.default_timeout

        # 将代码写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        container = None
        try:
            # 创建容器但不启动
            container = self.client.containers.create(
                image=self.image,
                command=["python", "/tmp/code.py"],
                volumes={tmp_path: {"bind": "/tmp/code.py", "mode": "ro"}},
                mem_limit=self.default_mem_limit,
                cpu_period=100000,
                cpu_quota=50000,
                network_disabled=True,
                detach=True,          # 后台运行，稍后 wait
                user="sandbox"
            )
            container.start()

            # 等待容器结束，设置 timeout（单位：秒）
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", -1)

            # 获取输出（合并 stdout 和 stderr，也可按需分离）
            logs = container.logs(stdout=True, stderr=True)
            stdout = logs.decode() if logs else ""
            # 注意：这里将 stderr 留空，因为 logs 已包含所有输出
            return {"stdout": stdout, "stderr": "", "error": None}

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            # wait 超时，杀死容器
            if container:
                container.kill()
                container.remove()
            return {"stdout": "", "stderr": "", "error": f"代码执行超时（{timeout}秒）"}
        except docker.errors.ContainerError as e:
            return {"stdout": "", "stderr": e.stderr.decode() if e.stderr else "", "error": str(e)}
        except Exception as e:
            return {"stdout": "", "stderr": "", "error": str(e)}
        finally:
            # 删除临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            # 确保容器已删除
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass