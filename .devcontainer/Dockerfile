# Guest Proxy Dockerfile
# 用于在容器内运行 Claude Code 代理服务

FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Claude Code CLI (可选，如果需要在容器内使用 claude 命令)
# RUN curl -fsSL https://claude.ai/install.sh | sh

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY src/ ./src/

# 创建数据目录
RUN mkdir -p /app/data

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 暴露端口
EXPOSE 8081

# 默认启动命令
CMD ["python", "-m", "src.guest_proxy.server"]