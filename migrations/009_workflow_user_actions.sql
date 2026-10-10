BEGIN;

CREATE TABLE workflow_user_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    actor_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pause_kind text NOT NULL CHECK (pause_kind IN ('facts','outline','plan_conflicts','critic_conflicts')),
    fact_version integer NOT NULL,
    decision jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, actor_user_id, fact_version, pause_kind)
);
ALTER TABLE workflow_user_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY workflow_user_actions_owner ON workflow_user_actions USING (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id
            AND c.owner_id=current_setting('app.user_id',true)::uuid)
);
GRANT SELECT, INSERT ON workflow_user_actions TO forge_app;
REVOKE UPDATE, DELETE ON workflow_user_actions FROM forge_app;

INSERT INTO schema_migrations(name) VALUES ('009_workflow_user_actions');
COMMIT;
