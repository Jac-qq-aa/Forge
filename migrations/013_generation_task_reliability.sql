-- CoWriter AI：文章生成任务、事件、审批、恢复与消费死信

ALTER TABLE workflow_jobs
    ALTER COLUMN status TYPE VARCHAR(32);

ALTER TABLE workflow_jobs
    DROP CONSTRAINT IF EXISTS workflow_jobs_status_check;

ALTER TABLE workflow_jobs
    ADD CONSTRAINT workflow_jobs_status_check CHECK (
        status IN (
            'queued', 'running', 'waiting_approval', 'retry_wait',
            'succeeded', 'failed', 'cancelling', 'cancelled'
        )
    );

ALTER TABLE workflow_jobs
    ADD COLUMN IF NOT EXISTS thread_id UUID,
    ADD COLUMN IF NOT EXISTS current_stage VARCHAR(64) NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS revision_round INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS execution_id UUID,
    ADD COLUMN IF NOT EXISTS active_run_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS execution_deadline_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pending_interrupt_id VARCHAR(256),
    ADD COLUMN IF NOT EXISTS pending_approval JSONB,
    ADD COLUMN IF NOT EXISTS content_version VARCHAR(128),
    ADD COLUMN IF NOT EXISTS last_error_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS max_revision_rounds INTEGER NOT NULL DEFAULT 2,
    ADD COLUMN IF NOT EXISTS token_budget INTEGER;

UPDATE workflow_jobs
SET thread_id = session_id
WHERE thread_id IS NULL AND session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_thread_execution
    ON workflow_jobs(thread_id, status, lease_expires_at)
    WHERE thread_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_expired_execution
    ON workflow_jobs(lease_expires_at, id)
    WHERE status IN ('running', 'cancelling');

CREATE TABLE IF NOT EXISTS workflow_events (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    action VARCHAR(32) NOT NULL CHECK (
        action IN ('start', 'resume', 'retry', 'recover', 'cancel', 'replay')
    ),
    operation_key VARCHAR(512) NOT NULL,
    expected_version BIGINT NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'processing', 'completed', 'stale', 'dead_lettered')
    ),
    approval_id UUID,
    interrupt_id VARCHAR(256),
    content_version VARCHAR(128),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    execution_id UUID,
    lease_owner VARCHAR(256),
    lease_expires_at TIMESTAMPTZ,
    last_error_code VARCHAR(64),
    last_error TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, operation_key)
);

CREATE INDEX IF NOT EXISTS idx_workflow_events_task_created
    ON workflow_events(task_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_workflow_events_expired
    ON workflow_events(lease_expires_at, id)
    WHERE status = 'processing';

CREATE TABLE IF NOT EXISTS workflow_approvals (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    actor_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    interrupt_id VARCHAR(256) NOT NULL,
    content_version VARCHAR(128) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    event_id UUID REFERENCES workflow_events(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    UNIQUE (task_id, idempotency_key),
    UNIQUE (task_id, interrupt_id, content_version)
);

ALTER TABLE workflow_events
    DROP CONSTRAINT IF EXISTS workflow_events_approval_id_fkey;

ALTER TABLE workflow_events
    ADD CONSTRAINT workflow_events_approval_id_fkey
    FOREIGN KEY (approval_id) REFERENCES workflow_approvals(id);

CREATE TABLE IF NOT EXISTS workflow_dead_letters (
    id BIGSERIAL PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES workflow_events(id) ON DELETE CASCADE,
    error_code VARCHAR(64) NOT NULL,
    error TEXT NOT NULL,
    payload JSONB NOT NULL,
    replayed_event_id UUID REFERENCES workflow_events(id),
    replayed_by VARCHAR(128),
    replayed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_dead_letters_created
    ON workflow_dead_letters(created_at DESC);

ALTER TABLE workflow_outbox
    ADD COLUMN IF NOT EXISTS event_id UUID REFERENCES workflow_events(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS action VARCHAR(32),
    ADD COLUMN IF NOT EXISTS expected_version BIGINT,
    ADD COLUMN IF NOT EXISTS delivery_timestamp TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_outbox_event
    ON workflow_outbox(event_id)
    WHERE event_id IS NOT NULL;

DROP TRIGGER IF EXISTS workflow_events_update_ts ON workflow_events;
CREATE TRIGGER workflow_events_update_ts
    BEFORE UPDATE ON workflow_events
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();
