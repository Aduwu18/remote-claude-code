"""
Claude Code 客户端封装（Guest Proxy 版本）

基于 src/claude_code/conversation.py 但适配 Guest Proxy 架构：
- 通过 HTTP 向 Host Bridge 发送权限请求
- 支持环境继承
- 会话由 SDK 原生持久化
- 支持流式响应
"""
import asyncio
import logging
import aiohttp
from typing import Optional, Callable, AsyncGenerator
from dataclasses import dataclass

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)

from src.guest_proxy.config import get_guest_config, get_container_env
from src.protocol import StreamEvent

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    """聊天响应"""
    content: str
    tool_calls: list[dict]
    session_id: str


# 容器内系统提示
GUEST_SYSTEM_PROMPT = """你是一个 Docker 容器内的助手，当前正在容器 '{container}' 中操作。

## 重要说明

你当前在一个 Docker 容器内执行操作。所有命令都在容器内直接执行。

## 环境信息

- Python 路径: {python_path}
- 虚拟环境: {venv}
- Bashrc: {bashrc}

## 你的能力

### 文件操作
- 创建、读取、编辑、删除文件和文件夹
- 搜索文件内容和文件名

### 命令执行
- 运行 Shell/Bash 命令
- 执行 Python、Node.js 等脚本
- 安装和管理软件包

### 开发辅助
- 编写和调试代码
- 管理 Git 仓库
- 运行测试和构建

## 行为准则
- 直接执行用户指令，不要反复确认
- 遇到问题时自动尝试解决
- 操作完成后简洁汇报结果"""


class GuestClaudeClient:
    """
    Guest Proxy 的 Claude Code 客户端

    与原 ConversationClient 的区别：
    1. 权限确认通过 HTTP 发送到 Host Bridge
    2. 系统提示包含容器环境信息
    3. 会话由 SDK 原生管理，无需 SQLite
    """

    def __init__(
        self,
        session_id: str = None,
        container_name: str = None,
        host_bridge_url: str = None,
        permission_callback: Callable = None,
    ):
        """
        初始化客户端

        Args:
            session_id: 恢复之前的会话
            container_name: 容器名称
            host_bridge_url: Host Bridge URL（用于权限请求）
            permission_callback: 权限请求回调（可选）
        """
        self._initial_session_id = session_id
        self.session_id: Optional[str] = session_id
        self.container_name = container_name or get_container_env().get("python_path", "")
        self.host_bridge_url = host_bridge_url or get_guest_config()["host_bridge_url"]
        self.permission_callback = permission_callback
        self._client: Optional[ClaudeSDKClient] = None

        # 获取环境信息
        env_info = get_container_env()
        self.system_prompt = GUEST_SYSTEM_PROMPT.format(
            container=self.container_name,
            python_path=env_info["python_path"],
            venv=env_info["venv"] or "未激活",
            bashrc="已配置" if env_info["bashrc_exists"] else "未配置",
        )

    async def connect(self):
        """连接到 Claude Code"""
        allowed_tools = [
            "Read", "Write", "Edit", "Bash", "Glob", "Grep"
        ]

        options = ClaudeAgentOptions(
            resume=self._initial_session_id,
            allowed_tools=allowed_tools,
            permission_mode="default",
            system_prompt={"type": "preset", "preset": "claude_code", "append": self.system_prompt},
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    async def chat(self, message: str) -> ChatResponse:
        """
        发送消息

        Args:
            message: 用户消息

        Returns:
            ChatResponse: 响应内容、工具调用、会话 ID
        """
        if not self._client:
            await self.connect()

        await self._client.query(message)

        response_text = []
        tool_calls = []

        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        response_text.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_info = {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        tool_calls.append(tool_info)

                        # 权限确认
                        approved = await self._check_permission(block.name, block.input)
                        if not approved:
                            return ChatResponse(
                                content=f"操作被拒绝: {block.name}",
                                tool_calls=tool_calls,
                                session_id=self.session_id or "",
                            )

            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id

        return ChatResponse(
            content="\n".join(response_text),
            tool_calls=tool_calls,
            session_id=self.session_id or "",
        )

    async def chat_stream(self, message: str) -> AsyncGenerator[StreamEvent, None]:
        """
        流式发送消息，yield 状态事件

        Args:
            message: 用户消息

        Yields:
            StreamEvent: 流式事件（状态、工具调用、内容、完成等）
        """
        if not self._client:
            await self.connect()

        # 发送初始状态
        yield StreamEvent.status("正在处理您的请求...")

        await self._client.query(message)

        response_text = []
        tool_calls = []

        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        # 内容片段
                        response_text.append(block.text)
                        yield StreamEvent.content(block.text)

                    elif isinstance(block, ToolUseBlock):
                        tool_info = {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        tool_calls.append(tool_info)

                        # 工具调用状态
                        yield StreamEvent.tool_call(block.name, block.input)
                        yield StreamEvent.status(f"执行工具: {block.name}")

                        # 权限确认
                        approved = await self._check_permission(block.name, block.input)
                        if not approved:
                            yield StreamEvent.error(f"操作被拒绝: {block.name}")
                            return

            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id

        # 完成事件
        yield StreamEvent.complete(
            session_id=self.session_id or "",
            content="\n".join(response_text)
        )

    async def _check_permission(self, tool_name: str, tool_input: dict) -> bool:
        """
        检查权限（通过回调或 HTTP 请求到 Host）

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            bool: True 表示允许，False 表示拒绝
        """
        # 安全工具不需要确认
        safe_tools = ["Read", "Glob", "Grep"]
        if tool_name in safe_tools:
            return True

        # 如果有回调，使用回调
        if self.permission_callback:
            return await self.permission_callback(tool_name, tool_input)

        # 否则向 Host Bridge 发送权限请求
        # TODO: 实现 HTTP 权限请求
        logger.warning(f"权限确认未实现，默认允许: {tool_name}")
        return True

    async def disconnect(self):
        """断开连接"""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


def chat_sync(
    message: str,
    session_id: str = None,
    container_name: str = None,
    host_bridge_url: str = None,
    permission_callback: Callable = None,
) -> tuple[str, str]:
    """
    同步调用 Claude Code（在独立线程中运行）

    Args:
        message: 用户消息
        session_id: 恢复之前的会话
        container_name: 容器名称
        host_bridge_url: Host Bridge URL
        permission_callback: 权限请求回调

    Returns:
        (回复内容, session_id)
    """
    import concurrent.futures

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _chat():
                async with GuestClaudeClient(
                    session_id=session_id,
                    container_name=container_name,
                    host_bridge_url=host_bridge_url,
                    permission_callback=permission_callback,
                ) as client:
                    r = await client.chat(message)
                    return r.content, r.session_id
            return loop.run_until_complete(_chat())
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_run_in_thread)
        return future.result()