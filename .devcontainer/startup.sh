#!/bin/bash
# Guest Proxy 启动脚本

set -e

echo "=== Guest Proxy 启动脚本 ==="

# 检查环境变量
if [ -z "$HOST_BRIDGE_URL" ]; then
    echo "警告: HOST_BRIDGE_URL 未设置，使用默认值"
    export HOST_BRIDGE_URL="http://host.docker.internal:8080"
fi

if [ -z "$CONTAINER_NAME" ]; then
    # 尝试从 hostname 获取容器名
    export CONTAINER_NAME=$(cat /etc/hostname 2>/dev/null || echo "unknown")
fi

echo "HOST_BRIDGE_URL: $HOST_BRIDGE_URL"
echo "CONTAINER_NAME: $CONTAINER_NAME"
echo "GUEST_PROXY_PORT: ${GUEST_PROXY_PORT:-8081}"

# 检查 Python 环境
if command -v python3 &> /dev/null; then
    PYTHON=python3
else
    PYTHON=python
fi

echo "Python: $($PYTHON --version)"

# 启动 Guest Proxy
exec $PYTHON -m src.guest_proxy.server