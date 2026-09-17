import docker
import os
import tempfile
import time
import requests  # 用于捕获 Windows 下的超时异常

class SandboxManager:
    # 语言配置映射
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
        self.default_mem_limit = "64m"
        self.default_timeout = 10
        self.client = None
        self.unavailable_reason = None
        try:
            self.client = docker.from_env()
        except Exception as e:
            # 导入期就实例化本类，因此 Docker 不可用只能降级，
            # 抛异常会让整个后端起不来（打包后表现为 EXE 闪退）。
            self.unavailable_reason = str(e)
            print(f"⚠️ Docker 不可用，代码执行沙箱暂时停用：{self.unavailable_reason}")

    def run_code(self, code: str, language: str = "python", timeout: int = None) -> dict:
        """
        在隔离沙箱中执行代码，支持 Python 和 JavaScript。
        返回字典包含 stdout, stderr, error, execution_time
        """
        if language not in self.LANGUAGE_IMAGES:
            supported = list(self.LANGUAGE_IMAGES.keys())
            return {"error": f"不支持的语言: {language}。当前支持: {supported}"}

        if self.client is None:
            return {
                "error": "代码执行沙箱不可用：Docker 未运行或未安装。"
                         f"（{self.unavailable_reason}）"
            }

        if timeout is None:
            timeout = self.default_timeout

        ext = self.FILE_EXTENSIONS[language]

        # 1. 将代码写入临时文件（utf-8 编码，避免中文报错）
        with tempfile.NamedTemporaryFile(mode='w', suffix=ext, delete=False, encoding='utf-8') as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        # NamedTemporaryFile 默认 0600、属主是宿主进程 uid；容器里跑的是非 root 的
        # sandbox 用户，两边 uid 不同，Linux 上这个只读挂载就是"读不到"。而 python
        # 打不开文件时退出码是 2、错误写到 stdout，如果只看 error 字段就会把
        # "一行代码都没执行"当成执行成功（CI 上正是这样绿了很久）。
        os.chmod(tmp_path, 0o644)

        start_time = time.time()
        container = None
        try:
            # 2. 创建容器但不启动
            container = self.client.containers.create(
                image=self.LANGUAGE_IMAGES[language],
                command=self.LANGUAGE_COMMANDS[language],
                volumes={tmp_path: {"bind": f"/tmp/code{ext}", "mode": "ro"}},
                tmpfs={"/home/sandbox/tmp": "size=32m"},  # 临时可写空间，内存级隔离
                mem_limit=self.default_mem_limit,
                cpu_period=100000,
                cpu_quota=50000,
                network_disabled=True,
                detach=True,
                user="sandbox"
            )
            container.start()

            # 3. 等待容器结束，设置超时
            status = container.wait(timeout=timeout)
            exit_code = status.get("StatusCode") if isinstance(status, dict) else None

            # 4. 获取输出
            logs = container.logs(stdout=True, stderr=True)
            stdout = logs.decode() if logs else ""
            execution_time = time.time() - start_time

            # 5. 资源使用统计
            print(f"[Sandbox] 语言: {language}, 耗时: {execution_time:.2f}s, "
                  f"退出码: {exit_code}, 代码长度: {len(code)}")

            return {
                "stdout": stdout,
                "stderr": "",
                "error": None,
                "exit_code": exit_code,
                "execution_time": execution_time
            }

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            # 超时（包括 Windows npipe 的 ConnectionError）
            if container:
                container.kill()
            execution_time = time.time() - start_time
            print(f"[Sandbox] 语言: {language}, 超时终止, 耗时: {execution_time:.2f}s")
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
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            # 确保容器已删除
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass