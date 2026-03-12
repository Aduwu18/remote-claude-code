"""
Claude Code 连续对话客户端

支持权限确认 hook，将 Claude 的操作请求发送到飞书端让用户确认
"""
import asyncio
from typing import Optional, Callable
from dataclasses import dataclass

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict]
    session_id: str


SYSTEM_PROMPT = """你是一个强大的本地电脑助手，拥有完整的系统操作权限。

## 你的能力

### 文件操作
- 创建、读取、编辑、删除文件和文件夹
- 搜索文件内容和文件名
- 整理和移动文件

### 脚本和命令
- 运行 Shell/Bash/PowerShell 命令
- 执行 Python、Node.js 等脚本
- 安装和管理软件包

### 应用程序控制
- 打开和关闭应用程序
- 操作浏览器（打开网页、搜索）
- 控制系统设置

### 开发辅助
- 编写和调试代码
- 管理 Git 仓库
- 运行测试和构建

## 行为准则
- 直接执行用户指令，不要反复确认
- 遇到问题时自动尝试解决
- 操作完成后简洁汇报结果
- 如果指令不明确，做出合理推断并执行"""


# 全局权限确认回调函数
# 由 main_websocket.py 设置，用于在权限请求时发送消息到飞书
_permission_request_callback: Optional[Callable[[str, str, str, dict], bool]] = None


def set_permission_request_callback(callback: Callable[[str, str, str, dict], bool]):
    """
    设置权限确认回调函数

    Args:
        callback: (chat_id, session_id, tool_name, tool_input) -> approved (bool)
                  阻塞函数，等待用户确认后返回结果
    """
    global _permission_request_callback
    _permission_request_callback = callback


# session_id -> chat_id 映射
# 用于权限确认时知道要发送到哪个聊天
_session_chat_map: dict[str, str] = {}


def register_session_chat(session_id: str, chat_id: str):
    """注册 session_id 和 chat_id 的映射"""
    _session_chat_map[session_id] = chat_id


def unregister_session_chat(session_id: str):
    """取消注册"""
    _session_chat_map.pop(session_id, None)


def get_chat_id_for_session(session_id: str) -> Optional[str]:
    """获取 session_id 对应的 chat_id"""
    return _session_chat_map.get(session_id)


class ConversationClient:
    """
    连续对话客户端

    Example:
        async with ConversationClient() as client:
            r1 = await client.chat("创建 hello.py")
            r2 = await client.chat("读取刚才的文件")
            print(client.session_id)

        # 恢复对话
        async with ConversationClient(session_id="xxx") as client:
            r = await client.chat("继续")
    """

    def __init__(
        self,
        session_id: str = None,
        chat_id: str = None,
        allowed_tools: list[str] = None,
        permission_mode: str = "default",
        system_prompt: str = None,
        require_confirmation: bool = True,
    ):
        self._initial_session_id = session_id
        self.session_id: Optional[str] = session_id
        self.chat_id = chat_id
        self.allowed_tools = allowed_tools or ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
        self.permission_mode = permission_mode
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.require_confirmation = require_confirmation
        self._client: Optional[ClaudeSDKClient] = None

    async def connect(self):
        options = ClaudeAgentOptions(
            resume=self._initial_session_id,
            allowed_tools=self.allowed_tools,
            permission_mode=self.permission_mode,
            system_prompt={"type": "preset", "preset": "claude_code", "append": self.system_prompt},
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    async def chat(self, message: str) -> ChatResponse:
        """发送消息"""
        if not self._client:
            await self.connect()

        # 注册 session 和 chat 的映射
        if self.chat_id and self.session_id:
            register_session_chat(self.session_id, self.chat_id)

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

                        # 如果需要权限确认，检查是否应该等待用户确认
                        if self.require_confirmation and _permission_request_callback:
                            approved = await self._check_permission(block.name, block.input)
                            if not approved:
                                # 用户拒绝，返回拒绝消息
                                return ChatResponse(
                                    content=f"操作被拒绝: {block.name}",
                                    tool_calls=tool_calls,
                                    session_id=self.session_id or "",
                                )

            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id
                # 更新映射
                if self.chat_id:
                    register_session_chat(self.session_id, self.chat_id)

        return ChatResponse(
            content="\n".join(response_text),
            tool_calls=tool_calls,
            session_id=self.session_id or "",
        )

    async def _check_permission(self, tool_name: str, tool_input: dict) -> bool:
        """
        检查权限，等待用户确认

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            bool: True 表示用户允许，False 表示用户拒绝
        """
        if not _permission_request_callback:
            return True  # 没有回调，默认允许

        if not self.chat_id or not self.session_id:
            return True  # 没有关联聊天，默认允许

        # 某些工具不需要确认
        safe_tools = ["Read", "Glob", "Grep"]
        if tool_name in safe_tools:
            return True

        # 调用回调进行确认（这是同步函数，在线程中运行）
        loop = asyncio.get_event_loop()
        try:
            approved = await loop.run_in_executor(
                None,
                _permission_request_callback,
                self.chat_id,
                self.session_id,
                tool_name,
                tool_input,
            )
            return approved
        except Exception:
            return False

    async def disconnect(self):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass  # SDK 的 anyio/asyncio 兼容性问题，忽略
            self._client = None
        # 清理映射
        if self.session_id:
            unregister_session_chat(self.session_id)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


def chat_sync(
    message: str,
    session_id: str = None,
    chat_id: str = None,
    require_confirmation: bool = True,
) -> tuple[str, str]:
    """
    同步调用 Claude Code（在独立线程中运行，避免事件循环冲突）

    Args:
        message: 用户消息
        session_id: 恢复之前的会话（可选）
        chat_id: 飞书聊天 ID（用于权限确认）
        require_confirmation: 是否需要权限确认

    Returns:
        (回复内容, session_id)

    Example:
        reply, session_id = chat_sync("你好")
        print(reply)
    """
    import concurrent.futures

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _chat():
                async with ConversationClient(
                    session_id=session_id,
                    chat_id=chat_id,
                    require_confirmation=require_confirmation,
                ) as client:
                    r = await client.chat(message)
                    return r.content, r.session_id
            return loop.run_until_complete(_chat())
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_run_in_thread)
        return future.result()