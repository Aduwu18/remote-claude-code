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

- Python 3.10+
- Redis（本地或 Docker）
- 飞书应用（需提前创建）

### 步骤概览

```
1. 安装依赖 → 2. 配置环境变量 → 3. 启动 Redis → 4. 启动服务 → 5. 获取 open_id 并加入白名单
```

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入飞书应用凭证：

```env
APP_ID=cli_xxxxxx
APP_SECRET=xxxxxx
```

> **获取凭证**: 在 [飞书开放平台](https://open.feishu.cn/) 创建应用后获取

### 3. 配置用户白名单

```bash
cp config.example.yaml config.yaml
```

**首次启动可先不配置白名单**，启动服务后在飞书发送消息，日志中会显示你的 `open_id`。

编辑 `config.yaml`：

```yaml
# 用户白名单（飞书 open_id）
authorized_users:
  - "ou_xxxxxx"  # 替换为你的 open_id

# 权限确认设置（可选）
permission:
  enabled: true   # 敏感操作需确认
  timeout: 0      # 0 = 无限等待

# Redis 配置
redis:
  url: "redis://localhost:6379/0"

# Host Bridge 配置
host_bridge:
  port: 8080
  host: "0.0.0.0"

# Guest Proxy 配置（容器内运行时需要）
guest_proxy:
  port: 8081
  host_bridge_url: "http://host.docker.internal:8080"
```

### 4. 启动 Redis

```bash
# 方式一：Docker（推荐）
docker run -d -p 6379:6379 --name redis redis:7-alpine

# 方式二：本地安装
# macOS: brew install redis && brew services start redis
# Ubuntu: sudo apt install redis-server && sudo systemctl start redis
```

验证 Redis：

```bash
redis-cli ping  # 应返回 PONG
```

### 5. 启动服务

```bash
# 前台运行（调试用）
python -m src.main_websocket

# 后台运行
./start.sh

# 停止服务
./stop.sh

# 查看日志
tail -f log.log
```

### 6. 获取 open_id 并完成配置

1. 在飞书中给机器人发送任意消息
2. 查看日志，找到类似内容：
   ```
   sender_id: ou_xxxxxx
   ```
3. 将 `ou_xxxxxx` 添加到 `config.yaml` 的 `authorized_users`
4. 重启服务：
   ```bash
   ./stop.sh && ./start.sh
   ```

### 7. 验证启动成功

```bash
# 检查服务状态
ps aux | grep main_websocket

# 检查日志无报错
tail -20 log.log
```

在飞书中发送消息，机器人应正常回复。

### Docker Compose 部署（可选）

适合生产环境一键部署：

```bash
docker-compose up -d
```

> **注意**: 需要先配置 `.env` 和 `config.yaml`

---

## 飞书应用配置

**首次使用必须完成以下配置：**

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

## 可插拔集成

将 Guest Proxy 集成到现有开发环境，详见 [集成文档](docs/GUEST_PROXY_INTEGRATION.md)。

**快速集成：**

```bash
# 1. 复制模块到目标项目
cp -r src/guest_proxy src/protocol /your-project/src/

# 2. 安装依赖
pip install -r deploy/requirements-guest-proxy.txt

# 3. 设置环境变量
export HOST_BRIDGE_URL=http://host.docker.internal:8080
export ANTHROPIC_API_KEY=your-key

# 4. 启动
./deploy/start-guest-proxy.sh
```

**Docker Compose 集成：**

```bash
docker-compose -f docker-compose.yml -f deploy/docker-compose.guest-proxy.yml up -d
```