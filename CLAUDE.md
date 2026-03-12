# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Feishu (飞书) bot that integrates Claude Code CLI, enabling users to interact with Claude Code through Feishu chat. The bot supports multiple users with independent conversation contexts and can execute local computer operations.

**Core Features:**
- Multi-user support with independent conversation contexts per chat (private/group)
- Session persistence via SQLite for conversation continuity across restarts
- User authorization via `config.yaml` whitelist
- Permission confirmation flow for sensitive operations (Write, Edit, Bash)
- WebSocket long connection (no public domain required)
- Docker container sessions with isolated contexts per container

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

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
| `src/main_websocket.py` | Main entry point - WebSocket long connection handler |
| `src/main.py` | Alternative HTTP webhook handler (requires public domain) |
| `src/claude_code/conversation.py` | Claude Code SDK wrapper with session management |
| `src/docker_mcp.py` | Docker MCP Server with `create_docker_session` tool |
| `src/context.py` | Request context management using `contextvars` |
| `src/docker_session_manager.py` | Docker container session persistence |
| `src/permission_manager.py` | Permission confirmation state management |
| `src/config.py` | Configuration loading (YAML + env vars) and user authorization |
| `src/feishu_utils/feishu_utils.py` | Feishu API utilities (send/reply messages) |
| `src/data_base_utils/session_store.py` | SQLite session persistence |

### Data Flow

```
Feishu Message → WebSocket → handle_message() → enqueue_message()
                                                        ↓
                                               chat_with_claude()
                                                        ↓
                                         session_store.get_session()
                                                        ↓
                                         claude_code.chat_sync()
                                                        ↓
                                         session_store.save_session()
                                                        ↓
                                         feishu_utils.reply/send_message()
```

### Session Model

```
┌─────────────────────────────────────────────────────┐
│  Feishu Chat ID          Claude Code Session        │
├─────────────────────────────────────────────────────┤
│  User A (private)  ────►  session_abc (isolated)    │
│  User B (private)  ────►  session_xyz (isolated)    │
│  Project Group     ────►  session_123 (shared)      │
│  Test Group        ────►  session_456 (shared)      │
│  Docker Container  ────►  session_789 (container)   │
└─────────────────────────────────────────────────────┘
```

Each `chat_id` (private or group) maps to an independent Claude Code session stored in SQLite.

### Docker Container Session Model

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Docker Container Session Flow                      │
├──────────────────────────────────────────────────────────────────────┤
│  User: "进入 xxx 容器"                                                │
│       ↓                                                               │
│  Claude calls create_docker_session MCP tool                         │
│       ↓                                                               │
│  Tool reads context via environment variables                         │
│       ↓                                                               │
│  Send confirmation request to Feishu                                 │
│       ↓                                                               │
│  User confirms (y/n)                                                 │
│       ↓                                                               │
│  Create group chat "🐳 {container} (Claude助手)"                     │
│       ↓                                                               │
│  Save mapping: docker_chat_id → container_name                       │
│       ↓                                                               │
│  User operates container in new group chat window                    │
└──────────────────────────────────────────────────────────────────────┘
```

Docker sessions create a dedicated group chat (not P2P) with a descriptive name, allowing Claude to execute commands in the specified container.

### Permission Model

1. **User Authorization**: `authorized_users` in `config.yaml` controls who can use the bot
2. **Permission Confirmation**: Sensitive tools (Write, Edit, Bash) require user confirmation via Feishu message
3. **Safe Tools**: Read, Glob, Grep bypass permission confirmation

### Concurrency Model

- **Per-chat serialization**: Messages from the same chat are processed FIFO via thread-safe queues
- **Cross-chat parallelism**: Different chats can be processed concurrently
- Active queues managed in `_active_queues` dict with `_queue_lock`

## Key Patterns

### 1. Claude Code Integration (`conversation.py`)

```python
# Async context manager for session lifecycle
async with ConversationClient(session_id="xxx") as client:
    response = await client.chat("message")
    session_id = client.session_id  # New or existing session

# Synchronous wrapper for non-async contexts
reply, session_id = chat_sync("message", session_id="xxx")
```

**Important**: `chat_sync()` uses `ThreadPoolExecutor` with a new event loop to avoid conflicts with existing async contexts.

### 2. MCP Server for Docker Sessions (`docker_mcp.py`)

Docker container sessions use SDK's standard MCP tool mechanism:

```python
@tool(
    name="create_docker_session",
    description="创建 Docker 容器专属会话...",
    input_schema={"container_name": str}
)
async def create_docker_session_tool(args: dict) -> dict:
    # Read context via contextvars
    chat_id = get_current_chat_id()
    user_open_id = get_current_user_open_id()
    # ... handle session creation
