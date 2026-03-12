#!/usr/bin/env python
"""
测试脚本 - 模拟飞书消息处理流程

用法:
    python test/test_docker_session.py "进入 urbansar-architect-main 容器"
    python test/test_docker_session.py "你好"

测试模式:
    - 完全在本地模拟，不发送真实飞书消息
    - 自动允许所有权限请求
    - 模拟 Docker 会话创建流程
"""

import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 取消 CLAUDECODE 环境变量（避免嵌套会话错误）
os.environ.pop('CLAUDECODE', None)

import logging
import subprocess

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# 降低 SDK 的日志级别
logging.getLogger('claude_agent_sdk').setLevel(logging.WARNING)
logging.getLogger('mcp').setLevel(logging.WARNING)


def run_test():
    """运行测试"""
    if len(sys.argv) < 2:
        print("用法: python test/test_docker_session.py \"消息内容\"")
        print("\n示例:")
        print("  python test/test_docker_session.py \"进入 urbansar-architect-main 容器\"")
        print("  python test/test_docker_session.py \"你好\"")
        sys.exit(1)

    message = sys.argv[1]

    # 模拟参数
    mock_chat_id = "test_chat_001"
    mock_user_open_id = "test_user_001"

    print("=" * 60)
    print("🧪 Docker 会话测试")
    print("=" * 60)
    print(f"📝 测试消息: {message}")
    print(f"🆔 Chat ID: {mock_chat_id}")
    print(f"👤 User Open ID: {mock_user_open_id}")
    print("=" * 60)

    # 延迟导入，确保环境变量已设置
    from src.claude_code import chat_sync, set_permission_request_callback
    from src.docker_mcp import set_docker_session_handler
    from src.context import set_request_context, clear_request_context

    # 权限请求计数
    permission_count = 0
    docker_session_count = 0

    def mock_permission_request(chat_id: str, session_id: str, tool_name: str, tool_input: dict) -> bool:
        """
        模拟权限确认 - 自动允许所有操作
        """
        nonlocal permission_count
        permission_count += 1

        print(f"\n🔒 权限确认请求 #{permission_count}")
        print(f"   操作: {tool_name}")
        if tool_name == "Bash":
            print(f"   命令: {tool_input.get('command', 'N/A')}")
        else:
            print(f"   详情: {tool_input}")
        print(f"   ✅ 自动允许")

        return True

    def mock_docker_session_handler(chat_id: str, user_open_id: str, container_name: str) -> dict:
        """
        模拟 Docker 会话处理
        """
        nonlocal docker_session_count
        docker_session_count += 1

        print(f"\n🐳 Docker 会话创建请求 #{docker_session_count}")
        print(f"   容器: {container_name}")
        print(f"   Chat ID: {chat_id}")
        print(f"   User Open ID: {user_open_id}")

        # 检查容器是否存在
        try:
            check_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0 or result.stdout.strip() != "true":
                print(f"   ❌ 容器不存在或未运行")
                return {
                    "success": False,
                    "message": f"容器 '{container_name}' 不存在或未运行"
                }
        except Exception as e:
            print(f"   ❌ 检查容器状态失败: {e}")
            return {
                "success": False,
                "message": f"检查容器状态失败: {e}"
            }

        print(f"   ✅ 模拟创建成功（不发送真实飞书消息）")

        return {
            "success": True,
            "message": f"容器会话已创建（测试模式）",
            "docker_chat_id": f"mock_docker_chat_{container_name}"
        }

    # 设置回调
    set_permission_request_callback(mock_permission_request)
    set_docker_session_handler(mock_docker_session_handler)

    # 设置请求上下文（同时设置环境变量）
    set_request_context(mock_chat_id, mock_user_open_id)

    # 打印环境变量确认
    print(f"\n📌 环境变量设置:")
    print(f"   MCP_CHAT_ID: {os.environ.get('MCP_CHAT_ID', '未设置')}")
    print(f"   MCP_USER_OPEN_ID: {os.environ.get('MCP_USER_OPEN_ID', '未设置')}")

    try:
        # 调用 chat_sync
        print("\n📤 发送消息到 Claude...")
        print("-" * 60)

        reply, session_id = chat_sync(
            message,
            session_id=None,
            chat_id=mock_chat_id,
            require_confirmation=True,
            user_open_id=mock_user_open_id,
        )

        print("-" * 60)
        print("\n📥 Claude 回复:")
        print("-" * 60)
        print(reply)
        print("-" * 60)

        print("\n📊 测试统计:")
        print(f"   权限请求次数: {permission_count}")
        print(f"   Docker 会话请求次数: {docker_session_count}")
        print(f"   Session ID: {session_id}")

        # 判断测试结果
        print("\n" + "=" * 60)
        if docker_session_count > 0:
            print("✅ 测试通过: Docker 会话工具被正确调用")
        elif "容器" in message or "docker" in message.lower():
            print("⚠️  警告: 消息包含容器相关内容，但 Docker 会话工具未被调用")
            print("   可能原因: Claude 选择了其他方式处理请求")
        else:
            print("✅ 测试完成: 非容器相关消息，正常处理")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        clear_request_context()


if __name__ == "__main__":
    run_test()