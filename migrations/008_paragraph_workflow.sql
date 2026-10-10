BEGIN;

ALTER TABLE article_facts ADD COLUMN required_for_article boolean NOT NULL DEFAULT false;
UPDATE article_facts SET required_for_article=true WHERE origin IN ('source','user') AND NOT excluded;

ALTER TABLE outline_sections ADD COLUMN paragraphs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE draft_versions ADD COLUMN paragraphs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE review_reports ADD COLUMN fact_checks jsonb NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE workflow_projection_runs (
    job_id uuid NOT NULL REFERENCES workflow_jobs(id) ON DELETE CASCADE,
    run_id text NOT NULL,
    projected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, run_id)
);

ALTER TABLE user_decisions ADD COLUMN issue_id uuid;
ALTER TABLE user_decisions ADD COLUMN evidence_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO schema_migrations(name) VALUES ('008_paragraph_workflow');
COMMIT;
