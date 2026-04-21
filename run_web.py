#!/usr/bin/env python
"""Run the Forge Web application."""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# LangSmith tracing - 必须在导入 langchain 之前设置
from dotenv import load_dotenv
load_dotenv()

# 确保 LangSmith 环境变量设置
if os.getenv('LANGCHAIN_API_KEY'):
    os.environ['LANGCHAIN_TRACING_V2'] = 'true'
    os.environ['LANGCHAIN_PROJECT'] = os.getenv('LANGCHAIN_PROJECT', 'Forge-Content-Workflow')
    # 使用后台异步上传，避免阻塞主流程
    os.environ['LANGCHAIN_CALLBACKS_BACKGROUND'] = 'true'
    print(f'[LangSmith] Tracing enabled (background mode)')
    print(f'[LangSmith] Project: {os.environ["LANGCHAIN_PROJECT"]}')
    print(f'[LangSmith] View traces at: https://smith.langchain.com')
else:
    print('[LangSmith] Tracing disabled (no LANGCHAIN_API_KEY)')

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "forge.web.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )