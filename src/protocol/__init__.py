"""
Host-Guest 通信协议

基于 JSON-RPC 2.0 规范的请求/响应模型
用于 Host Bridge 和 Guest Proxy 之间的通信
"""
from dataclasses import dataclass, field
from typing import Optional, Any, Union
from enum import Enum
import json
import uuid
import time


class RequestMethod(str, Enum):
    """请求方法枚举"""
    # 会话管理
    CHAT = "chat"                       # 发送消息（同步）
    CHAT_STREAM = "chat_stream"         # 发送消息（流式响应）

    # 会话控制
    CREATE_SESSION = "create_session"   # 创建新会话
    END_SESSION = "end_session"         # 结束会话

    # 权限确认
    PERMISSION_REQUEST = "permission"   # 权限请求（Guest -> Host）
    PERMISSION_RESPONSE = "permission_response"  # 权限响应（Host -> Guest）

    # 状态更新
    STATUS_UPDATE = "status_update"     # 状态更新（Guest -> Host）

    # 健康检查
    HEALTH_CHECK = "health_check"       # 健康检查
    HEARTBEAT = "heartbeat"             # 心跳

    # 注册
    REGISTER = "register"               # Guest 向 Host 注册
    UNREGISTER = "unregister"           # Guest 注销

    # 会话清理
    CLEANUP_SESSION = "cleanup_session" # 清理会话（Host -> Guest）

    # Terminal 注册
    REGISTER_TERMINAL = "register_terminal"  # Terminal 注册请求
    BIND_TERMINAL = "bind_terminal"          # 绑定 Terminal 到 chat_id


class ResponseStatus(str, Enum):
    """响应状态枚举"""
    COMPLETED = "completed"             # 任务完成
    IN_PROGRESS = "in_progress"         # 进行中
    FAILED = "failed"                   # 任务失败
    TIMEOUT = "timeout"                 # 超时
    PERMISSION_DENIED = "permission_denied"  # 权限被拒绝


class StreamEventType(str, Enum):
    """流式事件类型枚举"""
    HEARTBEAT = "heartbeat"      # 心跳（保持连接）
    STATUS = "status"            # 状态更新
    TOOL_CALL = "tool_call"      # 工具调用
    CONTENT = "content"          # 内容片段
    COMPLETE = "complete"        # 完成
    ERROR = "error"              # 错误


