"""
Terminal CLI 客户端 - 原生 Claude CLI 版本

直接运行原生 `claude` CLI，获得完全原生的会话体验。

特性：
- 原生 PTY CLI 体验
- 双向权限确认（CLI 和飞书）
- 两种同步模式：
  - 模式A (notify): 飞书只提醒权限请求和状态更新
  - 模式B (sync): 完全双向同步所有输出
- 飞书消息注入到 CLI

使用方式：
    # 启动终端（自动创建群聊）
    python -m src.terminal_client

    # 指定同步模式
    python -m src.terminal_client --sync-mode notify   # 默认，只提醒
    python -m src.terminal_client --sync-mode sync     # 完全同步

    # 恢复会话
    python -m src.terminal_client --session <session_id>

    # 保持群聊不退出
    python -m src.terminal_client --keep-chat
"""
import asyncio
import logging
import os
import re
import signal
import socket
import sys
import termios
import time
import tty
from typing import Optional

import aiohttp

from src.native_claude_client import NativeClaudeClient, NativeEventType, NativeEvent

logger = logging.getLogger(__name__)


# ============================================================================
# 状态检测模式
# ============================================================================

STATUS_PATTERNS = [
    # Claude CLI 状态输出模式
    (r"Reading\s+(.+?)\s*\.\.\.", "reading"),
    (r"Writing\s+(.+?)\s*\.\.\.", "writing"),
    (r"Editing\s+(.+?)\s*\.\.\.", "editing"),
    (r"Running:\s+(.+)", "running"),
    (r"Searching\s+for\s+(.+)", "searching"),
    (r"Analyzing\s+(.+)", "analyzing"),
    (r"Tool call:\s+(\w+)", "tool_call"),
]


