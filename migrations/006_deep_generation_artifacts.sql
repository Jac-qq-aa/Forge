-- Persist structured research provenance and adaptive generation reports.

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS research_result JSONB;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS quality_report JSONB;
