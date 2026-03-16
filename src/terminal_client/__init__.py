"""
Terminal Client 模块

Terminal CLI 客户端，连接到 Local Session Bridge：
1. 支持注册到 Feishu chat
2. 发送消息并接收流式响应
3. 实时显示 Claude 的响应

使用方式：
    python -m src.terminal_client --register
    python -m src.terminal_client
"""

from src.terminal_client.client import TerminalClaudeClient

__all__ = ["TerminalClaudeClient"]