# Guest Proxy 部署清单

此目录包含将 Guest Proxy 集成到现有开发环境所需的所有配置文件。

## 文件说明

| 文件 | 用途 |
|------|------|
| `requirements-guest-proxy.txt` | Python 依赖清单 |
| `start-guest-proxy.sh` | 启动脚本 |
| `docker-compose.guest-proxy.yml` | Docker Compose overlay 配置 |
| `Dockerfile.overlay` | Dockerfile 片段示例 |

## 快速开始

### 1. 复制模块文件

```bash
# 从本项目复制必需模块到目标项目
TARGET=/path/to/your/project

cp -r src/guest_proxy $TARGET/src/
cp -r src/protocol $TARGET/src/
cp -r src/host_bridge/client.py $TARGET/src/host_bridge/
```

### 2. 安装依赖

```bash
pip install -r deploy/requirements-guest-proxy.txt
```

### 3. 配置环境变量

```bash
export HOST_BRIDGE_URL=http://host.docker.internal:8080
export GUEST_PROXY_PORT=8081
export ANTHROPIC_API_KEY=your-api-key
```

### 4. 启动服务

```bash
./deploy/start-guest-proxy.sh
```

## Docker Compose 集成

```bash
# 合并配置
docker-compose -f docker-compose.yml -f docker-compose.guest-proxy.yml up -d
```

## 验证

```bash
# 健康检查
curl http://localhost:8081/health

# 预期响应
# {"status": "healthy", "container": "xxx", "active_sessions": 0}
```