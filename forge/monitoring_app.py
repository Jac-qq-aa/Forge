from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST

from forge.config import get_settings
from forge.infrastructure.postgres import Database


@asynccontextmanager
async def lifespan(app):
    app.state.db = Database(get_settings().database_url)
    await app.state.db.open()
    try:
        yield
    finally:
        await app.state.db.close()


app = FastAPI(lifespan=lifespan)


@app.get("/metrics")
async def metrics():
    registry = CollectorRegistry()
    jobs = Gauge("forge_jobs", "Durable job counts", ["status", "kind"], registry=registry)
    age = Gauge("forge_oldest_job_seconds", "Oldest unfinished job age", ["status", "kind"], registry=registry)
    outbox = Gauge("forge_outbox", "Outbox counts", ["status", "topic"], registry=registry)
    worker_capacity = Gauge("forge_worker_capacity", "Global admission capacity by worker kind", ["kind"], registry=registry)
    audit = Gauge("forge_audit_checks", "Completed audit results in last 24 hours", ["status"], registry=registry)
    settings = get_settings()
    for kind, limit in (("start", settings.graph_max_concurrency),
                        ("index_document", settings.index_max_concurrency),
                        ("audit_job", settings.audit_max_concurrency)):
        worker_capacity.labels(kind).set(limit)
    capacity = Gauge("forge_graph_capacity", "Configured graph admission capacity", registry=registry)
    capacity.set(get_settings().graph_max_concurrency)
    async with app.state.db.pool.acquire() as connection:
        for row in await connection.fetch("SELECT status,kind,count(*) AS n FROM workflow_jobs GROUP BY status,kind"):
            jobs.labels(row["status"], row["kind"]).set(row["n"])
        for row in await connection.fetch("""SELECT status,kind,extract(epoch FROM now()-min(created_at)) AS age
            FROM workflow_jobs WHERE status NOT IN ('completed','failed','cancelled','waiting') GROUP BY status,kind"""):
            age.labels(row["status"], row["kind"]).set(float(row["age"]))
        for row in await connection.fetch("SELECT status,topic,count(*) AS n FROM job_outbox GROUP BY status,topic"):
            outbox.labels(row["status"], row["topic"]).set(row["n"])
        for status in ("pass", "attention"):
            audit.labels(status).set(0)
        for row in await connection.fetch("""SELECT result->>'status' AS status,count(*) AS n
            FROM workflow_jobs WHERE kind='audit_job' AND status='completed'
            AND updated_at>now()-interval '24 hours' GROUP BY result->>'status'"""):
            if row["status"] in {"pass", "attention"}:
                audit.labels(row["status"]).set(row["n"])
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
