"""
Terminal CLI 客户端

启动时自动创建飞书群聊，实现会话持久化。

使用方式：
    # 启动终端（自动创建群聊）
    python -m src.terminal_client

    # 恢复会话
    python -m src.terminal_client --session <session_id>

    # 保持群聊不退出
    python -m src.terminal_client --keep-chat
"""
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from typing import Optional

import aiohttp

from src.protocol import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)


class TerminalClaudeClient:
    """
    Terminal CLI 客户端

    启动时自动创建飞书群聊，退出时自动解散。
    """

    def __init__(
        self,
        bridge_url: str = "http://localhost:8082",
        session_id: str = None,
        keep_chat: bool = False,
        user_open_id: str = None,
    ):
        """
        初始化客户端

        Args:
            bridge_url: Local Session Bridge URL
            session_id: 恢复的会话 ID
            keep_chat: 退出时是否保持群聊（默认 False，解散群聊）
            user_open_id: 用户 open_id（用于创建群聊）
        """
        self.bridge_url = bridge_url
        self.session_id = session_id
        self.keep_chat = keep_chat
        self.user_open_id = user_open_id

        # 终端唯一标识
        self.terminal_id = self._generate_terminal_id()

        # 会话状态
        self._chat_id: Optional[str] = None
        self._session_created = False

    def _generate_terminal_id(self) -> str:
        """生成终端唯一标识：hostname-timestamp"""
        hostname = socket.gethostname()
        timestamp = int(time.time())
        return f"{hostname}-{timestamp}"

    async def start(self) -> bool:
        """启动终端会话，自动创建飞书群聊"""
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

                    logger.info(f"会话创建成功: terminal={self.terminal_id}, chat={self._chat_id}")
                    return True

        except Exception as e:
            logger.error(f"创建会话异常: {e}")
            return False

    async def close(self) -> bool:
        """关闭终端会话（解散群聊）"""
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
                    logger.info(f"会话已关闭: {result.get('message')}")
                    return True

        except Exception as e:
            logger.error(f"关闭会话异常: {e}")
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

    async def chat_stream(self, message: str):
        """流式发送消息"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "chat_stream",
                    "params": {
                        "message": message,
                        "chat_id": self._chat_id or "",
                        "user_open_id": "terminal_user",
                        "session_id": self.session_id or "",
                    },
                    "id": "terminal-request",
                }

                async with session.post(
                    f"{self.bridge_url}/stream",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"请求失败: HTTP {resp.status}")

                    # 处理 NDJSON 流
                    buffer = ""
                    async for chunk in resp.content.iter_any():
                        buffer += chunk.decode("utf-8")

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if line.strip():
                                try:
                                    event_data = json.loads(line)
                                    event = StreamEvent.from_dict(event_data)
                                    yield event

                                    if event.event_type == StreamEventType.COMPLETE:
                                        self.session_id = event.data.get("session_id")
                                        print(f"\n[Session: {self.session_id[:8] if self.session_id else 'None'}...]")

                                except json.JSONDecodeError:
                                    logger.warning(f"无效的 JSON: {line}")

        except aiohttp.ClientError as e:
            yield StreamEvent.error(f"连接错误: {e}")

    async def run_interactive(self):
        """运行交互模式"""
        print("=" * 60)
        print("Terminal Claude Client")
        print("=" * 60)

        # 启动会话
        print(f"终端 ID: {self.terminal_id}")
        print("正在创建飞书群聊...", end=" ", flush=True)

        success = await self.start()
        if success:
            print(f"✅ 已绑定群聊: {self._chat_id[:8]}...")
        else:
            print("❌ 创建群聊失败")
            print("提示：请检查 Local Bridge 是否正常运行")
            return

        if self.session_id:
            print(f"会话: {self.session_id[:8]}...")

        print()
        print("输入消息后按 Enter 发送，输入 /exit 退出")
        print("-" * 60)

        # 注册退出处理
        def signal_handler(signum, frame):
            print("\n\n正在退出...")
            raise KeyboardInterrupt()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            while True:
                try:
                    message = input("\n你: ").strip()

                    if not message:
                        continue

                    if message.lower() in ["/exit", "/quit", "exit", "quit"]:
                        break

                    print("\nClaude: ", end="", flush=True)

                    full_response = []

                    async for event in self.chat_stream(message):
                        if event.event_type == StreamEventType.HEARTBEAT:
                            pass
                        elif event.event_type == StreamEventType.STATUS:
                            print(f"\r[状态: {event.data.get('text', '')}]", end="", flush=True)
                        elif event.event_type == StreamEventType.TOOL_CALL:
                            print(f"\n[工具: {event.data.get('name', '')}]", flush=True)
                        elif event.event_type == StreamEventType.CONTENT:
                            text = event.data.get("text", "")
                            print(text, end="", flush=True)
                            full_response.append(text)
                        elif event.event_type == StreamEventType.COMPLETE:
                            print()
                        elif event.event_type == StreamEventType.ERROR:
                            print(f"\n❌ 错误: {event.data.get('message', '')}")

                    # 同步输出到飞书（如果内容较长）
                    if full_response and len("".join(full_response)) > 100:
                        await self._sync_output("".join(full_response))

                except KeyboardInterrupt:
                    break
                except EOFError:
                    break

        finally:
            print("\n正在关闭会话...")
            await self.close()
            print("再见！")


async def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Terminal Claude Client")
    parser.add_argument("--session", type=str, help="恢复会话 ID")
    parser.add_argument("--bridge", type=str, default="http://localhost:8082", help="Bridge URL")
    parser.add_argument("--user", type=str, help="用户 open_id")
    parser.add_argument("--keep-chat", action="store_true", help="退出时保持群聊")
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
    )

    await client.run_interactive()


if __name__ == "__main__":
    asyncio.run(main())