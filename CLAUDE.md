# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Feishu (飞书) bot that integrates Claude Code CLI, enabling users to interact with Claude Code through Feishu chat. Uses a **Host-Guest architecture** for deep environment isolation.

**Architecture:**
```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  WebSocket   │───►│ Redis Router │───►│ Guest Proxy  │
│  (飞书消息)   │    │  (路由索引)   │    │ (容器内服务)  │
└──────────────┘    └──────────────┘    └──────────────┘
```

**Core Features:**
- Guest Proxy runs inside Docker containers, inheriting `.bashrc`, venv, and environment
- Redis stores `chat_id -> container_endpoint` routing
- Permission confirmation via Feishu messages for sensitive operations (Write, Edit, Bash)
- Protocol interceptor for management commands (`/ls`, `/start`, `/exit`)
- Independent Claude sessions per container

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start Redis (required)
docker run -d -p 6379:6379 --name redis redis:7-alpine

# Configure environment
cp .env.example .env
# Edit .env with APP_ID and APP_SECRET

# Run (foreground)
python -m src.main_websocket

# Run (background)
./start.sh

# Stop
./stop.sh

# View logs
tail -f log.log
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_ID` | Yes | Feishu application ID |
| `APP_SECRET` | Yes | Feishu application secret |
| `ANTHROPIC_API_KEY` | Yes | Claude API key (for Guest Proxy) |

## Configuration (`config.yaml`)

```yaml
# User whitelist (Feishu open_id)
authorized_users:
  - "ou_xxxxxx"  # Find open_id in logs after sending a message

# Permission confirmation settings
permission:
  enabled: true    # Require confirmation for sensitive operations
  timeout: 0       # 0 = wait indefinitely
```

- Copy `config.example.yaml` to `config.yaml` and configure
- `authorized_users` is **required** - unauthorized users will be rejected

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `src/main_websocket.py` | Main entry - WebSocket handler + Host Bridge initialization |
| `src/host_bridge/server.py` | HTTP server for Guest Proxy registration and permission forwarding |
| `src/host_bridge/client.py` | HTTP client for communicating with Guest Proxy |
| `src/guest_proxy/server.py` | HTTP server running inside Docker containers |
| `src/guest_proxy/claude_client.py` | Claude Code SDK wrapper with permission callbacks |
| `src/protocol/__init__.py` | JSON-RPC 2.0 protocol definitions (requests, responses, error codes) |
| `src/interceptor.py` | Protocol interceptor for management commands (`/ls`, `/start`, `/exit`) |
| `src/redis_client.py` | Redis client for route management (`chat_id -> endpoint`) |
| `src/docker_session_manager.py` | Docker session persistence (SQLite) |
| `src/permission_manager.py` | Permission confirmation state management |
| `src/config.py` | Configuration loading (YAML + env vars) and user authorization |
| `src/feishu_utils/feishu_utils.py` | Feishu API utilities (send/reply messages, create group chats) |
| `src/feishu_utils/card_builder.py` | Card message builder (interactive cards, buttons, status updates) |
| `src/status_manager.py` | Status message management with card-based in-place updates |

### HTTP Endpoints

**Host Bridge (`:8080`)**:
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rpc` | POST | JSON-RPC 2.0 requests (register, permission, status_update, heartbeat) |
| `/health` | GET | Health check (returns Redis connection status) |
| `/routes` | GET | List all chat_id -> endpoint routes |
| `/permission_response` | POST | Receive permission response from Feishu |

**Guest Proxy (`:8081` in containers)**:
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rpc` | POST | JSON-RPC 2.0 requests (chat, health_check) |
| `/stream` | POST | Streaming chat (NDJSON response) |
| `/health` | GET | Health check (returns container name, active sessions) |

### Request Flow

```
Feishu Message → WebSocket → interceptor.try_intercept()
                                  ↓ (not intercepted)
                           Redis lookup: get_route(chat_id)
                                  ↓
                           GuestProxyClient.chat_stream() → HTTP Stream to Guest Proxy
                                  ↓
                           GuestClaudeClient.chat_stream() → Claude SDK
                                  ↓
                           Stream events (status, tool_call, content, complete)
                                  ↓
                           Real-time status updates via StatusManager
                                  ↓
                           Final response → Feishu message
