-- 参考文章只保存结构与风格分析；公司事实和时效政策必须进入各自知识库。

CREATE TABLE IF NOT EXISTS reference_articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id VARCHAR(256) NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    platform VARCHAR(32) NOT NULL,
    author VARCHAR(256) NOT NULL DEFAULT '',
    publish_date DATE,
    composition_type VARCHAR(16) NOT NULL
        CHECK (composition_type IN ('company', 'policy', 'mixed')),
    archetype VARCHAR(32) NOT NULL
        CHECK (archetype IN (
            'company_profile', 'policy_quick_read', 'risk_warning',
            'industry_analysis', 'practical_guide', 'trend_brand_mix'
        )),
    topic_text TEXT NOT NULL,
    style_text TEXT NOT NULL,
    style_profile JSONB NOT NULL,
    section_blueprint JSONB NOT NULL,
    content_hash CHAR(64) NOT NULL UNIQUE,
    dedup_fingerprint CHAR(64) NOT NULL UNIQUE,
    vector_id VARCHAR(64),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    indexed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reference_articles_retrieval
    ON reference_articles(platform, archetype, composition_type, is_active);

CREATE INDEX IF NOT EXISTS idx_reference_articles_pending
    ON reference_articles(created_at)
    WHERE is_active = TRUE AND vector_id IS NULL;

-- 集团事实只进入待审区。审核通过后再由事实知识库导入流程发布；时效事实默认不能自动发布。
CREATE TABLE IF NOT EXISTS reference_company_fact_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id VARCHAR(256) NOT NULL,
    claim TEXT NOT NULL,
    normalized_hash CHAR(64) NOT NULL UNIQUE,
    is_temporal BOOLEAN NOT NULL DEFAULT FALSE,
    review_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'accepted', 'rejected', 'conflicted')),
    reviewer VARCHAR(128),
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reference_fact_review_queue
    ON reference_company_fact_candidates(review_status, is_temporal, created_at);
