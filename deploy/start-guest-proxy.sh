#!/bin/bash
# Guest Proxy 启动脚本
# 用法: ./start-guest-proxy.sh [HOST_BRIDGE_URL] [PORT]

set -e

# 默认配置
HOST_BRIDGE_URL="${1:-http://host.docker.internal:8080}"
GUEST_PROXY_PORT="${2:-8081}"

# 检查必需环境变量
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    exit 1
fi

# 检测容器名称
if [ -z "$CONTAINER_NAME" ]; then
    # 尝试从 /etc/hostname 获取
    if [ -f /etc/hostname ]; then
        export CONTAINER_NAME=$(cat /etc/hostname)
    fi
fi

# 导出环境变量
export HOST_BRIDGE_URL
export GUEST_PROXY_PORT

echo "=========================================="
echo "启动 Guest Proxy"
echo "------------------------------------------"
echo "Host Bridge: $HOST_BRIDGE_URL"
echo "本地端口: $GUEST_PROXY_PORT"
echo "容器名称: ${CONTAINER_NAME:-自动检测}"
echo "=========================================="

# 启动服务
exec python -m src.guest_proxy.server