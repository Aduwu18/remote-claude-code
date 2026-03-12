"""
飞书长连接方式接收消息

使用官方 SDK 的 WebSocket 长连接，无需公网域名
支持用户白名单和权限确认
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
from queue import Queue, Empty

import lark_oapi as lark
from lark_oapi.adapter.flask import *
from lark_oapi.api.im.v1 import *

from src.config import is_authorized, get_permission_config, get_authorized_users
from src.claude_code import chat_sync, set_permission_request_callback
from src.feishu_utils.feishu_utils import send_message, reply_message
from src.data_base_utils import get_session, save_session
from src.permission_manager import (
    permission_manager,
    format_permission_message,
)

APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 正在处理中的 chat_id 队列（只保留未完成的）
_active_queues: dict[str, Queue] = {}
_queue_lock = threading.Lock()

# 正在等待权限确认的 chat_id（避免与正常消息队列冲突）
_pending_permission_chats: set[str] = set()
_pending_permission_lock = threading.Lock()


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


def chat_with_claude(chat_id: str, message: str) -> str:
    """
    调用 Claude Code，基于 chat_id 保持对话连续性
    """
    # 从 SQLite 获取之前的 session_id
    session_id = get_session(chat_id)

    # 获取权限配置
    perm_config = get_permission_config()
    require_confirmation = perm_config.get("enabled", True)

    # 调用 Claude
    reply, new_session_id = chat_sync(
        message,
        session_id=session_id,
        chat_id=chat_id,
        require_confirmation=require_confirmation,
    )

    # 保存到 SQLite
    if new_session_id != session_id:
        save_session(chat_id, new_session_id)
        logger.info(f"会话映射: {chat_id[:8]}... -> {new_session_id[:8]}...")

    return reply


def _process_chat_queue(chat_id: str, message_id: str, chat_type: str, queue: Queue):
    """
    处理指定 chat_id 的消息队列（FIFO）
    同一 chat_id 串行处理，不同 chat_id 可并行
    """
    while True:
        # 在锁内检查并获取消息，确保线程安全
        with _queue_lock:
            if queue.empty():
                # 队列空了，销毁并退出
                _active_queues.pop(chat_id, None)
                return
            text = queue.get_nowait()

        try:
            reply = chat_with_claude(chat_id, text)
            if chat_type == "group":
                reply_message(message_id, reply)
            else:
                send_message(chat_id, reply)
            logger.info(f"回复: {reply[:100]}...")
        except Exception as e:
            logger.error(f"处理失败 [{chat_id[:8]}...]: {e}")
            import traceback
            traceback.print_exc()


def enqueue_message(chat_id: str, message_id: str, text: str, chat_type: str):
    """
    将消息加入队列，无队列则创建
    """
    with _queue_lock:
        if chat_id in _active_queues:
            # 已有队列，直接加入
            _active_queues[chat_id].put(text)
        else:
            # 创建新队列并启动 worker
            queue = Queue()
            queue.put(text)
            _active_queues[chat_id] = queue
            threading.Thread(
                target=_process_chat_queue,
                args=(chat_id, message_id, chat_type, queue),
                daemon=True
            ).start()


def handle_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """处理接收到的消息"""
    try:
        event = data.event
        message = event.message
        message_id = message.message_id

        # 获取发送者 ID
        sender_id = event.sender.sender_id.open_id

        # 解析消息内容
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

        # 检查用户白名单
        if not is_authorized(sender_id):
            logger.warning(f"未授权用户: {sender_id}")
            send_message(chat_id, "您没有使用此机器人的权限。")
            return

        # 检查是否是权限确认响应
        lower_text = text.lower().strip()
        with _pending_permission_lock:
            is_waiting_permission = chat_id in _pending_permission_chats

        if is_waiting_permission:
            # 处理权限确认响应
            if lower_text in ["y", "yes", "确认", "允许"]:
                if permission_manager.submit_response(chat_id, True):
                    send_message(chat_id, "✅ 已允许操作")
                    return
            elif lower_text in ["n", "no", "拒绝", "取消"]:
                if permission_manager.submit_response(chat_id, False):
                    send_message(chat_id, "❌ 已拒绝操作")
                    return
            else:
                # 不是权限确认响应，继续正常处理
                pass

        # 加入队列，按 chat_id 串行处理
        enqueue_message(chat_id, message_id, text, message.chat_type)

    except Exception as e:
        logger.error(f"处理消息失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    # 设置权限确认回调
    set_permission_request_callback(handle_permission_request)

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