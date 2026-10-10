BEGIN;
ALTER TABLE articles ADD COLUMN thesis text NOT NULL DEFAULT '';
ALTER TABLE articles ADD COLUMN key_points text[] NOT NULL DEFAULT '{}';
INSERT INTO schema_migrations(name) VALUES ('007_article_thesis');
COMMIT;
