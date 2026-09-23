-- 最终文章表达质量评估：仅评估结构、文笔与自然度，不存储事实评估结果。

CREATE TABLE IF NOT EXISTS article_expression_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    article_hash CHAR(64) NOT NULL,

    overall_score DECIMAL(5,4),
    structure_score DECIMAL(5,4),
    writing_score DECIMAL(5,4),
    naturalness_score DECIMAL(5,4),
    ai_style_risk DECIMAL(5,4),

    metric_reasons JSONB NOT NULL DEFAULT '{}'::JSONB,
    issues JSONB NOT NULL DEFAULT '[]'::JSONB,
    model VARCHAR(128),

    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    evaluated_at TIMESTAMP,

    CONSTRAINT uq_article_expression_session_hash UNIQUE (session_id, article_hash),
    CONSTRAINT ck_article_expression_status
        CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_article_expression_session_time
    ON article_expression_evaluations(session_id, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_expression_status
    ON article_expression_evaluations(status, created_at);
