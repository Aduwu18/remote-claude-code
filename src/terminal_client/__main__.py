"""
Terminal Client 入口点

支持: python -m src.terminal_client
"""
from src.terminal_client.client import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())