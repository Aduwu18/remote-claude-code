"""
协议拦截器（Protocol Interceptor）

在消息进入正常路由流程之前，拦截管理命令并直接处理。

消息链路：
消息捕获 → 前置扫描(Interceptor) → 命中则直接返回 → 未命中则走 Redis 路由

支持的管理命令：
- /ls - 列出所有容器
- /start <name> - 创建容器会话
- /stop - 退出当前容器会话
- /help - 显示帮助
"""
import docker
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
    ):
        """
        初始化拦截器

        Args:
            on_create_session: 创建会话的回调 (user_id, container_name, chat_id) -> new_chat_id
            on_delete_session: 删除会话的回调 (chat_id) -> bool
            is_authorized: 检查用户是否授权的回调 (user_id) -> bool
        """
        try:
            self.client = docker.from_env()
            logger.info("Docker 客户端初始化成功")
        except Exception as e:
            logger.warning(f"Docker 客户端初始化失败: {e}")
            self.client = None

        self.on_create_session = on_create_session
        self.on_delete_session = on_delete_session
        self.is_authorized = is_authorized

        # 注册管理命令与处理函数的映射
        self.handlers = {
            "/ls": self._list_containers,
            "/list": self._list_containers,
            "/start": self._start_session,
            "/enter": self._start_session,
            "/stop": self._stop_session,
            "/exit": self._stop_session,
            "/help": self._show_help,
            "/?": self._show_help,
        }

        # 存储异步回调的结果
        self._pending_results: dict[str, any] = {}

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
        # 1. 快速判断是否有拦截标识
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

        return "⚠️ 未知管理命令。发送 /help 查看可用命令。"

    def _list_containers(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """列出所有容器"""
        if not self.client:
            return "❌ Docker 服务不可用"

        try:
            containers = self.client.containers.list(all=True)
            if not containers:
                return "📦 没有找到任何容器"

            res = "📦 容器列表：\n\n"
            for c in containers:
                status = "🟢" if c.status == "running" else "🔴"
                res += f"{status} **{c.name}**\n"
                res += f"   状态: {c.status}\n"
                res += f"   ID: {c.short_id}\n\n"

            res += "💡 使用 /start <容器名> 进入容器会话"
            return res

        except Exception as e:
            logger.error(f"列出容器失败: {e}")
            return f"❌ 获取容器列表失败: {e}"

    def _start_session(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """创建容器会话"""
        if not args:
            return "⚠️ 请指定容器名称\n用法: /start <容器名>"

        container_name = args[0]

        # 检查容器是否存在
        if self.client:
            try:
                container = self.client.containers.get(container_name)
                if container.status != "running":
                    return f"❌ 容器 {container_name} 未运行\n状态: {container.status}"
            except docker.errors.NotFound:
                return f"❌ 找不到容器: {container_name}"
            except Exception as e:
                logger.error(f"检查容器状态失败: {e}")

        # 调用回调创建会话（异步回调需要特殊处理）
        if self.on_create_session:
            # 返回一个标记，让调用者知道需要异步处理
            return ("__ASYNC_CREATE_SESSION__", user_id, container_name, chat_id)

        return f"⚠️ 会话创建功能未配置"

    def _stop_session(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """退出容器会话"""
        if self.on_delete_session:
            return ("__ASYNC_DELETE_SESSION__", chat_id)

        return "⚠️ 会话管理功能未配置"

    def _show_help(
        self,
        user_id: str,
        chat_id: str,
        args: list
    ) -> str:
        """显示帮助信息"""
        return """📚 管理命令帮助

**容器管理**
/ls, /list - 列出所有容器
/start <名称> - 进入容器会话
/enter <名称> - 同上
/stop, /exit - 退出当前容器会话

**其他**
/help, /? - 显示此帮助

💡 提示：在容器会话中，直接发送消息即可与 Claude 交互"""


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
) -> ProtocolInterceptor:
    """初始化拦截器"""
    global _interceptor
    _interceptor = ProtocolInterceptor(
        on_create_session=on_create_session,
        on_delete_session=on_delete_session,
        is_authorized=is_authorized,
    )
    return _interceptor