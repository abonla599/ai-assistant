import docker

# 连接本地 Docker
client = docker.from_env()

# 1. 尝试拉取一个极小的镜像（约5MB）
print("正在拉取 alpine 镜像...")
client.images.pull("alpine")
print("拉取完成。")

# 2. 创建并运行一个容器，执行简单的打印命令
container = client.containers.run(
    image="alpine",
    command="echo 'Hello from sandbox'",
    detach=False,   # 等待命令执行完再继续
    remove=True,    # 执行完自动删除容器，不留垃圾
    stdout=True,
    stderr=True
)

# 3. 获取并打印容器输出
output = container.decode() if isinstance(container, bytes) else container
print("容器输出：", output)
# -- 新增部分：运行 Python 代码，并增加安全限制 --
print("\n" + "="*30)
code = "print('1+1=', 1+1)"

# 使用 python:3.11-slim 镜像，这个镜像有 Python
# 如果本地没有会先拉取
container = client.containers.run(
    image="python:3.11-slim",
    command=["python", "-c", code],
    detach=False,
    remove=True,
    stdout=True,
    stderr=True,
    mem_limit="64m",        # 限制内存 64MB
    cpu_period=100000,
    cpu_quota=50000,        # 限制最多用半个CPU
    network_disabled=True   # 禁止网络，安全
)

print("输出：", container.decode())