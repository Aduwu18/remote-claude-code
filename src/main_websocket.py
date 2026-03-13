"""
飞书消息处理主入口

Host-Guest 架构：

1. 初始化 Redis 连接
2. 启动 Host Bridge HTTP 服务
3. WebSocket 接收飞书消息
4. 通过 Redis 查找路由，转发到 Guest Proxy
5. 处理权限确认流程

架构：
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  WebSocket   │───►│ Redis Router │───►│ Guest Proxy  │
│  (飞书消息)   │    │  (路由索引)   │    │ (容器内服务)  │
└──────────────┘    └──────────────┘    └──────────────┘
       │                                       │
       │         ┌──────────────┐             │
       └────────►│  Permission  │◄────────────┘
                 │   Forwarder  │
                 └──────────────┘
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import asyncio
import json
import logging
import threading
from queue import Queue
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# 取消 CLAUDECODE 环境变量
os.environ.pop('CLAUDECODE', None)

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from src.config import (
    is_authorized,
    get_authorized_users,
    get_redis_config,
    get_host_bridge_config,
)
from src.redis_client import init_redis, redis_client
from src.host_bridge import start_host_bridge, GuestProxyClient
from src.host_bridge.server import HostBridgeServer
from src.feishu_utils.feishu_utils import send_message, create_group_chat
from src.permission_manager import format_permission_message
from src.docker_session_manager import docker_session_manager
from src.status_manager import StatusManager
from src.protocol import PermissionParams

APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 消息队列管理
_active_queues: dict[str, Queue] = {}
_queue_lock = threading.Lock()

# 权限确认状态
_pending_permission_chats: set[str] = set()
_pending_permission_lock = threading.Lock()
_pending_permission_futures: dict[str, asyncio.Future] = {}

# Docker 会话确认
_pending_docker_confirm: dict[str, dict] = {}
_pending_docker_lock = threading.Lock()

# 全局 Host Bridge 服务
_host_bridge: Optional[HostBridgeServer] = None


async def handle_permission_request_from_guest(params: PermissionParams) -> bool:
    """
    处理来自 Guest Proxy 的权限请求

    Args:
        params: 权限请求参数

    Returns:
        是否允许
    """
    chat_id = params.chat_id
    tool_name = params.tool_name
    tool_input = params.tool_input

    # 发送确认消息到飞书
    message = format_permission_message(tool_name, tool_input)
    send_message(chat_id, message)

    # 标记等待权限确认
    with _pending_permission_lock:
        _pending_permission_chats.add(chat_id)

    # 创建 Future 等待用户响应
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    _pending_permission_futures[chat_id] = future

    try:
        # 等待用户响应（通过 handle_message 中的响应处理）
        approved = await asyncio.wait_for(future, timeout=300)  # 5 分钟超时
        return approved
    except asyncio.TimeoutError:
        send_message(chat_id, "⏰ 权限确认超时")
        return False
    finally:
        with _pending_permission_lock:
            _pending_permission_chats.discard(chat_id)
            _pending_permission_futures.pop(chat_id, None)


async def handle_status_update(params):
    """处理状态更新"""
    chat_id = params.chat_id
    status = params.status
    details = params.details

    # 发送状态消息到飞书
    status_msg = f"📋 {status}"
    if details:
        status_msg += f": {details}"
    send_message(chat_id, status_msg)


def chat_with_guest_proxy(chat_id: str, message: str, user_open_id: str = None) -> str:
    """
    转发消息到 Guest Proxy

    流程：
    1. 查找 Redis 路由
    2. 转发到 Guest Proxy
    3. 返回结果
    """
    status_mgr = StatusManager(chat_id)
    status_mgr.send_status("⏳ 正在处理您的请求...")

    try:
        # 检查 Redis 路由
        endpoint = redis_client.get_route(chat_id)

        if not endpoint:
            # 没有路由，检查是否是 Docker 会话
            container = docker_session_manager.get_container_for_chat(chat_id)

            if container:
                status_mgr.finalize(
                    f"⚠️ 容器 {container} 的代理服务未就绪。\n"
                    f"请确保容器内的 Guest Proxy 已启动并注册。"
                )
            else:
                status_mgr.finalize(
                    "⚠️ 当前会话未绑定到任何容器。\n"
                    "请使用 Docker 容器会话功能。\n\n"
                    "示例：发送「进入 xxx 容器」创建容器会话。"
                )
            return "无可用路由"

        # 有路由，转发到 Guest Proxy
        logger.info(f"路由到 Guest Proxy: {endpoint}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _send():
                async with GuestProxyClient() as client:
                    result = await client.chat(
                        endpoint=endpoint,
                        message=message,
                        chat_id=chat_id,
                        user_open_id=user_open_id,
                    )
                    return result

            result = loop.run_until_complete(_send())
            reply = result.content

            if result.session_id:
                logger.info(f"会话: {chat_id[:8]}... -> {result.session_id[:8]}...")

            status_mgr.finalize(reply)
            return reply
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"处理请求失败: {e}")
        status_mgr.finalize(f"❌ 执行出错: {e}")
        raise


def _process_chat_queue(chat_id: str, queue: Queue, user_open_id: str = None):
    """处理消息队列"""
    while True:
        with _queue_lock:
            if queue.empty():
                _active_queues.pop(chat_id, None)
                return
            text = queue.get_nowait()

        try:
            reply = chat_with_guest_proxy(chat_id, text, user_open_id)
            logger.info(f"回复: {reply[:100]}...")
        except Exception as e:
            logger.error(f"处理失败 [{chat_id[:8]}...]: {e}")


def enqueue_message(chat_id: str, text: str, user_open_id: str = None):
    """将消息加入队列"""
    with _queue_lock:
        if chat_id in _active_queues:
            _active_queues[chat_id].put(text)
        else:
            queue = Queue()
            queue.put(text)
            _active_queues[chat_id] = queue
            threading.Thread(
                target=_process_chat_queue,
                args=(chat_id, queue, user_open_id),
                daemon=True
            ).start()


def handle_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """处理接收到的消息"""
    try:
        event = data.event
        message = event.message
        sender_id = event.sender.sender_id.open_id

        content = json.loads(message.content)
        text = content.get("text", "")

        # 去掉 @机器人
        if message.mentions:
            for mention in message.mentions:
                text = text.replace(f"@{mention.name}", "").strip()

        if not text:
            return

        chat_id = message.chat_id
        logger.info(f"收到消息 [{sender_id}]: {text}")

        # 检查是否是 Docker 会话
        is_docker = docker_session_manager.is_docker_session(chat_id)

        if is_docker:
            authorized_users = docker_session_manager.get_authorized_users(chat_id)
            if sender_id not in authorized_users:
                logger.warning(f"容器会话未授权用户: {sender_id}")
                send_message(chat_id, "您没有使用此容器会话的权限。")
                return
        else:
            if not is_authorized(sender_id):
                logger.warning(f"未授权用户: {sender_id}")
                send_message(chat_id, "您没有使用此机器人的权限。")
                return

        lower_text = text.lower().strip()

        # 检查 Docker 会话确认响应
        with _pending_docker_lock:
            pending_docker = _pending_docker_confirm.get(chat_id)

        if pending_docker and pending_docker.get("waiting"):
            if lower_text in ["y", "yes", "确认", "允许"]:
                with _pending_docker_lock:
                    _pending_docker_confirm[chat_id]["confirmed"] = True
                    _pending_docker_confirm[chat_id]["waiting"] = False
                return
            elif lower_text in ["n", "no", "拒绝", "取消"]:
                with _pending_docker_lock:
                    _pending_docker_confirm[chat_id]["confirmed"] = False
                    _pending_docker_confirm[chat_id]["waiting"] = False
                return

        # 检查退出容器会话命令
        if is_docker and lower_text in ["/exit", "exit", "退出"]:
            original_chat_id = docker_session_manager.get_original_chat_id(chat_id)
            docker_session_manager.delete_docker_session(chat_id)
            redis_client.delete_route(chat_id)
            send_message(chat_id, "👋 已退出容器会话。")
            if original_chat_id:
                send_message(original_chat_id, "容器会话已结束。")
            return

        # 检查权限确认响应
        with _pending_permission_lock:
            future = _pending_permission_futures.get(chat_id)

        if future and not future.done():
            if lower_text in ["y", "yes", "确认", "允许"]:
                future.set_result(True)
                send_message(chat_id, "✅ 已允许操作")
                return
            elif lower_text in ["n", "no", "拒绝", "取消"]:
                future.set_result(False)
                send_message(chat_id, "❌ 已拒绝操作")
                return

        # 加入队列处理
        enqueue_message(chat_id, text, sender_id)

    except Exception as e:
        logger.error(f"处理消息失败: {e}")
        import traceback
        traceback.print_exc()


async def async_main():
    """异步主函数"""
    global _host_bridge

    # 1. 初始化 Redis
    if not init_redis():
        logger.error("Redis 连接失败，服务无法启动")
        return

    # 2. 启动 Host Bridge HTTP 服务
    bridge_config = get_host_bridge_config()
    _host_bridge = await start_host_bridge(
        port=bridge_config["port"],
        on_permission_request=handle_permission_request_from_guest,
        on_status_update=handle_status_update,
    )

    # 3. 创建 WebSocket 客户端
    client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(handle_message)
            .build(),
        log_level=lark.LogLevel.INFO,
    )

    logger.info("启动飞书长连接...")
    logger.info(f"已配置授权用户数: {len(get_authorized_users())}")
    logger.info(f"Host Bridge 端口: {bridge_config['port']}")

    # 4. 在后台线程运行 WebSocket
    ws_thread = threading.Thread(target=client.start, daemon=True)
    ws_thread.start()

    # 主线程保持运行
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("收到中断信号")
    finally:
        await _host_bridge.stop()
        redis_client.close()


def main():
    """主入口"""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()