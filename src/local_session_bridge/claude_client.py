"""
本地 Claude 客户端

复用 GuestClaudeClient 的逻辑，但修改权限请求：
- 向 Host Bridge 发送 HTTP 请求
- Host Bridge 通过 Feishu 卡片弹出权限确认
- 用户在 Feishu 上点击确认/拒绝
- 结果返回给本地客户端

权限流程：
1. Terminal 发送消息
2. Claude SDK 触发敏感工具
3. LocalClaudeClient._check_permission() 发送 HTTP 到 Host Bridge
4. Host Bridge 发送 Feishu 卡片
5. 用户在 Feishu 点击按钮
6. Host Bridge 收到响应并返回给 LocalClaudeClient
7. 继续或中断操作
"""
import asyncio
import logging
import aiohttp
from typing import Optional, AsyncGenerator
from dataclasses import dataclass

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)

from src.protocol import StreamEvent, PermissionParams

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    """聊天响应"""
    content: str
    tool_calls: list[dict]
    session_id: str


# 本地系统提示
LOCAL_SYSTEM_PROMPT = """你是一个本地开发助手，当前在宿主机环境中操作。

## 你的能力

### 文件操作
- 创建、读取、编辑、删除文件和文件夹
- 搜索文件内容和文件名

### 命令执行
- 运行 Shell/Bash 命令
- 执行 Python、Node.js 等脚本
- 管理本地开发环境

### 开发辅助
- 编写和调试代码
- 管理 Git 仓库
- 运行测试和构建

## 行为准则
- 直接执行用户指令，不要反复确认
- 遇到问题时自动尝试解决
- 操作完成后简洁汇报结果"""


class LocalClaudeClient:
    """
    本地 Claude Code 客户端

    与 GuestClaudeClient 的区别：
    1. 运行在宿主机而非容器
    2. 权限确认通过 HTTP 发送到 Host Bridge
    3. Host Bridge 通过 Feishu 弹出权限确认卡片
    """

    def __init__(
        self,
        chat_id: str,
        session_id: str = None,
        host_bridge_url: str = "http://localhost:8080",
    ):
        """
        初始化客户端

        Args:
            chat_id: 绑定的飞书聊天 ID
            session_id: 恢复之前的会话
            host_bridge_url: Host Bridge URL（用于权限请求）
        """
        self.chat_id = chat_id
        self._initial_session_id = session_id
        self.session_id: Optional[str] = session_id
        self.host_bridge_url = host_bridge_url
        self._client: Optional[ClaudeSDKClient] = None

    async def connect(self):
        """连接到 Claude Code"""
        allowed_tools = [
            "Read", "Write", "Edit", "Bash", "Glob", "Grep"
        ]

        options = ClaudeAgentOptions(
            resume=self._initial_session_id,
            allowed_tools=allowed_tools,
            permission_mode="default",
            system_prompt={"type": "preset", "preset": "claude_code", "append": LOCAL_SYSTEM_PROMPT},
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
        检查权限（通过 HTTP 请求到 Host Bridge）

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

        # 发送权限请求到 Host Bridge
        logger.info(f"请求权限确认: {tool_name}")
        return await self._request_permission_from_host(tool_name, tool_input)

    async def _request_permission_from_host(self, tool_name: str, tool_input: dict) -> bool:
        """
        向 Host Bridge 发送权限请求

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            bool: True 表示允许，False 表示拒绝
        """
        try:
            async with aiohttp.ClientSession() as session:
                # 构建权限请求
                import uuid
                request_id = str(uuid.uuid4())

                payload = {
                    "jsonrpc": "2.0",
                    "method": "permission",
                    "params": {
                        "session_id": self.session_id or "",
                        "chat_id": self.chat_id,
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    },
                    "id": request_id,
                }

                async with session.post(
                    f"{self.host_bridge_url}/rpc",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300)  # 5 分钟超时
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"权限请求失败: HTTP {resp.status}")
                        return False

                    result = await resp.json()

                    # 检查是否有错误
                    if "error" in result:
                        logger.error(f"权限请求错误: {result['error']}")
                        return False

                    # 获取结果
                    approved = result.get("result", {}).get("approved", False)
                    logger.info(f"权限确认结果: {approved}")
                    return approved

        except asyncio.TimeoutError:
            logger.error("权限请求超时")
            return False
        except Exception as e:
            logger.error(f"权限请求异常: {e}")
            return False

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