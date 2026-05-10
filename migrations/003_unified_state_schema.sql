-- Migration 003: Unified State Schema
-- 为 UnifiedState 添加新字段，支持快速模式和深度模式的统一 workflow

-- ============================================================================
-- sessions 表：添加新字段
-- ============================================================================

-- 模式字段
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mode VARCHAR(16) DEFAULT 'fast';

-- 快速模式字段
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ai_score FLOAT DEFAULT 0.0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS humanize_revisions INT DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS revision_count INT DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS humanize_feedback TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ruibo_feedback TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS reflection_feedback TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS final_script TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS script_path VARCHAR(512);

-- 深度模式字段（已有部分，补充缺失）
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_input TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS rag_context TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tuning_messages JSONB DEFAULT '[]'::jsonb;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS human_decision VARCHAR(64);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS draft_v1 TEXT;

-- 控制字段
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS generate_video BOOLEAN DEFAULT FALSE;

-- ============================================================================
-- checkpoints 表：用于 LangGraph 状态快照
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id UUID PRIMARY KEY,
    checkpoint_id VARCHAR(64),
    checkpoint_data JSONB,
    step INT DEFAULT 0,
    source VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_id ON checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at ON checkpoints(created_at DESC);

-- ============================================================================
-- 更新现有数据
-- ============================================================================

-- 为现有会话设置默认 mode
UPDATE sessions SET mode = 'deep' WHERE mode IS NULL AND outline IS NOT NULL;
UPDATE sessions SET mode = 'fast' WHERE mode IS NULL;

-- ============================================================================
-- 注释
-- ============================================================================

COMMENT ON COLUMN sessions.mode IS 'Workflow mode: fast (quick rewrite) or deep (interactive generation)';
COMMENT ON COLUMN sessions.ai_score IS 'AI detection score (0.0-1.0) for fast mode';
COMMENT ON COLUMN sessions.humanize_revisions IS 'Number of humanization iterations';
COMMENT ON COLUMN sessions.revision_count IS 'Number of revision iterations';
COMMENT ON COLUMN sessions.human_decision IS 'User decision at interrupt points: accept/modify/finalize';
COMMENT ON TABLE checkpoints IS 'LangGraph state checkpoints for workflow persistence and recovery';