@dataclass
class StreamEvent:
    """
    流式事件

    用于 Guest Proxy 向 Host Bridge 发送流式状态更新

    Example:
        {
            "event_type": "status",
            "data": {"text": "正在读取文件..."},
            "timestamp": 1234567890.123
        }
    """
    event_type: StreamEventType
    data: dict
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> 'StreamEvent':
        return cls(
            event_type=StreamEventType(data["event_type"]),
            data=data.get("data", {}),
            timestamp=data.get("timestamp", time.time()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> 'StreamEvent':
        return cls.from_dict(json.loads(json_str))

    # 便捷工厂方法
    @classmethod
    def heartbeat(cls) -> 'StreamEvent':
        """创建心跳事件"""
        return cls(event_type=StreamEventType.HEARTBEAT, data={})

    @classmethod
    def status(cls, text: str, details: str = None) -> 'StreamEvent':
        """创建状态更新事件"""
        data = {"text": text}
        if details:
            data["details"] = details
        return cls(event_type=StreamEventType.STATUS, data=data)

    @classmethod
    def tool_call(cls, name: str, input: dict) -> 'StreamEvent':
        """创建工具调用事件"""
        return cls(event_type=StreamEventType.TOOL_CALL, data={"name": name, "input": input})

    @classmethod
    def content(cls, text: str) -> 'StreamEvent':
        """创建内容片段事件"""
        return cls(event_type=StreamEventType.CONTENT, data={"text": text})

    @classmethod
    def complete(cls, session_id: str, content: str = "") -> 'StreamEvent':
        """创建完成事件"""
        return cls(event_type=StreamEventType.COMPLETE, data={"session_id": session_id, "content": content})

    @classmethod
    def error(cls, message: str, error_type: str = None) -> 'StreamEvent':
        """创建错误事件"""
        data = {"message": message}
        if error_type:
            data["error_type"] = error_type
        return cls(event_type=StreamEventType.ERROR, data=data)


@dataclass
class JsonRpcRequest:
    """
    JSON-RPC 2.0 请求

    Example:
        {
            "jsonrpc": "2.0",
            "method": "chat",
            "params": {
                "message": "用户消息",
                "chat_id": "oc_xxx",
                "user_open_id": "ou_xxx"
            },
            "id": "req-123"
        }
    """
    method: str
    params: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        return {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
            "id": self.id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> 'JsonRpcRequest':
        return cls(
            method=data["method"],
            params=data.get("params", {}),
            id=data.get("id", str(uuid.uuid4())),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


@dataclass
class JsonRpcResponse:
    """
    JSON-RPC 2.0 响应

    Example (成功):
        {
            "jsonrpc": "2.0",
            "result": {
                "content": "回复内容",
                "status": "completed"
            },
            "id": "req-123"
        }

    Example (错误):
        {
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Invalid Request"
            },
            "id": "req-123"
        }
    """
    id: str
    result: Optional[dict] = None
    error: Optional[dict] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        resp = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.result is not None:
            resp["result"] = self.result
        if self.error is not None:
            resp["error"] = self.error
        return resp

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def success(cls, request_id: str, result: dict) -> 'JsonRpcResponse':
        return cls(id=request_id, result=result)

    @classmethod
    def create_error(cls, request_id: str, code: int, message: str) -> 'JsonRpcResponse':
        return cls(id=request_id, error={"code": code, "message": message})


# JSON-RPC 错误码
class ErrorCode:
    PARSE_ERROR = -32700          # 解析错误
    INVALID_REQUEST = -32600      # 无效请求
    METHOD_NOT_FOUND = -32601     # 方法未找到
    INVALID_PARAMS = -32602       # 无效参数
    INTERNAL_ERROR = -32603       # 内部错误

    # 自定义错误码
    PERMISSION_DENIED = -32001    # 权限被拒绝
    TIMEOUT = -32002              # 超时
    CONTAINER_NOT_FOUND = -32003  # 容器未找到
    SESSION_ERROR = -32004        # 会话错误


@dataclass
class ChatParams:
    """chat 方法的参数"""
    message: str
    chat_id: str
    user_open_id: str
    session_id: Optional[str] = None
    require_confirmation: bool = True

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "chat_id": self.chat_id,
            "user_open_id": self.user_open_id,
            "session_id": self.session_id,
            "require_confirmation": self.require_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ChatParams':
        return cls(
            message=data["message"],
            chat_id=data["chat_id"],
            user_open_id=data["user_open_id"],
            session_id=data.get("session_id"),
            require_confirmation=data.get("require_confirmation", True),
        )


@dataclass
class ChatResult:
    """chat 方法的结果"""
    content: str
    status: ResponseStatus
    session_id: str
    tool_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "status": self.status.value,
            "session_id": self.session_id,
            "tool_calls": self.tool_calls,
        }


@dataclass
class PermissionParams:
    """权限请求参数（Guest -> Host）"""
    session_id: str
    chat_id: str
    tool_name: str
    tool_input: dict

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "chat_id": self.chat_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PermissionParams':
        return cls(
            session_id=data["session_id"],
            chat_id=data["chat_id"],
            tool_name=data["tool_name"],
            tool_input=data["tool_input"],
        )


@dataclass
class PermissionResult:
    """权限响应结果（Host -> Guest）"""
    approved: bool
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "reason": self.reason,
        }


@dataclass
class StatusParams:
    """状态更新参数（Guest -> Host）"""
    chat_id: str
    status: str
    details: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class RegisterParams:
    """注册参数（Guest -> Host）"""
    container_name: str
    endpoint: str
    chat_id: str
    user_open_id: str

    def to_dict(self) -> dict:
        return {
            "container_name": self.container_name,
            "endpoint": self.endpoint,
            "chat_id": self.chat_id,
            "user_open_id": self.user_open_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RegisterParams':
        return cls(
            container_name=data["container_name"],
            endpoint=data["endpoint"],
            chat_id=data["chat_id"],
            user_open_id=data["user_open_id"],
        )


@dataclass
class BindTerminalParams:
    """绑定 Terminal 参数"""
    code: str
    chat_id: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "chat_id": self.chat_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'BindTerminalParams':
        return cls(
            code=data["code"],
            chat_id=data["chat_id"],
        )


@dataclass
class BindTerminalResult:
    """绑定 Terminal 结果"""
    success: bool
    message: Optional[str] = None
    endpoint: Optional[str] = None

    def to_dict(self) -> dict:
        result = {"success": self.success}
        if self.message:
            result["message"] = self.message
        if self.endpoint:
            result["endpoint"] = self.endpoint
        return result