"""
Use this instead of invoking uvicorn directly on Windows.
Sets WindowsProactorEventLoopPolicy BEFORE uvicorn creates its event loop,
which is required for Playwright to spawn subprocess-based browser instances.
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
