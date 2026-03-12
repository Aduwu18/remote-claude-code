"""
状态消息管理器

使用飞书消息更新状态
注意：飞书文本消息不支持 PATCH 更新，因此采用发送新消息的方式
对于需要原地更新的场景，可以使用卡片消息（interactive）
"""
import threading
import time
import logging
from typing import Optional

from src.feishu_utils.feishu_utils import send_message

logger = logging.getLogger(__name__)


class StatusManager:
    """
    管理执行状态的消息反馈

    用于在 Claude 执行长时间任务时，向用户展示实时状态

    注意：由于飞书文本消息不支持 PATCH 更新，状态更新会发送新消息
    最终结果会替换最后一条状态消息

    Usage:
        status_mgr = StatusManager(chat_id)
        status_mgr.send_status("正在处理...")

        # 在任务执行过程中更新状态（发送新消息）
        status_mgr.update_status("正在读取文件...")

        # 任务完成后替换为最终结果
        status_mgr.finalize("任务完成，结果如下...")
    """

    def __init__(self, chat_id: str, min_update_interval: float = 1.0):
        """
        初始化状态管理器

        Args:
            chat_id: 飞书聊天 ID
            min_update_interval: 最小更新间隔（秒），避免频繁消息
        """
        self.chat_id = chat_id
        self._lock = threading.Lock()
        self._last_update_time = 0.0
        self._min_update_interval = min_update_interval
        self._is_finalized = False
        self._last_status_text: Optional[str] = None

    def send_status(self, text: str) -> bool:
        """
        发送初始状态消息

        Args:
            text: 初始状态文本

        Returns:
            是否成功发送
        """
        with self._lock:
            if self._is_finalized:
                logger.warning("状态消息已终结，无法发送新状态")
                return False

            try:
                send_message(self.chat_id, text)
                self._last_update_time = time.time()
                self._last_status_text = text
                logger.debug(f"发送状态消息: {text[:30]}...")
                return True
            except Exception as e:
                logger.error(f"发送状态消息异常: {e}")
                return False

    def update_status(self, text: str) -> bool:
        """
        更新状态消息（发送新消息，带限流）

        Args:
            text: 新的状态文本

        Returns:
            bool: 是否成功更新
        """
        with self._lock:
            if self._is_finalized:
                return False

            # 限流：避免频繁发送消息
            now = time.time()
            if now - self._last_update_time < self._min_update_interval:
                return False

            try:
                send_message(self.chat_id, text)
                self._last_update_time = now
                self._last_status_text = text
                logger.debug(f"更新状态消息: {text[:30]}...")
                return True
            except Exception as e:
                logger.error(f"更新状态消息异常: {e}")
                return False

    def finalize(self, final_text: str):
        """
        发送最终结果

        Args:
            final_text: 最终结果文本
        """
        with self._lock:
            if self._is_finalized:
                return

            self._is_finalized = True

            try:
                send_message(self.chat_id, final_text)
                logger.debug("发送最终结果")
            except Exception as e:
                logger.error(f"发送最终结果失败: {e}")