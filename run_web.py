#!/usr/bin/env python
"""Run the Forge Web application.

前端 Web 界面，代理到 Gateway API。
不启用 LangSmith tracing，只让 LangGraph Server（2024）产生 trace。
"""

import os
import sys
import subprocess
import signal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# 加载环境变量（但不启用 tracing）
from dotenv import load_dotenv
load_dotenv()

# 前端不启用 LangSmith tracing - 只让 LangGraph Server 产生 trace
# 所有 HITL 交互通过 Gateway API -> LangGraph Server，trace 在 Server 内统一管理
print('[Web] LangSmith tracing disabled for frontend (only LangGraph Server traces)')

import uvicorn

# 评估 Worker 进程（后台运行）
_eval_worker_process = None


def start_evaluation_worker():
    """启动评估 Worker 后台进程。"""
    global _eval_worker_process

    try:
        # 启动 Worker 作为后台进程
        _eval_worker_process = subprocess.Popen(
            [sys.executable, "-m", "forge.evaluation.worker"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent),
        )
        print(f'[EvalWorker] Started evaluation worker (PID: {_eval_worker_process.pid})')
    except Exception as e:
        print(f'[EvalWorker] Failed to start worker: {e}')


def stop_evaluation_worker():
    """停止评估 Worker 后台进程。"""
    global _eval_worker_process

    if _eval_worker_process is not None:
        print(f'[EvalWorker] Stopping worker (PID: {_eval_worker_process.pid})')
        _eval_worker_process.terminate()
        try:
            _eval_worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _eval_worker_process.kill()
        _eval_worker_process = None


def signal_handler(signum, frame):
    """信号处理函数。"""
    print(f'\n[Main] Received signal {signum}, shutting down...')
    stop_evaluation_worker()
    sys.exit(0)


if __name__ == "__main__":
    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动评估 Worker
    start_evaluation_worker()

    try:
        uvicorn.run(
            "forge.web.app:app",
            host="0.0.0.0",
            port=8000,
            reload=False,
        )
    finally:
        # 确保 Worker 被停止
        stop_evaluation_worker()