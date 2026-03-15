"""
测试流式响应实现

测试内容：
1. StreamEvent 序列化/反序列化
2. GuestClaudeClient.chat_stream() 方法
3. GuestProxyClient.chat_stream() 方法
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
from aiohttp import web

from src.protocol import StreamEvent, StreamEventType


def test_stream_event():
    """测试 StreamEvent 序列化/反序列化"""
    print("=" * 50)
    print("测试 StreamEvent")
    print("=" * 50)

    # 测试工厂方法
    events = [
        ("心跳", StreamEvent.heartbeat()),
        ("状态", StreamEvent.status("正在处理...", "读取文件")),
        ("工具调用", StreamEvent.tool_call("Read", {"file_path": "/tmp/test.txt"})),
        ("内容", StreamEvent.content("这是响应内容")),
        ("完成", StreamEvent.complete("session-123", "任务完成")),
        ("错误", StreamEvent.error("出错了", "ValueError")),
    ]

    for name, event in events:
        json_str = event.to_json()
        parsed = StreamEvent.from_json(json_str)
        print(f"\n{name}:")
        print(f"  原始: {json_str}")
        print(f"  解析: event_type={parsed.event_type.value}, data={parsed.data}")

        # 验证类型一致
        assert event.event_type == parsed.event_type, f"类型不匹配: {event.event_type} != {parsed.event_type}"
        assert event.data == parsed.data, f"数据不匹配: {event.data} != {parsed.data}"

    print("\n✅ StreamEvent 测试通过!")


async def test_ndjson_parsing():
    """测试 NDJSON 解析"""
    print("\n" + "=" * 50)
    print("测试 NDJSON 解析")
    print("=" * 50)

    # 模拟 NDJSON 流
    ndjson_lines = [
        StreamEvent.status("开始处理").to_json(),
        StreamEvent.heartbeat().to_json(),
        StreamEvent.tool_call("Read", {"file_path": "test.py"}).to_json(),
        StreamEvent.heartbeat().to_json(),
        StreamEvent.content("文件内容...").to_json(),
        StreamEvent.heartbeat().to_json(),
        StreamEvent.complete("session-abc", "处理完成").to_json(),
    ]

    buffer = ""
    events = []

    for line in ndjson_lines:
        buffer += line + "\n"

        while '\n' in buffer:
            line, buffer = buffer.split('\n', 1)
            if line.strip():
                event = StreamEvent.from_json(line)
                events.append(event)

    print(f"解析了 {len(events)} 个事件:")
    for i, event in enumerate(events):
        print(f"  {i+1}. {event.event_type.value}: {event.data}")

    assert len(events) == 7, f"应该解析 7 个事件，实际: {len(events)}"
    print("\n✅ NDJSON 解析测试通过!")


async def test_mock_streaming_server():
    """测试模拟的流式服务器"""
    print("\n" + "=" * 50)
    print("测试模拟流式服务器")
    print("=" * 50)

    from src.host_bridge.client import GuestProxyClient
    from src.protocol import JsonRpcRequest, ChatParams, RequestMethod

    # 模拟服务器端口
    PORT = 18081
    server_running = asyncio.Event()

    async def mock_stream_handler(request: web.Request) -> web.StreamResponse:
        """模拟流式响应处理器"""
        response = web.StreamResponse()
        response.content_type = 'application/x-ndjson'
        await response.prepare(request)

        # 发送几个事件
        events = [
            StreamEvent.status("正在处理..."),
            StreamEvent.tool_call("Read", {"file_path": "test.py"}),
            StreamEvent.content("这是响应内容"),
            StreamEvent.complete("mock-session-123", "处理完成"),
        ]

        for event in events:
            await response.write(event.to_json().encode() + b'\n')
            await response.drain()
            await asyncio.sleep(0.1)

        return response

    # 创建模拟服务器
    app = web.Application()
    app.router.add_post("/stream", mock_stream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", PORT)
    await site.start()
    server_running.set()

    print(f"模拟服务器启动在端口 {PORT}")

    try:
        # 测试客户端
        status_updates = []

        async def on_status(status: str, details: str = None):
            status_updates.append((status, details))
            print(f"  状态更新: {status}")

        async with GuestProxyClient() as client:
            result = await client.chat_stream(
                endpoint=f"http://localhost:{PORT}",
                message="测试消息",
                chat_id="test-chat",
                user_open_id="test-user",
                status_callback=on_status,
            )

        print(f"\n结果:")
        print(f"  内容: {result.content}")
        print(f"  会话ID: {result.session_id}")
        print(f"  状态: {result.status}")
        print(f"  工具调用: {result.tool_calls}")

        assert result.status == "completed", f"状态应该是 completed，实际: {result.status}"
        assert result.session_id == "mock-session-123", f"会话ID不匹配"
        assert len(status_updates) > 0, "应该有状态更新"

        print("\n✅ 模拟流式服务器测试通过!")

    finally:
        await runner.cleanup()


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("流式响应实现测试")
    print("=" * 60)

    # 1. 测试 StreamEvent
    test_stream_event()

    # 2. 测试 NDJSON 解析
    await test_ndjson_parsing()

    # 3. 测试模拟流式服务器
    await test_mock_streaming_server()

    print("\n" + "=" * 60)
    print("✅ 所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())