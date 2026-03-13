# Guest Proxy 集成指南

> 将 Claude Code 容器代理能力嵌入到任意开发环境

## 快速集成

### 方式一：pip 安装（推荐）

在目标容器的 `Dockerfile` 或环境中：

```dockerfile
# 安装 Guest Proxy
RUN pip install guest-proxy

# 或从源码安装
RUN pip install git+https://github.com/your-org/remote-claude-code.git#subdirectory=src/guest_proxy
```

### 方式二：复制模块

将以下目录复制到目标项目：
```
src/guest_proxy/     # Guest Proxy 服务
src/protocol/        # 通信协议
src/host_bridge/client.py  # Host Bridge 客户端
```

---

## 配置

### 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `HOST_BRIDGE_URL` | 是 | - | Host Bridge 地址 |
| `GUEST_PROXY_PORT` | 否 | 8081 | 本地监听端口 |
| `CONTAINER_NAME` | 否 | 自动检测 | 容器名称 |
| `ANTHROPIC_API_KEY` | 是 | - | Claude API Key |

### 配置文件示例

```bash
# /app/.claude/settings.local.json
{
  "authorized_users": ["ou_xxxxxx"]
}
```

---

## 启动方式

### 1. 直接启动

```bash
# 在容器内执行
python -m src.guest_proxy.server
```

### 2. 作为进程管理器服务

**Supervisor 配置：**
```ini
[program:guest-proxy]
command=python -m src.guest_proxy.server
directory=/app
autostart=true
autorestart=true
stderr_logfile=/var/log/guest-proxy.err.log
stdout_logfile=/var/log/guest-proxy.out.log
environment=HOST_BRIDGE_URL="http://host.docker.internal:8080"
```

**systemd 配置：**
```ini
[Unit]
Description=Guest Proxy Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python -m src.guest_proxy.server
WorkingDirectory=/app
Environment=HOST_BRIDGE_URL=http://host.docker.internal:8080
Restart=always

[Install]
WantedBy=multi-user.target
```

### 3. 嵌入应用启动

```python
import asyncio
from src.guest_proxy.server import GuestProxyServer

async def main():
    server = GuestProxyServer()
    await server.start()
    # 继续其他应用逻辑...

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 网络配置

### 宿主机网络模式

如果容器使用 `network_mode: host`：

```bash
HOST_BRIDGE_URL=http://localhost:8080
```

### 桥接网络模式

默认 Docker 网络使用：

```bash
HOST_BRIDGE_URL=http://host.docker.internal:8080
```

Linux 上需要添加：
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### 自定义网络

```yaml
networks:
  claude-net:
    driver: bridge

services:
  your-app:
    networks:
      - claude-net
    environment:
      - HOST_BRIDGE_URL=http://host-bridge:8080
```

---

## 健康检查

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

---

## 完整集成示例

### Docker Compose 片段

```yaml
# 添加到现有 docker-compose.yml
services:
  your-existing-service:
    # ... 现有配置 ...

    # 添加以下配置
    environment:
      - HOST_BRIDGE_URL=${HOST_BRIDGE_URL:-http://host.docker.internal:8080}
      - GUEST_PROXY_PORT=${GUEST_PROXY_PORT:-8081}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    labels:
      - "claude.guest-proxy.enabled=true"
```

### Dockerfile 片段

```dockerfile
# 添加到现有 Dockerfile

# 安装依赖
COPY requirements-guest-proxy.txt /tmp/
RUN pip install -r /tmp/requirements-guest-proxy.txt

# 复制模块（如果不用 pip 安装）
COPY src/guest_proxy /app/src/guest_proxy
COPY src/protocol /app/src/protocol

# 创建启动脚本
RUN echo '#!/bin/bash\npython -m src.guest_proxy.server &\nexec "$@"' > /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

---

## 自注册机制

Guest Proxy 启动后会自动向 Host Bridge 注册：

```python
# 自动执行流程
1. 启动 HTTP 服务
2. 读取环境变量获取配置
3. 向 HOST_BRIDGE_URL 发送注册请求
4. 开始接受请求
```

无需手动配置路由，实现真正的即插即用。

---

## 故障排查

### 常见问题

1. **连接 Host Bridge 失败**
   ```bash
   # 检查网络连通性
   curl http://host.docker.internal:8080/health

   # Linux 需要添加 extra_hosts
   docker run --add-host=host.docker.internal:host-gateway ...
   ```

2. **Claude API 调用失败**
   ```bash
   # 检查 API Key
   echo $ANTHROPIC_API_KEY

   # 检查网络（容器内）
   curl https://api.anthropic.com
   ```

3. **权限确认超时**
   - 检查飞书消息是否正常接收
   - 检查 Host Bridge 日志

---

## 最小依赖

```
aiohttp>=3.8.0
claude-agent-sdk>=0.1.0
```