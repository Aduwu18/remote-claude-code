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
       │
       │         ┌──────────────┐
       └────────►│ Local Bridge │◄─────── Terminal CLI
                 │  (Terminal)   │
                 └──────────────┘
```

**Core Features:**
- Guest Proxy runs inside Docker containers, inheriting `.bashrc`, venv, and environment
- Redis stores `chat_id -> container_endpoint` routing
- Permission confirmation via Feishu messages for sensitive operations (Write, Edit, Bash)
- Protocol interceptor for management commands (`/ls`, `/start`, `/exit`)
- Independent Claude sessions per container
- **Terminal Auto-Create**: Terminal CLI automatically creates Feishu group chat on startup

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

## Development Commands

```bash
# Run Host Bridge (foreground, for debugging)
python -m src.main_websocket

# Run tests
python test/call_claude_code.py          # Claude SDK integration test
python test/test_docker_session.py       # Docker session creation test
python test/test_streaming.py            # Streaming response test

# Syntax check all modified modules
python -m py_compile src/main_websocket.py src/protocol/__init__.py src/docker_session_manager.py

# Health checks
curl http://localhost:8080/health        # Host Bridge
curl http://localhost:8081/health        # Guest Proxy (in container)
curl http://localhost:8082/health        # Local Session Bridge
```

## Terminal CLI Modes

Terminal CLI supports two modes:

### Mode 1: Native CLI Mode (Default, Recommended)

Runs the native `claude` CLI directly with full local experience:

```bash
# Start Terminal with native CLI
python -m src.terminal_client

# Specify sync mode
python -m src.terminal_client --sync-mode notify   # Default: only notifications
python -m src.terminal_client --sync-mode sync     # Full bidirectional sync

# Specify CLI mode
python -m src.terminal_client --cli-mode print     # Recommended: each message is a new process
python -m src.terminal_client --cli-mode pty       # Interactive PTY mode
```

**Features:**
- Native CLI experience (PTY or Print mode)
- Dual-channel permission confirmation (CLI and Feishu)
- Two sync modes: `notify` (only alerts) or `sync` (full sync)
- Feishu message injection into CLI

### Mode 2: SDK Mode (Legacy)

Uses Claude SDK through Local Bridge:

```bash
# Set environment variable to use SDK mode
export TERMINAL_USE_SDK=true
python -m src.terminal_client
```

### Configuration

```yaml
terminal_session:
  enabled: true
  user_open_id: "ou_xxxxxx"  # Your Feishu open_id
  group_name_prefix: "💻 Terminal"

  # Sync mode configuration
  sync_mode: "notify"  # "notify" (only alerts) or "sync" (full sync)

  # Permission confirmation settings
  permission:
    dual_channel: true  # Enable dual-channel confirmation (CLI + Feishu)
    cli_timeout: 60     # CLI confirmation timeout (seconds)
    feishu_timeout: 300 # Feishu confirmation timeout (seconds)
```

### Native Mode Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Terminal CLI    │───►│ Native Client   │───►│ Claude CLI      │
│ (交互界面)       │    │ (PTY/Print)     │    │ (原生进程)       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                       │
        │ WebSocket             │ HTTP
        ▼                       ▼
┌─────────────────┐    ┌─────────────────┐
│ Local Bridge    │───►│ Feishu API      │
│ (:8082/ws)      │    │ (权限/同步)      │
└─────────────────┘    └─────────────────┘
```

### Dual-Channel Permission Confirmation

When Claude requests permission for sensitive operations:
1. CLI prompts for user input (y/n)
2. Feishu sends permission card simultaneously
3. Whichever channel responds first is used

This allows users to confirm permissions from either terminal or mobile.

## Configuration (`config.yaml`)

```yaml
# User whitelist (Feishu open_id)
authorized_users:
  - "ou_xxxxxx"  # Find open_id in logs after sending a message

# Permission confirmation settings
permission:
  enabled: true    # Require confirmation for sensitive operations
  timeout: 0       # 0 = wait indefinitely

# Terminal session settings (for Terminal CLI auto-create)
terminal_session:
  enabled: true              # Enable Terminal auto-create feature
  auto_create_chat: true     # Auto-create group chat on Terminal startup
  auto_disband_on_exit: true # Auto-disband group chat on Terminal exit
  user_open_id: "ou_xxxxxx"  # User open_id for creating group chats
  group_name_prefix: "💻 Terminal"  # Group chat name prefix
```

