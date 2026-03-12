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


def get_permission_config() -> dict:
    """
    获取权限确认配置

    Returns:
        dict: {"enabled": bool, "timeout": int}
    """
    config = load_config()
    return config.get("permission", {"enabled": True, "timeout": 0})


def get_authorized_users() -> List[str]:
    """获取授权用户列表"""
    config = load_config()
    authorized = config.get("authorized_users")
    if authorized is None:
        return []
    if isinstance(authorized, str):
        return [authorized]
    return list(authorized)