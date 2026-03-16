# Guest Proxy 集成指南

> 将 Claude Code 容器代理能力嵌入到任意开发环境

## 关键设计：预编译依赖 + PYTHONPATH

**重要：不要直接运行 `python -m guest_proxy.server`，必须运行 `start.sh`！**

### Python 环境架构

```
┌─────────────────────────────────────────────────────────────────┐
│  容器内运行 start.sh                                             │
│                                                                 │
│  export PYTHONPATH=/opt/guest-proxy/libs:/opt/guest-proxy/src   │
│  exec python -m guest_proxy.server                              │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ python = 容器内 conda 环境的 Python 3.11                 │   │
│  │                                                          │   │
│  │ 依赖加载 (通过 PYTHONPATH):                              │   │
│  │   - /opt/guest-proxy/libs/ (预编译的依赖包)             │   │
│  │   - aiohttp, claude_agent_sdk, pydantic 等              │   │
│  │   - 预编译好的 .so 文件 (如 _cffi_backend.cpython-311)  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 为什么这样设计？

| 优点 | 说明 |
|------|------|
| **无需容器内安装** | libs 目录已预编译所有依赖，挂载即用 |
| **统一版本** | 所有容器共享同一份预编译依赖 |
| **快速部署** | 只需挂载 libs + src + start.sh |
| **环境隔离** | 不污染容器内的 pip 环境 |

### Python 版本要求

libs 目录中的 `.so` 文件是 `cpython-311-x86_64-linux-gnu`，要求：

- **容器内 Python 版本必须是 3.11**
- 如果容器使用其他 Python 版本，需要重新编译依赖

---

## 快速集成

### 方式一：挂载预编译目录（推荐）

在目标容器的 `docker-compose.yml` 中：

```yaml
volumes:
  # 必须三个目录同时挂载
  - ~/opt/claude-guest-proxy/src:/opt/guest-proxy/src:ro
  - ~/opt/claude-guest-proxy/libs:/opt/guest-proxy/libs:ro
  - ~/opt/claude-guest-proxy/start.sh:/opt/guest-proxy/start.sh:ro

environment:
  - HOST_BRIDGE_URL=http://host.docker.internal:8080
  - GUEST_PROXY_PORT=8081
  - CONTAINER_NAME=your-container-name
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

extra_hosts:
  - "host.docker.internal:host-gateway"
```

### 方式二：pip 安装

在目标容器的 `Dockerfile` 或环境中：

```dockerfile
# 安装 Guest Proxy
RUN pip install guest-proxy

# 或从源码安装
RUN pip install git+https://github.com/your-org/remote-claude-code.git#subdirectory=src/guest_proxy
```

### 方式三：复制模块

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
| `CONTAINER_NAME` | 否 | 自动检测 | 容器名称（用于显示） |
| `ANTHROPIC_API_KEY` | 是 | - | Claude API Key |

---

## 启动方式

### 1. 使用 start.sh（推荐）

```bash
# 在容器内执行（挂载方式部署）
/opt/guest-proxy/start.sh
```

**为什么必须使用 start.sh？**
- start.sh 会设置 `PYTHONPATH=/opt/guest-proxy/libs:/opt/guest-proxy/src`
- 直接运行 `python -m` 会找不到预编译的依赖包

### 2. 直接启动（pip 安装方式）

```bash
# 仅适用于 pip 安装方式，依赖已安装到 site-packages
python -m src.guest_proxy.server
```

### 3. 作为进程管理器服务

**Supervisor 配置（挂载方式）：**
```ini
[program:guest-proxy]
command=/opt/guest-proxy/start.sh
directory=/opt/guest-proxy
autostart=true
autorestart=true
stderr_logfile=/var/log/guest-proxy.err.log
stdout_logfile=/var/log/guest-proxy.out.log
environment=HOST_BRIDGE_URL="http://host.docker.internal:8080"
```

**Supervisor 配置（pip 安装方式）：**
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

**systemd 配置（挂载方式）：**
```ini
[Unit]
Description=Guest Proxy Service
After=network.target

[Service]
Type=simple
ExecStart=/opt/guest-proxy/start.sh
WorkingDirectory=/opt/guest-proxy
Environment=HOST_BRIDGE_URL=http://host.docker.internal:8080
Restart=always

[Install]
WantedBy=multi-user.target
```

### 4. 嵌入应用启动

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

## HTTP 端点

Guest Proxy 在容器内提供以下 HTTP 端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/rpc` | POST | JSON-RPC 2.0 请求（chat, health_check, cleanup_session） |
| `/stream` | POST | 流式聊天（NDJSON 响应） |
| `/health` | GET | 健康检查 |

### 流式响应（/stream）

使用 NDJSON (Newline-Delimited JSON) 格式返回流式响应：

```
{"event_type":"status","data":{"text":"正在处理..."},"timestamp":1234567890}
{"event_type":"tool_call","data":{"name":"Read","input":{...}},"timestamp":1234567891}
{"event_type":"content","data":{"text":"部分响应内容"},"timestamp":1234567892}
{"event_type":"complete","data":{"session_id":"xxx","content":"完整响应"},"timestamp":1234567893}
```

