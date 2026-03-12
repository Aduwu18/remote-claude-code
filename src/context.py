"""
请求上下文管理

使用 contextvars 传递当前请求的上下文信息到 MCP 工具
"""
from contextvars import ContextVar
from typing import Optional

# 当前请求的 chat_id
current_chat_id: ContextVar[Optional[str]] = ContextVar('current_chat_id', default=None)

# 当前请求的用户 open_id
current_user_open_id: ContextVar[Optional[str]] = ContextVar('current_user_open_id', default=None)


def set_request_context(chat_id: str, user_open_id: str):
    """
    设置当前请求的上下文

    Args:
        chat_id: 飞书聊天 ID
        user_open_id: 用户 open_id
    """
    current_chat_id.set(chat_id)
    current_user_open_id.set(user_open_id)


def clear_request_context():
    """清除当前请求的上下文"""
    current_chat_id.set(None)
    current_user_open_id.set(None)


def get_current_chat_id() -> Optional[str]:
    """获取当前请求的 chat_id"""
    return current_chat_id.get()


def get_current_user_open_id() -> Optional[str]:
    """获取当前请求的用户 open_id"""
    return current_user_open_id.get()