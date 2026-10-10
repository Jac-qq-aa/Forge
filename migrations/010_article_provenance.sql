BEGIN;

ALTER TABLE draft_versions ADD COLUMN snapshot_id uuid UNIQUE;
ALTER TABLE review_reports ADD COLUMN review_id uuid UNIQUE;

ALTER TABLE draft_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY draft_versions_owner ON draft_versions USING (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
);

ALTER TABLE review_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY review_reports_owner ON review_reports USING (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
);

CREATE TABLE paragraph_provenance (
    article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    draft_version integer NOT NULL,
    paragraph_id uuid NOT NULL,
    section_id uuid NOT NULL,
    fact_refs jsonb NOT NULL,
    issue_ids uuid[] NOT NULL DEFAULT '{}',
    previous_draft_version integer,
    previous_content_fingerprint text,
    content_fingerprint text NOT NULL,
    step text NOT NULL CHECK (step IN ('generator','revise')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, draft_version, paragraph_id),
    FOREIGN KEY (article_id, draft_version) REFERENCES draft_versions(article_id, version)
);
CREATE INDEX paragraph_provenance_fact_refs ON paragraph_provenance USING gin(fact_refs);

ALTER TABLE paragraph_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE paragraph_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY paragraph_provenance_owner ON paragraph_provenance USING (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
            WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true))
);
GRANT SELECT, INSERT ON paragraph_provenance TO forge_app;
REVOKE UPDATE, DELETE ON paragraph_provenance FROM forge_app;
REVOKE ALL ON paragraph_provenance FROM forge_query;

INSERT INTO schema_migrations(name) VALUES ('010_article_provenance');
COMMIT;
