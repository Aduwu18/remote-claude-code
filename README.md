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

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入飞书应用的 APP_ID 和 APP_SECRET
```

```env
APP_ID=cli_xxxxxx
APP_SECRET=xxxxxx
```

### 3. 配置用户白名单

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
# 用户白名单（飞书 open_id）
authorized_users:
  - "ou_xxxxxx"

# Redis 配置
redis:
  url: "redis://localhost:6379/0"

# Host Bridge 配置
host_bridge:
  port: 8080

# Guest Proxy 配置
guest_proxy:
  port: 8081
  host_bridge_url: "http://host.docker.internal:8080"
```

### 4. 启动服务

#### 方式一：Docker Compose（推荐）

```bash
# 启动 Redis + Host Bridge
docker-compose up -d
```

#### 方式二：手动部署

```bash
# 1. 启动 Redis
docker run -d -p 6379:6379 redis:7-alpine

# 2. 启动 Host Bridge
python -m src.main_websocket
```

### 5. 在目标容器内启动 Guest Proxy

```bash
# 安装依赖
pip install claude-agent-sdk aiohttp redis

# 设置环境变量
export HOST_BRIDGE_URL=http://host.docker.internal:8080
export CONTAINER_NAME=my-container

# 启动
python -m src.guest_proxy.server
```

## 飞书应用配置

1. 进入 [飞书开放平台](https://open.feishu.cn/)
2. 创建应用，获取 APP_ID 和 APP_SECRET
3. 事件订阅 → 选择"使用长连接接收事件"
4. 添加事件：`im.message.receive_v1`
5. 权限管理 → 添加以下权限

| 权限 | 说明 |
|------|------|
| `im:chat` | 创建群聊 |
| `im:message` | 基础消息权限 |
| `im:message:readonly` | 读取消息内容 |
| `im:message:send_as_bot` | 以机器人身份发送消息 |
| `im:message.group_at_msg:readonly` | 接收群聊 @ 消息 |
| `im:message.group_msg` | 接收群聊所有消息 |

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

.devcontainer/               # 开发容器配置
├── devcontainer.json
├── Dockerfile
└── startup.sh
```

## 技术栈

- Python 3.10+
- claude-agent-sdk（Claude Code Python SDK）
- lark-oapi（飞书 SDK）
- Redis（路由索引）
- aiohttp（HTTP 服务）

## .devcontainer 注入

将 `.devcontainer/` 目录复制到目标容器内，可快速部署 Guest Proxy：

```bash
# 在容器内
cd /path/to/.devcontainer
./startup.sh
```

环境变量：
- `HOST_BRIDGE_URL`: Host Bridge 地址
- `CONTAINER_NAME`: 容器名称
- `GUEST_PROXY_PORT`: 监听端口（默认 8081）