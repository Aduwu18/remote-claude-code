"""
Local Session Bridge 入口点

支持: python -m src.local_session_bridge
"""
from src.local_session_bridge.server import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())