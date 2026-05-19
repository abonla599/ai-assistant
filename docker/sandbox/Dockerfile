FROM python:3.11-slim

# 创建一个无 root 权限的普通用户 sandbox
RUN useradd -m sandbox && \
    chown -R sandbox:sandbox /home/sandbox

WORKDIR /home/sandbox

# 切换到非 root 用户
USER sandbox

# 默认命令（实际运行时会被覆盖）
CMD ["bash"]