```

### Streaming Response Architecture

The system uses **streaming responses** for real-time feedback during long-running tasks:

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  WebSocket   │───►│ Redis Router │───►│ Guest Proxy  │
│  (飞书消息)   │    │  (路由索引)   │    │ (容器内服务)  │
└──────────────┘    └──────────────┘    └──────────────┘
       │                                       │
       │         ┌──────────────┐             │
       └────────►│ Status Card  │◄────────────┘
                 │ (实时更新)    │
                 └──────────────┘
```

**Stream Event Types:**
| Event | Description |
|-------|-------------|
| `heartbeat` | Keep-alive signal |
| `status` | Status text update |
| `tool_call` | Tool being executed |
| `content` | Response content chunk |
| `complete` | Task finished |
| `error` | Error occurred |

### Host-Guest Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Host Bridge (宿主机)                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐      │
│  │ WebSocket    │    │ Redis        │    │ HTTP Server          │      │
│  │ (飞书长连接)  │    │ (路由索引)    │    │ :8080 (RPC + 注册)   │      │
│  └──────────────┘    └──────────────┘    └──────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────┘
                              │ HTTP
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Guest Proxy #1  │  │ Guest Proxy #2  │  │ Guest Proxy #N  │
│ (容器 A 内)      │  │ (容器 B 内)      │  │ (容器 N 内)      │
│                 │  │                 │  │                 │
│ 继承容器环境:    │  │ 继承容器环境:    │  │ 继承容器环境:    │
│ • .bashrc      │  │ • .bashrc      │  │ • .bashrc      │
│ • venv         │  │ • venv         │  │ • venv         │
│ • env vars     │  │ • env vars     │  │ • env vars     │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

**Key Points:**
- Guest Proxy runs inside Docker containers, inherits all environment settings
- Each container has independent Claude sessions
- Host Bridge is stateless - all routing stored in Redis

### Session Model

```
┌─────────────────────────────────────────────────────┐
│  Feishu Chat ID          Guest Proxy Endpoint       │
├─────────────────────────────────────────────────────┤
│  User A (private)  ────►  container_a:8081         │
│  User B (private)  ────►  container_b:8081         │
│  Project Group     ────►  container_c:8081         │
│  Docker Session    ────►  specific container        │
└─────────────────────────────────────────────────────┘
```

Redis stores `chat_id -> endpoint` mapping. Docker sessions create dedicated group chats named "🐳 {container_name}".

### Permission Model

1. **User Authorization**: `authorized_users` in `config.yaml` controls who can use the bot
2. **Permission Confirmation**: Sensitive tools (Write, Edit, Bash) require user confirmation via Feishu message
3. **Safe Tools**: Read, Glob, Grep bypass permission confirmation

### Concurrency Model

- **Per-chat serialization**: Messages from the same chat are processed FIFO via thread-safe queues
- **Cross-chat parallelism**: Different chats can be processed concurrently
- Active queues managed in `_active_queues` dict with `_queue_lock`

## Key Patterns

### 1. JSON-RPC Protocol (`src/protocol/__init__.py`)

All Host-Guest communication uses JSON-RPC 2.0:

```python
# Request
{
    "jsonrpc": "2.0",
    "method": "chat",
    "params": {"message": "...", "chat_id": "...", "user_open_id": "..."},
    "id": "req-123"
}

# Response
{
    "jsonrpc": "2.0",
    "result": {"content": "...", "status": "completed", "session_id": "..."},
    "id": "req-123"
}
```

**Key methods:** `chat`, `chat_stream`, `register`, `permission`, `status_update`, `heartbeat`

### 2. Protocol Interceptor (`src/interceptor.py`)

