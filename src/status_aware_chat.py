"""
状态感知聊天封装

监听 SDK 消息流，实时更新状态消息
"""
import logging
from typing import Optional, TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
    TaskStartedMessage,
    TaskProgressMessage,
    TaskNotificationMessage,
)

if TYPE_CHECKING:
    from src.status_manager import StatusManager

logger = logging.getLogger(__name__)


class StatusAwareChat:
    """
    封装消息处理逻辑，监听 SDK 消息流更新状态

    根据消息类型更新状态消息：
    - TaskStartedMessage: 任务开始
    - TaskProgressMessage: 任务进度（含 last_tool_name）
    - AssistantMessage.ToolUseBlock: 工具调用详情
    - ResultMessage: 任务完成
    """

    def __init__(self, status_manager: "StatusManager"):
        """
        初始化状态感知聊天

        Args:
            status_manager: 状态管理器实例
        """
        self.status_manager = status_manager

    def on_message(self, msg) -> None:
        """
        处理 SDK 消息，更新状态

        Args:
            msg: SDK 消息对象
        """
        try:
            # 1. 任务开始
            if isinstance(msg, TaskStartedMessage):
                desc = msg.description
                if len(desc) > 50:
                    desc = desc[:50] + "..."
                self.status_manager.update_status(f"⏳ 任务开始: {desc}")
                logger.debug(f"任务开始: {desc}")

            # 2. 任务进度
            elif isinstance(msg, TaskProgressMessage):
                tool_info = ""
                if hasattr(msg, "last_tool_name") and msg.last_tool_name:
                    tool_info = f" (正在执行: {msg.last_tool_name})"
                self.status_manager.update_status(f"🔄 处理中{tool_info}")

            # 3. 任务完成/失败
            elif isinstance(msg, TaskNotificationMessage):
                if hasattr(msg, "status"):
                    if msg.status == "completed":
                        self.status_manager.update_status("✅ 任务完成，正在整理结果...")
                    elif msg.status == "failed":
                        error_info = ""
                        if hasattr(msg, "summary") and msg.summary:
                            error_info = f": {msg.summary[:50]}"
                        self.status_manager.update_status(f"❌ 任务失败{error_info}")

            # 4. 助手消息 - 处理工具调用
            elif isinstance(msg, AssistantMessage):
                self._on_assistant_message(msg)

            # 5. 结果消息 - 任务完成
            elif isinstance(msg, ResultMessage):
                logger.debug(f"收到结果消息，session: {msg.session_id[:8] if msg.session_id else 'None'}...")

        except Exception as e:
            logger.error(f"处理状态消息失败: {e}")

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        """
        处理助手消息，特别关注工具调用

        Args:
            msg: 助手消息
        """
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                self._on_tool_use(block)

    def _on_tool_use(self, block: ToolUseBlock) -> None:
        """
        工具调用时更新状态

        Args:
            block: 工具使用块
        """
        name = block.name
        inp = block.input if block.input else {}

        # 根据工具类型生成友好的状态消息
        status = self._format_tool_status(name, inp)
        self.status_manager.update_status(status)
        logger.debug(f"工具调用: {name}")

    def _format_tool_status(self, name: str, inp: dict) -> str:
        """
        格式化工具状态消息

        Args:
            name: 工具名称
            inp: 工具输入参数

        Returns:
            格式化的状态消息
        """
        # 工具状态映射
        status_formatters = {
            "Read": lambda: f"📖 正在读取: {self._short_path(inp.get('file_path', '文件'))}",
            "Write": lambda: f"✏️ 正在写入: {self._short_path(inp.get('file_path', '文件'))}",
            "Edit": lambda: f"📝 正在编辑: {self._short_path(inp.get('file_path', '文件'))}",
            "Bash": lambda: f"⚡ 正在执行: {self._short_cmd(inp.get('command', '命令'))}",
            "Glob": lambda: f"🔍 正在搜索文件: {inp.get('pattern', '模式')[:30]}",
            "Grep": lambda: f"🔍 正在搜索内容: {inp.get('pattern', '模式')[:30]}",
        }

        formatter = status_formatters.get(name)
        if formatter:
            try:
                return formatter()
            except Exception:
                pass

        # 默认状态
        return f"🔧 正在执行: {name}"

    @staticmethod
    def _short_path(path: str) -> str:
        """
        截断文件路径，只保留文件名

        Args:
            path: 完整文件路径

        Returns:
            简短的文件名
        """
        if not path:
            return "文件"
        parts = path.split("/")
        return parts[-1] if parts else path

    @staticmethod
    def _short_cmd(cmd: str) -> str:
        """
        截断命令，避免过长

        Args:
            cmd: 完整命令

        Returns:
            简短的命令
        """
        if not cmd:
            return "命令"
        if len(cmd) > 40:
            return cmd[:40] + "..."
        return cmd