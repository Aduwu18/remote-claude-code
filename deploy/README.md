# Guest Proxy 部署清单

此目录包含将 Guest Proxy 集成到现有开发环境所需的所有配置文件。

## 文件说明

| 文件 | 用途 |
|------|------|
| `requirements-guest-proxy.txt` | Python 依赖清单 |
| `start-guest-proxy.sh` | 启动脚本（设置 PYTHONPATH） |
| `docker-compose.guest-proxy.yml` | Docker Compose overlay 配置 |
| `Dockerfile.overlay` | Dockerfile 片段示例 |

## 核心概念

### 预编译依赖 + PYTHONPATH

Guest Proxy 使用预编译的依赖包，通过 `PYTHONPATH` 加载，无需在容器内安装：

```
/opt/guest-proxy/
├── libs/          # 预编译的依赖包（.so 文件）
├── src/           # 源代码
│   ├── guest_proxy/
│   ├── protocol/
│   └── host_bridge/client.py
└── start.sh       # 启动脚本（设置 PYTHONPATH）
```

**Python 版本要求：** 容器内必须使用 Python 3.11（与预编译 libs 匹配）

## 快速开始

### 1. 准备 Guest Proxy 目录

在宿主机上准备 Guest Proxy 目录：

```bash
# 创建目录
mkdir -p ~/opt/claude-guest-proxy

# 复制文件
cp -r src/guest_proxy ~/opt/claude-guest-proxy/src/
cp -r src/protocol ~/opt/claude-guest-proxy/src/
cp -r src/host_bridge ~/opt/claude-guest-proxy/src/
cp deploy/start-guest-proxy.sh ~/opt/claude-guest-proxy/start.sh

# 预编译依赖（首次部署）
pip install -r deploy/requirements-guest-proxy.txt -t ~/opt/claude-guest-proxy/libs
```

### 2. 配置目标容器

在目标容器的 `docker-compose.yml` 中添加：

```yaml
services:
  your-service:
    # ... 现有配置 ...

    # 添加挂载（必须三个目录同时挂载）
    volumes:
      - ~/opt/claude-guest-proxy/src:/opt/guest-proxy/src:ro
      - ~/opt/claude-guest-proxy/libs:/opt/guest-proxy/libs:ro
      - ~/opt/claude-guest-proxy/start.sh:/opt/guest-proxy/start.sh:ro

    # 添加环境变量
    environment:
      - HOST_BRIDGE_URL=http://host.docker.internal:8080
      - GUEST_PROXY_PORT=8081
      - CONTAINER_NAME=your-container-name
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

    # Linux 宿主机必需
    extra_hosts:
      - "host.docker.internal:host-gateway"

    # 健康检查
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 3. 在容器内启动 Guest Proxy

```bash
# 进入容器
docker exec -it your-container bash

# 启动 Guest Proxy（必须使用 start.sh）
/opt/guest-proxy/start.sh
```

**为什么必须用 start.sh？**

start.sh 会设置 `PYTHONPATH=/opt/guest-proxy/libs:/opt/guest-proxy/src`，让 Python 能找到预编译的依赖包。直接运行 `python -m guest_proxy.server` 会找不到依赖。

### 4. 验证部署

```bash
# 健康检查
curl http://localhost:8081/health

# 预期响应
{
  "status": "healthy",
  "container": "your-container-name",
  "active_sessions": 0
}
```

## Docker Compose 集成

### 方式一：Overlay 配置

```bash
# 合并配置
docker-compose -f docker-compose.yml -f docker-compose.guest-proxy.yml up -d
```

### 方式二：直接修改

将 `docker-compose.guest-proxy.yml` 中的配置合并到现有的 `docker-compose.yml`。

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `HOST_BRIDGE_URL` | 是 | - | Host Bridge 地址 |
| `GUEST_PROXY_PORT` | 否 | 8081 | 本地监听端口 |
| `CONTAINER_NAME` | 否 | 自动检测 | 容器名称（用于显示） |
| `ANTHROPIC_API_KEY` | 是 | - | Claude API Key |

## HTTP 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/rpc` | POST | JSON-RPC 2.0 请求 |
| `/stream` | POST | 流式聊天（NDJSON 响应） |
| `/health` | GET | 健康检查 |

## 网络配置

### Linux 宿主机

必须添加 `extra_hosts`：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### macOS / Windows

无需额外配置，`host.docker.internal` 默认可用。

### 自定义网络

如果使用自定义网络，可以直接用服务名：

```yaml
environment:
  - HOST_BRIDGE_URL=http://host-bridge:8080
```

## 故障排查

### 1. 找不到依赖包

```bash
# 检查 PYTHONPATH
echo $PYTHONPATH
# 应输出: /opt/guest-proxy/libs:/opt/guest-proxy/src

# 确保使用 start.sh
/opt/guest-proxy/start.sh
```

### 2. Python 版本不匹配

```bash
# 检查版本
python --version
# 必须是 3.11
```

### 3. 连接 Host Bridge 失败

```bash
# 测试网络
curl http://host.docker.internal:8080/health
```

## 更多文档

- [Guest Proxy 集成指南](../docs/GUEST_PROXY_INTEGRATION.md) - 详细的配置说明和故障排查
- [README.md](../README.md) - 项目概述和架构说明