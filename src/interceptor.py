"""
协议拦截器（Protocol Interceptor）

在消息进入正常路由流程之前，拦截管理命令并直接处理。

消息链路：
消息捕获 → 前置扫描(Interceptor) → 命中则直接返回 → 未命中则走本地 Claude 处理

支持的管理命令：
- /bind <注册码> - 绑定 Terminal 到当前聊天
- /help - 显示帮助
"""
import logging
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class ProtocolInterceptor:
    """
    协议拦截器

    处理以 / 或 ! 开头的管理命令，不经过 Claude 处理。
    所有方法都是同步的，避免在已有 event loop 的线程中创建新 loop。
    """

    def __init__(
        self,
        on_create_session: Callable[[str, str, str], Awaitable[str]] = None,
        on_delete_session: Callable[[str], Awaitable[bool]] = None,
        is_authorized: Callable[[str], bool] = None,
        on_bind_terminal: Callable[[str, str], Awaitable[bool]] = None,
    ):
        """
        初始化拦截器

        Args:
            on_create_session: 创建会话的回调 (user_id, container_name, chat_id) -> new_chat_id (保留兼容性)
            on_delete_session: 删除会话的回调 (chat_id) -> bool (保留兼容性)
            is_authorized: 检查用户是否授权的回调 (user_id) -> bool
            on_bind_terminal: 绑定 Terminal 的回调 (code, chat_id) -> bool
        """
        self.on_create_session = on_create_session
        self.on_delete_session = on_delete_session
        self.is_authorized = is_authorized
        self.on_bind_terminal = on_bind_terminal

        # 注册管理命令与处理函数的映射
        self.handlers = {
            "/bind": self._bind_terminal,
            "/help": self._show_help,
            "/?": self._show_help,
        }

    def try_intercept(
        self,
        user_id: str,
        chat_id: str,
        message: str
    ) -> Optional[str]:
        """
        尝试拦截消息（同步版本）

        Args:
            user_id: 用户 open_id
            chat_id: 当前会话 chat_id
            message: 消息内容

        Returns:
            如果拦截成功，返回响应内容；否则返回 None
        """
        # 1. 检查命令前缀格式
        message = message.strip()
        if not message.startswith(("/", "!")):
            return None

        # 2. 解析命令和参数
        parts = message.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        # 3. 匹配处理器
        handler = self.handlers.get(cmd)
        if handler:
            return handler(user_id, chat_id, args)

        return "⚠️ 未知命令。发送 /help 查看可用命令。"

    def _bind_terminal(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """绑定 Terminal 到当前聊天"""
        if not args:
            return "⚠️ 请提供注册码\n用法: /bind <注册码>"

        code = args[0]

        if self.on_bind_terminal:
            return ("__ASYNC_BIND_TERMINAL__", code, chat_id)

        return "⚠️ Terminal 绑定功能未配置"

    def _show_help(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """显示帮助信息"""
        return """📚 管理命令帮助

**命令格式**
/bind <注册码> - 绑定 Terminal 到当前聊天
/help, /? - 显示此帮助

💡 提示：直接发送消息即可与 Claude 交互"""


# 全局单例
_interceptor: Optional[ProtocolInterceptor] = None


def get_interceptor() -> ProtocolInterceptor:
    """获取全局拦截器单例"""
    global _interceptor
    if _interceptor is None:
        _interceptor = ProtocolInterceptor()
    return _interceptor


def init_interceptor(
    on_create_session: Callable = None,
    on_delete_session: Callable = None,
    is_authorized: Callable = None,
    on_bind_terminal: Callable = None,
) -> ProtocolInterceptor:
    """初始化拦截器"""
    global _interceptor
    _interceptor = ProtocolInterceptor(
        on_create_session=on_create_session,
        on_delete_session=on_delete_session,
        is_authorized=is_authorized,
        on_bind_terminal=on_bind_terminal,
    )
    return _interceptor