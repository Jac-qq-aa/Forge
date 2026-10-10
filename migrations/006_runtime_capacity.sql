BEGIN;
ALTER TABLE workflow_jobs DROP CONSTRAINT workflow_jobs_status_check;
ALTER TABLE workflow_jobs ADD CONSTRAINT workflow_jobs_status_check CHECK (
    status IN ('pending','queued','dispatching','submission_unknown','submitted','running','waiting','completed','failed','cancelled')
);
ALTER TABLE workflow_jobs ADD COLUMN retry_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX workflow_jobs_dispatch ON workflow_jobs(status, retry_at, created_at);
CREATE TABLE workflow_projections (
    job_id uuid PRIMARY KEY REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON workflow_projections TO forge_app;
INSERT INTO schema_migrations(name) VALUES ('006_runtime_capacity');
COMMIT;
