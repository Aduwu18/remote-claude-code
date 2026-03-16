"""
Local Session Bridge 模块

本地 Session 桥接服务，运行在宿主机上，提供：
1. HTTP 端点供 Terminal CLI 连接
2. 创建和管理本地 Claude session
3. 向 Host Bridge 转发权限请求（通过 Feishu 弹窗确认）

架构：
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Terminal CLI    │───►│ Local Bridge    │───►│ Host Bridge     │
│ (用户输入)       │    │ (:8082)         │    │ (:8080)         │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                                      │
                                                      ▼
                                               ┌─────────────────┐
                                               │ Feishu 权限卡片  │
                                               └─────────────────┘
"""

from src.local_session_bridge.server import LocalSessionBridge
from src.local_session_bridge.claude_client import LocalClaudeClient

__all__ = ["LocalSessionBridge", "LocalClaudeClient"]