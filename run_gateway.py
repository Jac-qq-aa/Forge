#!/usr/bin/env python
"""Run Gateway API (FastAPI) for LangGraph Server.

Gateway API 使用 LangGraph SDK 调用 LangGraph Server。
需要先启动 LangGraph Server: langgraph dev --port 2024

简化设计：
- Gateway 纯透传，不启用 LangSmith tracing
- 只认 thread_id，不存储任何状态
- 观察视角：LangSmith Threads 或 LangGraph Studio
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Gateway 不启用 LangSmith tracing - 让 LangGraph Server 自然产生独立 trace
from dotenv import load_dotenv
load_dotenv()

print('[Gateway] LangSmith tracing disabled for Gateway (纯透传代理)')
print('[Gateway] View traces in LangSmith Threads or LangGraph Studio by thread_id')

import uvicorn

if __name__ == "__main__":
    print('[Gateway] Starting Gateway API on port 8001...')
    print('[Gateway] LangGraph Server should be running on port 2024')
    print('[Gateway] Start LangGraph Server: langgraph dev --port 2024')

    uvicorn.run(
        "forge.gateway.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )