"""
Terminal 会话管理器

负责：
1. 创建 Terminal 会话（自动创建飞书群聊）
2. 存储会话信息（chat_id, session_id, 创建时间等）
3. 解散会话（退出时解散群聊）
4. 会话持久化（存储到本地 JSON 文件）

使用方式：
    manager = TerminalSessionManager()

    # 创建会话
    session = await manager.create_session(terminal_id, user_open_id)

    # 恢复会话
    session = await manager.restore_session(terminal_id)

    # 同步输出
    await manager.sync_output(terminal_id, content)

    # 同步状态
    await manager.sync_status(terminal_id, "running", {"message": "正在执行..."})

    # 关闭会话
    await manager.close_session(terminal_id, disband_chat=True)
"""
import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from src.feishu_utils.feishu_utils import (
    create_group_chat,
    disband_group_chat,
    get_chat_info,
    send_terminal_status_card,
    send_card_message,
)
from src.feishu_utils.card_builder import CardBuilder

logger = logging.getLogger(__name__)


@dataclass
class TerminalSession:
    """Terminal 会话数据"""
    terminal_id: str          # 终端唯一标识 (hostname + timestamp)
    chat_id: str              # 飞书群聊 ID
    session_id: Optional[str] # Claude 会话 ID
    user_open_id: str         # 用户 open_id
    created_at: str           # 创建时间 (ISO 格式)
    status: str               # 状态: running/idle/stopped
    message_count: int        # 消息计数
    hostname: str             # 主机名

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'TerminalSession':
        return cls(
            terminal_id=data.get("terminal_id", ""),
            chat_id=data.get("chat_id", ""),
            session_id=data.get("session_id"),
            user_open_id=data.get("user_open_id", ""),
            created_at=data.get("created_at", ""),
            status=data.get("status", "idle"),
            message_count=data.get("message_count", 0),
            hostname=data.get("hostname", ""),
        )


