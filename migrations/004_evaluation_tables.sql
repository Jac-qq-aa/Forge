-- migrations/004_evaluation_tables.sql

-- 评估系统表迁移

-- probe_logs: 节点探针日志
CREATE TABLE IF NOT EXISTS probe_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 关联信息
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    article_id VARCHAR(64),

    -- 节点信息
    node_name VARCHAR(32) NOT NULL,
    node_sequence INT NOT NULL,
    timestamp TIMESTAMP NOT NULL,

    -- 执行特征
    input_metrics JSONB,
    output_metrics JSONB,
    duration_ms INT,

    -- 循环标记
    loop_type VARCHAR(32),
    loop_iteration INT DEFAULT 0,

    -- 元数据
    metadata JSONB
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_probe_logs_session ON probe_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_probe_logs_node ON probe_logs(node_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_probe_logs_loop ON probe_logs(loop_type, session_id);

-- evaluation_results: 评估结果汇总
CREATE TABLE IF NOT EXISTS evaluation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 关联信息
    session_id UUID NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    article_id VARCHAR(64),

    -- 一级指标（用户展示）- 评分范围 0.000-1.000
    overall_score DECIMAL(5,3),
    faithfulness_score DECIMAL(5,3),
    relevance_score DECIMAL(5,3),
    human_score DECIMAL(5,3),

    -- 二级指标详情（后台分析）
    metrics_detail JSONB,

    -- 节点效率汇总
    node_effectiveness JSONB,

    -- 循环ROI汇总
    loop_roi JSONB,

    -- 时间戳
    created_at TIMESTAMP DEFAULT NOW(),
    evaluated_at TIMESTAMP,

    -- 状态
    status VARCHAR(16) DEFAULT 'pending',
    error_message TEXT
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_eval_results_score ON evaluation_results(overall_score, created_at);
CREATE INDEX IF NOT EXISTS idx_eval_results_status ON evaluation_results(status);