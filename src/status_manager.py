"""
状态消息管理器

使用飞书卡片消息实现状态更新，支持原地更新而非发送新消息。

特性：
- 使用卡片消息 (msg_type: interactive) 展示状态
- 通过 PATCH API 实现消息原地更新
- 支持限流，避免频繁更新
"""
import threading
import time
import logging
from typing import Optional

from src.feishu_utils.feishu_utils import (
    send_card_message_with_id,
    update_card_message,
    send_message,
    send_long_markdown_message,
    FEISHU_CARD_MD_MAX_LENGTH,
)
from src.feishu_utils.card_builder import build_status_card, build_markdown_card

logger = logging.getLogger(__name__)


class StatusManager:
    """
    管理执行状态的消息反馈

    用于在 Claude 执行长时间任务时，向用户展示实时状态

    Usage:
        status_mgr = StatusManager(chat_id)
        status_mgr.send_status("正在处理...")

        # 在任务执行过程中更新状态（原地更新卡片）
        status_mgr.update_status("正在读取文件...")

        # 任务完成后替换为最终结果
        status_mgr.finalize("任务完成，结果如下...")
    """

    def __init__(self, chat_id: str, min_update_interval: float = 1.0, use_card: bool = True):
        """
        初始化状态管理器

        Args:
            chat_id: 飞书聊天 ID
            min_update_interval: 最小更新间隔（秒），避免频繁消息
            use_card: 是否使用卡片模式（默认 True）
        """
        self.chat_id = chat_id
        self._lock = threading.Lock()
        self._last_update_time = 0.0
        self._min_update_interval = min_update_interval
        self._is_finalized = False
        self._last_status_text: Optional[str] = None
        self._use_card = use_card
        self._message_id: Optional[str] = None  # 卡片消息 ID，用于更新

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
                if self._use_card:
                    # 使用卡片模式
                    card = build_status_card("执行中", text, icon="⏳", header_template="blue")
                    res = send_card_message_with_id(self.chat_id, card)

                    if res.get("code") == 0:
                        self._message_id = res.get("data", {}).get("message_id")
                        logger.debug(f"发送状态卡片: {text[:30]}... (msg_id: {self._message_id[:8] if self._message_id else 'N/A'})")
                    else:
                        logger.warning(f"发送卡片失败，降级为文本: {res}")
                        send_message(self.chat_id, f"⏳ {text}")
                else:
                    # 使用文本模式
                    send_message(self.chat_id, f"⏳ {text}")

                self._last_update_time = time.time()
                self._last_status_text = text
                return True
            except Exception as e:
                logger.error(f"发送状态消息异常: {e}")
                return False

    def update_status(self, text: str) -> bool:
        """
        更新状态消息（原地更新卡片，带限流）

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
                if self._use_card and self._message_id:
                    # 原地更新卡片
                    card = build_status_card("执行中", text, icon="⏳", header_template="blue")
                    res = update_card_message(self._message_id, card)

                    if res.get("code") != 0:
                        logger.warning(f"更新卡片失败: {res}")
                        # 降级为发送新消息
                        send_message(self.chat_id, f"⏳ {text}")
                else:
                    # 发送新消息
                    if self._use_card:
                        card = build_status_card("执行中", text, icon="⏳", header_template="blue")
                        res = send_card_message_with_id(self.chat_id, card)
                        if res.get("code") == 0:
                            self._message_id = res.get("data", {}).get("message_id")
                    else:
                        send_message(self.chat_id, f"⏳ {text}")

                self._last_update_time = now
                self._last_status_text = text
                logger.debug(f"更新状态消息: {text[:30]}...")
                return True
            except Exception as e:
                logger.error(f"更新状态消息异常: {e}")
                return False

    def finalize(self, final_text: str, title: str = "完成"):
        """
        发送最终结果（更新现有卡片或发送新卡片）

        支持长消息自动分块发送。

        Args:
            final_text: 最终结果文本
            title: 结果标题（默认"完成"）
        """
        with self._lock:
            if self._is_finalized:
                return

            self._is_finalized = True

            try:
                # 检查消息长度，决定是否分块发送
                if len(final_text) > FEISHU_CARD_MD_MAX_LENGTH:
                    # 长消息：更新状态卡片为"完成"提示，然后分块发送内容
                    if self._use_card and self._message_id:
                        # 更新状态卡片为简短提示
                        card = build_markdown_card(
                            title=f"✅ {title}",
                            content="内容较长，已分多条消息发送 👇",
                            header_template="green"
                        )
                        update_card_message(self._message_id, card)

                    # 分块发送完整内容
                    send_long_markdown_message(self.chat_id, final_text, title="")
                    logger.debug(f"发送长消息结果，长度: {len(final_text)}")
                else:
                    # 短消息：正常处理
                    if self._use_card and self._message_id:
                        # 更新现有卡片为最终结果
                        card = build_markdown_card(
                            title=f"✅ {title}",
                            content=final_text,
                            header_template="green"
                        )
                        res = update_card_message(self._message_id, card)

                        if res.get("code") != 0:
                            logger.warning(f"更新最终结果卡片失败: {res}")
                            # 降级为发送新消息
                            send_message(self.chat_id, final_text)
                    else:
                        # 发送新的卡片或文本
                        if self._use_card:
                            card = build_markdown_card(
                                title=f"✅ {title}",
                                content=final_text,
                                header_template="green"
                            )
                            send_card_message_with_id(self.chat_id, card)
                        else:
                            send_message(self.chat_id, final_text)

                logger.debug("发送最终结果")
            except Exception as e:
                logger.error(f"发送最终结果失败: {e}")
                # 最后的降级：发送纯文本
                try:
                    send_long_markdown_message(self.chat_id, final_text, title="")
                except Exception:
                    pass

    def finalize_error(self, error_text: str, title: str = "执行出错"):
        """
        发送错误结果

        支持长消息自动分块发送。

        Args:
            error_text: 错误信息
            title: 错误标题
        """
        with self._lock:
            if self._is_finalized:
                return

            self._is_finalized = True

            try:
                # 检查消息长度
                if len(error_text) > FEISHU_CARD_MD_MAX_LENGTH:
                    # 长错误消息：更新状态卡片，然后分块发送
                    if self._use_card and self._message_id:
                        card = build_markdown_card(
                            title=f"❌ {title}",
                            content="错误信息较长，已分多条消息发送 👇",
                            header_template="red"
                        )
                        update_card_message(self._message_id, card)

                    # 分块发送错误内容
                    send_long_markdown_message(self.chat_id, error_text, title="")
                else:
                    if self._use_card and self._message_id:
                        card = build_markdown_card(
                            title=f"❌ {title}",
                            content=error_text,
                            header_template="red"
                        )
                        res = update_card_message(self._message_id, card)

                        if res.get("code") != 0:
                            send_message(self.chat_id, f"❌ {error_text}")
                    else:
                        if self._use_card:
                            card = build_markdown_card(
                                title=f"❌ {title}",
                                content=error_text,
                                header_template="red"
                            )
                            send_card_message_with_id(self.chat_id, card)
                        else:
                            send_message(self.chat_id, f"❌ {error_text}")

            except Exception as e:
                logger.error(f"发送错误结果失败: {e}")
                try:
                    send_long_markdown_message(self.chat_id, error_text, title="")
                except Exception:
                    pass