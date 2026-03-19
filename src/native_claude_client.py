"""
原生 Claude CLI 客户端

提供两种模式：
1. PTY 模式：完整的交互式体验
2. Print 模式：每条消息独立进程（更稳定，推荐）

支持：
- 权限确认双向处理（CLI 和飞书）
- 飞书同步（提醒模式/同步模式）
"""
import asyncio
import fcntl
import json
import logging
import os
import pty
import re
import signal
import struct
import termios
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)


# ============================================================================
# 权限请求检测模式
# ============================================================================

PERMISSION_PATTERNS = [
    # Claude CLI 标准权限请求格式
    r"Claude wants to use (\w+)(?:\s+with\s+.*)?\.?\s*(?:Allow|Approve)\?",
    r"Permission required for (\w+)",
    r"(\w+) requires confirmation",
    r"Tool call: (\w+).*\n.*\?\s*\[(?:y/n|Y/N)\]",
]


# ============================================================================
# 事件类型
# ============================================================================

class NativeEventType(str, Enum):
    """原生客户端事件类型"""
    STATUS = "status"               # 状态更新
    CONTENT = "content"             # 内容片段
    TOOL_CALL = "tool_call"         # 工具调用
    PERMISSION_REQUEST = "permission_request"  # 权限请求
    COMPLETE = "complete"           # 完成
    ERROR = "error"                 # 错误
    RAW_OUTPUT = "raw_output"       # 原始输出（PTY 模式）


@dataclass
class NativeEvent:
    """原生客户端事件"""
    event_type: NativeEventType
    data: dict
    timestamp: float = field(default_factory=lambda: __import__('time').time())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ============================================================================
# PTY 模式客户端
# ============================================================================

