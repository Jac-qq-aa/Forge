BEGIN;

ALTER TABLE workflow_jobs
    ADD COLUMN trace_id UUID NOT NULL DEFAULT gen_random_uuid(),
    ADD COLUMN remote_thread_id TEXT,
    ADD COLUMN remote_run_id TEXT,
    ADD COLUMN submitted_at TIMESTAMPTZ;

CREATE UNIQUE INDEX workflow_jobs_remote_run
    ON workflow_jobs(remote_thread_id, remote_run_id)
    WHERE remote_run_id IS NOT NULL;

CREATE TABLE job_outbox (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL UNIQUE REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    message_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'publishing', 'published', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    transport_message_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_outbox_dispatch
    ON job_outbox(status, available_at, created_at);

CREATE TABLE message_inbox (
    consumer_group TEXT NOT NULL,
    message_id UUID NOT NULL,
    job_id UUID NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'processed')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    PRIMARY KEY (consumer_group, message_id)
);

DROP INDEX IF EXISTS workflow_jobs_claim;
ALTER TABLE workflow_jobs
    DROP COLUMN available_at,
    DROP COLUMN lease_owner,
    DROP COLUMN lease_token,
    DROP COLUMN lease_expires_at;

GRANT SELECT, INSERT, UPDATE, DELETE ON job_outbox, message_inbox TO forge_app;

INSERT INTO schema_migrations (name) VALUES ('003_runtime_dispatch')
ON CONFLICT (name) DO NOTHING;

COMMIT;
