"""
Terminal CLI 客户端

连接到 Local Session Bridge，实现：
1. 注册功能：生成注册码，在 Feishu 绑定
2. 交互模式：发送消息，接收流式响应
3. 会话恢复：支持恢复之前的会话

使用方式：
    # 注册模式
    python -m src.terminal_client --register

    # 交互模式
    python -m src.terminal_client

    # 恢复会话
    python -m src.terminal_client --session <session_id>
"""
import asyncio
import json
import logging
import os
import sys
from typing import Optional

import aiohttp

from src.protocol import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)


class TerminalClaudeClient:
    """
    Terminal CLI 客户端

    连接到 Local Session Bridge 进行交互
    """

    def __init__(
        self,
        bridge_url: str = "http://localhost:8082",
        session_id: str = None,
    ):
        """
        初始化客户端

        Args:
            bridge_url: Local Session Bridge URL
            session_id: 恢复的会话 ID
        """
        self.bridge_url = bridge_url
        self.session_id = session_id
        self._chat_id: Optional[str] = None

    async def register(self) -> str:
        """
        注册终端到 Local Bridge

        Returns:
            注册码
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.bridge_url}/register",
                    json={"session_id": self.session_id},
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"注册失败: HTTP {resp.status}")

                    result = await resp.json()
                    if not result.get("success"):
                        raise Exception(f"注册失败: {result.get('error')}")

                    return result["code"]

        except aiohttp.ClientError as e:
            raise Exception(f"连接失败: {e}")

    async def check_binding(self) -> Optional[str]:
        """
        检查是否已绑定

        Returns:
            绑定的 chat_id 或 None
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.bridge_url}/status") as resp:
                    if resp.status != 200:
                        return None

                    result = await resp.json()
                    sessions = result.get("sessions", [])
                    if sessions:
                        return sessions[0].get("chat_id")
                    return None

        except Exception:
            return None

    async def chat_stream(self, message: str):
        """
        流式发送消息

        Args:
            message: 用户消息
        """
        try:
            async with aiohttp.ClientSession() as session:
                # 构建请求
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

                        # 按行分割
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if line.strip():
                                try:
                                    event_data = json.loads(line)
                                    event = StreamEvent.from_dict(event_data)
                                    yield event

                                    # 更新 session_id
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

        # 检查是否已绑定
        chat_id = await self.check_binding()
        if chat_id:
            print(f"已绑定聊天: {chat_id[:8]}...")
            self._chat_id = chat_id
        else:
            print("⚠️  未绑定聊天，请先运行 --register 注册")

        if self.session_id:
            print(f"会话: {self.session_id[:8]}...")

        print()
        print("输入消息后按 Enter 发送，输入 /exit 退出")
        print("-" * 60)

        while True:
            try:
                # 读取用户输入
                message = input("\n你: ").strip()

                if not message:
                    continue

                # 检查退出命令
                if message.lower() in ["/exit", "/quit", "exit", "quit"]:
                    print("再见！")
                    break

                # 显示处理状态
                print("\nClaude: ", end="", flush=True)

                # 流式处理响应
                async for event in self.chat_stream(message):
                    if event.event_type == StreamEventType.HEARTBEAT:
                        # 心跳，忽略
                        pass
                    elif event.event_type == StreamEventType.STATUS:
                        print(f"\r[状态: {event.data.get('text', '')}]", end="", flush=True)
                    elif event.event_type == StreamEventType.TOOL_CALL:
                        print(f"\n[工具: {event.data.get('name', '')}]", flush=True)
                    elif event.event_type == StreamEventType.CONTENT:
                        print(event.data.get("text", ""), end="", flush=True)
                    elif event.event_type == StreamEventType.COMPLETE:
                        # 完成时换行
                        print()
                    elif event.event_type == StreamEventType.ERROR:
                        print(f"\n❌ 错误: {event.data.get('message', '')}")

            except KeyboardInterrupt:
                print("\n\n已中断")
                break
            except EOFError:
                print("\n再见！")
                break


async def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Terminal Claude Client")
    parser.add_argument("--register", action="store_true", help="注册终端")
    parser.add_argument("--session", type=str, help="恢复会话 ID")
    parser.add_argument("--bridge", type=str, default="http://localhost:8082", help="Bridge URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    # 配置日志
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    client = TerminalClaudeClient(
        bridge_url=args.bridge,
        session_id=args.session,
    )

    if args.register:
        # 注册模式
        try:
            code = await client.register()
            print("=" * 60)
            print("终端注册")
            print("=" * 60)
            print(f"\n注册码: {code}")
            print(f"\n请在飞书发送以下命令完成绑定：")
            print(f"  /bind-terminal {code}")
            print(f"\n注册码 5 分钟内有效")
            print("=" * 60)
        except Exception as e:
            print(f"❌ 注册失败: {e}")
            sys.exit(1)
    else:
        # 交互模式
        await client.run_interactive()


if __name__ == "__main__":
    asyncio.run(main())