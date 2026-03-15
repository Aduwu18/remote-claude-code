# Claude Code 飞书机器人

将 Claude Code 接入飞书，实现 Docker 容器操作助手功能。

## 架构概述

采用 Host-Guest 架构，实现深度环境隔离：

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Host Bridge (无状态网关)                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐      │
│  │ WebSocket    │    │ Redis        │    │ Permission           │      │
│  │ (飞书长连接)  │    │ (路由索引)    │    │ Forwarder            │      │
│  └──────┬───────┘    └──────┬───────┘    └──────────┬───────────┘      │
│         │                   │                       │                   │
│         └───────────────────┼───────────────────────┘                   │
│                             │ HTTP                                      │
│                             ▼                                           │
└─────────────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Guest Proxy #1  │  │ Guest Proxy #2  │  │ Guest Proxy #N  │
│ (容器 A 内)      │  │ (容器 B 内)      │  │ (容器 N 内)      │
│                 │  │                 │  │                 │
│ ┌─────────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │
│ │ Claude SDK  │ │  │ │ Claude SDK  │ │  │ │ Claude SDK  │ │
│ │ + Watchdog  │ │  │ │ + Watchdog  │ │  │ │ + Watchdog  │ │
│ └─────────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │
│                 │  │                 │  │                 │
│ 继承容器环境:    │  │ 继承容器环境:    │  │ 继承容器环境:    │
│ • .bashrc      │  │ • .bashrc      │  │ • .bashrc      │
│ • venv         │  │ • venv         │  │ • venv         │
│ • env vars     │  │ • env vars     │  │ • env vars     │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## 核心特性

**环境隔离**
- Guest Proxy 在容器内运行，继承 `.bashrc`、虚拟环境等
- 每个容器独立的 Claude 会话上下文

**路由管理**
- Redis 存储 `chat_id -> container_endpoint` 映射
- 支持多容器并行操作

**权限确认**
- 敏感操作（写文件、执行命令）需要用户确认
- 飞书端实时弹窗，回复 "y/n" 控制

**异常感知**
- Watchdog 监控任务状态
- 任务超时、进程卡死主动推送

## 快速开始

### 前置条件

- Python 3.11+
- Redis（本地或 Docker）
- 飞书应用（需提前创建）
- 目标容器内 Python 3.11（与预编译 libs 匹配）

### 部署流程

```
1. 部署 Host Bridge → 2. 配置目标容器 → 3. 启动服务
```

---

### 步骤 1：部署 Host Bridge（宿主机）

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 APP_ID 和 APP_SECRET

# 配置用户白名单
cp config.example.yaml config.yaml
# 首次启动可暂不配置，从日志获取 open_id 后再添加

# 启动 Redis
docker run -d -p 6379:6379 --name redis redis:7-alpine

# 启动 Host Bridge
./start.sh
```

验证启动：
```bash
tail -f log.log  # 查看日志
ps aux | grep main_websocket  # 检查进程
```

---

### 步骤 2：配置目标容器

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

**关键配置说明：**

| 配置项 | 说明 |
|--------|------|
| `volumes` | 挂载预编译依赖和启动脚本 |
| `extra_hosts` | Linux 必需，macOS/Windows 可省略 |
| `HOST_BRIDGE_URL` | Host Bridge 地址，容器内访问宿主机 |
| `Python 3.11` | 目标容器必须使用 Python 3.11（与预编译 libs 匹配） |

---

### 步骤 3：在容器内启动 Guest Proxy

**重要：必须运行 `start.sh`，不能直接运行 `python -m`！**

```bash
# 进入容器
docker exec -it your-container bash

