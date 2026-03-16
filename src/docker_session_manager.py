"""
Docker 容器会话管理器

管理 Docker 容器会话的创建、存储和配置读取
"""
import json
import sqlite3
import subprocess
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 数据库路径
DB_PATH = Path(__file__).parent.parent / "data" / "docker_sessions.db"


def _get_conn():
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS docker_sessions (
            docker_chat_id TEXT PRIMARY KEY,
            original_chat_id TEXT,
            container_name TEXT,
            user_open_id TEXT,
            authorized_users TEXT,
            created_at TIMESTAMP
        )
    """)
    conn.commit()
    return conn


class DockerSessionManager:
    """管理 Docker 容器会话"""

    def create_docker_session(
        self,
        original_chat_id: str,
        container_name: str,
        user_open_id: str,
        docker_chat_id: str
    ) -> None:
        """
        创建容器会话记录

        Args:
            original_chat_id: 原始会话的 chat_id
            container_name: 容器名称或 ID
            user_open_id: 用户 open_id
            docker_chat_id: 新创建的容器会话 chat_id
        """
        # 读取容器内的配置
        authorized_users = self.read_container_settings(container_name)

        # 创建者自动成为授权用户
        if user_open_id not in authorized_users:
            authorized_users.append(user_open_id)
            logger.info(f"创建者 {user_open_id[:8]}... 已加入授权用户列表")

        conn = _get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO docker_sessions
            (docker_chat_id, original_chat_id, container_name, user_open_id, authorized_users, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            docker_chat_id,
            original_chat_id,
            container_name,
            user_open_id,
            json.dumps(authorized_users, ensure_ascii=False),
            datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
        logger.info(f"创建容器会话: {docker_chat_id[:8]}... -> {container_name}")

    def get_container_for_chat(self, chat_id: str) -> Optional[str]:
        """
        获取 chat_id 对应的容器名

        Args:
            chat_id: 飞书聊天 ID

        Returns:
            容器名或 None
        """
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT container_name FROM docker_sessions WHERE docker_chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def is_docker_session(self, chat_id: str) -> bool:
        """
        判断是否是容器会话

        Args:
            chat_id: 飞书聊天 ID

        Returns:
            True 如果是容器会话
        """
        return self.get_container_for_chat(chat_id) is not None

    def get_authorized_users(self, chat_id: str) -> list[str]:
        """
        获取容器会话的授权用户列表

        Args:
            chat_id: 飞书聊天 ID

        Returns:
            授权用户 open_id 列表
        """
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT authorized_users FROM docker_sessions WHERE docker_chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                logger.warning(f"解析授权用户列表失败: {row[0]}")
                return []
        return []

    def get_original_chat_id(self, chat_id: str) -> Optional[str]:
        """
        获取容器会话对应的原始会话 ID

        Args:
            chat_id: 容器会话的 chat_id

        Returns:
            原始会话 ID 或 None
        """
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT original_chat_id FROM docker_sessions WHERE docker_chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def read_container_settings(self, container_name: str) -> list[str]:
        """
        读取容器内的 settings.local.json

        Args:
            container_name: 容器名称或 ID

        Returns:
            授权用户列表
        """
        try:
            # 检查容器是否存在且运行
            check_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0 or result.stdout.strip() != "true":
                logger.warning(f"容器 {container_name} 不存在或未运行")
                return []
        except subprocess.TimeoutExpired:
            logger.error(f"检查容器状态超时: {container_name}")
            return []
        except Exception as e:
            logger.error(f"检查容器状态失败: {e}")
            return []

        try:
            # 读取容器内的配置文件
            cmd = [
                "docker", "exec", container_name,
                "cat", "/app/.claude/settings.local.json"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.info(f"容器 {container_name} 内没有配置文件")
                return []

            settings = json.loads(result.stdout)
            authorized = settings.get("authorized_users", [])

            # 支持字符串或列表格式
            if isinstance(authorized, str):
                authorized = [authorized]

            logger.info(f"从容器 {container_name} 读取到 {len(authorized)} 个授权用户")
            return authorized

        except json.JSONDecodeError as e:
            logger.warning(f"解析容器配置文件失败: {e}")
            return []
        except subprocess.TimeoutExpired:
            logger.error(f"读取容器配置超时: {container_name}")
            return []
        except Exception as e:
            logger.error(f"读取容器配置失败: {e}")
            return []

    def delete_docker_session(self, chat_id: str) -> bool:
        """
        删除容器会话记录

        Args:
            chat_id: 容器会话的 chat_id

        Returns:
            True 如果删除成功
        """
        conn = _get_conn()
        cursor = conn.execute(
            "DELETE FROM docker_sessions WHERE docker_chat_id = ?",
            (chat_id,)
        )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    def get_session_info(self, chat_id: str) -> Optional[dict]:
        """
        获取容器会话的完整信息

        Args:
            chat_id: 容器会话的 chat_id

        Returns:
            会话信息字典或 None
        """
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT docker_chat_id, original_chat_id, container_name, user_open_id, authorized_users, created_at FROM docker_sessions WHERE docker_chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "docker_chat_id": row[0],
                "original_chat_id": row[1],
                "container_name": row[2],
                "user_open_id": row[3],
                "authorized_users": json.loads(row[4]) if row[4] else [],
                "created_at": row[5],
            }
        return None

    def list_all_sessions(self) -> list[dict]:
        """
        列出所有容器会话

        Returns:
            会话信息列表
        """
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT docker_chat_id, original_chat_id, container_name, user_open_id, authorized_users, created_at FROM docker_sessions"
        )
        rows = cursor.fetchall()
        conn.close()

        sessions = []
        for row in rows:
            sessions.append({
                "docker_chat_id": row[0],
                "original_chat_id": row[1],
                "container_name": row[2],
                "user_open_id": row[3],
                "authorized_users": json.loads(row[4]) if row[4] else [],
                "created_at": row[5],
            })
        return sessions


# 全局单例
docker_session_manager = DockerSessionManager()