- Copy `config.example.yaml` to `config.yaml` and configure
- `authorized_users` is **required** - unauthorized users will be rejected
- `terminal_session.user_open_id` is **required** for Terminal CLI auto-create

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `src/main_websocket.py` | Main entry - WebSocket handler + Host Bridge initialization |
| `src/host_bridge/server.py` | HTTP server for Guest Proxy registration and permission forwarding |
| `src/local_session_bridge/server.py` | Local Session Bridge for Terminal CLI connections |
| `src/local_session_bridge/claude_client.py` | Local Claude client with permission forwarding |
| `src/terminal_client/client.py` | Terminal CLI client (native mode with Feishu sync) |
| `src/native_claude_client.py` | Native Claude CLI client (PTY/Print modes, dual-channel permissions) |
| `src/terminal_session_manager.py` | Terminal session management (create/disband group chats) |
| `src/host_bridge/client.py` | HTTP client for communicating with Guest Proxy |
| `src/guest_proxy/server.py` | HTTP server running inside Docker containers |
| `src/guest_proxy/claude_client.py` | Claude Code SDK wrapper with permission callbacks |
| `src/protocol/__init__.py` | JSON-RPC 2.0 protocol definitions (requests, responses, error codes) |
| `src/interceptor.py` | Protocol interceptor for management commands (`/ls`, `/start`, `/exit`, `/bind`) |
| `src/redis_client.py` | Redis client for route management (`chat_id -> endpoint`) |
| `src/docker_session_manager.py` | Docker session persistence (SQLite) |
| `src/permission_manager.py` | Permission confirmation state management |
| `src/config.py` | Configuration loading (YAML + env vars) and user authorization |
| `src/feishu_utils/feishu_utils.py` | Feishu API utilities (send/reply messages, create/disband group chats) |
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

**Local Session Bridge (`:8082`)**:
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rpc` | POST | JSON-RPC 2.0 requests (chat, health_check) |
| `/stream` | POST | Streaming chat (NDJSON response) |
| `/health` | GET | Health check (returns active sessions) |
| `/status` | GET | Detailed status including session list |
| `/ws` | GET | WebSocket connection for bidirectional communication |
| `/terminal/create` | POST | Create Terminal session (auto-create Feishu group chat) |
| `/terminal/close` | POST | Close Terminal session (disband group chat) |
| `/terminal/sync` | POST | Sync output/status to group chat |
| `/permission/request` | POST | Permission request from native client to Feishu |
| `/permission/response` | POST | Permission response from Feishu to native client |

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

### Session Cleanup

When users leave group chats or chats are disbanded, the system automatically cleans up:

**Triggers:**
- User withdraws from group chat (`im.chat.member.user_withdrawn_v1`)
- Group chat is disbanded (`im.chat.disbanded_v1`)
- User sends `/exit` command

**Cleanup Process:**
1. Delete Redis route (`chat_id -> endpoint`)
2. Delete SQLite session record
3. Notify Guest Proxy to clean up Claude session

**Implementation:**
- `handle_member_withdrawn()` - Handles user withdrawal events
- `handle_chat_disbanded()` - Handles chat disband events
- `cleanup_session()` - Unified cleanup logic

**Required Feishu Permissions:**
- Subscribe to `im.chat.member.user_withdrawn_v1` event
- Subscribe to `im.chat.disbanded_v1` event

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

**Key methods:** `chat`, `chat_stream`, `register`, `permission`, `status_update`, `heartbeat`, `cleanup_session`

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

**Supported commands:** `/ls`, `/start <name>`, `/enter <name>`, `/stop`, `/exit`, `/bind <code>`, `/help`

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

## Project Structure

```
├── src/
│   ├── main_websocket.py      # Main entry (Host Bridge + WebSocket)
│   ├── config.py              # Configuration & user authorization
│   ├── redis_client.py        # Redis route management
│   ├── interceptor.py         # Protocol interceptor for /commands
│   ├── docker_session_manager.py  # Docker session persistence
│   ├── terminal_session_manager.py # Terminal session management
│   ├── native_claude_client.py    # Native Claude CLI client (PTY/Print modes)
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
│   ├── terminal_client/       # Terminal CLI
│   │   ├── __init__.py
│   │   └── client.py          # Terminal client (native mode with Feishu sync)
│   └── feishu_utils/          # Feishu API helpers
│       ├── __init__.py
│       ├── feishu_utils.py    # Message API functions
│       └── card_builder.py    # Card message builder
├── data/
│   ├── docker_sessions.db     # Docker session mappings (auto-created)
│   └── terminal_sessions.json # Terminal sessions (auto-created)
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
3. Add events:
   - `im.message.receive_v1` - Receive messages
   - `im.chat.member.user_withdrawn_v1` - User leaves group (for session cleanup)
   - `im.chat.disbanded_v1` - Group disbanded (for session cleanup)
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
- No health check endpoint for WebSocket mode