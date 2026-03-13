"""
Host Bridge 模块

宿主机上的网关服务，负责：
1. 接收飞书消息，路由到对应的 Guest Proxy
2. 接收 Guest Proxy 的注册、权限请求、状态更新
3. Redis 路由管理
"""
from src.host_bridge.server import HostBridgeServer, start_host_bridge, get_host_bridge
from src.host_bridge.client import GuestProxyClient

__all__ = ["HostBridgeServer", "start_host_bridge", "get_host_bridge", "GuestProxyClient"]