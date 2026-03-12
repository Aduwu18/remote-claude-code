"""
飞书长连接方式接收消息

使用官方 SDK 的 WebSocket 长连接，无需公网域名
支持用户白名单和权限确认
支持 Docker 容器会话（通过 Claude 工具调用）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv
load_dotenv()  # 必须在导入其他模块之前加载

import json
import logging
import threading
from queue import Queue

import lark_oapi as lark
from lark_oapi.adapter.flask import *
from lark_oapi.api.im.v1 import *

from src.config import is_authorized, get_permission_config, get_authorized_users
from src.claude_code import chat_sync, set_permission_request_callback
from src.feishu_utils.feishu_utils import send_message, reply_message, create_group_chat
from src.data_base_utils import get_session, save_session
from src.permission_manager import (
    permission_manager,
    format_permission_message,
)
from src.docker_session_manager import docker_session_manager
from src.docker_mcp import set_docker_session_handler
from src.context import set_request_context, clear_request_context

APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 消息队列管理（每个 chat_id 一个队列，串行处理）
_active_queues: dict[str, Queue] = {}
_queue_lock = threading.Lock()

# 正在等待权限确认的 chat_id
_pending_permission_chats: set[str] = set()
_pending_permission_lock = threading.Lock()

# 正在等待 Docker 会话确认的 chat_id
# {chat_id: {"container": "容器名", "user_open_id": "用户ID"}}
_pending_docker_confirm: dict[str, dict] = {}
_pending_docker_lock = threading.Lock()


def handle_permission_request(
    chat_id: str,
    session_id: str,
    tool_name: str,
    tool_input: dict
) -> bool:
    """
    处理权限确认请求（在权限管理器中阻塞等待用户响应）

    Args:
        chat_id: 飞书聊天 ID
        session_id: Claude Code 会话 ID
        tool_name: 工具名称
        tool_input: 工具输入参数

    Returns:
        bool: True 表示用户允许，False 表示用户拒绝
    """
    # 发送确认请求到飞书
    message = format_permission_message(tool_name, tool_input)
    send_message(chat_id, message)

    # 标记该聊天正在等待权限确认
    with _pending_permission_lock:
        _pending_permission_chats.add(chat_id)

    try:
        # 在权限管理器中等待用户响应
        approved = permission_manager.request_permission(
            session_id=session_id,
            chat_id=chat_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        return approved
    finally:
        # 清理标记
        with _pending_permission_lock:
            _pending_permission_chats.discard(chat_id)


def handle_docker_session_request(
    chat_id: str,
    user_open_id: str,
    container_name: str
) -> dict:
    """
    处理 Docker 会话创建请求（由 MCP 工具调用触发）

    Args:
        chat_id: 飞书聊天 ID
        user_open_id: 用户 open_id
        container_name: 容器名称

    Returns:
        dict: {"success": bool, "message": str, "docker_chat_id": str|None}
    """
    logger.info(f"收到 Docker 会话请求: {container_name} (from {chat_id[:8]}..., user={user_open_id[:8]}...)")

    # 检查容器是否存在且运行
    import subprocess
    try:
        check_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
        result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or result.stdout.strip() != "true":
            return {
                "success": False,
                "message": f"容器 '{container_name}' 不存在或未运行"
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"检查容器状态失败: {e}"
        }

    # 发送确认请求到飞书
    send_message(
        chat_id,
        f"🐳 创建容器会话: {container_name}\n\n"
        f"是否创建专属容器会话？(y/n)\n"
        f"创建后将在新群聊窗口进行容器内操作。"
    )

    # 等待用户确认（阻塞）
    with _pending_docker_lock:
        _pending_docker_confirm[chat_id] = {
            "container": container_name,
            "user_open_id": user_open_id,
            "waiting": True,
            "confirmed": None,
        }

    # 阻塞等待用户响应
    import time
    timeout = 300  # 5 分钟超时
    start = time.time()
    while time.time() - start < timeout:
        with _pending_docker_lock:
            pending = _pending_docker_confirm.get(chat_id)
            if pending and pending.get("confirmed") is not None:
                break
        time.sleep(0.5)

    with _pending_docker_lock:
        pending = _pending_docker_confirm.pop(chat_id, {})

    confirmed = pending.get("confirmed")
    if confirmed is None:
        return {
            "success": False,
            "message": "确认超时，请重新请求"
        }

    if not confirmed:
        return {
            "success": False,
            "message": "用户拒绝创建容器会话"
        }

    # 用户确认，创建群聊会话
    try:
        group_name = f"🐳 {container_name} (Claude助手)"
        docker_chat_id = create_group_chat(user_open_id, group_name)

        # 保存 Docker 会话映射
        docker_session_manager.create_docker_session(
            original_chat_id=chat_id,
            container_name=container_name,
            user_open_id=user_open_id,
            docker_chat_id=docker_chat_id
        )

        # 发送欢迎消息到新窗口
        welcome_msg = f"🐳 已进入容器: {container_name}\n\n"
        welcome_msg += "在此窗口发送的命令将在容器内执行。\n"
        welcome_msg += "输入 /exit 退出容器会话。"
        send_message(docker_chat_id, welcome_msg)

        # 在原窗口通知
        send_message(chat_id, f"✅ 已创建容器会话，请在新的群聊窗口继续操作。")

        return {
            "success": True,
            "message": f"容器会话已创建，请在新的群聊窗口继续",
            "docker_chat_id": docker_chat_id
        }

    except Exception as e:
        logger.error(f"创建 Docker 会话失败: {e}")
        return {
            "success": False,
            "message": f"创建容器会话失败: {e}"
        }


def chat_with_claude(chat_id: str, message: str, user_open_id: str = None) -> str:
    """
    调用 Claude Code，基于 chat_id 保持对话连续性
    支持 Docker 容器会话
    """
    # 设置请求上下文（供 MCP 工具使用）
    if user_open_id:
        set_request_context(chat_id, user_open_id)

    try:
        # 从 SQLite 获取之前的 session_id
        session_id = get_session(chat_id)

        # 获取权限配置
        perm_config = get_permission_config()
        require_confirmation = perm_config.get("enabled", True)

        # 检查是否是 Docker 会话
        container = docker_session_manager.get_container_for_chat(chat_id)

        # 调用 Claude
        reply, new_session_id = chat_sync(
            message,
            session_id=session_id,
            chat_id=chat_id,
            require_confirmation=require_confirmation,
            container=container,
            user_open_id=user_open_id,
        )

        # 保存到 SQLite
        if new_session_id != session_id:
            save_session(chat_id, new_session_id)
            logger.info(f"会话映射: {chat_id[:8]}... -> {new_session_id[:8]}...")

        return reply
    finally:
        # 清理请求上下文
        clear_request_context()


def _process_chat_queue(chat_id: str, message_id: str, chat_type: str, queue: Queue, user_open_id: str = None):
    """
    处理指定 chat_id 的消息队列（FIFO）
    同一 chat_id 串行处理，不同 chat_id 可并行
    """
    while True:
        with _queue_lock:
            if queue.empty():
                _active_queues.pop(chat_id, None)
                return
            text = queue.get_nowait()

        try:
            reply = chat_with_claude(chat_id, text, user_open_id)
            if chat_type == "group":
                reply_message(message_id, reply)
            else:
                send_message(chat_id, reply)
            logger.info(f"回复: {reply[:100]}...")
        except Exception as e:
            logger.error(f"处理失败 [{chat_id[:8]}...]: {e}")
            import traceback
            traceback.print_exc()


def enqueue_message(chat_id: str, message_id: str, text: str, chat_type: str, user_open_id: str = None):
    """
    将消息加入队列，无队列则创建
    """
    with _queue_lock:
        if chat_id in _active_queues:
            _active_queues[chat_id].put(text)
        else:
            queue = Queue()
            queue.put(text)
            _active_queues[chat_id] = queue
            threading.Thread(
                target=_process_chat_queue,
                args=(chat_id, message_id, chat_type, queue, user_open_id),
                daemon=True
            ).start()


def handle_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """处理接收到的消息"""
    try:
        event = data.event
        message = event.message
        message_id = message.message_id

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

        # 检查是否是 Docker 会话确认响应
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

        # 检查是否是退出容器会话命令
        if is_docker and lower_text in ["/exit", "exit", "退出"]:
            original_chat_id = docker_session_manager.get_original_chat_id(chat_id)
            docker_session_manager.delete_docker_session(chat_id)
            send_message(chat_id, "👋 已退出容器会话。")
            if original_chat_id:
                send_message(original_chat_id, f"容器会话已结束。")
            return

        # 检查是否是权限确认响应
        with _pending_permission_lock:
            is_waiting_permission = chat_id in _pending_permission_chats

        if is_waiting_permission:
            if lower_text in ["y", "yes", "确认", "允许"]:
                if permission_manager.submit_response(chat_id, True):
                    send_message(chat_id, "✅ 已允许操作")
                    return
            elif lower_text in ["n", "no", "拒绝", "取消"]:
                if permission_manager.submit_response(chat_id, False):
                    send_message(chat_id, "❌ 已拒绝操作")
                    return

        # 加入队列，按 chat_id 串行处理
        enqueue_message(chat_id, message_id, text, message.chat_type, sender_id)

    except Exception as e:
        logger.error(f"处理消息失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    # 设置权限确认回调
    set_permission_request_callback(handle_permission_request)

    # 设置 Docker 会话处理函数
    set_docker_session_handler(handle_docker_session_request)

    # 创建客户端
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
    client.start()


if __name__ == "__main__":
    main()