# Claude Code 飞书机器人

将 Claude Code CLI 接入飞书，实现 Docker 容器操作助手功能。采用 **Host-Guest 架构** 实现深度环境隔离。

## 架构概述

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Host Bridge (宿主机网关)                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐      │
│  │ WebSocket    │    │ Redis        │    │ HTTP Server          │      │
│  │ (飞书长连接)  │    │ (路由索引)    │    │ :8080 (RPC + 注册)   │      │
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

**Terminal 原生模式**
- 直接运行原生 `claude` CLI，获得完整本地体验
- PTY 模式（交互式）和 Print 模式（推荐）两种选择
- 双向权限确认：CLI 和飞书都可确认权限
- 两种同步模式：`notify`（只提醒）和 `sync`（完全同步）

**路由管理**
- Redis 存储 `chat_id -> container_endpoint` 映射
- 支持多容器并行操作

**权限确认**
- 敏感操作（Write, Edit, Bash）需要用户确认
- 卡片消息交互 + 文本回复 "y/n" 双重确认
- Terminal CLI 支持 CLI 和飞书双向确认

**流式响应**
- 实时状态更新（卡片原地更新）
- 工具调用进度反馈
- 长消息自动分块

**会话清理**
- 用户退群/群解散自动清理
- `/exit` 命令手动退出

**异常感知**
- Watchdog 监控任务状态
- 任务超时、进程卡死主动推送

## 快速开始

### 前置条件

- Python 3.10+
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

## 使用示例

### 创建容器会话

在飞书中发送：

```
进入 nginx 容器
```

或使用命令格式：

```
/start nginx
```

系统会创建专属群聊 "🐳 nginx"，在新窗口中操作容器。

### 容器内操作

```
查看 /app 目录
读取 config.json
运行 python script.py
```

### 权限确认

当 Claude 执行敏感操作时，会发送卡片消息：

```
🔒 权限确认请求

操作: Write
详情:
{
  "file_path": "/app/config.json",
  "content": "{...}"
}

[✅ 允许]  [❌ 拒绝]
```

点击按钮或回复 "y"/"n" 确认。

### 退出容器会话

```
/exit
```

或：

```
退出
```

---

## Terminal CLI 原生模式

Terminal CLI 支持直接运行原生 `claude` CLI，获得完整的本地体验：

```bash
# 启动 Terminal（原生模式，默认）
python -m src.terminal_client

# 指定同步模式
python -m src.terminal_client --sync-mode notify   # 默认：只提醒交互需求
python -m src.terminal_client --sync-mode sync     # 完全双向同步

# 指定 CLI 模式
python -m src.terminal_client --cli-mode print     # 推荐：每条消息独立进程
python -m src.terminal_client --cli-mode pty       # 交互式 PTY 模式
```

**特性：**
- 原生 CLI 体验（PTY 或 Print 模式）
- 双向权限确认（CLI 和飞书都可以确认权限）
- 两种同步模式（只提醒/完全同步）
- 飞书消息自动注入到 CLI

---

## 飞书应用配置

