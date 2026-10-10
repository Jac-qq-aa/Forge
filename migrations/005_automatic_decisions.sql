BEGIN;
ALTER TABLE approval_snapshots ALTER COLUMN approved_by DROP NOT NULL;
ALTER TABLE approval_snapshots ADD COLUMN actor_kind text NOT NULL DEFAULT 'user'
    CHECK (actor_kind IN ('user','system'));
ALTER TABLE approval_snapshots ADD COLUMN rule text NOT NULL DEFAULT '';
CREATE TABLE system_decisions (
    id uuid PRIMARY KEY,
    article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    fact_id uuid NOT NULL,
    fact_version integer NOT NULL,
    action text NOT NULL CHECK (action='keep'),
    rule text NOT NULL,
    note text NOT NULL,
    conflict jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE system_decisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY system_decisions_owner ON system_decisions USING (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id=current_setting('app.user_id',true)::uuid)
);
GRANT SELECT, INSERT ON system_decisions TO forge_app;
REVOKE UPDATE, DELETE ON system_decisions FROM forge_app;
INSERT INTO schema_migrations(name) VALUES ('005_automatic_decisions');
COMMIT;
