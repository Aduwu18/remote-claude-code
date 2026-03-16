"""
Guest Proxy 客户端

用于从 Host Bridge 向 Guest Proxy 发送请求
支持同步和流式响应
"""
import asyncio
import aiohttp
import logging
import traceback
from typing import Optional, Callable, Awaitable

from src.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    ChatParams,
    ChatResult,
    RequestMethod,
    StreamEvent,
    StreamEventType,
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

    async def cleanup_session(self, endpoint: str, chat_id: str) -> bool:
        """
        通知 Guest Proxy 清理会话

        Args:
            endpoint: Guest Proxy 端点 (http://host:port)
            chat_id: 聊天 ID

        Returns:
            是否成功
        """
        if self._session is None:
            await self.connect()

        request = JsonRpcRequest(
            method=RequestMethod.CLEANUP_SESSION.value,
            params={"chat_id": chat_id},
        )

        try:
            async with self._session.post(
                f"{endpoint}/rpc",
                json=request.to_dict(),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"清理会话请求失败: {response.status} - {text}")
                    return False

                data = await response.json()
                if data.get("error"):
                    logger.warning(f"清理会话错误: {data['error']}")
                    return False

                logger.info(f"会话清理成功: {chat_id[:8]}...")
                return True

        except Exception as e:
            logger.warning(f"清理会话异常: {e}")
            return False

    async def chat_stream(
        self,
        endpoint: str,
        message: str,
        chat_id: str,
        user_open_id: str,
        status_callback: Callable[[str, Optional[str]], Awaitable[None]],
        session_id: str = None,
        require_confirmation: bool = True,
        total_timeout: int = 1800,  # 30分钟总超时
    ) -> ChatResult:
        """
        发送流式聊天请求到 Guest Proxy

        Args:
            endpoint: Guest Proxy 端点 (http://host:port)
            message: 用户消息
            chat_id: 聊天 ID
            user_open_id: 用户 open_id
            status_callback: 状态更新回调函数 (status_text, details)
            session_id: 会话 ID（用于恢复）
            require_confirmation: 是否需要权限确认
            total_timeout: 总超时时间（秒），默认 30 分钟

        Returns:
            ChatResult: 聊天结果
        """
        if self._session is None:
            await self.connect()

        # 构造请求
        request = JsonRpcRequest(
            method=RequestMethod.CHAT_STREAM.value,
            params=ChatParams(
                message=message,
                chat_id=chat_id,
                user_open_id=user_open_id,
                session_id=session_id,
                require_confirmation=require_confirmation,
            ).to_dict(),
        )

        final_content = []
        final_session_id = ""
        tool_calls = []

        try:
            timeout = aiohttp.ClientTimeout(total=total_timeout)
            async with self._session.post(
                f"{endpoint}/stream",
                json=request.to_dict(),
                timeout=timeout,
            ) as response:
                if response.status == 413:
                    logger.error(f"Guest Proxy 请求体过大 (413): 消息长度 {len(message)}")
                    return ChatResult(
                        content="消息过长，请缩短内容后重试",
                        status="failed",
                        session_id="",
                    )
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Guest Proxy 流式请求失败: {response.status} - {text}")
                    return ChatResult(
                        content=f"请求失败: {response.status}",
                        status="failed",
                        session_id="",
                    )

                # 读取 NDJSON 流
                buffer = ""
                async for chunk in response.content:
                    buffer += chunk.decode('utf-8', errors='replace')

                    # 按行分割处理
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            event = StreamEvent.from_json(line)
                        except Exception as e:
                            logger.warning(f"解析流事件失败: {e}, line: {line[:100]}")
                            continue

                        # 处理不同类型的事件
                        if event.event_type == StreamEventType.HEARTBEAT:
                            # 心跳，不需要特殊处理
                            logger.debug("收到心跳")

                        elif event.event_type == StreamEventType.STATUS:
                            # 状态更新
                            status_text = event.data.get("text", "")
                            details = event.data.get("details")
                            if status_callback:
                                await status_callback(status_text, details)

                        elif event.event_type == StreamEventType.TOOL_CALL:
                            # 工具调用
                            tool_name = event.data.get("name", "")
                            tool_input = event.data.get("input", {})
                            tool_calls.append({
                                "name": tool_name,
                                "input": tool_input,
                            })
                            logger.info(f"工具调用: {tool_name}")

                        elif event.event_type == StreamEventType.CONTENT:
                            # 内容片段
                            text = event.data.get("text", "")
                            final_content.append(text)

                        elif event.event_type == StreamEventType.COMPLETE:
                            # 完成
                            final_session_id = event.data.get("session_id", "")
                            complete_content = event.data.get("content", "")
                            if complete_content and not final_content:
                                final_content.append(complete_content)
                            logger.info(f"流式完成: session={final_session_id[:8] if final_session_id else 'N/A'}...")

                        elif event.event_type == StreamEventType.ERROR:
                            # 错误
                            error_msg = event.data.get("message", "未知错误")
                            logger.error(f"流式错误: {error_msg}")
                            return ChatResult(
                                content=f"错误: {error_msg}",
                                status="failed",
                                session_id="",
                            )

                return ChatResult(
                    content="\n".join(final_content) if final_content else "（无响应内容）",
                    status="completed",
                    session_id=final_session_id,
                    tool_calls=tool_calls,
                )

        except asyncio.CancelledError:
            logger.error("流式请求被取消")
            return ChatResult(
                content="请求被取消，可能是处理超时",
                status="failed",
                session_id="",
            )
        except aiohttp.ClientError as e:
            logger.error(f"流式连接失败: {type(e).__name__}: {e}")
            return ChatResult(
                content=f"连接失败: {type(e).__name__}: {e}",
                status="failed",
                session_id="",
            )
        except TimeoutError as e:
            logger.error(f"流式请求超时: {e}")
            return ChatResult(
                content="请求超时，请稍后重试",
                status="failed",
                session_id="",
            )
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "(无详细信息)"
            tb = traceback.format_exc()
            logger.error(f"流式请求异常: {error_type}: {error_msg}\n{tb}")
            return ChatResult(
                content=f"请求异常 [{error_type}]: {error_msg}",
                status="failed",
                session_id="",
            )


# 全局客户端实例
_client: Optional[GuestProxyClient] = None


def get_guest_proxy_client() -> GuestProxyClient:
    """获取全局 Guest Proxy 客户端"""
    global _client
    if _client is None:
        _client = GuestProxyClient()
    return _client