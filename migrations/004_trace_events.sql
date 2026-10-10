BEGIN;
CREATE TABLE trace_events (
    id UUID PRIMARY KEY,
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    job_id UUID REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    trace_id UUID,
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX trace_events_job ON trace_events(job_id, sequence);
CREATE INDEX trace_events_trace ON trace_events(trace_id, sequence);
CREATE INDEX trace_events_retention ON trace_events(created_at);
ALTER TABLE trace_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE trace_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owned_trace_events ON trace_events USING (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id
            AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id
            AND c.owner_id::text=current_setting('app.user_id', true))
    AND (job_id IS NULL OR EXISTS (SELECT 1 FROM workflow_jobs j
         WHERE j.id=job_id AND j.conversation_id=trace_events.conversation_id))
);
GRANT SELECT, INSERT ON trace_events TO forge_app;
REVOKE UPDATE, DELETE ON trace_events FROM forge_app;
REVOKE ALL ON trace_events FROM forge_query;
GRANT USAGE, SELECT ON SEQUENCE trace_events_sequence_seq TO forge_app;
INSERT INTO schema_migrations(name) VALUES ('004_trace_events');
COMMIT;
