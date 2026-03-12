"""
权限确认管理器

管理 Claude Code 的权限请求与飞书端的用户确认交互
"""
import json
import threading
import time
from typing import Optional, Callable
from dataclasses import dataclass, field


@dataclass
class PermissionRequest:
    """权限请求信息"""
    session_id: str
    chat_id: str
    tool_name: str
    tool_input: dict
    timestamp: float = field(default_factory=time.time)


class PermissionManager:
    """
    权限确认管理器

    负责管理 Claude Code 权限请求与飞书端用户确认的交互：
    - 记录待确认的权限请求
    - 等待用户在飞书端确认
    - 处理用户的确认响应
    """

    def __init__(self):
        # session_id -> PermissionRequest
        self._pending: dict[str, PermissionRequest] = {}
        # session_id -> approved (bool)
        self._responses: dict[str, bool] = {}
        # chat_id -> session_id (每个聊天同时只能有一个待确认请求)
        self._chat_session_map: dict[str, str] = {}
        # 线程锁
        self._lock = threading.Lock()
        # 回调函数：发送权限请求到飞书
        self._send_request_callback: Optional[Callable] = None

    def set_send_request_callback(self, callback: Callable[[str, str, dict], None]):
        """
        设置发送权限请求的回调函数

        Args:
            callback: (chat_id, tool_name, tool_input) -> None
        """
        self._send_request_callback = callback

    def request_permission(
        self,
        session_id: str,
        chat_id: str,
        tool_name: str,
        tool_input: dict
    ) -> bool:
        """
        请求权限确认（阻塞等待）

        Args:
            session_id: Claude Code 会话 ID
            chat_id: 飞书聊天 ID
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            bool: True 表示用户允许，False 表示用户拒绝
        """
        with self._lock:
            # 记录请求
            self._pending[session_id] = PermissionRequest(
                session_id=session_id,
                chat_id=chat_id,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            self._chat_session_map[chat_id] = session_id

        # 发送确认请求到飞书
        if self._send_request_callback:
            self._send_request_callback(chat_id, tool_name, tool_input)

        # 等待用户响应
        while True:
            with self._lock:
                if session_id not in self._pending:
                    # 请求已被处理（可能是超时或用户响应）
                    return self._responses.pop(session_id, False)

                # 检查是否超时（如果设置了超时）
                # timeout = 0 表示无限等待
                # 这里暂时不支持超时，一直等待

            time.sleep(0.3)

    def submit_response(self, chat_id: str, approved: bool) -> bool:
        """
        提交用户的权限确认响应

        Args:
            chat_id: 飞书聊天 ID
            approved: True 表示允许，False 表示拒绝

        Returns:
            bool: True 表示成功处理响应，False 表示没有待处理的请求
        """
        with self._lock:
            # 查找该聊天的待处理请求
            session_id = self._chat_session_map.get(chat_id)
            if not session_id:
                return False

            # 检查是否有对应的待处理请求
            if session_id not in self._pending:
                return False

            # 记录响应
            self._responses[session_id] = approved
            # 清理待处理状态
            del self._pending[session_id]
            del self._chat_session_map[chat_id]

        return True

    def has_pending_request(self, chat_id: str) -> bool:
        """检查指定聊天是否有待处理的权限请求"""
        with self._lock:
            return chat_id in self._chat_session_map

    def get_pending_request(self, chat_id: str) -> Optional[PermissionRequest]:
        """获取指定聊天的待处理权限请求"""
        with self._lock:
            session_id = self._chat_session_map.get(chat_id)
            if session_id:
                return self._pending.get(session_id)
            return None

    def cancel_request(self, session_id: str):
        """取消权限请求"""
        with self._lock:
            if session_id in self._pending:
                req = self._pending[session_id]
                del self._pending[session_id]
                self._chat_session_map.pop(req.chat_id, None)


# 全局单例
permission_manager = PermissionManager()


def format_permission_message(tool_name: str, tool_input: dict) -> str:
    """
    格式化权限确认消息

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数

    Returns:
        格式化的消息文本
    """
    # 简化工具输入的显示
    input_display = json.dumps(tool_input, ensure_ascii=False, indent=2)
    if len(input_display) > 500:
        input_display = input_display[:500] + "\n... (内容过长，已截断)"

    return f"""🔒 权限确认请求

操作: {tool_name}
详情:
{input_display}

请回复:
• "y" 或 "确认" - 允许执行
• "n" 或 "拒绝" - 拒绝执行"""