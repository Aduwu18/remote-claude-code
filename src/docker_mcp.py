"""
Docker MCP Server

提供 Docker 容器会话创建工具，使用 SDK 的标准 MCP Server 机制
"""
import asyncio
import logging
from typing import Optional, Callable, Any

from claude_agent_sdk import tool, create_sdk_mcp_server, McpSdkServerConfig

from src.context import get_current_chat_id, current_user_open_id

logger = logging.getLogger(__name__)

# Docker 会话创建回调函数
# 由 main_websocket.py 设置，处理确认流程和会话创建
# 回调签名: (chat_id: str, user_open_id: str, container_name: str) -> dict
# 返回: {"success": bool, "message": str, "docker_chat_id": str|None}
_docker_session_handler: Optional[Callable[[str, str, str], dict]] = None


def set_docker_session_handler(handler: Callable[[str, str, str], dict]):
    """
    设置 Docker 会话处理函数

    Args:
        handler: 处理函数，接收 (chat_id, user_open_id, container_name)，
                 返回 {"success": bool, "message": str, "docker_chat_id": str|None}
    """
    global _docker_session_handler
    _docker_session_handler = handler


@tool(
    name="create_docker_session",
    description=(
        "创建 Docker 容器专属会话。"
        "当用户想要进入、连接或操作 Docker 容器时调用此工具。"
        "例如用户说：进入 xxx 容器、连接到 xxx 容器、我要操作 xxx 容器。"
    ),
    input_schema={"container_name": str}
)
async def create_docker_session_tool(args: dict) -> dict:
    """
    创建 Docker 容器会话

    Args:
        args: {"container_name": "容器名称或ID"}

    Returns:
        MCP 工具响应格式: {"content": [{"type": "text", "text": "..."}]}
    """
    container_name = args.get("container_name", "")
    if not container_name:
        return {
            "content": [{"type": "text", "text": "错误：未指定容器名称"}]
        }

    # 从上下文获取当前请求信息
    chat_id = get_current_chat_id()
    user_open_id = current_user_open_id.get()

    if not chat_id:
        return {
            "content": [{"type": "text", "text": "错误：无法获取聊天信息"}]
        }

    if not user_open_id:
        return {
            "content": [{"type": "text", "text": "错误：无法获取用户信息"}]
        }

    if not _docker_session_handler:
        return {
            "content": [{"type": "text", "text": "错误：Docker 会话功能未配置"}]
        }

    logger.info(f"MCP 工具调用: create_docker_session(container={container_name}, chat={chat_id[:8]}...)")

    # 在线程池中执行同步的会话处理（因为处理函数会阻塞等待用户确认）
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _docker_session_handler,
            chat_id,
            user_open_id,
            container_name,
        )

        if result.get("success"):
            message = f"✅ {result.get('message', '容器会话已创建')}"
            if result.get("docker_chat_id"):
                message += f"\n\n请在新的私聊窗口继续操作容器 {container_name}。"
        else:
            message = f"❌ {result.get('message', '创建容器会话失败')}"

        return {
            "content": [{"type": "text", "text": message}]
        }

    except Exception as e:
        logger.error(f"Docker 会话处理异常: {e}")
        return {
            "content": [{"type": "text", "text": f"错误：处理请求时发生异常: {e}"}]
        }


def create_docker_mcp_server() -> McpSdkServerConfig:
    """
    创建 Docker MCP Server

    Returns:
        MCP Server 配置，可用于 ClaudeAgentOptions.mcp_servers
    """
    return create_sdk_mcp_server(
        name="docker",
        version="1.0.0",
        tools=[create_docker_session_tool]
    )


# 全局 Docker MCP Server 实例（延迟创建）
_docker_mcp_server: Optional[McpSdkServerConfig] = None


def get_docker_mcp_server() -> McpSdkServerConfig:
    """
    获取 Docker MCP Server 单例

    Returns:
        MCP Server 配置
    """
    global _docker_mcp_server
    if _docker_mcp_server is None:
        _docker_mcp_server = create_docker_mcp_server()
    return _docker_mcp_server