class NativeClaudePTYClient:
    """
    原生 Claude CLI 客户端 (PTY 模式)

    使用 PTY 直接运行交互式 claude CLI。
    """

    def __init__(
        self,
        session_id: str = None,
        working_dir: str = None,
        on_output: callable = None,
        raw_mode: bool = False,  # 原始模式：不启动内部读取任务
    ):
        """
        初始化客户端

        Args:
            session_id: 恢复的会话 ID
            working_dir: 工作目录
            on_output: 输出回调 (output: str) -> None
            raw_mode: 原始模式，不启动内部读取任务（用于外部直接读取）
        """
        self.session_id = session_id
        self.working_dir = working_dir or os.getcwd()
        self.on_output = on_output
        self.raw_mode = raw_mode

        self._master_fd = None
        self._slave_fd = None
        self._process_pid = None
        self._output_task = None
        self._running = False

    async def start(self):
        """启动 Claude CLI 进程"""
        # 创建 PTY
        self._master_fd, self._slave_fd = pty.openpty()

        # 获取当前终端大小
        try:
            import shutil
            cols, rows = shutil.get_terminal_size()
        except Exception:
            cols, rows = 80, 24

        # 设置终端大小
        winsize = struct.pack('HHHH', rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

        # Fork 子进程
        pid = os.fork()

        if pid == 0:
            # 子进程
            os.setsid()

            # 设置控制终端
            fcntl.ioctl(self._slave_fd, termios.TIOCSCTTY, 0)

            # 重定向 stdin/stdout/stderr
            os.dup2(self._slave_fd, 0)
            os.dup2(self._slave_fd, 1)
            os.dup2(self._slave_fd, 2)

            # 关闭不需要的 fd
            os.close(self._master_fd)
            os.close(self._slave_fd)

            # 切换工作目录
            os.chdir(self.working_dir)

            # 设置环境变量
            os.environ['TERM'] = 'xterm-256color'

            # 构建 claude 命令
            args = ['claude']
            if self.session_id:
                args.extend(['--resume', self.session_id])

            # 执行 claude
            os.execvp('claude', args)

        else:
            # 父进程
            self._process_pid = pid
            os.close(self._slave_fd)
            self._slave_fd = None

            # 设置 master fd 为非阻塞
            flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            self._running = True

            # 仅在非原始模式下启动输出读取任务
            if not self.raw_mode:
                self._output_task = asyncio.create_task(self._read_output())

            logger.info(f"Claude CLI 已启动 (PID: {pid}, raw_mode={self.raw_mode})")

    async def _read_output(self):
        """读取 PTY 输出"""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # 使用 asyncio 读取
                data = await loop.run_in_executor(
                    None,
                    self._blocking_read,
                    1024
                )

                if data:
                    output = data.decode('utf-8', errors='replace')
                    if self.on_output:
                        self.on_output(output)

            except Exception as e:
                if self._running:
                    logger.debug(f"读取输出失败: {e}")
                break

    def _blocking_read(self, size: int) -> bytes:
        """阻塞读取"""
        import select
        ready, _, _ = select.select([self._master_fd], [], [], 0.1)
        if ready:
            return os.read(self._master_fd, size)
        return b''

    def write(self, data: str):
        """写入数据到 PTY"""
        if self._master_fd is not None:
            os.write(self._master_fd, data.encode('utf-8'))

    def resize(self, rows: int, cols: int):
        """调整终端大小"""
        if self._master_fd is not None:
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    async def stop(self):
        """停止进程"""
        self._running = False

        if self._output_task:
            self._output_task.cancel()
            try:
                await self._output_task
            except asyncio.CancelledError:
                pass

        if self._process_pid:
            try:
                os.kill(self._process_pid, signal.SIGTERM)
                os.waitpid(self._process_pid, 0)
            except Exception as e:
                logger.warning(f"停止进程失败: {e}")

        if self._master_fd is not None:
            os.close(self._master_fd)
            self._master_fd = None

        logger.info("Claude CLI 已停止")


class NativeClaudePrintClient:
    """
    原生 Claude CLI 客户端 (Print 模式)

    使用 --print 模式，每条消息启动一个新进程。
    支持 --resume 恢复会话。
    """

    def __init__(
        self,
        session_id: str = None,
        working_dir: str = None,
    ):
        """
        初始化客户端

        Args:
            session_id: 恢复的会话 ID
            working_dir: 工作目录
        """
        self.session_id = session_id
        self.working_dir = working_dir or os.getcwd()

    async def chat(self, message: str) -> dict:
        """
        发送消息

        Args:
            message: 用户消息

        Returns:
            dict: 包含 response, session_id 等
        """
        import json

        cmd = ['claude', '--print', '--output-format', 'stream-json', '--verbose']

        if self.session_id:
            cmd.extend(['--resume', self.session_id])

        logger.info(f"执行: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir,
        )

        # 发送消息
        proc.stdin.write(message.encode())
        proc.stdin.close()
        await proc.stdin.drain()

        # 读取输出
        lines = []
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            lines.append(line.decode())

        await proc.wait()

        # 解析结果
        result = {
            "response": "",
            "session_id": None,
            "cost": 0,
            "raw_events": [],
        }

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                result["raw_events"].append(event)

                if event.get("type") == "system":
                    result["session_id"] = event.get("session_id")

                elif event.get("type") == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            result["response"] += block.get("text", "")

                elif event.get("type") == "result":
                    result["session_id"] = event.get("session_id")
                    result["cost"] = event.get("total_cost_usd", 0)
                    if not result["response"]:
                        result["response"] = event.get("result", "")

            except json.JSONDecodeError:
                continue

        # 更新 session_id
        if result["session_id"]:
            self.session_id = result["session_id"]

        return result


# ============================================================================
# 权限处理器
# ============================================================================

class PermissionHandler:
    """
    权限确认双向处理器

    支持 CLI 和飞书两个渠道的权限确认：
    1. 检测原生 CLI 的权限请求
    2. 同时发送飞书提醒
    3. 等待任一渠道确认
    """

    def __init__(
        self,
        bridge_url: str = None,
        chat_id: str = None,
        on_cli_prompt: Callable[[str, dict], None] = None,
    ):
        """
        初始化权限处理器

        Args:
            bridge_url: Bridge URL（用于飞书同步）
            chat_id: 飞书群聊 ID
            on_cli_prompt: CLI 权限提示回调
        """
        self.bridge_url = bridge_url
        self.chat_id = chat_id
        self.on_cli_prompt = on_cli_prompt

        # 权限确认 Future
        self._permission_future: Optional[asyncio.Future] = None

        # CLI 输入队列（用于飞书注入）
        self._cli_input_queue: asyncio.Queue = asyncio.Queue()

    def detect_permission_request(self, output: str) -> Optional[dict]:
        """
        检测权限请求

        Args:
            output: CLI 输出

        Returns:
            检测到权限请求时返回 {"tool_name": str, "tool_input": dict}
            否则返回 None
        """
        for pattern in PERMISSION_PATTERNS:
            match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
            if match:
                tool_name = match.group(1)
                # 尝试提取工具输入（如果有的话）
                tool_input = {}
                input_match = re.search(r"input:\s*(.+?)(?:\n|$)", output, re.DOTALL)
                if input_match:
                    try:
                        tool_input = json.loads(input_match.group(1))
                    except json.JSONDecodeError:
                        tool_input = {"raw": input_match.group(1)}

                return {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "raw_output": output,
                }

        return None

    async def request_dual_confirmation(
        self,
        tool_name: str,
        tool_input: dict,
        timeout: float = 60.0,
    ) -> bool:
        """
        双向权限确认

        同时启动两个等待任务：
        1. CLI 输入监听
        2. 飞书确认等待

        任一返回即结束

        Args:
            tool_name: 工具名称
            tool_input: 工具输入
            timeout: 超时时间（秒）

        Returns:
            是否批准
        """
        self._permission_future = asyncio.Future()

        # 发送飞书提醒
        await self._send_feishu_permission_request(tool_name, tool_input)

        # 等待任一渠道确认
        try:
            result = await asyncio.wait_for(
                self._permission_future,
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"权限确认超时: {tool_name}")
            return False
        finally:
            self._permission_future = None

    async def _send_feishu_permission_request(self, tool_name: str, tool_input: dict):
        """发送飞书权限请求"""
        if not self.bridge_url or not self.chat_id:
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                }

                async with session.post(
                    f"{self.bridge_url}/permission/request",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"发送飞书权限请求失败: HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"发送飞书权限请求异常: {e}")

    def resolve_permission(self, approved: bool):
        """
        解析权限确认结果

        Args:
            approved: 是否批准
        """
        if self._permission_future and not self._permission_future.done():
            self._permission_future.set_result(approved)

    async def inject_cli_response(self, response: str):
        """
        注入 CLI 响应（来自飞书）

        Args:
            response: 响应内容（"y" 或 "n"）
        """
        await self._cli_input_queue.put(response)

    async def get_cli_input(self, timeout: float = None) -> Optional[str]:
        """
        获取 CLI 输入（可能是用户输入或飞书注入）

        Args:
            timeout: 超时时间

        Returns:
            输入内容
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._cli_input_queue.get(),
                    timeout=timeout
                )
            else:
                return await self._cli_input_queue.get()
        except asyncio.TimeoutError:
            return None


# ============================================================================
# 同步处理器
# ============================================================================

class SyncHandler:
    """
    飞书同步处理器

    支持两种模式：
    - 模式A (notify): 只同步关键事件
    - 模式B (sync): 同步所有内容
    """

    def __init__(
        self,
        bridge_url: str = None,
        chat_id: str = None,
        mode: str = "notify",  # "notify" or "sync"
    ):
        """
        初始化同步处理器

        Args:
            bridge_url: Bridge URL
            chat_id: 飞书群聊 ID
            mode: 同步模式
        """
        self.bridge_url = bridge_url
        self.chat_id = chat_id
        self.mode = mode

        # 模式A 只同步这些事件
        self._notify_events = {
            NativeEventType.PERMISSION_REQUEST,
            NativeEventType.COMPLETE,
            NativeEventType.ERROR,
        }

    async def sync_event(self, event: NativeEvent):
        """
        同步事件到飞书

        Args:
            event: 事件对象
        """
        if not self.bridge_url or not self.chat_id:
            return

        # 根据模式过滤事件
        if self.mode == "notify" and event.event_type not in self._notify_events:
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "event": event.to_dict(),
                }

                async with session.post(
                    f"{self.bridge_url}/terminal/sync",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    pass  # 忽略响应

        except Exception as e:
            logger.debug(f"同步事件失败: {e}")


# ============================================================================
# 统一的原生客户端
# ============================================================================

class NativeClaudeClient:
    """
    统一的原生 Claude CLI 客户端

    支持两种模式：
    - PTY 模式：完整的交互式体验
    - Print 模式：每条消息独立进程（推荐）

    支持权限确认的双向处理：
    - 解析原生 CLI 的权限请求
    - 同时通知飞书
    - 任一渠道确认即可

    支持飞书同步：
    - 模式A (notify): 只同步关键事件
    - 模式B (sync): 同步所有内容
    """

    def __init__(
        self,
        session_id: str = None,
        working_dir: str = None,
        mode: str = "print",  # "pty" or "print"
        sync_mode: str = "notify",  # "notify" or "sync"
        bridge_url: str = None,  # 用于飞书同步
        chat_id: str = None,
        on_event: Callable[[NativeEvent], None] = None,
        raw_pty: bool = False,  # PTY 原始模式（外部直接读取）
    ):
        """
        初始化客户端

        Args:
            session_id: 恢复的会话 ID
            working_dir: 工作目录
            mode: 运行模式（"pty" 或 "print"）
            sync_mode: 同步模式（"notify" 或 "sync"）
            bridge_url: Bridge URL（用于飞书同步）
            chat_id: 飞书群聊 ID
            on_event: 事件回调
            raw_pty: PTY 原始模式，不启动内部读取任务（用于外部直接读取 PTY）
        """
        self.session_id = session_id
        self.working_dir = working_dir or os.getcwd()
        self.mode = mode
        self.sync_mode = sync_mode
        self.bridge_url = bridge_url
        self.chat_id = chat_id
        self.on_event = on_event
        self.raw_pty = raw_pty

        # 底层客户端
        self._pty_client: Optional[NativeClaudePTYClient] = None
        self._print_client: Optional[NativeClaudePrintClient] = None

        # 权限处理器
        self._permission_handler = PermissionHandler(
            bridge_url=bridge_url,
            chat_id=chat_id,
        )

        # 同步处理器
        self._sync_handler = SyncHandler(
            bridge_url=bridge_url,
            chat_id=chat_id,
            mode=sync_mode,
        )

        # 运行状态
        self._running = False

    async def start(self):
        """启动客户端"""
        if self.mode == "pty":
            self._pty_client = NativeClaudePTYClient(
                session_id=self.session_id,
                working_dir=self.working_dir,
                on_output=self._handle_pty_output if not self.raw_pty else None,
                raw_mode=self.raw_pty,
            )
            await self._pty_client.start()
        else:
            self._print_client = NativeClaudePrintClient(
                session_id=self.session_id,
                working_dir=self.working_dir,
            )

        self._running = True
        logger.info(f"NativeClaudeClient 已启动 (mode={self.mode})")

    async def stop(self):
        """停止客户端"""
        self._running = False

        if self._pty_client:
            await self._pty_client.stop()
            self._pty_client = None

        self._print_client = None
        logger.info("NativeClaudeClient 已停止")

    def _handle_pty_output(self, output: str):
        """处理 PTY 输出"""
        # 发送原始输出事件
        self._emit_event(NativeEvent(
            event_type=NativeEventType.RAW_OUTPUT,
            data={"output": output},
        ))

        # 检测权限请求
        permission = self._permission_handler.detect_permission_request(output)
        if permission:
            self._emit_event(NativeEvent(
                event_type=NativeEventType.PERMISSION_REQUEST,
                data=permission,
            ))

    def _emit_event(self, event: NativeEvent):
        """发送事件"""
        # 回调
        if self.on_event:
            self.on_event(event)

        # 异步同步到飞书
        asyncio.create_task(self._sync_handler.sync_event(event))

    async def chat(self, message: str) -> dict:
        """
        发送消息（同步模式）

        Args:
            message: 用户消息

        Returns:
            响应结果
        """
        if self.mode == "pty":
            # PTY 模式：写入消息
            if self._pty_client:
                self._pty_client.write(message + "\n")
                return {"status": "sent"}
            return {"status": "error", "error": "PTY client not initialized"}
        else:
            # Print 模式：执行命令
            if self._print_client:
                result = await self._print_client.chat(message)

                # 更新 session_id
                if result.get("session_id"):
                    self.session_id = result["session_id"]

                return result
            return {"status": "error", "error": "Print client not initialized"}

    async def chat_stream(self, message: str) -> AsyncGenerator[NativeEvent, None]:
        """
        发送消息（流式模式）

        Args:
            message: 用户消息

        Yields:
            事件对象
        """
        if self.mode == "pty":
            # PTY 模式：发送消息并监听输出
            if self._pty_client:
                self._pty_client.write(message + "\n")
                # PTY 输出通过回调处理，这里不 yield
                yield NativeEvent(
                    event_type=NativeEventType.STATUS,
                    data={"text": "消息已发送"},
                )
            return
        else:
            # Print 模式：执行命令并解析流式输出
            if not self._print_client:
                yield NativeEvent(
                    event_type=NativeEventType.ERROR,
                    data={"message": "Print client not initialized"},
                )
                return

            # 发送状态更新
            yield NativeEvent(
                event_type=NativeEventType.STATUS,
                data={"text": "正在处理..."},
            )

            # 执行命令
            result = await self._print_client.chat(message)

            # 更新 session_id
            if result.get("session_id"):
                self.session_id = result["session_id"]

            # 解析原始事件
            for raw_event in result.get("raw_events", []):
                event_type = raw_event.get("type")

                if event_type == "assistant":
                    # 助手消息
                    msg = raw_event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            yield NativeEvent(
                                event_type=NativeEventType.CONTENT,
                                data={"text": block.get("text", "")},
                            )

                        elif block.get("type") == "tool_use":
                            # 工具调用
                            yield NativeEvent(
                                event_type=NativeEventType.TOOL_CALL,
                                data={
                                    "name": block.get("name", ""),
                                    "input": block.get("input", {}),
                                },
                            )

                elif event_type == "result":
                    # 完成
                    yield NativeEvent(
                        event_type=NativeEventType.COMPLETE,
                        data={
                            "session_id": raw_event.get("session_id", ""),
                            "content": raw_event.get("result", ""),
                            "cost": raw_event.get("total_cost_usd", 0),
                        },
                    )

            # 如果没有 result 事件，手动发送完成
            if not any(e.get("type") == "result" for e in result.get("raw_events", [])):
                yield NativeEvent(
                    event_type=NativeEventType.COMPLETE,
                    data={
                        "session_id": self.session_id or "",
                        "content": result.get("response", ""),
                    },
                )

    def write(self, data: str):
        """
        写入数据（PTY 模式）

        Args:
            data: 数据内容
        """
        if self._pty_client:
            self._pty_client.write(data)

    async def resolve_permission(self, approved: bool):
        """
        解析权限确认结果（来自飞书）

        Args:
            approved: 是否批准
        """
        self._permission_handler.resolve_permission(approved)

        # 如果是 PTY 模式，注入响应
        if self._pty_client and approved:
            self._pty_client.write("y\n")
        elif self._pty_client and not approved:
            self._pty_client.write("n\n")