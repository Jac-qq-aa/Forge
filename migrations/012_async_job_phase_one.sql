-- 第一阶段高并发加固：Outbox 死信、重放和租户活跃任务查询索引

ALTER TABLE workflow_outbox
    ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ;

ALTER TABLE workflow_outbox
    ADD COLUMN IF NOT EXISTS replayed_at TIMESTAMPTZ;

ALTER TABLE workflow_jobs
    ADD COLUMN IF NOT EXISTS actor_id VARCHAR(128);

UPDATE workflow_jobs
SET actor_id = tenant_id
WHERE actor_id IS NULL;

ALTER TABLE workflow_jobs
    ALTER COLUMN actor_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_outbox_dead_letters
    ON workflow_outbox(dead_lettered_at DESC)
    WHERE dead_lettered_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_tenant_active
    ON workflow_jobs(tenant_id, created_at)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_actor_active
    ON workflow_jobs(tenant_id, actor_id, created_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS workflow_session_owners (
    session_id UUID PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    owner_subject VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflow_session_owners_tenant
    ON workflow_session_owners(tenant_id, created_at DESC);
