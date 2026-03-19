"""
Local Session Bridge HTTP 服务

在宿主机上运行的 HTTP 服务，负责：
1. 提供 HTTP 端点供 Terminal CLI 连接
2. 创建和管理本地 Claude session
3. 向 Host Bridge 转发权限请求
4. Terminal 会话管理（自动创建/解散飞书群聊）

端点：
- POST /stream - 流式聊天（NDJSON）
- POST /rpc - JSON-RPC 请求
- GET /health - 健康检查
- GET /status - 状态查询
- POST /terminal/create - 创建 Terminal 会话（自动创建群聊）
- POST /terminal/close - 关闭 Terminal 会话（解散群聊）
- POST /terminal/sync - 同步输出/状态到群聊
"""
import asyncio
import json
import logging
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
from src.terminal_session_manager import TerminalSessionManager, get_terminal_session_manager

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
        terminal_session_manager: TerminalSessionManager = None,
    ):
        """
        初始化服务

        Args:
            port: 监听端口
            host_bridge_url: Host Bridge URL（用于权限请求）
            terminal_session_manager: Terminal 会话管理器（可选）
        """
        self.port = port
        self.host_bridge_url = host_bridge_url

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        # 会话缓存：chat_id -> (session_id, client)
        self._sessions: dict[str, tuple[str, LocalClaudeClient]] = {}

        # Terminal 会话管理器
        self._terminal_manager = terminal_session_manager

    async def start(self):
        """启动服务"""
        # 初始化 Terminal 会话管理器
        if self._terminal_manager is None:
            self._terminal_manager = get_terminal_session_manager()

        # 创建 aiohttp 应用 (10MB max body size for long messages)
        self._app = web.Application(client_max_size=10 * 1024 * 1024)
        self._app.router.add_post("/rpc", self._handle_rpc)
        self._app.router.add_post("/stream", self._handle_stream)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/status", self._handle_status)

        # Terminal 会话管理端点
        self._app.router.add_post("/terminal/create", self._handle_terminal_create)
        self._app.router.add_post("/terminal/close", self._handle_terminal_close)
        self._app.router.add_post("/terminal/sync", self._handle_terminal_sync)

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
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """状态查询"""
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self._sessions),
            "sessions": [
                {
                    "chat_id": chat_id[:8] + "...",
                    "session_id": session_id[:8] + "..." if session_id else "None",
                }
                for chat_id, (session_id, _) in self._sessions.items()
            ],
        })

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """处理 JSON-RPC 请求"""
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
        """处理流式聊天请求（NDJSON 格式）"""
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

            if not chat_id:
                event = StreamEvent.error("缺少 chat_id")
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
        """获取或创建 Claude 客户端"""
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

    async def _handle_terminal_create(self, request: web.Request) -> web.Response:
        """创建 Terminal 会话（自动创建飞书群聊）"""
        try:
            body = await request.json()
            terminal_id = body.get("terminal_id")
            user_open_id = body.get("user_open_id")
            session_id = body.get("session_id")

            if not terminal_id:
                return web.json_response({
                    "success": False,
                    "error": "缺少 terminal_id"
                }, status=400)

            # 创建会话
            session = await self._terminal_manager.create_session(
                terminal_id=terminal_id,
                user_open_id=user_open_id,
                session_id=session_id,
            )

            logger.info(f"创建 Terminal 会话: {terminal_id} -> {session.chat_id}")

            return web.json_response({
                "success": True,
                "terminal_id": terminal_id,
                "chat_id": session.chat_id,
                "message": "会话创建成功"
            })

        except Exception as e:
            logger.error(f"创建 Terminal 会话失败: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def _handle_terminal_close(self, request: web.Request) -> web.Response:
        """关闭 Terminal 会话（解散群聊）"""
        try:
            body = await request.json()
            terminal_id = body.get("terminal_id")
            disband_chat = body.get("disband_chat", True)

            if not terminal_id:
                return web.json_response({
                    "success": False,
                    "error": "缺少 terminal_id"
                }, status=400)

            # 关闭会话
            success = await self._terminal_manager.close_session(
                terminal_id=terminal_id,
                disband_chat=disband_chat,
            )

            if success:
                logger.info(f"关闭 Terminal 会话: {terminal_id}")
                return web.json_response({
                    "success": True,
                    "message": "会话已关闭"
                })
            else:
                return web.json_response({
                    "success": False,
                    "error": "会话不存在"
                }, status=404)

        except Exception as e:
            logger.error(f"关闭 Terminal 会话失败: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def _handle_terminal_sync(self, request: web.Request) -> web.Response:
        """同步输出/状态到群聊"""
        try:
            body = await request.json()
            terminal_id = body.get("terminal_id")
            sync_type = body.get("type", "output")

            if not terminal_id:
                return web.json_response({
                    "success": False,
                    "error": "缺少 terminal_id"
                }, status=400)

            if sync_type == "output":
                content = body.get("content", "")
                success = await self._terminal_manager.sync_output(terminal_id, content)
            elif sync_type == "status":
                status = body.get("status", "idle")
                details = body.get("details", {})
                success = await self._terminal_manager.sync_status(terminal_id, status, details)
            else:
                return web.json_response({
                    "success": False,
                    "error": f"未知的同步类型: {sync_type}"
                }, status=400)

            return web.json_response({"success": success})

        except Exception as e:
            logger.error(f"同步失败: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    def get_terminal_chat_id(self, terminal_id: str) -> Optional[str]:
        """获取 Terminal 的 chat_id"""
        return self._terminal_manager.get_chat_id(terminal_id)


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