Management commands are intercepted before routing:

```python
interceptor = get_interceptor()
result = interceptor.try_intercept(user_id, chat_id, message)
if result:
    # Command was handled (e.g., /ls, /start, /exit)
    send_message(chat_id, result)
else:
    # Route to Guest Proxy via Redis
    endpoint = redis_client.get_route(chat_id)
```

**Supported commands:** `/ls`, `/start <name>`, `/enter <name>`, `/stop`, `/exit`, `/help`

### 3. Guest Proxy Client (`src/host_bridge/client.py`)

HTTP client for communicating with Guest Proxy:

```python
# Streaming (recommended for long tasks)
async with GuestProxyClient() as client:
    result = await client.chat_stream(
        endpoint="http://container:8081",
        message="...",
        chat_id="...",
        user_open_id="...",
        status_callback=lambda status, details: print(status),
    )

# Synchronous (for simple queries)
async with GuestProxyClient() as client:
    result = await client.chat(
        endpoint="http://container:8081",
        message="...",
        chat_id="...",
        user_open_id="...",
    )
```

### 4. Claude Client (`src/guest_proxy/claude_client.py`)

Claude SDK wrapper with permission callbacks:

```python
client = GuestClaudeClient(
    session_id="...",  # Resume session
    container_name="nginx",
    host_bridge_url="http://host:8080",
)
await client.connect()

# Streaming (yields real-time events)
async for event in client.chat_stream("message"):
    if event.event_type == StreamEventType.STATUS:
        print(f"Status: {event.data['text']}")
    elif event.event_type == StreamEventType.CONTENT:
        print(event.data['text'])
    elif event.event_type == StreamEventType.COMPLETE:
        print(f"Done: {event.data['session_id']}")

# Synchronous
response = await client.chat("message")
# response.content, response.session_id, response.tool_calls
```

**Permission flow:** Sensitive tools (Write, Edit, Bash) trigger HTTP request to Host Bridge → Feishu confirmation.

### 5. Permission Confirmation Flow

```
Guest Proxy detects sensitive tool
       ↓
HTTP POST to Host Bridge /rpc (method: permission)
       ↓
Host Bridge sends card to Feishu with Approve/Deny buttons
       ↓
User clicks button OR replies "y"/"n"
       ↓
Host Bridge resolves Future
       ↓
Guest Proxy receives approved/denied
```

**Card-based Confirmation**: Permission requests now use interactive card messages with clickable buttons. Text fallback ("y"/"n") is still supported for compatibility.

### 6. Redis Route Management

```python
# Set route (when container session created)
redis_client.set_route(chat_id, "http://container:8081")

# Get route (when message received)
endpoint = redis_client.get_route(chat_id)

# Delete route (when session ends)
redis_client.delete_route(chat_id)
```

### 7. Feishu Message Handling

- Group chat: Use `reply_message(message_id, text)` to reply to specific message
- Private chat: Use `send_message(chat_id, text)` for direct message
- Create group: `create_group_chat(user_open_id, group_name)`
- **Card messages**: Use `send_card_message()`, `update_card_message()` for interactive cards
- **Card builder**: Use `CardBuilder` class or helper functions in `card_builder.py`

### 8. Status Manager

Status updates use card messages with in-place updates via PATCH API:

```python
status_mgr = StatusManager(chat_id, use_card=True)
status_mgr.send_status("Processing...")      # Send initial card
status_mgr.update_status("Reading files...")  # Update in-place
status_mgr.finalize("Task completed!")        # Final result (green header)
status_mgr.finalize_error("Error occurred")   # Error result (red header)
```

## Claude Code Configuration

The `GuestClaudeClient` configures Claude Code with:

- **Allowed Tools**: `["Read", "Write", "Edit", "Bash", "Glob", "Grep"]`
- **Permission Mode**: `default` (SDK handles permission prompts)
- **System Prompt**: Container-aware prompt with environment info