1. 进入 [飞书开放平台](https://open.feishu.cn/)
2. 创建应用，获取 APP_ID 和 APP_SECRET
3. **事件订阅** → 选择「使用长连接接收事件」
4. **添加事件**:
   - `im.message.receive_v1` - 接收消息
   - `im.chat.member.user_withdrawn_v1` - 用户退群（会话清理）
   - `im.chat.disbanded_v1` - 群解散（会话清理）
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

---

## 项目结构

```
src/
├── main_websocket.py          # 主入口（Host Bridge + WebSocket）
├── config.py                  # 配置加载与用户授权
├── redis_client.py            # Redis 路由管理
├── interceptor.py             # 协议拦截器（管理命令）
├── docker_session_manager.py  # Docker 会话持久化
├── native_claude_client.py    # 原生 Claude CLI 客户端（PTY/Print 模式）
├── permission_manager.py      # 权限确认状态管理
├── status_manager.py          # 状态消息管理（卡片更新）
├── protocol/                  # JSON-RPC 2.0 协议定义
│   └── __init__.py
├── host_bridge/               # Host Bridge 模块（宿主机）
│   ├── __init__.py
│   ├── server.py              # HTTP 服务
│   └── client.py              # Guest Proxy 客户端
├── guest_proxy/               # Guest Proxy 模块（容器内）
│   ├── __init__.py
│   ├── server.py              # HTTP 服务
│   ├── claude_client.py       # Claude SDK 封装
│   ├── status_handler.py      # 状态处理
│   ├── watchdog.py            # 异常监控
│   └── config.py              # 配置
├── terminal_client/           # Terminal CLI 模块
│   ├── __init__.py
│   └── client.py              # Terminal 客户端（原生模式 + 飞书同步）
└── feishu_utils/              # 飞书工具
    ├── __init__.py
    ├── feishu_utils.py        # 消息 API
    └── card_builder.py        # 卡片消息构建器

data/
└── docker_sessions.db         # Docker 会话数据库（自动创建）

deploy/                        # 可插拔部署配置
├── docker-compose.guest-proxy.yml
├── Dockerfile.overlay
├── README.md
├── requirements-guest-proxy.txt
└── start-guest-proxy.sh

docs/
└── GUEST_PROXY_INTEGRATION.md # 容器集成详细指南
```

---

## HTTP 端点

### Host Bridge (`:8080`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/rpc` | POST | JSON-RPC 请求（register, permission, status_update, heartbeat） |
| `/health` | GET | 健康检查（返回 Redis 连接状态） |
| `/routes` | GET | 列出所有路由 |
| `/permission_response` | POST | 接收权限响应 |

### Guest Proxy (`:8081`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/rpc` | POST | JSON-RPC 请求（chat, health_check, cleanup_session） |
| `/stream` | POST | 流式聊天（NDJSON 响应） |
| `/health` | GET | 健康检查（返回容器名、活跃会话数） |

### Local Session Bridge (`:8082`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/rpc` | POST | JSON-RPC 请求 |
| `/stream` | POST | 流式聊天（NDJSON 响应） |
| `/health` | GET | 健康检查 |
| `/ws` | GET | WebSocket 连接（双向通信） |
| `/terminal/create` | POST | 创建 Terminal 会话 |
| `/terminal/close` | POST | 关闭 Terminal 会话 |
| `/permission/request` | POST | 权限请求（从原生客户端发来） |
| `/permission/response` | POST | 权限响应（从飞书发来） |

---

## 管理命令

| 命令 | 说明 | 自然语言 |
|------|------|----------|
| `/ls` | 列出所有容器 | 列出容器 / 查看容器 |
| `/start <名称>` | 进入容器会话 | 进入 xxx 容器 |
| `/enter <名称>` | 进入容器会话 | 进入 xxx |
| `/exit` | 退出容器会话 | 退出 / 退出容器 |
| `/bind <注册码>` | 绑定 Terminal 到当前聊天 | - |
| `/help` | 显示帮助 | - |

---

## 技术栈

- Python 3.10+
- claude-agent-sdk（Claude Code Python SDK）
- lark-oapi（飞书 SDK）
- Redis（路由索引）
- aiohttp（HTTP 服务）
- Docker（容器隔离）

---

## 更多文档

- [Guest Proxy 集成指南](docs/GUEST_PROXY_INTEGRATION.md) - 详细的容器集成配置
- [部署配置](deploy/README.md) - Docker Compose 和 Dockerfile 模板

---

## 常见问题

### Q: 为什么容器内无法连接 Host Bridge？

检查网络配置：
```bash
# 容器内测试
curl http://host.docker.internal:8080/health

# Linux 需要添加 extra_hosts
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Q: 为什么权限确认没有响应？

1. 检查飞书应用是否订阅了「卡片回传交互」事件
2. 检查日志中是否有 `P2CardActionTrigger` 回调

### Q: 长消息被截断怎么办？

系统会自动分块发送超过 10KB 的消息。如果仍有问题，检查日志中的 `split_long_message` 输出。