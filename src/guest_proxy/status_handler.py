"""
状态处理器（Guest Proxy 版本）

通过 HTTP 向 Host Bridge 发送状态更新
"""
import logging
import aiohttp
from typing import Optional

from src.guest_proxy.config import get_guest_config
from src.protocol import RequestMethod, StatusParams

logger = logging.getLogger(__name__)


class StatusHandler:
    """
    状态处理器

    通过 HTTP 向 Host Bridge 发送状态更新
    """

    def __init__(self, host_bridge_url: str = None):
        """
        初始化状态处理器

        Args:
            host_bridge_url: Host Bridge URL
        """
        config = get_guest_config()
        self.host_bridge_url = host_bridge_url or config["host_bridge_url"]
        self._session: Optional[aiohttp.ClientSession] = None

    async def connect(self):
        """创建 HTTP 会话"""
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session:
            await self._session.close()
            self._session = None

    async def send_status(self, chat_id: str, status: str, details: str = None):
        """
        发送状态更新到 Host Bridge

        Args:
            chat_id: 聊天 ID
            status: 状态文本
            details: 详细信息
        """
        if self._session is None:
            await self.connect()

        params = StatusParams(
            chat_id=chat_id,
            status=status,
            details=details,
        )

        try:
            async with self._session.post(
                f"{self.host_bridge_url}/rpc",
                json={
                    "jsonrpc": "2.0",
                    "method": RequestMethod.STATUS_UPDATE.value,
                    "params": params.to_dict(),
                    "id": "status-update",
                }
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"状态更新失败: {response.status} - {text}")
                else:
                    logger.debug(f"状态更新成功: {status}")

        except aiohttp.ClientError as e:
            logger.warning(f"状态更新连接失败: {e}")
        except Exception as e:
            logger.error(f"状态更新异常: {e}")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# 全局单例
_status_handler: Optional[StatusHandler] = None


def get_status_handler() -> StatusHandler:
    """获取全局状态处理器单例"""
    global _status_handler
    if _status_handler is None:
        _status_handler = StatusHandler()
    return _status_handler