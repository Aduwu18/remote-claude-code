"""
Guest Proxy 配置
"""
import os
from typing import Optional


def get_guest_config() -> dict:
    """
    获取 Guest Proxy 配置

    环境变量优先级高于配置文件

    Returns:
        dict: {"port": int, "host_bridge_url": str, "container_name": str}
    """
    return {
        "port": int(os.getenv("GUEST_PROXY_PORT", "8081")),
        "host_bridge_url": os.getenv("HOST_BRIDGE_URL", "http://host.docker.internal:8080"),
        "container_name": os.getenv("CONTAINER_NAME", ""),
        "session_timeout": int(os.getenv("SESSION_TIMEOUT", "1800")),  # 30 分钟
    }


def get_container_name() -> Optional[str]:
    """
    获取当前容器名称

    从环境变量或 /etc/hostname 读取

    Returns:
        容器名称或 None
    """
    # 优先使用环境变量
    name = os.getenv("CONTAINER_NAME")
    if name:
        return name

    # 尝试从 hostname 获取（Docker 默认使用容器 ID 作为 hostname）
    try:
        with open("/etc/hostname", "r") as f:
            return f.read().strip()
    except Exception:
        return None


def get_container_env() -> dict:
    """
    获取容器环境信息

    用于环境继承检测

    Returns:
        dict: {"python_path": str, "venv": str|None, "bashrc_exists": bool}
    """
    import shutil

    result = {
        "python_path": shutil.which("python") or shutil.which("python3") or "python",
        "venv": os.getenv("VIRTUAL_ENV"),
        "bashrc_exists": os.path.exists(os.path.expanduser("~/.bashrc")),
    }
    return result