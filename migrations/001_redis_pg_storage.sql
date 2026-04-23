-- migrations/001_redis_pg_storage.sql

-- Deep Mode 会话存储迁移

-- 会话元数据表
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_article JSONB NOT NULL,
    user_input TEXT,
    stage VARCHAR(20) NOT NULL,
    outline JSONB,
    outline_version INT DEFAULT 0,
    rag_context TEXT,
    current_draft TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    last_heartbeat TIMESTAMP,
    lock_version INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    finalized_at TIMESTAMP,
    final_draft TEXT
);

-- 消息历史表
CREATE TABLE IF NOT EXISTS session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    is_question BOOLEAN DEFAULT FALSE,
    token_count INT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 文章版本表
CREATE TABLE IF NOT EXISTS session_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    version INT NOT NULL,
    draft TEXT NOT NULL,
    token_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_sessions_stage ON sessions(stage);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active, last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_versions_session ON session_versions(session_id, version);

-- 更新触发器
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sessions_update_ts ON sessions;
CREATE TRIGGER sessions_update_ts
    BEFORE UPDATE ON sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();