class TerminalSessionManager:
    """
    Terminal 会话管理器

    管理终端与飞书群聊的绑定关系，支持会话持久化。
    """

    def __init__(
        self,
        storage_path: str = "data/terminal_sessions.json",
        user_open_id: str = None,
        group_name_prefix: str = "💻 Terminal",
        auto_disband_on_exit: bool = True,
    ):
        """
        初始化会话管理器

        Args:
            storage_path: 会话存储路径（JSON 文件）
            user_open_id: 默认用户 open_id
            group_name_prefix: 群聊名称前缀
            auto_disband_on_exit: 退出时是否自动解散群聊
        """
        self._storage_path = Path(storage_path)
        self._sessions: Dict[str, TerminalSession] = {}
        self._user_open_id = user_open_id
        self._group_name_prefix = group_name_prefix
        self._auto_disband_on_exit = auto_disband_on_exit
        self._lock = asyncio.Lock()

        # 确保存储目录存在
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

        # 加载已有会话
        self._load_sessions()

    def _load_sessions(self):
        """从文件加载会话"""
        if self._storage_path.exists():
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for terminal_id, session_data in data.items():
                        self._sessions[terminal_id] = TerminalSession.from_dict(session_data)
                logger.info(f"已加载 {len(self._sessions)} 个 Terminal 会话")
            except Exception as e:
                logger.error(f"加载会话失败: {e}")

    def _save_sessions(self):
        """保存会话到文件"""
        try:
            data = {
                terminal_id: session.to_dict()
                for terminal_id, session in self._sessions.items()
            }
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存 {len(self._sessions)} 个 Terminal 会话")
        except Exception as e:
            logger.error(f"保存会话失败: {e}")

    @staticmethod
    def generate_terminal_id() -> str:
        """
        生成终端唯一标识

        格式: hostname-timestamp (例如: myhost-1705920000)

        Returns:
            终端唯一标识
        """
        hostname = socket.gethostname()
        timestamp = int(time.time())
        return f"{hostname}-{timestamp}"

    async def create_session(
        self,
        terminal_id: str,
        user_open_id: str = None,
        session_id: str = None,
    ) -> TerminalSession:
        """
        创建 Terminal 会话（自动创建飞书群聊）

        Args:
            terminal_id: 终端唯一标识
            user_open_id: 用户 open_id（可选，使用默认值）
            session_id: Claude 会话 ID（可选，用于恢复）

        Returns:
            TerminalSession: 创建的会话

        Raises:
            Exception: 创建群聊失败时抛出异常
        """
        async with self._lock:
            # 检查是否已存在
            if terminal_id in self._sessions:
                existing = self._sessions[terminal_id]
                # 验证群聊是否仍然有效
                chat_info = get_chat_info(existing.chat_id)
                if chat_info:
                    logger.info(f"会话已存在: {terminal_id}, chat_id: {existing.chat_id}")
                    return existing
                else:
                    # 群聊已不存在，删除旧会话
                    del self._sessions[terminal_id]

            # 使用默认用户 ID
            if user_open_id is None:
                user_open_id = self._user_open_id
            if not user_open_id:
                raise ValueError("需要提供 user_open_id")

            # 提取主机名
            hostname = terminal_id.rsplit("-", 1)[0] if "-" in terminal_id else socket.gethostname()

            # 创建群聊
            group_name = f"{self._group_name_prefix} {hostname}"
            chat_id = create_group_chat(user_open_id, group_name)
            logger.info(f"创建群聊成功: {group_name} ({chat_id})")

            # 创建会话对象
            session = TerminalSession(
                terminal_id=terminal_id,
                chat_id=chat_id,
                session_id=session_id,
                user_open_id=user_open_id,
                created_at=datetime.now().isoformat(),
                status="started",
                message_count=0,
                hostname=hostname,
            )

            # 保存会话
            self._sessions[terminal_id] = session
            self._save_sessions()

            # 发送启动状态卡片
            await self.sync_status(terminal_id, "started", {
                "terminal_id": terminal_id,
                "hostname": hostname,
                "message": "终端已启动，等待输入...",
                "session_id": session_id,
            })

            return session

    async def restore_session(self, terminal_id: str) -> Optional[TerminalSession]:
        """
        恢复会话

        Args:
            terminal_id: 终端唯一标识

        Returns:
            TerminalSession 或 None（如果会话不存在或群聊已解散）
        """
        async with self._lock:
            if terminal_id not in self._sessions:
                return None

            session = self._sessions[terminal_id]

            # 验证群聊是否仍然有效
            chat_info = get_chat_info(session.chat_id)
            if not chat_info:
                logger.warning(f"群聊 {session.chat_id} 已不存在，删除会话")
                del self._sessions[terminal_id]
                self._save_sessions()
                return None

            # 更新状态
            session.status = "idle"
            self._save_sessions()

            logger.info(f"恢复会话: {terminal_id}, chat_id: {session.chat_id}")
            return session

    async def close_session(
        self,
        terminal_id: str,
        disband_chat: bool = None,
    ) -> bool:
        """
        关闭会话（解散群聊）

        Args:
            terminal_id: 终端唯一标识
            disband_chat: 是否解散群聊（None 时使用默认配置）

        Returns:
            bool: True 表示成功，False 表示失败
        """
        async with self._lock:
            if terminal_id not in self._sessions:
                logger.warning(f"会话不存在: {terminal_id}")
                return False

            session = self._sessions[terminal_id]

            # 发送停止状态卡片
            try:
                await self.sync_status(terminal_id, "stopped", {
                    "terminal_id": terminal_id,
                    "hostname": session.hostname,
                    "message": f"终端已关闭，共处理 {session.message_count} 条消息",
                })
            except Exception as e:
                logger.warning(f"发送停止状态卡片失败: {e}")

            # 是否解散群聊
            if disband_chat is None:
                disband_chat = self._auto_disband_on_exit

            if disband_chat:
                try:
                    disband_group_chat(session.chat_id)
                    logger.info(f"解散群聊成功: {session.chat_id}")
                except Exception as e:
                    logger.error(f"解散群聊失败: {e}")

            # 删除会话
            del self._sessions[terminal_id]
            self._save_sessions()

            logger.info(f"关闭会话: {terminal_id}")
            return True

    async def sync_output(self, terminal_id: str, content: str) -> bool:
        """
        同步输出到群聊

        Args:
            terminal_id: 终端唯一标识
            content: 输出内容

        Returns:
            bool: True 表示成功
        """
        session = self._sessions.get(terminal_id)
        if not session:
            logger.warning(f"会话不存在: {terminal_id}")
            return False

        try:
            # 使用卡片发送
            builder = CardBuilder()
            builder.add_div(content, "lark_md")
            send_card_message(session.chat_id, builder.build())

            # 更新消息计数
            session.message_count += 1
            self._save_sessions()

            return True
        except Exception as e:
            logger.error(f"同步输出失败: {e}")
            return False

    async def sync_status(
        self,
        terminal_id: str,
        status: str,
        details: dict,
    ) -> bool:
        """
        同步状态到群聊

        Args:
            terminal_id: 终端唯一标识
            status: 状态类型 (started, running, idle, stopped, error)
            details: 状态详情

        Returns:
            bool: True 表示成功
        """
        session = self._sessions.get(terminal_id)
        if not session:
            logger.warning(f"会话不存在: {terminal_id}")
            return False

        try:
            # 补充详情
            if "terminal_id" not in details:
                details["terminal_id"] = terminal_id
            if "hostname" not in details:
                details["hostname"] = session.hostname

            send_terminal_status_card(session.chat_id, status, details)

            # 更新状态
            session.status = status
            self._save_sessions()

            return True
        except Exception as e:
            logger.error(f"同步状态失败: {e}")
            return False

    def update_session_id(self, terminal_id: str, session_id: str):
        """
        更新 Claude 会话 ID

        Args:
            terminal_id: 终端唯一标识
            session_id: 新的 Claude 会话 ID
        """
        if terminal_id in self._sessions:
            self._sessions[terminal_id].session_id = session_id
            self._save_sessions()

    def get_session(self, terminal_id: str) -> Optional[TerminalSession]:
        """
        获取会话信息

        Args:
            terminal_id: 终端唯一标识

        Returns:
            TerminalSession 或 None
        """
        return self._sessions.get(terminal_id)

    def get_chat_id(self, terminal_id: str) -> Optional[str]:
        """
        获取群聊 ID

        Args:
            terminal_id: 终端唯一标识

        Returns:
            chat_id 或 None
        """
        session = self._sessions.get(terminal_id)
        return session.chat_id if session else None

    def list_sessions(self) -> list[TerminalSession]:
        """
        列出所有会话

        Returns:
            会话列表
        """
        return list(self._sessions.values())


# 全局单例
_session_manager: Optional[TerminalSessionManager] = None


def get_terminal_session_manager() -> TerminalSessionManager:
    """获取全局会话管理器单例"""
    global _session_manager
    if _session_manager is None:
        from src.config import load_config

        config = load_config()
        terminal_config = config.get("terminal_session", {})

        _session_manager = TerminalSessionManager(
            user_open_id=terminal_config.get("user_open_id"),
            group_name_prefix=terminal_config.get("group_name_prefix", "💻 Terminal"),
            auto_disband_on_exit=terminal_config.get("auto_disband_on_exit", True),
        )
    return _session_manager


def init_terminal_session_manager(
    user_open_id: str = None,
    group_name_prefix: str = "💻 Terminal",
    auto_disband_on_exit: bool = True,
) -> TerminalSessionManager:
    """初始化会话管理器"""
    global _session_manager
    _session_manager = TerminalSessionManager(
        user_open_id=user_open_id,
        group_name_prefix=group_name_prefix,
        auto_disband_on_exit=auto_disband_on_exit,
    )
    return _session_manager