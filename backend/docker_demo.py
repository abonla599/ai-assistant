import docker

client = docker.from_env()
print("已连接到 Docker")

print("正在拉取 alpine 镜像...")
client.images.pull("alpine")
print("镜像拉取完成")

print("正在运行容器...")
container_output = client.containers.run(
    image="alpine",
    command="echo 'Hello from sandbox'",
    detach=False,
    remove=True,
    stdout=True,
    stderr=True
)

print("容器输出：", container_output.decode())