```

**Why MCP Tools instead of Prompt Engineering:**
- **Reliable**: SDK-native tool calling, not regex parsing
- **Type-safe**: Schema validation on inputs
- **Result feedback**: Claude sees tool execution results
- **Standard**: Follows MCP protocol specification

### 3. Request Context (`context.py`)

Context is passed to MCP tools using environment variables (cross-process compatible):

```python
# Set context before calling Claude
set_request_context(chat_id, user_open_id)
try:
    reply = chat_sync(message, ...)
finally:
    clear_request_context()

# MCP tool reads context from environment variables
import os
chat_id = os.environ.get("MCP_CHAT_ID")
user_open_id = os.environ.get("MCP_USER_OPEN_ID")
```

**Why environment variables:** MCP Server runs in a separate process, so `contextvars` cannot cross process boundaries. Environment variables provide a simple cross-process communication mechanism.

### 4. Permission Confirmation Flow

```
Claude calls tool → _check_permission() → handle_permission_request()
                                                    ↓
                                    send_message() to Feishu
                                                    ↓
                                    permission_manager.request_permission() (blocks)
                                                    ↓
                            User replies "y"/"n" → submit_response() → unblocks
```

- `PermissionManager` manages pending requests and responses
- Permission callback is set via `set_permission_request_callback()` at startup
- User confirmation keywords: "y", "yes", "确认", "允许" (approve) / "n", "no", "拒绝", "取消" (deny)

### 5. Session Persistence

- `get_session(chat_id)` → returns `session_id` or `None`
- `save_session(chat_id, session_id)` → upsert mapping

Database location: `data/sessions.db`

### 6. Feishu Message Handling

- Group chat: Use `reply_message(message_id, text)` to reply to specific message
- Private chat: Use `send_message(chat_id, text)` for direct message

### 7. WebSocket vs Webhook

| Mode | File | Requirements |
|------|------|--------------|
| WebSocket | `main_websocket.py` | No public domain (recommended) |
| Webhook | `main.py` | Public domain + ENCRYPT_KEY |

## Claude Code Configuration

The `ConversationClient` configures Claude Code with:

- **Permission Mode**: `acceptEdits` (auto-accept file edits)
- **Allowed Tools**: `["Read", "Write", "Edit", "Bash", "Glob", "Grep"]`
- **System Prompt**: Local computer assistant with full system access

See `SYSTEM_PROMPT` in `conversation.py` for full prompt text.

## Extension Points

### Replacing the Agent Backend

Implement a `chat_sync(message, session_id)` function:

```python
def chat_sync(message: str, session_id: str = None) -> tuple[str, str]:
    """
    Args:
        message: User message
        session_id: Session ID for context continuity

    Returns:
        (reply_content, new_session_id)
    """
    # Your implementation
    return reply, session_id
```

Then update the import in `main_websocket.py`:

```python
# Replace
from src.claude_code import chat_sync

# With
from src.your_agent import chat_sync
```

## Testing

```bash
# Test Claude Code integration
python test/call_claude_code.py
```

## Project Structure

```
├── src/
│   ├── main_websocket.py      # Main entry (WebSocket)
│   ├── main.py                # Alternative (HTTP webhook)
│   ├── config.py              # Configuration & user authorization
│   ├── context.py             # Request context management (contextvars)
│   ├── docker_mcp.py          # Docker MCP Server & tools
│   ├── docker_session_manager.py  # Docker session persistence
│   ├── permission_manager.py  # Permission confirmation state
│   ├── claude_code/
│   │   ├── __init__.py
│   │   └── conversation.py    # Claude Code client
│   ├── feishu_utils/
│   │   ├── __init__.py
│   │   └── feishu_utils.py    # Feishu API helpers
│   └── data_base_utils/
│       ├── __init__.py
│       └── session_store.py   # SQLite session storage
├── data/
│   ├── sessions.db            # Session mappings (auto-created)
│   └── docker_sessions.db     # Docker session mappings (auto-created)
├── test/
│   └── call_claude_code.py    # Integration test
├── config.yaml                # User configuration (gitignored)
├── config.example.yaml        # Configuration template
├── .env                       # Environment variables
├── start.sh / stop.sh         # Process management
└── requirements.txt
```

## Dependencies

- `claude-agent-sdk` - Claude Code Python SDK
- `lark-oapi` - Feishu/Lark official SDK
- `pycryptodome` - AES encryption (webhook mode)
- `python-dotenv` - Environment management
- `PyYAML` - Configuration file parsing

## Feishu App Configuration

1. Create app at [Feishu Open Platform](https://open.feishu.cn/)
2. Event subscription → Select "Use long connection"
3. Add event: `im.message.receive_v1`
4. Permissions: Configure the following `im:message` related permissions:

### Required Permissions

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
- Session cleanup not implemented (sessions accumulate in SQLite)
- No health check endpoint for WebSocket mode