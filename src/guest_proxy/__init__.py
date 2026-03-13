"""
Guest Proxy 模块

在 Docker 容器内运行的代理服务，负责：
1. 接收来自 Host Bridge 的请求
2. 调用 Claude Code SDK
3. 向 Host Bridge 发送状态更新和权限请求
4. 异常监控和心跳
"""
from src.guest_proxy.server import GuestProxyServer
from src.guest_proxy.config import get_guest_config

__all__ = ["GuestProxyServer", "get_guest_config"]