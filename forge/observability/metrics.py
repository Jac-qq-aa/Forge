"""低基数 Prometheus 指标，供 HPA/KEDA 与告警使用。"""

from __future__ import annotations

import os
import time
import hmac
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app, start_http_server


HTTP_REQUESTS = Counter(
    "forge_http_requests_total",
    "HTTP 请求数",
    ("service", "method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "forge_http_request_duration_seconds",
    "HTTP 请求耗时",
    ("service", "method", "route"),
)
JOB_EXECUTIONS = Counter(
    "forge_job_executions_total",
    "任务执行结果数",
    ("resource", "operation", "status"),
)
JOB_DURATION = Histogram(
    "forge_job_execution_duration_seconds",
    "任务执行耗时",
    ("resource", "operation"),
)
WORKER_IN_FLIGHT = Gauge(
    "forge_worker_in_flight_jobs",
    "进程内正在执行的任务数",
    ("resource",),
)
OUTBOX_PUBLISH = Counter(
    "forge_outbox_publish_total",
    "Outbox 发布结果数",
    ("status",),
)
GENERATION_EVENTS = Counter(
    "forge_generation_events_total",
    "文章生成事件处理结果数",
    ("action", "outcome"),
)
GENERATION_RECOVERIES = Counter(
    "forge_generation_recoveries_total",
    "过期文章生成执行的恢复事件数",
)
LLM_TOKENS = Counter(
    "forge_llm_tokens_total",
    "LLM 输入和输出 Token 数",
    ("model", "role", "direction"),
)


class HttpMetricsMiddleware:
    def __init__(self, app: Any, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").rstrip("/") == "/metrics":
            expected = os.getenv("METRICS_BEARER_TOKEN", "")
            headers = dict(scope.get("headers", ()))
            actual = headers.get(b"authorization", b"").decode("utf-8")
            if expected and not hmac.compare_digest(actual, f"Bearer {expected}"):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        started = time.perf_counter()
        status = 500

        async def send_with_status(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route_object = scope.get("route")
            route = getattr(route_object, "path", None) or "unmatched"
            method = scope.get("method", "UNKNOWN")
            HTTP_REQUESTS.labels(self.service, method, route, str(status)).inc()
            HTTP_DURATION.labels(self.service, method, route).observe(
                time.perf_counter() - started
            )


def install_http_metrics(app: Any, service: str) -> None:
    app.add_middleware(HttpMetricsMiddleware, service=service)
    app.mount("/metrics", make_asgi_app())


def start_worker_metrics_server() -> int:
    port = int(os.getenv("METRICS_PORT", "9100"))
    start_http_server(port)
    return port


def record_llm_usage(response: Any, *, model: str, role: str) -> None:
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    if input_tokens:
        LLM_TOKENS.labels(model, role, "input").inc(int(input_tokens))
    if output_tokens:
        LLM_TOKENS.labels(model, role, "output").inc(int(output_tokens))
