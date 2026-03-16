"""
Local Session Bridge HTTP 服务

在宿主机上运行的 HTTP 服务，负责：
1. 提供 HTTP 端点供 Terminal CLI 连接
2. 创建和管理本地 Claude session
3. 向 Host Bridge 转发权限请求

端点：
- POST /stream - 流式聊天（NDJSON）
- POST /rpc - JSON-RPC 请求
- GET /health - 健康检查
- POST /register - 生成注册码
- POST /bind - 绑定注册码到 chat_id
"""
import asyncio
import json
import logging
import os
import random
import string
import time
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
    StreamEvent,
    StreamEventType,
)
from src.local_session_bridge.claude_client import LocalClaudeClient

logger = logging.getLogger(__name__)


class LocalSessionBridge:
    """
    Local Session Bridge HTTP 服务

    提供 Terminal CLI 与 Host Bridge 之间的桥接功能
    """

    def __init__(
        self,
        port: int = 8082,
        host_bridge_url: str = "http://localhost:8080",
    ):
        """
        初始化服务

        Args:
            port: 监听端口
            host_bridge_url: Host Bridge URL（用于权限请求）
        """
        self.port = port
        self.host_bridge_url = host_bridge_url

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # 会话缓存：chat_id -> (session_id, client)
        self._sessions: dict[str, tuple[str, LocalClaudeClient]] = {}

        # 待处理的注册：code -> {chat_id, created_at, session_id}
        self._pending_registers: dict[str, dict] = {}
        self._register_lock = asyncio.Lock()

    async def start(self):
        """启动服务"""
        # 创建 aiohttp 应用 (10MB max body size for long messages)
        self._app = web.Application(client_max_size=10 * 1024 * 1024)
        self._app.router.add_post("/rpc", self._handle_rpc)
        self._app.router.add_post("/stream", self._handle_stream)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/register", self._handle_register)
        self._app.router.add_post("/bind", self._handle_bind)
        self._app.router.add_get("/status", self._handle_status)

        # 启动服务
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()

        logger.info(f"Local Session Bridge 已启动，端口: {self.port}")
        logger.info(f"Host Bridge URL: {self.host_bridge_url}")

    async def stop(self):
        """停止服务"""
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

        logger.info("Local Session Bridge 已停止")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self._sessions),
            "pending_registers": len(self._pending_registers),
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """状态查询"""
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self._sessions),
            "pending_registers": len(self._pending_registers),
            "sessions": [
                {
                    "chat_id": chat_id[:8] + "...",
                    "session_id": session_id[:8] + "..." if session_id else "None",
                }
                for chat_id, (session_id, _) in self._sessions.items()
            ],
        })

    async def _handle_register(self, request: web.Request) -> web.Response:
        """
        生成注册码

        Terminal CLI 调用此端点获取注册码，然后在 Feishu 发送 /bind-terminal <code> 完成绑定
        """
        try:
            body = await request.json()
            session_id = body.get("session_id")

            # 生成 6 位注册码
            code = await self._generate_register_code(session_id)

            logger.info(f"生成注册码: {code}")

            return web.json_response({
                "success": True,
                "code": code,
                "message": f"请在飞书发送: /bind-terminal {code}",
                "expires_in": 300,  # 5 分钟有效
            })

        except Exception as e:
            logger.error(f"生成注册码失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def _handle_bind(self, request: web.Request) -> web.Response:
        """
        绑定注册码到 chat_id

        由 Host Bridge 调用（通过 interceptor）
        """
        try:
            body = await request.json()
            code = body.get("code")
            chat_id = body.get("chat_id")

            if not code or not chat_id:
                return web.json_response({"success": False, "error": "Missing code or chat_id"}, status=400)

            # 验证并绑定
            success = await self._bind_register_code(code, chat_id)

            if success:
                return web.json_response({"success": True})
            else:
                return web.json_response({"success": False, "error": "Invalid or expired code"}, status=400)

        except Exception as e:
            logger.error(f"绑定注册码失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def _generate_register_code(self, session_id: str = None) -> str:
        """
        生成 6 位注册码

        Args:
            session_id: 可选的会话 ID

        Returns:
            注册码
        """
        async with self._register_lock:
            # 生成唯一注册码
            while True:
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                if code not in self._pending_registers:
                    break

            self._pending_registers[code] = {
                "created_at": time.time(),
                "session_id": session_id,
            }

            return code

    async def _bind_register_code(self, code: str, chat_id: str) -> bool:
        """
        绑定注册码到 chat_id

        Args:
            code: 注册码
            chat_id: 飞书聊天 ID

        Returns:
            是否绑定成功
        """
        async with self._register_lock:
            if code not in self._pending_registers:
                return False

            # 检查有效期（5 分钟）
            register_info = self._pending_registers[code]
            if time.time() - register_info["created_at"] > 300:
                del self._pending_registers[code]
                return False

            # 绑定 chat_id
            register_info["chat_id"] = chat_id
            logger.info(f"注册码 {code} 已绑定到 chat_id: {chat_id[:8]}...")

            return True

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
            import traceback
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "(无详细信息)"
            tb = traceback.format_exc()
            logger.error(f"RPC 处理异常: {error_type}: {error_msg}\n{tb}")
            return web.json_response(
                JsonRpcResponse.create_error("", ErrorCode.INTERNAL_ERROR, f"{error_type}: {error_msg}").to_dict()
            )

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        """
        处理流式聊天请求

        使用 NDJSON (Newline-Delimited JSON) 格式返回流式响应
        """
        # 创建流式响应
        response = web.StreamResponse()
        response.content_type = 'application/x-ndjson'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        await response.prepare(request)

        cancelled = False

        try:
            body = await request.json()
            rpc_request = JsonRpcRequest.from_dict(body)
            logger.info(f"收到流式请求: {rpc_request.method}")

            chat_params = ChatParams.from_dict(rpc_request.params)
            chat_id = chat_params.chat_id
            session_id = chat_params.session_id

            # 检查是否已绑定
            if not chat_id:
                # 尝试查找已绑定的 chat_id
                chat_id = await self._find_bound_chat_id()
                if not chat_id:
                    event = StreamEvent.error("未绑定聊天，请先运行 --register")
                    await response.write(event.to_json().encode() + b'\n')
                    return response

            logger.info(f"流式聊天: chat={chat_id[:8]}..., session={session_id[:8] if session_id else 'None'}...")

            # 心跳任务
            heartbeat_interval = 5

            async def send_heartbeat():
                while not cancelled:
                    try:
                        event = StreamEvent.heartbeat()
                        await response.write(event.to_json().encode() + b'\n')
                        await response.drain()
                    except Exception as e:
                        logger.debug(f"心跳发送失败: {e}")
                        break
                    await asyncio.sleep(heartbeat_interval)

            # 获取客户端
            client = await self._get_or_create_client(chat_id, session_id)

            # 启动心跳任务
            heartbeat_task = asyncio.create_task(send_heartbeat())

            try:
                # 流式处理消息
                async for event in client.chat_stream(chat_params.message):
                    if cancelled:
                        break

                    await response.write(event.to_json().encode() + b'\n')
                    await response.drain()

                    # 更新会话缓存
                    if event.event_type == StreamEventType.COMPLETE:
                        new_session_id = event.data.get("session_id")
                        if new_session_id:
                            self._sessions[chat_id] = (new_session_id, client)

            finally:
                cancelled = True
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        except json.JSONDecodeError as e:
            logger.error(f"流式请求 JSON 解析错误: {e}")
            event = StreamEvent.error("无效的 JSON 格式")
            await response.write(event.to_json().encode() + b'\n')
        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "(无详细信息)"
            tb = traceback.format_exc()
            logger.error(f"流式处理异常: {error_type}: {error_msg}\n{tb}")
            try:
                event = StreamEvent.error(f"{error_type}: {error_msg}")
                await response.write(event.to_json().encode() + b'\n')
            except Exception:
                pass

        return response

    def _get_handler(self, method: str):
        """获取请求处理器"""
        handlers = {
            RequestMethod.CHAT.value: self._handle_chat,
            RequestMethod.CHAT_STREAM.value: self._handle_chat,
            RequestMethod.HEALTH_CHECK.value: self._handle_health_check,
        }
        return handlers.get(method)

    async def _handle_chat(self, params: dict) -> dict:
        """处理聊天请求"""
        try:
            chat_params = ChatParams.from_dict(params)
            chat_id = chat_params.chat_id
            session_id = chat_params.session_id

            logger.info(f"聊天请求: chat={chat_id[:8]}..., session={session_id[:8] if session_id else 'None'}...")

            # 获取或创建客户端
            client = await self._get_or_create_client(chat_id, session_id)

            # 发送消息
            response = await client.chat(chat_params.message)

            # 更新会话缓存
            self._sessions[chat_id] = (response.session_id, client)

            return ChatResult(
                content=response.content,
                status=ResponseStatus.COMPLETED,
                session_id=response.session_id,
                tool_calls=response.tool_calls,
            ).to_dict()

        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "(无详细信息)"
            tb = traceback.format_exc()
            logger.error(f"聊天处理异常: {error_type}: {error_msg}\n{tb}")
            return {
                "content": f"处理请求失败 [{error_type}]: {error_msg}",
                "status": ResponseStatus.FAILED.value,
                "session_id": "",
                "tool_calls": [],
            }

    async def _handle_health_check(self, params: dict) -> dict:
        """处理健康检查请求"""
        return {
            "status": "healthy",
            "active_sessions": len(self._sessions),
        }

    async def _get_or_create_client(
        self,
        chat_id: str,
        session_id: str = None
    ) -> LocalClaudeClient:
        """
        获取或创建 Claude 客户端

        Args:
            chat_id: 聊天 ID
            session_id: 会话 ID（用于恢复）

        Returns:
            LocalClaudeClient 实例
        """
        # 检查缓存
        if chat_id in self._sessions:
            cached_session_id, client = self._sessions[chat_id]
            if session_id and session_id == cached_session_id:
                return client
            # 否则创建新客户端
            await client.disconnect()

        # 创建新客户端
        client = LocalClaudeClient(
            chat_id=chat_id,
            session_id=session_id,
            host_bridge_url=self.host_bridge_url,
        )
        await client.connect()
        return client

    async def _find_bound_chat_id(self) -> Optional[str]:
        """
        查找已绑定的 chat_id

        Returns:
            绑定的 chat_id 或 None
        """
        async with self._register_lock:
            for code, info in self._pending_registers.items():
                if "chat_id" in info:
                    return info["chat_id"]
        return None

    def get_bound_chat_id(self, code: str) -> Optional[str]:
        """
        获取注册码绑定的 chat_id（同步版本）

        Args:
            code: 注册码

        Returns:
            chat_id 或 None
        """
        if code in self._pending_registers:
            return self._pending_registers[code].get("chat_id")
        return None


# 全局单例
_local_bridge: Optional[LocalSessionBridge] = None


def get_local_bridge() -> LocalSessionBridge:
    """获取全局 Local Bridge 单例"""
    global _local_bridge
    if _local_bridge is None:
        _local_bridge = LocalSessionBridge()
    return _local_bridge


async def start_local_bridge(
    port: int = 8082,
    host_bridge_url: str = "http://localhost:8080",
) -> LocalSessionBridge:
    """启动 Local Bridge"""
    global _local_bridge
    _local_bridge = LocalSessionBridge(
        port=port,
        host_bridge_url=host_bridge_url,
    )
    await _local_bridge.start()
    return _local_bridge


async def main():
    """启动 Local Session Bridge"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    bridge = LocalSessionBridge()
    try:
        await bridge.start()
        # 保持运行
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("收到中断信号")
    finally:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())