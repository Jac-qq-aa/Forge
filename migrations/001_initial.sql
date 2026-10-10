BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    CREATE ROLE forge_query NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE ROLE forge_app LOGIN PASSWORD 'forge_app'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT forge_query TO forge_app;
GRANT USAGE, CREATE ON SCHEMA public TO forge_app;

CREATE TYPE account_type AS ENUM ('guest', 'employee', 'admin');
CREATE TYPE knowledge_scope AS ENUM ('public', 'company', 'personal');
CREATE TYPE article_stage AS ENUM (
    'idle', 'researching', 'planning', 'awaiting_approval', 'generating',
    'critic', 'awaiting_decision', 'revising', 'ready', 'finalized',
    'needs_attention', 'failed', 'cancelled'
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    account_type account_type NOT NULL DEFAULT 'guest',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE knowledge_spaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    scope knowledge_scope NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (scope = 'public' AND tenant_id IS NULL AND owner_id IS NULL) OR
        (scope = 'company' AND tenant_id IS NOT NULL AND owner_id IS NULL) OR
        (scope = 'personal' AND owner_id IS NOT NULL)
    )
);

CREATE TABLE resource_grants (
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT NOT NULL CHECK (permission IN ('read', 'manage')),
    PRIMARY KEY (space_id, user_id, permission)
);

CREATE FUNCTION can_read_space(candidate UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM knowledge_spaces s
        WHERE s.id = candidate AND (
            s.scope = 'public'
            OR s.owner_id::text = current_setting('app.user_id', true)
            OR EXISTS (
                SELECT 1 FROM resource_grants g
                WHERE g.space_id = s.id
                  AND g.user_id::text = current_setting('app.user_id', true)
                  AND g.permission IN ('read', 'manage')
            )
        )
    )
$$;

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    legal_name TEXT NOT NULL,
    organization_type TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    registration_code TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    verified_at TIMESTAMPTZ,
    valid_from DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, legal_name, registration_code)
);

CREATE TABLE organization_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    valid_from DATE,
    valid_to DATE,
    UNIQUE (organization_id, normalized_alias)
);

CREATE TABLE organization_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES organizations(id),
    object_id UUID NOT NULL REFERENCES organizations(id),
    relation_type TEXT NOT NULL,
    confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    valid_from DATE,
    valid_to DATE,
    source_url TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    CHECK (subject_id <> object_id),
    UNIQUE (space_id, subject_id, object_id, relation_type, source_url)
);

CREATE TABLE business_offerings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    valid_from DATE,
    valid_to DATE,
    source_url TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    UNIQUE (space_id, organization_id, name, source_url)
);

CREATE TABLE legal_references (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    jurisdiction TEXT NOT NULL DEFAULT '中国',
    article_number TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    effective_from DATE,
    effective_to DATE,
    UNIQUE (space_id, title, article_number, source_url)
);

CREATE TABLE qualifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    certificate_number TEXT NOT NULL DEFAULT '',
    issuer TEXT NOT NULL DEFAULT '',
    valid_from DATE,
    valid_to DATE,
    source_url TEXT NOT NULL,
    source_quote TEXT NOT NULL
);

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    content_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'withdrawn')),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, external_id, version)
);

CREATE TABLE document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    UNIQUE (document_id, position)
);

CREATE TABLE chunk_embeddings (
    chunk_id UUID NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, embedding_model)
);
CREATE INDEX chunk_embeddings_cosine_hnsw
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id),
    stage article_stage NOT NULL DEFAULT 'idle',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT '沿用原文主题与方向',
    platform TEXT NOT NULL DEFAULT '',
    fact_version INTEGER NOT NULL DEFAULT 0,
    outline_version INTEGER NOT NULL DEFAULT 0,
    current_draft_version INTEGER NOT NULL DEFAULT 0,
    plan_dirty BOOLEAN NOT NULL DEFAULT false,
    approved_fact_version INTEGER,
    approved_outline_version INTEGER,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE article_facts (
    row_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id UUID NOT NULL,
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    claim TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    predicate TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    qualifiers JSONB NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL CHECK (origin IN ('source', 'company', 'web', 'history', 'user', 'draft')),
    verification TEXT NOT NULL CHECK (verification IN ('verified','unverified','conflicted','pending')),
    marked BOOLEAN NOT NULL DEFAULT false,
    mark_reasons TEXT[] NOT NULL DEFAULT '{}',
    excluded BOOLEAN NOT NULL DEFAULT false,
    decision_id UUID,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, id, version)
);
CREATE UNIQUE INDEX one_current_fact_version ON article_facts(article_id, id) WHERE is_current;

CREATE TABLE fact_evidence (
    id UUID PRIMARY KEY,
    article_fact_row_id UUID NOT NULL REFERENCES article_facts(row_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    quote TEXT NOT NULL,
    document_id UUID REFERENCES documents(id),
    document_version TEXT NOT NULL DEFAULT '',
    authority NUMERIC(4,3) NOT NULL CHECK (authority BETWEEN 0 AND 1),
    metadata JSONB NOT NULL DEFAULT '{}',
    retrieved_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE outline_sections (
    row_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    outline_version INTEGER NOT NULL,
    section_id UUID NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL,
    main_points JSONB NOT NULL,
    fact_ids UUID[] NOT NULL DEFAULT '{}',
    UNIQUE (article_id, outline_version, section_id)
);

CREATE TABLE approval_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    fact_version INTEGER NOT NULL,
    outline_version INTEGER NOT NULL,
    approved_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, fact_version, outline_version)
);

CREATE TABLE user_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    fact_id UUID NOT NULL,
    fact_version INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('keep', 'replace', 'exclude')),
    selected_evidence_id UUID,
    note TEXT NOT NULL DEFAULT '',
    decided_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, fact_id, fact_version)
);

