"""
配置管理模块

支持从 config.yaml 加载配置，支持环境变量覆盖
"""
import os
import yaml
from pathlib import Path
from typing import Optional, List


_config: Optional[dict] = None


def get_config_path() -> Path:
    """获取配置文件路径"""
    # 优先使用项目根目录的 config.yaml
    return Path(__file__).parent.parent / "config.yaml"


def load_config() -> dict:
    """加载配置文件（带缓存）"""
    global _config
    if _config is None:
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                _config = yaml.safe_load(f) or {}
        else:
            _config = {}
    return _config


def reload_config() -> dict:
    """重新加载配置文件"""
    global _config
    _config = None
    return load_config()


def is_authorized(open_id: str) -> bool:
    """
    检查用户是否在白名单中

    Args:
        open_id: 飞书用户的 open_id

    Returns:
        True 如果用户已授权，False 否则
    """
    config = load_config()
    authorized = config.get("authorized_users", [])

    # 支持字符串或列表格式
    if isinstance(authorized, str):
        authorized = [authorized]

    return open_id in authorized


def get_authorized_users() -> List[str]:
    """获取授权用户列表"""
    config = load_config()
    authorized = config.get("authorized_users")
    if authorized is None:
        return []
    if isinstance(authorized, str):
        return [authorized]
    return list(authorized)


def get_redis_config() -> dict:
    """
    获取 Redis 配置

    Returns:
        dict: {"url": str, "password": str|None}
    """
    config = load_config()
    redis_config = config.get("redis", {})

    # 环境变量优先
    url = os.getenv("REDIS_URL") or redis_config.get("url", "redis://localhost:6379/0")
    password = os.getenv("REDIS_PASSWORD") or redis_config.get("password")

    return {
        "url": url,
        "password": password,
    }


def get_host_bridge_config() -> dict:
    """
    获取 Host Bridge 配置

    Returns:
        dict: {"port": int, "host": str}
    """
    config = load_config()
    bridge_config = config.get("host_bridge", {})
    return {
        "port": bridge_config.get("port", 8080),
        "host": bridge_config.get("host", "0.0.0.0"),
    }


def get_guest_proxy_config() -> dict:
    """
    获取 Guest Proxy 配置

    Returns:
        dict: {"port": int, "host_bridge_url": str}
    """
    config = load_config()
    guest_config = config.get("guest_proxy", {})
    return {
        "port": guest_config.get("port", 8081),
        "host_bridge_url": os.getenv("HOST_BRIDGE_URL") or guest_config.get("host_bridge_url", "http://host.docker.internal:8080"),
    }


def get_terminal_session_config() -> dict:
    """
    获取 Terminal 会话配置

    Returns:
        dict: {
            "enabled": bool,
            "auto_create_chat": bool,
            "auto_disband_on_exit": bool,
            "user_open_id": str,
            "group_name_prefix": str
        }
    """
    config = load_config()
    terminal_config = config.get("terminal_session", {})

    return {
        "enabled": terminal_config.get("enabled", True),
        "auto_create_chat": terminal_config.get("auto_create_chat", True),
        "auto_disband_on_exit": terminal_config.get("auto_disband_on_exit", True),
        "user_open_id": os.getenv("FEISHU_USER_OPEN_ID") or terminal_config.get("user_open_id", ""),
        "group_name_prefix": terminal_config.get("group_name_prefix", "💻 Terminal"),
    }