class TerminalClaudeClient:
    """
    Terminal CLI 客户端

    直接运行原生 Claude CLI (PTY 模式)，同时同步到飞书群聊。

    支持：
    - 原生 PTY CLI 体验
    - 双向权限确认（CLI 和飞书）
    - 两种同步模式（notify/sync）
    - 飞书消息注入
    """

    def __init__(
        self,
        bridge_url: str = "http://localhost:8082",
        session_id: str = None,
        keep_chat: bool = False,
        user_open_id: str = None,
        sync_mode: str = "notify",  # "notify" or "sync"
    ):
        """
        初始化客户端

        Args:
            bridge_url: Local Session Bridge URL（用于飞书同步）
            session_id: 恢复的会话 ID
            keep_chat: 退出时是否保持群聊（默认 False，解散群聊）
            user_open_id: 用户 open_id（用于创建群聊）
            sync_mode: 同步模式（"notify" 只提醒，"sync" 完全同步）
        """
        self.bridge_url = bridge_url
        self.session_id = session_id
        self.keep_chat = keep_chat
        self.user_open_id = user_open_id
        self.sync_mode = sync_mode

        # 终端唯一标识
        self.terminal_id = self._generate_terminal_id()

        # 会话状态
        self._chat_id: Optional[str] = None
        self._session_created = False

        # 原生 Claude 客户端
        self._claude: Optional[NativeClaudeClient] = None

        # WebSocket 连接（用于接收飞书消息）
        self._ws_session: Optional[aiohttp.ClientSession] = None
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None

        # 输入队列（飞书消息注入）
        self._input_queue: asyncio.Queue = asyncio.Queue()

        # 运行标志
        self._running = False

    def _generate_terminal_id(self) -> str:
        """生成终端唯一标识：hostname-timestamp"""
        hostname = socket.gethostname()
        timestamp = int(time.time())
        return f"{hostname}-{timestamp}"

    async def _create_feishu_session(self) -> bool:
        """创建飞书群聊会话"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "terminal_id": self.terminal_id,
                    "user_open_id": self.user_open_id,
                    "session_id": self.session_id,
                }

                async with session.post(
                    f"{self.bridge_url}/terminal/create",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"创建会话失败: HTTP {resp.status} - {error}")
                        return False

                    result = await resp.json()
                    if not result.get("success"):
                        logger.error(f"创建会话失败: {result.get('error')}")
                        return False

                    self._chat_id = result.get("chat_id")
                    self._session_created = True

                    logger.info(f"飞书群聊创建成功: terminal={self.terminal_id}, chat={self._chat_id}")
                    return True

        except Exception as e:
            logger.error(f"创建飞书会话异常: {e}")
            return False

    async def _close_feishu_session(self) -> bool:
        """关闭飞书群聊会话"""
        if not self._session_created:
            return True

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "terminal_id": self.terminal_id,
                    "disband_chat": not self.keep_chat,
                }

                async with session.post(
                    f"{self.bridge_url}/terminal/close",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"关闭会话失败: HTTP {resp.status}")
                        return False

                    result = await resp.json()
                    logger.info(f"飞书会话已关闭: {result.get('message')}")
                    return True

        except Exception as e:
            logger.error(f"关闭飞书会话异常: {e}")
            return False
        finally:
            self._session_created = False

    async def _sync_output(self, content: str):
        """同步输出到飞书群聊"""
        if not self._session_created or not self._chat_id:
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "terminal_id": self.terminal_id,
                    "type": "output",
                    "content": content,
                }

                async with session.post(
                    f"{self.bridge_url}/terminal/sync",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    pass  # 忽略响应，不阻塞主流程

        except Exception as e:
            logger.debug(f"同步输出失败: {e}")

    async def _sync_status(self, status: str, details: dict = None):
        """同步状态到飞书群聊"""
        if not self._session_created or not self._chat_id:
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "terminal_id": self.terminal_id,
                    "type": "status",
                    "status": status,
                    "details": details or {},
                }

                async with session.post(
                    f"{self.bridge_url}/terminal/sync",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    pass

        except Exception as e:
            logger.debug(f"同步状态失败: {e}")

    async def _connect_websocket(self):
        """连接到 Bridge 的 WebSocket（用于接收飞书消息）"""
        try:
            ws_url = self.bridge_url.replace("http://", "ws://").replace("https://", "wss://")
            ws_url = f"{ws_url}/ws"

            self._ws_session = aiohttp.ClientSession()
            self._ws = await self._ws_session.ws_connect(ws_url)

            # 注册终端
            await self._ws.send_json({
                "type": "register",
                "terminal_id": self.terminal_id,
            })

            logger.info(f"WebSocket 已连接: {ws_url}")

            # 接收消息循环
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = msg.json()
                        if data.get("type") == "feishu_message":
                            # 注入到输入队列
                            content = data.get("content", "")
                            await self._input_queue.put(content)
                            print(f"\n[飞书消息注入] {content}")
                        elif data.get("type") == "permission_response":
                            # 权限确认响应
                            approved = data.get("approved", False)
                            if self._claude:
                                await self._claude.resolve_permission(approved)
                    except Exception as e:
                        logger.debug(f"WebSocket 消息解析失败: {e}")

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket 错误: {self._ws.exception()}")
                    break

        except Exception as e:
            logger.warning(f"WebSocket 连接失败: {e}")
        finally:
            if self._ws:
                await self._ws.close()
            if self._ws_session:
                await self._ws_session.close()

    def _on_claude_event(self, event: NativeEvent):
        """处理 Claude 事件回调"""
        if event.event_type == NativeEventType.PERMISSION_REQUEST:
            # 权限请求
            tool_name = event.data.get("tool_name", "")
            print(f"\n[权限请求] Claude 想要使用 {tool_name}")
            print("请在 CLI 输入 y/n 确认，或在飞书群聊中点击按钮")
        elif event.event_type == NativeEventType.TOOL_CALL:
            # 工具调用
            tool_name = event.data.get("name", "")
            if self.sync_mode == "sync":
                print(f"\n[工具调用] {tool_name}")
        elif event.event_type == NativeEventType.RAW_OUTPUT:
            # 原始输出（PTY 模式）
            output = event.data.get("output", "")
            print(output, end="", flush=True)

    async def run_interactive(self):
        """运行交互模式"""
        print("=" * 60)
        print("Terminal Claude Client (Native PTY CLI)")
        print("=" * 60)

        # 启动会话
        print(f"终端 ID: {self.terminal_id}")
        print(f"同步模式: {self.sync_mode} ({'只提醒权限/状态' if self.sync_mode == 'notify' else '完全同步'})")
        print("正在创建飞书群聊...", end=" ", flush=True)

        success = await self._create_feishu_session()
        if success:
            print(f"✅ 已绑定群聊: {self._chat_id[:8]}...")
        else:
            print("❌ 创建群聊失败")
            print("提示：请检查 Local Bridge 是否正常运行")
            print("将继续运行，但不会同步到飞书")

        if self.session_id:
            print(f"会话: {self.session_id[:8]}...")

        print()

        # 初始化原生客户端（固定使用 PTY 模式）
        self._claude = NativeClaudeClient(
            session_id=self.session_id,
            working_dir=os.getcwd(),
            mode="pty",
            sync_mode=self.sync_mode,
            bridge_url=self.bridge_url,
            chat_id=self._chat_id,
            on_event=self._on_claude_event,
            raw_pty=True,  # PTY 模式下使用原始模式，外部直接读取
        )

        await self._claude.start()

        # 同步状态
        await self._sync_status("started", {
            "terminal_id": self.terminal_id,
            "message": "终端已启动，等待输入...",
            "session_id": self.session_id,
            "sync_mode": self.sync_mode,
        })

        # 启动 WebSocket 连接（用于接收飞书消息）
        self._running = True
        self._ws_task = asyncio.create_task(self._connect_websocket())

        try:
            # PTY 模式：直接终端透传
            await self._run_pty_mode()

        finally:
            self._running = False

            # 取消 WebSocket 任务
            if self._ws_task:
                self._ws_task.cancel()
                try:
                    await self._ws_task
                except asyncio.CancelledError:
                    pass

            print("\n正在关闭会话...")

            # 同步状态
            await self._sync_status("stopped", {
                "terminal_id": self.terminal_id,
                "message": "终端已关闭",
            })

            # 停止 Claude 客户端
            if self._claude:
                await self._claude.stop()

            # 关闭飞书会话
            await self._close_feishu_session()
            print("再见！")

    async def _run_pty_mode(self):
        """
        PTY 模式：直接终端透传

        将用户终端直接连接到 Claude CLI 进程，提供完全原生的交互体验。
        同时根据同步模式将输出转发到飞书。
        """
        import select
        import threading

        print("PTY 模式：直接连接到 Claude CLI...")
        print("按 Ctrl+C 退出")
        print("-" * 60)

        # 保存原始终端设置
        old_settings = termios.tcgetattr(sys.stdin.fileno())

        # 设置终端为原始模式
        tty.setraw(sys.stdin.fileno())

        # 获取底层 PTY 客户端
        pty_client = self._claude._pty_client
        if not pty_client:
            print("错误：PTY 客户端未初始化")
            return

        master_fd = pty_client._master_fd
        stdin_fd = sys.stdin.fileno()

        # 终端大小变化处理
        def handle_sigwinch(signum, frame):
            import shutil
            try:
                cols, rows = shutil.get_terminal_size()
                pty_client.resize(rows, cols)
            except Exception:
                pass

        old_sigwinch = signal.signal(signal.SIGWINCH, handle_sigwinch)

        # 获取 asyncio loop（用于从线程中调度协程）
        loop = asyncio.get_event_loop()

        # 运行标志
        running = True

        # 用于检测权限请求的缓冲区
        output_buffer = ""

        def pty_relay_loop():
            """同步 PTY 转发循环（在独立线程运行）"""
            nonlocal running, output_buffer

            while running:
                try:
                    # 使用 select 同时监听 stdin 和 master_fd
                    ready_read, _, _ = select.select([stdin_fd, master_fd], [], [], 0.05)

                    for fd in ready_read:
                        if fd == stdin_fd:
                            # stdin → PTY
                            data = os.read(stdin_fd, 1024)
                            if data:
                                os.write(master_fd, data)
                        elif fd == master_fd:
                            # PTY → stdout + Feishu sync
                            data = os.read(master_fd, 4096)
                            if data:
                                # 输出到本地终端
                                sys.stdout.buffer.write(data)
                                sys.stdout.buffer.flush()

                                # 异步同步到飞书
                                try:
                                    asyncio.run_coroutine_threadsafe(
                                        _sync_pty_output(data),
                                        loop
                                    )
                                except Exception as e:
                                    logger.debug(f"同步输出失败: {e}")

                except OSError:
                    break
                except Exception:
                    break

        # 启动 PTY 转发线程
        relay_thread = threading.Thread(target=pty_relay_loop, daemon=True)
        relay_thread.start()

        async def _sync_pty_output(data: bytes):
            """根据同步模式处理 PTY 输出"""
            if not self._session_created or not self._chat_id:
                return

            output = data.decode('utf-8', errors='replace')

            if self.sync_mode == "sync":
                # 同步模式：转发所有输出
                await self._sync_output(output)
            else:
                # Notify 模式：检测并转发关键事件
                # 1. 检测权限请求
                permission = self._detect_permission_request(output)
                if permission:
                    await self._sync_status("permission_request", permission)
                    return

                # 2. 检测状态更新
                status = self._detect_status_update(output)
                if status:
                    await self._sync_status(status["type"], status["data"])

        try:
            # 主线程等待退出信号或处理飞书消息注入
            while self._running and relay_thread.is_alive():
                try:
                    # 检查飞书消息注入
                    feishu_msg = await asyncio.wait_for(
                        self._input_queue.get(),
                        timeout=0.1
                    )
                    # 注入到 PTY（使用 \r 模拟回车键）
                    os.write(master_fd, feishu_msg.encode() + b"\r")
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

        except KeyboardInterrupt:
            pass
        finally:
            running = False
            self._running = False

            # 等待转发线程结束
            relay_thread.join(timeout=1.0)

            # 恢复终端设置
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)

            # 恢复信号处理
            signal.signal(signal.SIGWINCH, old_sigwinch)

            print("\n" + "-" * 60)

    def _detect_permission_request(self, output: str) -> Optional[dict]:
        """
        检测权限请求

        Args:
            output: PTY 输出

        Returns:
            检测到权限请求时返回 {"tool_name": str, "tool_input": dict}
            否则返回 None
        """
        from src.native_claude_client import PERMISSION_PATTERNS

        for pattern in PERMISSION_PATTERNS:
            match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
            if match:
                tool_name = match.group(1)
                # 尝试提取工具输入（如果有的话）
                tool_input = {}
                input_match = re.search(r"input:\s*(.+?)(?:\n|$)", output, re.DOTALL)
                if input_match:
                    try:
                        tool_input = __import__('json').loads(input_match.group(1))
                    except Exception:
                        tool_input = {"raw": input_match.group(1)}

                return {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "raw_output": output,
                }

        return None

    def _detect_status_update(self, output: str) -> Optional[dict]:
        """
        检测状态更新

        Args:
            output: PTY 输出

        Returns:
            检测到状态更新时返回 {"type": str, "data": dict}
            否则返回 None
        """
        for pattern, status_type in STATUS_PATTERNS:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return {
                    "type": status_type,
                    "data": {"target": match.group(1).strip()}
                }
        return None


async def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Terminal Claude Client (Native PTY CLI)")
    parser.add_argument("--session", type=str, help="恢复会话 ID")
    parser.add_argument("--bridge", type=str, default="http://localhost:8082", help="Bridge URL")
    parser.add_argument("--user", type=str, help="用户 open_id")
    parser.add_argument("--keep-chat", action="store_true", help="退出时保持群聊")
    parser.add_argument("--sync-mode", type=str, choices=["notify", "sync"], default="notify",
                        help="同步模式: notify(只提醒权限/状态) 或 sync(完全同步)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    # 配置日志
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 从环境变量或参数获取 user_open_id
    user_open_id = args.user or os.getenv("FEISHU_USER_OPEN_ID")

    client = TerminalClaudeClient(
        bridge_url=args.bridge,
        session_id=args.session,
        keep_chat=args.keep_chat,
        user_open_id=user_open_id,
        sync_mode=args.sync_mode,
    )

    await client.run_interactive()


if __name__ == "__main__":
    asyncio.run(main())