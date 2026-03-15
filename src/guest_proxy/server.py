"""
Guest Proxy HTTP 服务

在 Docker 容器内运行的 HTTP 服务，接收来自 Host Bridge 的请求
"""
import asyncio
import json
import logging
import os
from typing import Optional
from aiohttp import web

from src.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    ChatParams,
    ChatResult,
    ResponseStatus,
    ErrorCode,
    RequestMethod,
)
from src.guest_proxy.claude_client import GuestClaudeClient, chat_sync
from src.guest_proxy.watchdog import get_watchdog, init_watchdog, WatchdogEvent
from src.guest_proxy.config import get_guest_config, get_container_name

logger = logging.getLogger(__name__)


class GuestProxyServer:
    """
    Guest Proxy HTTP 服务

    提供以下端点：
    - POST /rpc - JSON-RPC 请求处理
    - GET /health - 健康检查
    """

    def __init__(
        self,
        port: int = None,
        host_bridge_url: str = None,
    ):
        """
        初始化服务

        Args:
            port: 监听端口
            host_bridge_url: Host Bridge URL（用于权限请求）
        """
        config = get_guest_config()
        self.port = port or config["port"]
        self.host_bridge_url = host_bridge_url or config["host_bridge_url"]
        self.container_name = get_container_name()

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # 会话缓存：chat_id -> (session_id, client)
        self._sessions: dict[str, tuple[str, GuestClaudeClient]] = {}

    async def start(self):
        """启动服务"""
        # 初始化 Watchdog
        watchdog = init_watchdog(
            timeout=1800,  # 30 分钟
            check_interval=60,
            on_event=self._on_watchdog_event,
        )
        watchdog.start()

        # 创建 aiohttp 应用
        self._app = web.Application()
        self._app.router.add_post("/rpc", self._handle_rpc)
        self._app.router.add_get("/health", self._handle_health)

        # 启动服务
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()

        logger.info(f"Guest Proxy 已启动，端口: {self.port}")
        logger.info(f"容器名称: {self.container_name}")
        logger.info(f"Host Bridge URL: {self.host_bridge_url}")

    async def stop(self):
        """停止服务"""
        # 停止 Watchdog
        get_watchdog().stop()

        # 清理会话
        for chat_id, (session_id, client) in self._sessions.items():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._sessions.clear()

        # 停止 HTTP 服务
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

        logger.info("Guest Proxy 已停止")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({
            "status": "healthy",
            "container": self.container_name,
            "active_sessions": len(self._sessions),
        })

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """
        处理 JSON-RPC 请求

        Args:
            request: HTTP 请求

        Returns:
            JSON-RPC 响应
        """
        try:
            body = await request.json()
            rpc_request = JsonRpcRequest.from_dict(body)
            logger.info(f"收到 RPC 请求: {rpc_request.method}")

            # 路由请求
            handler = self._get_handler(rpc_request.method)
            if handler is None:
                return web.json_response(
                    JsonRpcResponse.create_error(
                        rpc_request.id,
                        ErrorCode.METHOD_NOT_FOUND,
                        f"Method not found: {rpc_request.method}"
                    ).to_dict()
                )

            result = await handler(rpc_request.params)
            return web.json_response(
                JsonRpcResponse.success(rpc_request.id, result).to_dict()
            )

        except json.JSONDecodeError:
            return web.json_response(
                JsonRpcResponse.create_error("", ErrorCode.PARSE_ERROR, "Parse error").to_dict()
            )
        except Exception as e:
            logger.error(f"RPC 处理异常: {e}")
            return web.json_response(
                JsonRpcResponse.create_error("", ErrorCode.INTERNAL_ERROR, str(e)).to_dict()
            )

    def _get_handler(self, method: str):
        """获取请求处理器"""
        handlers = {
            RequestMethod.CHAT.value: self._handle_chat,
            RequestMethod.HEALTH_CHECK.value: self._handle_health_check,
        }
        return handlers.get(method)

    async def _handle_chat(self, params: dict) -> dict:
        """
        处理聊天请求

        Args:
            params: 请求参数

        Returns:
            响应结果
        """
        try:
            chat_params = ChatParams.from_dict(params)
            chat_id = chat_params.chat_id
            session_id = chat_params.session_id

            logger.info(f"聊天请求: chat={chat_id[:8]}..., session={session_id[:8] if session_id else 'None'}...")

            # 启动任务监控
            task_id = f"chat-{chat_id}-{os.urandom(4).hex()}"
            get_watchdog().start_task(task_id, chat_id)

            try:
                # 获取或创建客户端
                client = await self._get_or_create_client(chat_id, session_id)

                # 发送消息
                response = await client.chat(chat_params.message)

                # 更新会话缓存
                self._sessions[chat_id] = (response.session_id, client)

                # 更新任务
                get_watchdog().update_task(task_id)

                return ChatResult(
                    content=response.content,
                    status=ResponseStatus.COMPLETED,
                    session_id=response.session_id,
                    tool_calls=response.tool_calls,
                ).to_dict()

            finally:
                get_watchdog().end_task(task_id, success=True)

        except Exception as e:
            logger.error(f"聊天处理异常: {e}")
            return {
                "content": f"处理请求失败: {e}",
                "status": ResponseStatus.FAILED.value,
                "session_id": "",
                "tool_calls": [],
            }

    async def _handle_health_check(self, params: dict) -> dict:
        """处理健康检查请求"""
        return {
            "status": "healthy",
            "container": self.container_name,
            "active_sessions": len(self._sessions),
        }

    async def _get_or_create_client(
        self,
        chat_id: str,
        session_id: str = None
    ) -> GuestClaudeClient:
        """
        获取或创建 Claude 客户端

        Args:
            chat_id: 聊天 ID
            session_id: 会话 ID（用于恢复）

        Returns:
            GuestClaudeClient 实例
        """
        # 检查缓存
        if chat_id in self._sessions:
            cached_session_id, client = self._sessions[chat_id]
            # 如果 session_id 匹配，复用客户端
            if session_id and session_id == cached_session_id:
                return client
            # 否则创建新客户端
            await client.disconnect()

        # 创建新客户端
        client = GuestClaudeClient(
            session_id=session_id,
            container_name=self.container_name,
            host_bridge_url=self.host_bridge_url,
        )
        await client.connect()
        return client

    def _on_watchdog_event(self, event: WatchdogEvent, data: dict):
        """
        Watchdog 事件回调

        Args:
            event: 事件类型
            data: 事件数据
        """
        logger.warning(f"Watchdog 事件: {event.value}, 数据: {data}")

        # TODO: 向 Host Bridge 发送异常状态
        # 可以通过 HTTP POST 到 Host Bridge 的 /status 端点


async def main():
    """启动 Guest Proxy"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    server = GuestProxyServer()
    try:
        await server.start()
        # 保持运行
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("收到中断信号")
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())