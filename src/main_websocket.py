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
import signal
import atexit
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
from src.interceptor import init_interceptor, get_interceptor

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

# 全局 shutdown 事件
_shutdown_event = asyncio.Event()


def _signal_handler():
    """信号处理器"""
    logger.info("收到终止信号，正在关闭...")
    _shutdown_event.set()


def _cleanup_on_exit():
    """进程退出时的清理函数（atexit 备份机制）"""
    global _host_bridge
    if _host_bridge:
        try:
            # 同步版本的清理
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_host_bridge.stop())
            finally:
                loop.close()
            logger.info("atexit 清理完成")
        except Exception as e:
            logger.error(f"atexit 清理失败: {e}")


async def create_docker_session_handler(
    user_id: str,
    container_name: str,
    original_chat_id: str
) -> str:
    """
    创建 Docker 容器会话的回调函数

    Args:
        user_id: 用户 open_id
        container_name: 容器名称
        original_chat_id: 原始会话 chat_id

    Returns:
        结果消息
    """
    try:
        # 1. 创建群聊
        group_name = f"🐳 {container_name}"
        docker_chat_id = create_group_chat(user_id, group_name)
        logger.info(f"创建群聊: {docker_chat_id[:8]}... -> {container_name}")

        # 2. 保存会话映射
        docker_session_manager.create_docker_session(
            original_chat_id=original_chat_id,
            container_name=container_name,
            user_open_id=user_id,
            docker_chat_id=docker_chat_id
        )

        # 3. 设置 Redis 路由（关键！）
        # 通过 Docker API 获取容器的端口映射
        import docker
        try:
            client = docker.from_env()
            container = client.containers.get(container_name)
            # 获取端口映射
            ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})

            # 尝试匹配 Guest Proxy 端口（格式为 "8081/tcp"）
            endpoint = None
            for port_key, bindings in ports.items():
                if bindings and "8081" in port_key:
                    host_port = bindings[0]['HostPort']
                    endpoint = f"http://localhost:{host_port}"
                    break

            if not endpoint:
                endpoint = f"http://localhost:8081"

            # 设置路由
            redis_client.set_route(docker_chat_id, endpoint)
            logger.info(f"设置路由: {docker_chat_id[:8]}... -> {endpoint}")

        except Exception as e:
            logger.warning(f"获取容器端口失败: {e}，使用默认端口")
            redis_client.set_route(docker_chat_id, f"http://localhost:8081")

        # 4. 发送欢迎消息到新群聊
        welcome_msg = f"""🚀 已连接到容器 **{container_name}**

现在你可以在这个群聊中与 Claude 交互，执行容器内的操作。

💡 提示：
- 直接发送消息即可与 Claude 对话
- 发送 /exit 或「退出」结束会话"""

        send_message(docker_chat_id, welcome_msg)

        # 5. 通知原会话
        send_message(
            original_chat_id,
            f"✅ 已创建容器会话\n"
            f"容器: {container_name}\n"
            f"请在新的群聊窗口中继续操作。"
        )

        return f"✅ 会话创建成功，请在群聊「{group_name}」中继续操作。"

    except Exception as e:
        logger.error(f"创建容器会话失败: {e}")
        return f"❌ 创建会话失败: {e}"


async def delete_docker_session_handler(chat_id: str) -> bool:
    """
    删除 Docker 容器会话的回调函数

    Args:
        chat_id: 容器会话 chat_id

    Returns:
        是否删除成功
    """
    try:
        # 检查是否是 Docker 会话
        if not docker_session_manager.is_docker_session(chat_id):
            return False

        # 删除路由和会话记录
        redis_client.delete_route(chat_id)
        docker_session_manager.delete_docker_session(chat_id)

        return True

    except Exception as e:
        logger.error(f"删除容器会话失败: {e}")
        return False


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

        # 尝试拦截管理命令
        interceptor = get_interceptor()
        intercepted = interceptor.try_intercept(sender_id, chat_id, text)
        if intercepted:
            # 检查是否需要异步处理
            if isinstance(intercepted, tuple):
                action = intercepted[0]
                if action == "__ASYNC_CREATE_SESSION__":
                    # 异步创建会话 - 放到线程中处理
                    _, user_id, container_name, orig_chat_id = intercepted
                    def _create_session():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            result = loop.run_until_complete(
                                create_docker_session_handler(user_id, container_name, orig_chat_id)
                            )
                            send_message(chat_id, result)
                        except Exception as e:
                            logger.error(f"创建会话失败: {e}")
                            send_message(chat_id, f"❌ 创建会话失败: {e}")
                        finally:
                            loop.close()
                    threading.Thread(target=_create_session, daemon=True).start()
                    return
                elif action == "__ASYNC_DELETE_SESSION__":
                    # 异步删除会话
                    _, session_chat_id = intercepted
                    def _delete_session():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            success = loop.run_until_complete(
                                delete_docker_session_handler(session_chat_id)
                            )
                            if success:
                                send_message(chat_id, "👋 已退出容器会话")
                            else:
                                send_message(chat_id, "⚠️ 当前不在容器会话中")
                        except Exception as e:
                            logger.error(f"退出会话失败: {e}")
                            send_message(chat_id, f"❌ 退出会话失败: {e}")
                        finally:
                            loop.close()
                    threading.Thread(target=_delete_session, daemon=True).start()
                    return
            else:
                send_message(chat_id, intercepted)
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

    # 注册 atexit 清理（备份机制）
    atexit.register(_cleanup_on_exit)

    # 注册信号处理器
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    # 1. 初始化 Redis
    if not init_redis():
        logger.error("Redis 连接失败，服务无法启动")
        return

    # 2. 初始化协议拦截器
    init_interceptor(
        on_create_session=create_docker_session_handler,
        on_delete_session=delete_docker_session_handler,
        is_authorized=is_authorized,
    )
    logger.info("协议拦截器已初始化")

    # 3. 启动 Host Bridge HTTP 服务
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

    # 主线程保持运行，等待 shutdown 信号
    try:
        await _shutdown_event.wait()
    finally:
        logger.info("正在停止服务...")
        await _host_bridge.stop()
        redis_client.close()
        logger.info("服务已停止")


def main():
    """主入口"""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()