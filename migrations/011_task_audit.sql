BEGIN;
CREATE TABLE task_audit_reports (
    audit_job_id uuid PRIMARY KEY REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    source_job_id uuid NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    source_execution_id uuid NOT NULL,
    source_run_id text,
    check_version integer NOT NULL,
    status text NOT NULL CHECK (status IN ('pass','attention')),
    checks jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_audit_reports_source ON task_audit_reports(source_job_id,created_at);
ALTER TABLE task_audit_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_audit_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY task_audit_reports_owner ON task_audit_reports USING (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id
            AND c.owner_id::text=current_setting('app.user_id',true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id
            AND c.owner_id::text=current_setting('app.user_id',true))
);
GRANT SELECT,INSERT ON task_audit_reports TO forge_app;
REVOKE UPDATE,DELETE ON task_audit_reports FROM forge_app;
REVOKE ALL ON task_audit_reports FROM forge_query;
INSERT INTO schema_migrations(name) VALUES ('011_task_audit');
COMMIT;
