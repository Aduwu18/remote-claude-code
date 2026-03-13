"""
Host Bridge HTTP 服务

宿主机上的 HTTP 服务，负责：
1. 接收来自 Guest Proxy 的注册请求
2. 接收来自 Guest Proxy 的权限请求
3. 接收来自 Guest Proxy 的状态更新
4. 提供 Redis 路由管理接口
"""
import asyncio
import json
import logging
from typing import Optional, Callable
from aiohttp import web

from src.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    PermissionParams,
    PermissionResult,
    RegisterParams,
    StatusParams,
    ErrorCode,
    RequestMethod,
)
from src.redis_client import redis_client

logger = logging.getLogger(__name__)


class HostBridgeServer:
    """
    Host Bridge HTTP 服务

    端点：
    - POST /rpc - JSON-RPC 请求处理
    - GET /health - 健康检查
    - GET /routes - 列出所有路由
    """

    def __init__(
        self,
        port: int = 8080,
        on_permission_request: Callable[[PermissionParams], bool] = None,
        on_status_update: Callable[[StatusParams], None] = None,
    ):
        """
        初始化服务

        Args:
            port: 监听端口
            on_permission_request: 权限请求回调
            on_status_update: 状态更新回调
        """
        self.port = port
        self.on_permission_request = on_permission_request
        self.on_status_update = on_status_update

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # 权限请求映射：chat_id -> asyncio.Future
        self._permission_futures: dict[str, asyncio.Future] = {}

    async def start(self):
        """启动服务"""
        # 创建 aiohttp 应用
        self._app = web.Application()
        self._app.router.add_post("/rpc", self._handle_rpc)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/routes", self._handle_routes)
        self._app.router.add_post("/permission_response", self._handle_permission_response)

        # 启动服务
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()

        logger.info(f"Host Bridge 已启动，端口: {self.port}")

    async def stop(self):
        """停止服务"""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

        logger.info("Host Bridge 已停止")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({
            "status": "healthy",
            "redis_connected": redis_client.is_connected(),
        })

    async def _handle_routes(self, request: web.Request) -> web.Response:
        """列出所有路由"""
        routes = redis_client.list_routes()
        return web.json_response({
            "routes": routes,
            "count": len(routes),
        })

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """处理 JSON-RPC 请求"""
        try:
            body = await request.json()
            rpc_request = JsonRpcRequest.from_dict(body)
            logger.info(f"收到 RPC 请求: {rpc_request.method}")

            handler = self._get_handler(rpc_request.method)
            if handler is None:
                return web.json_response(
                    JsonRpcResponse.error(
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
                JsonRpcResponse.error("", ErrorCode.PARSE_ERROR, "Parse error").to_dict()
            )
        except Exception as e:
            logger.error(f"RPC 处理异常: {e}")
            return web.json_response(
                JsonRpcResponse.error("", ErrorCode.INTERNAL_ERROR, str(e)).to_dict()
            )

    async def _handle_permission_response(self, request: web.Request) -> web.Response:
        """
        处理权限响应（来自飞书）

        用户在飞书回复 "y"/"n" 后，前端会调用此端点
        """
        try:
            body = await request.json()
            chat_id = body.get("chat_id")
            approved = body.get("approved", False)

            if not chat_id:
                return web.json_response({"error": "Missing chat_id"}, status=400)

            # 触发对应的 Future
            future = self._permission_futures.get(chat_id)
            if future and not future.done():
                future.set_result(approved)
                return web.json_response({"success": True})
            else:
                return web.json_response({"error": "No pending permission request"}, status=404)

        except Exception as e:
            logger.error(f"处理权限响应异常: {e}")
            return web.json_response({"error": str(e)}, status=500)

    def _get_handler(self, method: str):
        """获取请求处理器"""
        handlers = {
            RequestMethod.REGISTER.value: self._handle_register,
            RequestMethod.UNREGISTER.value: self._handle_unregister,
            RequestMethod.PERMISSION_REQUEST.value: self._handle_permission_request,
            RequestMethod.STATUS_UPDATE.value: self._handle_status_update,
            RequestMethod.HEARTBEAT.value: self._handle_heartbeat,
        }
        return handlers.get(method)

    async def _handle_register(self, params: dict) -> dict:
        """
        处理注册请求

        Guest Proxy 启动时向 Host 注册
        """
        try:
            reg_params = RegisterParams.from_dict(params)

            # 设置路由
            success = redis_client.set_route(
                chat_id=reg_params.chat_id,
                endpoint=reg_params.endpoint,
            )

            # 设置心跳
            redis_client.set_heartbeat(reg_params.container_name)

            logger.info(
                f"Guest 注册: container={reg_params.container_name}, "
                f"chat={reg_params.chat_id[:8]}..., endpoint={reg_params.endpoint}"
            )

            return {
                "success": success,
                "message": "Registration successful" if success else "Registration failed",
            }

        except Exception as e:
            logger.error(f"注册处理异常: {e}")
            return {"success": False, "message": str(e)}

    async def _handle_unregister(self, params: dict) -> dict:
        """处理注销请求"""
        chat_id = params.get("chat_id")
        if chat_id:
            redis_client.delete_route(chat_id)
            logger.info(f"Guest 注销: chat={chat_id[:8]}...")
            return {"success": True}
        return {"success": False, "message": "Missing chat_id"}

    async def _handle_permission_request(self, params: dict) -> dict:
        """
        处理权限请求

        Guest Proxy 请求权限确认
        """
        try:
            perm_params = PermissionParams.from_dict(params)

            # 调用回调（发送确认消息到飞书）
            if self.on_permission_request:
                approved = await self.on_permission_request(perm_params)
            else:
                # 没有回调，默认允许
                approved = True

            return PermissionResult(approved=approved).to_dict()

        except Exception as e:
            logger.error(f"权限请求处理异常: {e}")
            return PermissionResult(approved=False, reason=str(e)).to_dict()

    async def _handle_status_update(self, params: dict) -> dict:
        """
        处理状态更新

        Guest Proxy 发送状态更新
        """
        try:
            status_params = StatusParams(
                chat_id=params.get("chat_id", ""),
                status=params.get("status", ""),
                details=params.get("details"),
            )

            if self.on_status_update:
                await self.on_status_update(status_params)

            return {"success": True}

        except Exception as e:
            logger.error(f"状态更新处理异常: {e}")
            return {"success": False, "message": str(e)}

    async def _handle_heartbeat(self, params: dict) -> dict:
        """处理心跳"""
        container_name = params.get("container_name")
        if container_name:
            redis_client.set_heartbeat(container_name)
            return {"success": True}
        return {"success": False, "message": "Missing container_name"}


# 全局单例
_host_bridge: Optional[HostBridgeServer] = None


def get_host_bridge() -> HostBridgeServer:
    """获取全局 Host Bridge 单例"""
    global _host_bridge
    if _host_bridge is None:
        _host_bridge = HostBridgeServer()
    return _host_bridge


async def start_host_bridge(
    port: int = 8080,
    on_permission_request: Callable = None,
    on_status_update: Callable = None,
) -> HostBridgeServer:
    """启动 Host Bridge"""
    global _host_bridge
    _host_bridge = HostBridgeServer(
        port=port,
        on_permission_request=on_permission_request,
        on_status_update=on_status_update,
    )
    await _host_bridge.start()
    return _host_bridge