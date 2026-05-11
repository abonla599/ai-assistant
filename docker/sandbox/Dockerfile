FROM python:3.11-slim

# 创建受限用户 sandbox，无 root 权限
RUN useradd -m sandbox && \
    chown -R sandbox:sandbox /home/sandbox

# 设置工作目录
WORKDIR /home/sandbox

# 使用 sandbox 用户运行
USER sandbox

# 默认命令可以留空，我们会通过容器运行时覆盖
CMD ["bash"]