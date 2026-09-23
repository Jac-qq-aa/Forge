-- 用户偏好记忆：保存可审计的编辑事件，并从已接受事件中聚合稳定规则。

CREATE TABLE IF NOT EXISTS edit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_owner_id VARCHAR(128) NOT NULL,
    session_id UUID NOT NULL,
    stage VARCHAR(32) NOT NULL,
    target_platform VARCHAR(32) NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    instruction TEXT NOT NULL,
    intent_key VARCHAR(64) NOT NULL,
    before_text TEXT NOT NULL,
    after_text TEXT NOT NULL,
    diff_summary TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    accepted BOOLEAN,
    explicit_scope BOOLEAN NOT NULL DEFAULT FALSE,
    learned_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_edit_events_session
    ON edit_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_edit_events_learning
    ON edit_events(memory_owner_id, accepted, learned_at);

CREATE TABLE IF NOT EXISTS user_preference_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_owner_id VARCHAR(128) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    target_platform VARCHAR(32) NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    intent_key VARCHAR(64) NOT NULL,
    instruction TEXT NOT NULL,
    evidence_count INT NOT NULL DEFAULT 0,
    success_count INT NOT NULL DEFAULT 0,
    contradiction_count INT NOT NULL DEFAULT 0,
    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (
        memory_owner_id,
        stage,
        target_platform,
        content_type,
        intent_key
    ),
    CHECK (status IN ('candidate', 'active', 'deprecated')),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_preference_rules_lookup
    ON user_preference_rules(
        memory_owner_id,
        stage,
        target_platform,
        content_type,
        status,
        confidence DESC
    );
