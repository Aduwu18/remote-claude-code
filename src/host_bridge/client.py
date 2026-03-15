"""
Guest Proxy 客户端

用于从 Host Bridge 向 Guest Proxy 发送请求
"""
import asyncio
import aiohttp
import logging
import traceback
from typing import Optional

from src.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    ChatParams,
    ChatResult,
    RequestMethod,
)

logger = logging.getLogger(__name__)


class GuestProxyClient:
    """
    Guest Proxy HTTP 客户端

    用于从 Host Bridge 向 Guest Proxy 发送请求
    """

    def __init__(self, timeout: int = 300):
        """
        初始化客户端

        Args:
            timeout: 请求超时时间（秒）
        """
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self):
        """创建 HTTP 会话"""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session:
            await self._session.close()
            self._session = None

    async def chat(
        self,
        endpoint: str,
        message: str,
        chat_id: str,
        user_open_id: str,
        session_id: str = None,
        require_confirmation: bool = True,
    ) -> ChatResult:
        """
        发送聊天请求到 Guest Proxy

        Args:
            endpoint: Guest Proxy 端点 (http://host:port)
            message: 用户消息
            chat_id: 聊天 ID
            user_open_id: 用户 open_id
            session_id: 会话 ID（用于恢复）
            require_confirmation: 是否需要权限确认

        Returns:
            ChatResult: 聊天结果
        """
        if self._session is None:
            await self.connect()

        # 构造请求
        request = JsonRpcRequest(
            method=RequestMethod.CHAT.value,
            params=ChatParams(
                message=message,
                chat_id=chat_id,
                user_open_id=user_open_id,
                session_id=session_id,
                require_confirmation=require_confirmation,
            ).to_dict(),
        )

        try:
            async with self._session.post(
                f"{endpoint}/rpc",
                json=request.to_dict(),
            ) as response:
                if response.status == 413:
                    # Request body too large
                    logger.error(f"Guest Proxy 请求体过大 (413): 消息长度 {len(message)}")
                    return ChatResult(
                        content="消息过长，请缩短内容后重试",
                        status="failed",
                        session_id="",
                    )
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Guest Proxy 请求失败: {response.status} - {text}")
                    return ChatResult(
                        content=f"请求失败: {response.status}",
                        status="failed",
                        session_id="",
                    )

                data = await response.json()
                rpc_response = JsonRpcResponse(
                    id=data.get("id", ""),
                    result=data.get("result"),
                    error=data.get("error"),
                )

                if rpc_response.error:
                    error_msg = rpc_response.error.get("message", "Unknown error")
                    return ChatResult(
                        content=f"错误: {error_msg}",
                        status="failed",
                        session_id="",
                    )

                result = rpc_response.result or {}
                return ChatResult(
                    content=result.get("content", ""),
                    status=result.get("status", "completed"),
                    session_id=result.get("session_id", ""),
                    tool_calls=result.get("tool_calls", []),
                )

        except asyncio.CancelledError:
            logger.error("Guest Proxy 请求被取消")
            return ChatResult(
                content="请求被取消，可能是处理超时",
                status="failed",
                session_id="",
            )
        except aiohttp.ClientError as e:
            logger.error(f"Guest Proxy 连接失败: {type(e).__name__}: {e}")
            return ChatResult(
                content=f"连接失败: {type(e).__name__}: {e}",
                status="failed",
                session_id="",
            )
        except TimeoutError as e:
            logger.error(f"Guest Proxy 请求超时: {e}")
            return ChatResult(
                content="请求超时，请稍后重试",
                status="failed",
                session_id="",
            )
        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "(无详细信息)"
            tb = traceback.format_exc()
            logger.error(f"Guest Proxy 请求异常: {error_type}: {error_msg}\n{tb}")
            return ChatResult(
                content=f"请求异常 [{error_type}]: {error_msg}",
                status="failed",
                session_id="",
            )

    async def health_check(self, endpoint: str) -> bool:
        """
        健康检查

        Args:
            endpoint: Guest Proxy 端点

        Returns:
            是否健康
        """
        if self._session is None:
            await self.connect()

        try:
            async with self._session.get(f"{endpoint}/health") as response:
                return response.status == 200
        except Exception:
            return False


# 全局客户端实例
_client: Optional[GuestProxyClient] = None


def get_guest_proxy_client() -> GuestProxyClient:
    """获取全局 Guest Proxy 客户端"""
    global _client
    if _client is None:
        _client = GuestProxyClient()
    return _client