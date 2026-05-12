import docker

client = docker.from_env()

code = "print('沙箱内的 Python 说：1+1 =', 1+1)"

print("正在运行带资源限制的容器...")
container = client.containers.run(
    image="python:3.11-slim",
    command=["python", "-c", code],
    detach=False,
    remove=True,
    stdout=True,
    stderr=True,
    mem_limit="64m",
    cpu_period=100000,
    cpu_quota=50000,
    network_disabled=True
)

print("运行结果：", container.decode())