# 启动 Guest Proxy
/opt/guest-proxy/start.sh
```

**为什么必须用 start.sh？**

start.sh 会设置 `PYTHONPATH=/opt/guest-proxy/libs:/opt/guest-proxy/src`，让 Python 能找到预编译的依赖包。直接运行 `python -m guest_proxy.server` 会找不到依赖。

---

### 步骤 4：完成用户白名单配置

1. 在飞书中给机器人发送任意消息
2. 查看日志，找到 `sender_id: ou_xxxxxx`
3. 编辑 `config.yaml`，将 `ou_xxxxxx` 添加到 `authorized_users`
4. 重启 Host Bridge：`./stop.sh && ./start.sh`

---

### 验证部署

```bash
# 1. 检查 Host Bridge
curl http://localhost:8080/health

# 2. 检查 Guest Proxy（在容器内）
curl http://localhost:8081/health

# 3. 在飞书中发送消息测试
```

---

### 进程管理（可选）

**Supervisor 配置：**
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

**嵌入应用启动：**
```python
import asyncio
from guest_proxy.server import GuestProxyServer

async def main():
    server = GuestProxyServer()
    await server.start()
    # 继续应用逻辑...
```

---

## 飞书应用配置

1. 进入 [飞书开放平台](https://open.feishu.cn/)
2. 创建应用，获取 APP_ID 和 APP_SECRET
3. **事件订阅** → 选择「使用长连接接收事件」
4. **添加事件**: `im.message.receive_v1`
5. **权限管理** → 添加以下权限：

| 权限 | 说明 |
|------|------|
| `im:chat` | 创建群聊 |
| `im:message` | 基础消息权限 |
| `im:message:readonly` | 读取消息内容 |
| `im:message:send_as_bot` | 以机器人身份发送消息 |
| `im:message.group_at_msg:readonly` | 接收群聊 @ 消息 |
| `im:message.group_msg` | 接收群聊所有消息 |

6. **发布版本** - 添加权限后必须发布应用版本才能生效

## 使用示例

**创建容器会话：**

```
进入 nginx 容器
```

系统会创建专属群聊 "🐳 nginx (Claude助手)"，在新窗口中操作容器。

**容器内操作：**

```
查看 /app 目录
读取 config.json
运行 python script.py
```

**权限确认：**

```
🔒 权限确认请求

操作: Write
详情:
{
  "file_path": "/app/config.json",
  "content": "{...}"
}

请回复:
• "y" 或 "确认" - 允许执行
• "n" 或 "拒绝" - 拒绝执行
```

**退出容器会话：**

```
/exit
```

## 项目结构

```
src/
├── main_websocket.py        # 主入口（Host Bridge + WebSocket）
├── redis_client.py          # Redis 客户端封装
├── config.py                # 配置加载与用户授权
├── permission_manager.py    # 权限确认状态管理
├── docker_session_manager.py # Docker 会话持久化
├── status_manager.py        # 状态消息管理
├── protocol/                # 通信协议定义
│   └── __init__.py
├── host_bridge/             # Host Bridge 模块
│   ├── __init__.py
│   ├── server.py            # HTTP 服务
│   └── client.py            # Guest Proxy 客户端
├── guest_proxy/             # Guest Proxy 模块（容器内运行）
│   ├── __init__.py
│   ├── server.py            # HTTP 服务
│   ├── claude_client.py     # Claude SDK 封装
│   ├── status_handler.py    # 状态处理
│   ├── watchdog.py          # 异常监控
│   └── config.py            # 配置
└── feishu_utils/            # 飞书工具
    └── feishu_utils.py

data/
└── docker_sessions.db       # Docker 会话数据库

deploy/                      # 可插拔部署配置
├── docker-compose.guest-proxy.yml
├── Dockerfile.overlay
├── README.md
├── requirements-guest-proxy.txt
└── start-guest-proxy.sh

docs/                        # 文档
└── GUEST_PROXY_INTEGRATION.md
```

## 技术栈

- Python 3.10+
- claude-agent-sdk（Claude Code Python SDK）
- lark-oapi（飞书 SDK）
- Redis（路由索引）
- aiohttp（HTTP 服务）

## 更多文档

- [Guest Proxy 集成指南](docs/GUEST_PROXY_INTEGRATION.md) - 详细的容器集成配置
- [部署配置](deploy/README.md) - Docker Compose 和 Dockerfile 模板