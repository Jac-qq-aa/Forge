#!/usr/bin/env python
"""Run Gateway API (FastAPI) for LangGraph Server.

Gateway API 使用 LangGraph SDK 调用 LangGraph Server。
需要先启动 LangGraph Server: langgraph dev --port 2024

设计：
- Gateway 负责 LangSmith trace stitching
- trace headers 存在 LangGraph thread state 中
- 业务状态仍由 LangGraph checkpointer 持久化
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

print('[Gateway] LangSmith trace stitching enabled in Gateway')
print('[Gateway] View stitched traces in LangSmith, or inspect thread state by thread_id')

import uvicorn

if __name__ == "__main__":
    gateway_port = int(os.environ["GATEWAY_PORT"])
    print(f'[Gateway] Starting Gateway API on port {gateway_port}...')
    print('[Gateway] LangGraph Server should be reachable via LANGGRAPH_API_URL')
    print('[Gateway] Start LangGraph Server before Gateway')

    uvicorn.run(
        "forge.gateway.app:app",
        host="0.0.0.0",
        port=gateway_port,
        reload=False,
    )