**事件类型：**
| 事件 | 说明 |
|------|------|
| `heartbeat` | 心跳（保持连接） |
| `status` | 状态更新 |
| `tool_call` | 工具调用 |
| `content` | 内容片段 |
| `complete` | 完成 |
| `error` | 错误 |

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

### 端点解析机制

Guest Proxy 使用以下逻辑解析 Host Bridge 端点：

1. 检查 `HOST_BRIDGE_URL` 环境变量
2. 如果是 `http://host.docker.internal:8080`，解析 `host.docker.internal`：
   - 容器内 `/etc/hosts` 中 `host.docker.internal` → `172.17.0.1`（Docker 网关）
   - 通过 Docker 网关访问宿主机
3. 如果是自定义网络内的服务名，直接使用 Docker DNS 解析

---

## 健康检查

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

**健康检查响应：**
```json
{
  "status": "healthy",
  "container": "your-container-name",
  "active_sessions": 0
}
```

---

## 会话管理

### 会话缓存

Guest Proxy 维护会话缓存，支持会话恢复：

```python
# 会话缓存：chat_id -> (session_id, client)
_sessions: dict[str, tuple[str, GuestClaudeClient]]
```

### 会话清理

当用户退群或群解散时，Host Bridge 会向 Guest Proxy 发送清理请求：

```json
{
  "jsonrpc": "2.0",
  "method": "cleanup_session",
  "params": {"chat_id": "oc_xxx"},
  "id": "req-123"
}
```

Guest Proxy 收到后会：
1. 断开 Claude 会话连接
2. 从缓存中移除会话

---

## 完整集成示例

### Docker Compose 片段

```yaml
# 添加到现有 docker-compose.yml
services:
  your-existing-service:
    # ... 现有配置 ...

    # 添加挂载（必须三个目录同时挂载）
    volumes:
      - ~/opt/claude-guest-proxy/src:/opt/guest-proxy/src:ro
      - ~/opt/claude-guest-proxy/libs:/opt/guest-proxy/libs:ro
      - ~/opt/claude-guest-proxy/start.sh:/opt/guest-proxy/start.sh:ro

    # 添加环境变量
    environment:
      - HOST_BRIDGE_URL=${HOST_BRIDGE_URL:-http://host.docker.internal:8080}
      - GUEST_PROXY_PORT=${GUEST_PROXY_PORT:-8081}
      - CONTAINER_NAME=your-container-name
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

    # Linux 宿主机必需（让容器能访问宿主机）
    extra_hosts:
      - "host.docker.internal:host-gateway"

    # 健康检查
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
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

## 权限确认流程

当 Claude 执行敏感操作（Write, Edit, Bash）时：

```
1. Guest Proxy 检测到敏感工具调用
       ↓
2. HTTP POST 到 Host Bridge /rpc (method: permission)
       ↓
3. Host Bridge 发送卡片消息到飞书
       ↓
4. 用户点击「允许」或「拒绝」按钮
       ↓
5. Host Bridge 返回权限结果
       ↓
6. Guest Proxy 继续或取消操作
```

**安全工具**（Read, Glob, Grep）无需权限确认。

---

## Watchdog 监控

Guest Proxy 内置 Watchdog 监控：

- **任务超时检测**：默认 30 分钟超时
- **进程卡死检测**：定期检查任务状态
- **异常推送**：通过回调通知 Host Bridge

```python
# Watchdog 配置
watchdog = init_watchdog(
    timeout=1800,        # 30 分钟超时
    check_interval=60,   # 每分钟检查一次
    on_event=self._on_watchdog_event,
)
```

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

2. **找不到依赖包**
   ```bash
   # 检查 PYTHONPATH
   echo $PYTHONPATH
   # 应该输出: /opt/guest-proxy/libs:/opt/guest-proxy/src

   # 确保使用 start.sh 启动
   /opt/guest-proxy/start.sh
   ```

3. **Python 版本不匹配**
   ```bash
   # 检查 Python 版本
   python --version
   # 必须是 3.11
   ```

4. **Claude API 调用失败**
   ```bash
   # 检查 API Key
   echo $ANTHROPIC_API_KEY

   # 检查网络（容器内）
   curl https://api.anthropic.com
   ```

5. **权限确认超时**
   - 检查飞书消息是否正常接收
   - 检查 Host Bridge 日志

---

## 最小依赖

```
aiohttp>=3.8.0
claude-agent-sdk>=0.1.0
```

---

## 新容器配置检查清单

- [ ] 容器内 Python 版本为 **3.11**（与预编译 libs/ 匹配）
- [ ] 挂载 `src/`、`libs/`、`start.sh` 三个目录
- [ ] 设置 `HOST_BRIDGE_URL`、`GUEST_PROXY_PORT`、`CONTAINER_NAME` 环境变量
- [ ] 设置 `ANTHROPIC_API_KEY` 环境变量
- [ ] 添加 `extra_hosts: host.docker.internal:host-gateway`
- [ ] 容器加入与 Host Bridge 相同的网络（如使用自定义网络）
- [ ] 容器启动后运行 `/opt/guest-proxy/start.sh`（不是 `python -m`）
- [ ] 验证健康检查：`curl http://localhost:8081/health`