# Claude Code 飞书机器人

将 Claude Code 接入飞书，实现本地电脑助手功能。

## 核心特性

**多人协作，独立上下文**

- 支持多人同时使用，互不干扰
- 每个聊天窗口（私聊/群聊）拥有独立的对话上下文
- 上下文自动持久化，重启后可继续之前的对话

```
┌─────────────────────────────────────────────────────┐
│  飞书聊天窗口          Claude Code Session          │
├─────────────────────────────────────────────────────┤
│  张三 私聊  ────────►  session_abc (独立上下文)      │
│  李四 私聊  ────────►  session_xyz (独立上下文)      │
│  项目群聊   ────────►  session_123 (共享上下文)      │
│  测试群聊   ────────►  session_456 (共享上下文)      │
└─────────────────────────────────────────────────────┘
```

**本地电脑操作能力**

- 文件读写、创建、删除
- 执行 Shell/Python 脚本
- 打开/关闭应用程序
- Git 操作、包管理等开发任务

**Docker 容器会话**

- 通过自然语言进入指定容器：`进入 xxx 容器`
- 为每个容器创建独立的私聊窗口
- 在容器内执行命令、操作文件
- 自动读取容器内的授权用户配置

## 与 ClawdBot 的比较

| 特性 | 本项目 | ClawdBot |
|------|--------|----------|
| 聊天平台 | 飞书 | Slack/Discord |
| AI 后端 | Claude Code (本地 CLI) | Claude API |
| 核心能力 | **本地电脑操作** | 对话助手 |
| 连接方式 | 飞书长连接 WebSocket | Webhook |
| 多用户支持 | **每个聊天独立上下文** | 全局/按用户 |
| 会话管理 | SQLite 持久化 | 内存/Redis |

**相似之处：**
- 都是将 Claude 接入企业聊天工具
- 都支持多轮连续对话
- 都通过 session/thread 管理对话上下文

**本项目特色：**
- 使用 Claude Code，可执行本地命令、操作文件
- **每个聊天窗口独立上下文**，群聊成员共享同一上下文
- 飞书长连接方式，无需公网域名
- 轻量级，单文件即可运行

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

### 3. 安装 Claude Code

```bash
# macOS/Linux
curl -fsSL https://claude.ai/install.sh | bash

# Windows
npm install -g @anthropic-ai/claude-code

# 登录
claude login
```

### 4. 飞书应用配置

1. 进入 [飞书开放平台](https://open.feishu.cn/)
2. 创建应用，获取 APP_ID 和 APP_SECRET
3. 事件订阅 → 选择"使用长连接接收事件"
4. 添加事件：`im.message.receive_v1`
5. 权限管理 → 添加 `im:message` 相关权限

### 5. 启动

```bash
# 前台运行
python -m src.main_websocket

# 后台运行
./start.sh

# 停止
./stop.sh

# 查看日志
tail -f log.log
```

## 使用示例

在飞书中 @机器人：

```
@机器人 帮我创建一个 hello.py 文件
@机器人 运行刚才的脚本
@机器人 打开网易云音乐
@机器人 当前目录有哪些文件
```

## 项目结构

```
├── src/
│   ├── main_websocket.py      # 主程序（飞书长连接）
│   ├── context.py             # 请求上下文管理
│   ├── docker_mcp.py          # Docker MCP Server
│   ├── docker_session_manager.py  # Docker 会话管理
│   ├── claude_code/           # Claude Code 封装
│   │   ├── conversation.py    # 对话客户端
│   │   └── __init__.py
│   ├── feishu_utils/          # 飞书工具
│   │   └── feishu_utils.py
│   └── data_base_utils/       # 数据库
│       └── session_store.py   # 会话存储
├── data/
│   ├── sessions.db            # SQLite 数据库
│   └── docker_sessions.db     # Docker 会话数据库
├── .env                       # 环境变量
├── start.sh / stop.sh         # 启停脚本
└── requirements.txt
```

## 技术栈

- Python 3.10+
- claude-agent-sdk（Claude Code Python SDK）
- lark-oapi（飞书 SDK）
- SQLite（会话持久化）

## 扩展其他 Agent

本项目采用模块化设计，可轻松替换或扩展后端 Agent：

```
┌──────────────┐      ┌─────────────────┐      ┌────────────────────┐
│   飞书消息    │ ───► │  main_websocket │ ───► │   Agent 后端        │
│   (chat_id)  │      │   (路由/分发)    │      │                    │
└──────────────┘      └─────────────────┘      └────────────────────┘
                                                        │
                              ┌──────────────────────────┼──────────────────────────┐
                              ▼                          ▼                          ▼
                      ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
                      │ Claude Code  │          │   OpenAI     │          │  自定义 Agent │
                      │ (当前实现)    │          │   Agent      │          │              │
                      └──────────────┘          └──────────────┘          └──────────────┘
```

**扩展方式**

只需实现一个 `chat_sync(message, session_id)` 函数：

```python
# src/your_agent/client.py

def chat_sync(message: str, session_id: str = None) -> tuple[str, str]:
    """
    Args:
        message: 用户消息
        session_id: 会话 ID（用于保持上下文）
    
    Returns:
        (回复内容, 新的 session_id)
    """
    # 你的 Agent 实现
    reply = your_agent.chat(message, session_id)
    return reply, session_id
```

然后在 `main_websocket.py` 中替换导入：

```python
# 替换这行
from src.claude_code import chat_sync

# 改为
from src.your_agent import chat_sync
```

**可扩展的 Agent 示例**

| Agent | 能力 | 适用场景 |
|-------|------|---------|
| Claude Code | 本地文件/命令操作 | 开发助手、自动化 |
| OpenAI Assistants | 对话 + 代码解释器 | 数据分析、问答 |
| LangChain Agent | 自定义工具链 | 复杂工作流 |
| Dify/Coze | 可视化编排 | 快速原型 |
| 本地 LLM | Ollama/vLLM | 私有部署 |
