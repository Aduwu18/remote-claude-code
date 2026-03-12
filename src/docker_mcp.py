"""
Docker MCP Server

提供 Docker 容器会话创建工具，使用 SDK 的标准 MCP Server 机制
"""
import asyncio
import logging
import os
from typing import Optional, Callable, Any

from claude_agent_sdk import tool, create_sdk_mcp_server, McpSdkServerConfig

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
        "【必须调用】创建 Docker 容器专属会话。"
        "**当用户提到 Docker 容器相关操作时，必须调用此工具，而不是使用 Bash 的 docker exec。**"
        "触发场景：用户说'进入容器'、'连接容器'、'在容器里操作'、'容器名称'等。"
        "此工具会创建新的私聊窗口，用户可以在其中安全地操作容器。"
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
    logger.info(f"=== MCP 工具被调用 === args: {args}")

    container_name = args.get("container_name", "")
    if not container_name:
        logger.warning("MCP 工具错误：未指定容器名称")
        return {
            "content": [{"type": "text", "text": "错误：未指定容器名称"}]
        }

    # 从环境变量获取上下文（SDK 通过 env 参数传递）
    chat_id = os.environ.get("MCP_CHAT_ID")
    user_open_id = os.environ.get("MCP_USER_OPEN_ID")

    logger.info(f"MCP 工具上下文: chat_id={chat_id}, user_open_id={user_open_id}")

    # 本地 CLI 模式：没有飞书上下文时，直接提供容器操作指引
    if not chat_id or not user_open_id:
        logger.info("MCP 工具：本地 CLI 模式，无飞书上下文")
        import subprocess
        try:
            # 检查容器是否存在且运行
            result = subprocess.run(
                ["docker", "inspect", "--format={{.State.Running}}", container_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return {
                    "content": [{"type": "text", "text": f"错误：容器 '{container_name}' 不存在"}]
                }
            if result.stdout.strip() != "true":
                return {
                    "content": [{"type": "text", "text": f"错误：容器 '{container_name}' 未运行"}]
                }

            # 返回本地模式提示
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ 容器 '{container_name}' 已就绪（本地 CLI 模式）\n\n"
                        f"容器正在运行，你可以直接让我在容器中执行命令。\n"
                        f"例如：\n"
                        f"- \"查看容器内的 /app 目录\"\n"
                        f"- \"在容器里运行 ls -la\"\n"
                        f"- \"检查容器的环境变量\"\n\n"
                        f"我会使用 docker exec 帮你执行命令。"
                    )
                }]
            }
        except subprocess.TimeoutExpired:
            return {
                "content": [{"type": "text", "text": "错误：检查容器状态超时"}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"错误：检查容器失败: {e}"}]
            }

    if not _docker_session_handler:
        logger.error("MCP 工具错误：Docker 会话功能未配置 (_docker_session_handler is None)")
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