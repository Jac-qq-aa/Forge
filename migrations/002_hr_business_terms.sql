BEGIN;

CREATE TABLE hr_term_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'law', 'regulation', 'department_rule', 'national_standard',
        'statistical_system', 'policy', 'official_guidance'
    )),
    title TEXT NOT NULL,
    issuing_authority TEXT NOT NULL,
    document_number TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    article_clause TEXT NOT NULL DEFAULT '',
    published_on DATE,
    effective_from DATE,
    effective_to DATE,
    source_quote TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verification_status TEXT NOT NULL DEFAULT 'verified'
        CHECK (verification_status IN ('verified', 'pending_fulltext', 'superseded')),
    supersedes_source_id UUID REFERENCES hr_term_sources(id),
    UNIQUE (space_id, source_url, article_clause)
);

CREATE TABLE hr_business_terms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    term_code TEXT NOT NULL,
    term_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    term_type TEXT NOT NULL CHECK (term_type IN (
        'legal_mandatory', 'statistical', 'industry_service', 'company_defined'
    )),
    definition TEXT NOT NULL,
    calculation_rule TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    applicable_scope TEXT NOT NULL,
    exclusions TEXT NOT NULL DEFAULT '',
    mandatory_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'verified'
        CHECK (status IN ('draft', 'verified', 'superseded')),
    effective_from DATE,
    effective_to DATE,
    version TEXT NOT NULL,
    source_id UUID NOT NULL REFERENCES hr_term_sources(id),
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, term_code, version)
);

CREATE TABLE hr_compliance_obligations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    obligation_code TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    trigger_event TEXT NOT NULL,
    required_action TEXT NOT NULL,
    deadline_value NUMERIC,
    deadline_unit TEXT NOT NULL DEFAULT '',
    threshold_expression TEXT NOT NULL DEFAULT '',
    exception_rule TEXT NOT NULL DEFAULT '',
    article_clause TEXT NOT NULL DEFAULT '',
    effective_from DATE,
    effective_to DATE,
    version TEXT NOT NULL,
    source_id UUID NOT NULL REFERENCES hr_term_sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, obligation_code, version)
);

CREATE TABLE hr_regional_parameters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    term_id UUID NOT NULL REFERENCES hr_business_terms(id) ON DELETE CASCADE,
    region_code TEXT NOT NULL,
    employment_type TEXT NOT NULL,
    parameter_name TEXT NOT NULL,
    value_numeric NUMERIC,
    value_text TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    source_id UUID NOT NULL REFERENCES hr_term_sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (value_numeric IS NOT NULL OR value_text <> ''),
    UNIQUE (
        space_id, term_id, region_code, employment_type,
        parameter_name, valid_from
    )
);

CREATE TABLE hr_classification_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
    classification_system TEXT NOT NULL,
    code TEXT NOT NULL,
    parent_code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    definition TEXT NOT NULL,
    inclusions TEXT NOT NULL DEFAULT '',
    exclusions TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_id UUID NOT NULL REFERENCES hr_term_sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, classification_system, code, version)
);

CREATE INDEX hr_term_sources_space ON hr_term_sources(space_id);
CREATE INDEX hr_business_terms_lookup
    ON hr_business_terms(space_id, domain, term_type, status, term_name);
CREATE INDEX hr_compliance_obligations_lookup
    ON hr_compliance_obligations(space_id, actor_type, trigger_event);
CREATE INDEX hr_regional_parameters_lookup
    ON hr_regional_parameters(space_id, region_code, parameter_name, valid_from, valid_to);
CREATE INDEX hr_classification_codes_lookup
    ON hr_classification_codes(space_id, classification_system, code);

ALTER TABLE hr_term_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_business_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_compliance_obligations ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_regional_parameters ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_classification_codes ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_term_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE hr_business_terms FORCE ROW LEVEL SECURITY;
ALTER TABLE hr_compliance_obligations FORCE ROW LEVEL SECURITY;
ALTER TABLE hr_regional_parameters FORCE ROW LEVEL SECURITY;
ALTER TABLE hr_classification_codes FORCE ROW LEVEL SECURITY;

CREATE POLICY readable_hr_term_sources ON hr_term_sources
    FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_hr_business_terms ON hr_business_terms
    FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_hr_compliance_obligations ON hr_compliance_obligations
    FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_hr_regional_parameters ON hr_regional_parameters
    FOR SELECT USING (can_read_space(space_id));
CREATE POLICY readable_hr_classification_codes ON hr_classification_codes
    FOR SELECT USING (can_read_space(space_id));

REVOKE ALL ON hr_term_sources, hr_business_terms, hr_compliance_obligations,
    hr_regional_parameters, hr_classification_codes FROM PUBLIC, forge_app;
GRANT SELECT ON hr_term_sources, hr_business_terms, hr_compliance_obligations,
    hr_regional_parameters, hr_classification_codes TO forge_query;

INSERT INTO schema_migrations (name) VALUES ('002_hr_business_terms')
ON CONFLICT (name) DO NOTHING;

COMMIT;
