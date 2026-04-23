-- migrations/002_add_deleted_flag.sql

-- 历史记录软删除功能

-- 新增 deleted_at 字段
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP DEFAULT NULL;

-- 索引：支持查询未删除记录
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted_at);

-- 注释
COMMENT ON COLUMN sessions.deleted_at IS '软删除时间戳，NULL 表示未删除';