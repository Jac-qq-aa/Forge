-- migrations/005_evolution_tables.sql
-- 深度模式自进化系统表结构

-- ============================================================================
-- 1. prompt_templates - Prompt模板版本表
-- ============================================================================

CREATE TABLE IF NOT EXISTS prompt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 模板标识
    template_key VARCHAR(64) NOT NULL,  -- 如 "deep_content_generator"
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN DEFAULT FALSE,    -- 当前激活版本

    -- 模板内容
    system_prompt TEXT NOT NULL,
    user_prompt_template TEXT NOT NULL,  -- 支持变量占位符 {outline}, {raw_content} 等

    -- 元数据
    change_reason TEXT,           -- 为什么修改（LLM生成的分析）
    change_summary TEXT,          -- 改动摘要
    previous_version_id UUID REFERENCES prompt_templates(id),

    -- 效果统计
    avg_quality_score DECIMAL(5,3),
    avg_human_score DECIMAL(5,3),
    avg_revision_count DECIMAL(3,1),
    sample_count INT DEFAULT 0,   -- 使用该模板的文章数量

    created_at TIMESTAMP DEFAULT NOW(),
    activated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_key ON prompt_templates(template_key, is_active);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_version ON prompt_templates(template_key, version);

-- ============================================================================
-- 2. evolution_sessions - 自进化分析会话表
-- ============================================================================

CREATE TABLE IF NOT EXISTS evolution_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 触发信息
    trigger_type VARCHAR(16) NOT NULL,  -- "threshold" / "scheduled"
    trigger_threshold INT,              -- 阈值触发时的样本数量
    analyzed_session_ids UUID[],        -- 分析的深度模式session列表

    -- 分析结果
    analysis_result JSONB,              -- LLM生成的分析报告
    suggested_changes JSONB,            -- 建议的Prompt修改

    -- 执行状态
    status VARCHAR(16) DEFAULT 'pending',  -- pending/approved/applied/rejected
    applied_template_id UUID REFERENCES prompt_templates(id),

    -- 时间戳
    created_at TIMESTAMP DEFAULT NOW(),
    analyzed_at TIMESTAMP,
    applied_at TIMESTAMP
);

-- ============================================================================
-- 3. quality_cases - 高质量案例表
-- ============================================================================

CREATE TABLE IF NOT EXISTS quality_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 来源
    source_session_id UUID NOT NULL REFERENCES sessions(id),

    -- 评分筛选条件（入库时满足）
    quality_score DECIMAL(5,3) NOT NULL,
    human_score DECIMAL(5,3) NOT NULL,
    revision_count INT NOT NULL,

    -- 精炼内容
    original_draft TEXT NOT NULL,       -- 初版草稿
    final_draft TEXT NOT NULL,          -- 定稿版本
    key_changes TEXT,                   -- 关键修改点摘要（LLM提取）

    -- 完整对话（用于Prompt优化分析）
    tuning_history JSONB NOT NULL,      -- 完整微调对话

    -- 元数据
    target_platform VARCHAR(32),
    extracted_at TIMESTAMP DEFAULT NOW(),

    -- 向量ID（关联Milvus）
    vector_id VARCHAR(64)
);

-- 防止同一session重复入库
ALTER TABLE quality_cases ADD CONSTRAINT unique_source_session UNIQUE (source_session_id);

-- 索引
CREATE INDEX IF NOT EXISTS idx_quality_cases_score ON quality_cases(quality_score);
CREATE INDEX IF NOT EXISTS idx_quality_cases_platform ON quality_cases(target_platform);
CREATE INDEX IF NOT EXISTS idx_quality_cases_session ON quality_cases(source_session_id);