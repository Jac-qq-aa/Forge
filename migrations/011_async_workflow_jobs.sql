-- 高并发异步工作流：任务、执行租约与事务 Outbox

CREATE TABLE IF NOT EXISTS workflow_jobs (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    session_id UUID,
    status VARCHAR(16) NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    payload JSONB NOT NULL,
    result JSONB,
    error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    lease_owner VARCHAR(256),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_status_created
    ON workflow_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_session_active
    ON workflow_jobs(session_id, status, lease_expires_at)
    WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS workflow_outbox (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    topic VARCHAR(255) NOT NULL,
    message_key VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by VARCHAR(256),
    locked_until TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflow_outbox_pending
    ON workflow_outbox(available_at, id)
    WHERE published_at IS NULL;

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workflow_jobs_update_ts ON workflow_jobs;
CREATE TRIGGER workflow_jobs_update_ts
    BEFORE UPDATE ON workflow_jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();
