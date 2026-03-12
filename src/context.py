"""
请求上下文管理

使用环境变量传递当前请求的上下文信息到 MCP 工具
（SDK 的 MCP Server 运行在独立进程中，全局变量/ContextVar 都无法跨进程）
"""
import os
import threading
from typing import Optional

# 使用线程锁保护本地变量（用于非 MCP 场景）
_context_lock = threading.Lock()

# 当前请求的上下文（本地使用）
_current_chat_id: Optional[str] = None
_current_user_open_id: Optional[str] = None

# 环境变量名称
ENV_CHAT_ID = "MCP_CHAT_ID"
ENV_USER_OPEN_ID = "MCP_USER_OPEN_ID"


def set_request_context(chat_id: str, user_open_id: str):
    """
    设置当前请求的上下文

    同时设置本地变量和环境变量，确保跨进程可用

    Args:
        chat_id: 飞书聊天 ID
        user_open_id: 用户 open_id
    """
    global _current_chat_id, _current_user_open_id
    with _context_lock:
        _current_chat_id = chat_id
        _current_user_open_id = user_open_id

    # 设置环境变量（用于跨进程传递）
    os.environ[ENV_CHAT_ID] = chat_id
    os.environ[ENV_USER_OPEN_ID] = user_open_id


def clear_request_context():
    """清除当前请求的上下文"""
    global _current_chat_id, _current_user_open_id
    with _context_lock:
        _current_chat_id = None
        _current_user_open_id = None

    # 清除环境变量
    os.environ.pop(ENV_CHAT_ID, None)
    os.environ.pop(ENV_USER_OPEN_ID, None)


def get_current_chat_id() -> Optional[str]:
    """获取当前请求的 chat_id（优先从环境变量获取）"""
    # 优先使用环境变量（跨进程）
    env_val = os.environ.get(ENV_CHAT_ID)
    if env_val:
        return env_val

    # 回退到本地变量
    with _context_lock:
        return _current_chat_id


def get_current_user_open_id() -> Optional[str]:
    """获取当前请求的用户 open_id（优先从环境变量获取）"""
    # 优先使用环境变量（跨进程）
    env_val = os.environ.get(ENV_USER_OPEN_ID)
    if env_val:
        return env_val

    # 回退到本地变量
    with _context_lock:
        return _current_user_open_id