See `GUEST_SYSTEM_PROMPT` in `guest_proxy/claude_client.py` for full prompt text.

## Extension Points

### Adding New RPC Methods

1. Define method in `src/protocol/__init__.py` (`RequestMethod` enum)
2. Add params/result dataclasses
3. Add handler in `GuestProxyServer._get_handler()` or `HostBridgeServer._get_handler()`

### Adding New Management Commands

1. Add handler method in `src/interceptor.py`
2. Register in `self.handlers` dict

### Deploying to New Containers

See `docs/GUEST_PROXY_INTEGRATION.md` for:
- Docker Compose volume mounts
- Environment variables
- Network configuration
- Health checks

## Testing

```bash
# Test Claude Code integration (requires ANTHROPIC_API_KEY)
python test/call_claude_code.py

# Test Docker session creation
python test/test_docker_session.py

# Test streaming response
python test/test_streaming.py
```

## Project Structure

```
├── src/
│   ├── main_websocket.py      # Main entry (Host Bridge + WebSocket)
│   ├── config.py              # Configuration & user authorization
│   ├── redis_client.py        # Redis route management
│   ├── interceptor.py         # Protocol interceptor for /commands
│   ├── docker_session_manager.py  # Docker session persistence
│   ├── permission_manager.py  # Permission confirmation state
│   ├── status_manager.py      # Status message management
│   ├── protocol/              # JSON-RPC protocol definitions
│   │   └── __init__.py
│   ├── host_bridge/           # Host Bridge (runs on host)
│   │   ├── __init__.py
│   │   ├── server.py          # HTTP server
│   │   └── client.py          # Guest Proxy client
│   ├── guest_proxy/           # Guest Proxy (runs in containers)
│   │   ├── __init__.py
│   │   ├── server.py          # HTTP server
│   │   ├── claude_client.py   # Claude SDK wrapper
│   │   ├── watchdog.py        # Task monitoring
│   │   ├── status_handler.py  # Status handling
│   │   └── config.py          # Configuration
│   └── feishu_utils/          # Feishu API helpers
│       ├── __init__.py
│       ├── feishu_utils.py    # Message API functions
│       └── card_builder.py    # Card message builder
├── data/
│   └── docker_sessions.db     # Docker session mappings (auto-created)
├── test/
│   ├── call_claude_code.py    # Claude integration test
│   ├── test_docker_session.py # Docker session test
│   └── test_streaming.py      # Streaming response test
├── docs/
│   └── GUEST_PROXY_INTEGRATION.md  # Container integration guide
├── config.yaml                # User configuration (gitignored)
├── config.example.yaml        # Configuration template
├── .env                       # Environment variables
├── start.sh / stop.sh         # Process management
└── requirements.txt
```

## Dependencies

- `claude-agent-sdk` - Claude Code Python SDK
- `lark-oapi` - Feishu/Lark official SDK
- `redis` - Route management
- `aiohttp` - HTTP server/client
- `python-dotenv` - Environment management
- `PyYAML` - Configuration file parsing
- `pycryptodome` - Encryption for Feishu message verification
- `nest-asyncio` - Nested event loop support

## Feishu App Configuration

1. Create app at [Feishu Open Platform](https://open.feishu.cn/)
2. Event subscription → Select "Use long connection"
3. Add event: `im.message.receive_v1`
4. Permissions: Configure the following `im:message` related permissions:

| Permission | Description |
|------------|-------------|
| `im:chat` | Create and manage chats |
| `im:message` | Basic message permissions |
| `im:message:readonly` | Read message content |
| `im:message:send_as_bot` | Send messages as bot |
| `im:message.group_at_msg:readonly` | Receive @bot messages in groups |
| `im:message.group_msg` | Receive all group messages (sensitive) |

**Important:** After adding permissions, you must publish the app version for changes to take effect.

## Known Limitations

- No message rate limiting
- Session cleanup not implemented
- No health check endpoint for WebSocket mode
- Permission confirmation HTTP flow not fully implemented in Guest Proxy