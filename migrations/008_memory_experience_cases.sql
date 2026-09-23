-- 第二阶段记忆：将已接受的编辑事件沉淀为可按任务条件检索的经验案例。

CREATE TABLE IF NOT EXISTS memory_experience_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_event_id UUID NOT NULL UNIQUE REFERENCES edit_events(id) ON DELETE CASCADE,
    memory_owner_id VARCHAR(128) NOT NULL,
    session_id UUID NOT NULL,
    stage VARCHAR(32) NOT NULL,
    target_platform VARCHAR(32) NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    instruction TEXT NOT NULL,
    before_excerpt TEXT NOT NULL,
    after_excerpt TEXT NOT NULL,
    diff_summary TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    success_score DECIMAL(5,4) NOT NULL DEFAULT 1,
    vector_id VARCHAR(64),
    use_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_cases_scope
    ON memory_experience_cases(
        memory_owner_id,
        stage,
        target_platform,
        content_type,
        success_score DESC,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_memory_cases_vector
    ON memory_experience_cases(vector_id)
    WHERE vector_id IS NOT NULL;
