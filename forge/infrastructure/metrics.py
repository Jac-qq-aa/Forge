"""低基数运行指标。任务、用户、租户和原文只进入授权日志，不进入标签。"""
from prometheus_client import Counter, Histogram, start_http_server

OPERATIONS = Counter("forge_operations_total", "Completed operations", ["operation", "status"])
DURATION = Histogram("forge_operation_seconds", "Operation duration", ["operation"],
                     buckets=(.1, .5, 1, 3, 10, 30, 60, 120, 300, 600))
EVENTS = Counter("forge_events_total", "Runtime events", ["event"])
TOKENS = Counter("forge_model_tokens_total", "Reported model tokens", ["role", "direction"])
RETRIES = Counter("forge_model_retries_total", "Model retries", ["role", "code"])
WAIT = Histogram("forge_model_budget_wait_seconds", "Budget wait time", ["role"],
                 buckets=(1, 5, 10, 30, 60, 120, 180))
_models = {}
_server = None
_operations = {
    "intent", "planner", "fact_approval", "outline", "outline_approval", "approval", "validate_user_facts", "plan_conflict_decision",
    "generator", "critic", "revise", "conflict_decision", "answer", "clarify",
    "model.completion", "model.embedding", "model.structured", "consumer.handle",
    "langgraph.submit", "langgraph.resume", "langgraph.reconcile", "workflow.project",
    "search.tavily", "search.pgvector", "sql.execute", "skill.text2sql", "skill.route_model",
    "rocketmq.publish",
}
_events = {"telemetry.persistence_failed", "fallback.used", "system.decision", "plan.approved",
           "submission.failed", "reconcile.failed", "job.admitted", "job.finished", "model.limit_wait",
           "research.core_facts_selected", "research.evidence_screened"}


def start(settings):
    global _server
    _models.update({settings.llm_model: "main", settings.skill_router_model: "router",
                    settings.embedding_model: "embedding"})
    if _server is None:
        _server = start_http_server(settings.metrics_port, addr="0.0.0.0")


def observe(event, status, data):
    if event in _events:
        EVENTS.labels(event).inc()
    role = _models.get(data.get("model"), "other")
    if event == "model.retry":
        code = str(data.get("status_code"))
        RETRIES.labels(role, code if code in {"408", "429", "500", "502", "503", "504"} else "other").inc()
    if event == "model.limit_acquired":
        WAIT.labels(role).observe(data.get("wait_seconds", 0))
    if event != "span.finished":
        return
    name = data.get("name")
    operation = name if name in _operations else "other"
    outcome = status if status in {"succeeded", "failed", "cancelled", "paused", "degraded", "needs_attention"} else "other"
    OPERATIONS.labels(operation, outcome).inc()
    DURATION.labels(operation).observe(data.get("duration_ms", 0) / 1000)
    if name in {"model.completion", "model.embedding"}:
        for direction in ("input", "output"):
            TOKENS.labels(role, direction).inc((data.get("tokens") or {}).get(direction, 0))
