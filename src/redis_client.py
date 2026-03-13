"""
Redis 客户端封装

提供 Redis 连接管理和路由索引操作
用于 Host-Guest 通信的会话路由
"""
import os
import json
import logging
from typing import Optional, Any
from datetime import timedelta

import redis

logger = logging.getLogger(__name__)

# Redis 键前缀
PREFIX_CHAT_ROUTE = "chat:route:"      # chat_id -> container_endpoint
PREFIX_SESSION_TTL = "session:ttl:"    # session_id -> TTL 管理
PREFIX_GUEST_HEARTBEAT = "guest:hb:"   # container_name -> heartbeat timestamp


class RedisClient:
    """
    Redis 客户端单例

    用途:
    1. 存储 chat_id -> container_endpoint 路由
    2. 管理会话 TTL
    3. Guest Proxy 心跳检测
    """

    _instance: Optional['RedisClient'] = None
    _client: Optional[redis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def connect(self, url: str = None, password: str = None) -> bool:
        """
        连接 Redis

        Args:
            url: Redis URL (redis://host:port/db)
            password: Redis 密码

        Returns:
            是否连接成功
        """
        if self._client is not None:
            return True

        # 从环境变量获取配置
        url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        password = password or os.getenv("REDIS_PASSWORD")

        try:
            self._client = redis.from_url(
                url,
                password=password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # 测试连接
            self._client.ping()
            logger.info(f"Redis 连接成功: {url}")
            return True
        except redis.ConnectionError as e:
            logger.error(f"Redis 连接失败: {e}")
            self._client = None
            return False
        except Exception as e:
            logger.error(f"Redis 初始化异常: {e}")
            self._client = None
            return False

    @property
    def client(self) -> redis.Redis:
        """获取 Redis 客户端"""
        if self._client is None:
            raise RuntimeError("Redis 未连接，请先调用 connect()")
        return self._client

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._client is not None

    def close(self):
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("Redis 连接已关闭")

    # ============ 路由管理 ============

    def set_route(self, chat_id: str, endpoint: str, ttl: int = 86400) -> bool:
        """
        设置聊天路由

        Args:
            chat_id: 飞书聊天 ID
            endpoint: Guest Proxy HTTP 端点 (http://host:port)
            ttl: 过期时间（秒），默认 24 小时

        Returns:
            是否设置成功
        """
        try:
            key = f"{PREFIX_CHAT_ROUTE}{chat_id}"
            self.client.setex(key, ttl, endpoint)
            logger.debug(f"设置路由: {chat_id[:8]}... -> {endpoint}")
            return True
        except Exception as e:
            logger.error(f"设置路由失败: {e}")
            return False

    def get_route(self, chat_id: str) -> Optional[str]:
        """
        获取聊天路由

        Args:
            chat_id: 飞书聊天 ID

        Returns:
            Guest Proxy 端点或 None
        """
        try:
            key = f"{PREFIX_CHAT_ROUTE}{chat_id}"
            endpoint = self.client.get(key)
            return endpoint
        except Exception as e:
            logger.error(f"获取路由失败: {e}")
            return None

    def delete_route(self, chat_id: str) -> bool:
        """
        删除聊天路由

        Args:
            chat_id: 飞书聊天 ID

        Returns:
            是否删除成功
        """
        try:
            key = f"{PREFIX_CHAT_ROUTE}{chat_id}"
            self.client.delete(key)
            logger.debug(f"删除路由: {chat_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"删除路由失败: {e}")
            return False

    def list_routes(self) -> dict[str, str]:
        """
        列出所有路由

        Returns:
            {chat_id: endpoint} 字典
        """
        try:
            keys = self.client.keys(f"{PREFIX_CHAT_ROUTE}*")
            routes = {}
            for key in keys:
                chat_id = key.replace(PREFIX_CHAT_ROUTE, "")
                endpoint = self.client.get(key)
                if endpoint:
                    routes[chat_id] = endpoint
            return routes
        except Exception as e:
            logger.error(f"列出路由失败: {e}")
            return {}

    # ============ 心跳管理 ============

    def set_heartbeat(self, container_name: str, ttl: int = 60) -> bool:
        """
        设置容器心跳

        Args:
            container_name: 容器名称
            ttl: 心跳过期时间（秒）

        Returns:
            是否设置成功
        """
        import time
        try:
            key = f"{PREFIX_GUEST_HEARTBEAT}{container_name}"
            self.client.setex(key, ttl, str(time.time()))
            return True
        except Exception as e:
            logger.error(f"设置心跳失败: {e}")
            return False

    def check_heartbeat(self, container_name: str) -> bool:
        """
        检查容器心跳

        Args:
            container_name: 容器名称

        Returns:
            容器是否存活
        """
        try:
            key = f"{PREFIX_GUEST_HEARTBEAT}{container_name}"
            return self.client.exists(key) > 0
        except Exception as e:
            logger.error(f"检查心跳失败: {e}")
            return False

    # ============ 会话 TTL 管理 ============

    def refresh_session_ttl(self, session_id: str, ttl: int = 86400) -> bool:
        """
        刷新会话 TTL

        Args:
            session_id: Claude Code 会话 ID
            ttl: 过期时间（秒）

        Returns:
            是否刷新成功
        """
        try:
            key = f"{PREFIX_SESSION_TTL}{session_id}"
            self.client.setex(key, ttl, "1")
            return True
        except Exception as e:
            logger.error(f"刷新会话 TTL 失败: {e}")
            return False


# 全局单例
redis_client = RedisClient()


def init_redis() -> bool:
    """
    初始化 Redis 连接

    Returns:
        是否初始化成功
    """
    return redis_client.connect()