CREATE TABLE draft_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_by_step UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, version)
);

CREATE TABLE review_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    draft_version INTEGER NOT NULL,
    failure_number INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('pass','fail','needs_user','incomplete','stopped')),
    issues JSONB NOT NULL DEFAULT '[]',
    stop_reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workflow_jobs (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','running','waiting','completed','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner TEXT,
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workflow_jobs_claim ON workflow_jobs(status, available_at, created_at);

CREATE TABLE workflow_events (
    sequence BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    job_id UUID REFERENCES workflow_jobs(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workflow_events_conversation ON workflow_events(conversation_id, sequence);

ALTER TABLE knowledge_spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_offerings ENABLE ROW LEVEL SECURITY;
ALTER TABLE qualifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_references ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_facts ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge_spaces FORCE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE organization_aliases FORCE ROW LEVEL SECURITY;
ALTER TABLE organization_relations FORCE ROW LEVEL SECURITY;
ALTER TABLE business_offerings FORCE ROW LEVEL SECURITY;
ALTER TABLE qualifications FORCE ROW LEVEL SECURITY;
ALTER TABLE legal_references FORCE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
ALTER TABLE articles FORCE ROW LEVEL SECURITY;
ALTER TABLE article_facts FORCE ROW LEVEL SECURITY;

CREATE POLICY readable_spaces ON knowledge_spaces FOR SELECT USING (
    scope = 'public'
    OR owner_id::text = current_setting('app.user_id', true)
    OR EXISTS (
        SELECT 1 FROM resource_grants g
        WHERE g.space_id=id
          AND g.user_id::text=current_setting('app.user_id', true)
          AND g.permission IN ('read', 'manage')
    )
);
CREATE POLICY readable_organizations ON organizations FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_aliases ON organization_aliases FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_relations ON organization_relations FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_businesses ON business_offerings FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_qualifications ON qualifications FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_legal_references ON legal_references FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_documents ON documents FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_chunks ON document_chunks FOR SELECT USING (
    EXISTS (SELECT 1 FROM documents d WHERE d.id=document_id AND can_read_space(d.space_id))
);
CREATE POLICY readable_embeddings ON chunk_embeddings FOR SELECT USING (
    EXISTS (
        SELECT 1 FROM document_chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.id=chunk_id AND can_read_space(d.space_id)
    )
);
CREATE POLICY personal_space_writes ON knowledge_spaces FOR INSERT WITH CHECK (
    scope='personal' AND owner_id::text=current_setting('app.user_id', true)
);
CREATE POLICY writable_personal_documents ON documents FOR ALL USING (
    EXISTS (
        SELECT 1 FROM knowledge_spaces s WHERE s.id=space_id
        AND s.owner_id::text=current_setting('app.user_id', true)
    )
) WITH CHECK (
    EXISTS (
        SELECT 1 FROM knowledge_spaces s WHERE s.id=space_id
        AND s.owner_id::text=current_setting('app.user_id', true)
    )
);
CREATE POLICY writable_personal_chunks ON document_chunks FOR ALL USING (
    EXISTS (
        SELECT 1 FROM documents d JOIN knowledge_spaces s ON s.id=d.space_id
        WHERE d.id=document_id AND s.owner_id::text=current_setting('app.user_id', true)
    )
) WITH CHECK (
    EXISTS (
        SELECT 1 FROM documents d JOIN knowledge_spaces s ON s.id=d.space_id
        WHERE d.id=document_id AND s.owner_id::text=current_setting('app.user_id', true)
    )
);
CREATE POLICY writable_personal_embeddings ON chunk_embeddings FOR ALL USING (
    EXISTS (
        SELECT 1 FROM document_chunks c JOIN documents d ON d.id=c.document_id
        JOIN knowledge_spaces s ON s.id=d.space_id
        WHERE c.id=chunk_id AND s.owner_id::text=current_setting('app.user_id', true)
    )
) WITH CHECK (
    EXISTS (
        SELECT 1 FROM document_chunks c JOIN documents d ON d.id=c.document_id
        JOIN knowledge_spaces s ON s.id=d.space_id
        WHERE c.id=chunk_id AND s.owner_id::text=current_setting('app.user_id', true)
    )
);

CREATE POLICY owned_conversations ON conversations USING (
    owner_id::text = current_setting('app.user_id', true)
) WITH CHECK (owner_id::text = current_setting('app.user_id', true));
CREATE POLICY owned_messages ON messages USING (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id AND c.owner_id::text=current_setting('app.user_id', true))
);
CREATE POLICY owned_articles ON articles USING (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id AND c.owner_id::text=current_setting('app.user_id', true))
) WITH CHECK (
    EXISTS (SELECT 1 FROM conversations c WHERE c.id=conversation_id AND c.owner_id::text=current_setting('app.user_id', true))
);
CREATE POLICY owned_article_facts ON article_facts USING (
    EXISTS (
        SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
        WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true)
    )
) WITH CHECK (
    EXISTS (
        SELECT 1 FROM articles a JOIN conversations c ON c.id=a.conversation_id
        WHERE a.id=article_id AND c.owner_id::text=current_setting('app.user_id', true)
    )
);

GRANT SELECT ON organizations, organization_aliases, organization_relations,
    business_offerings, qualifications, legal_references, documents, document_chunks, chunk_embeddings TO forge_query;
GRANT EXECUTE ON FUNCTION can_read_space(UUID) TO forge_query;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO forge_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO forge_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO forge_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO forge_app;

INSERT INTO schema_migrations (name) VALUES ('001_initial')
ON CONFLICT (name) DO NOTHING;

COMMIT;
