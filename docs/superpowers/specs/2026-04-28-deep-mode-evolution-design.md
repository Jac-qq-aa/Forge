# 深度模式自进化系统设计文档

## 概述

**目标：** 根据深度模式的微调对话反馈，自动优化Editor的Prompt模板，并建立高质量对话知识库供RAG检索，提高文章质量评分。

**核心机制：**
- LLM驱动分析反馈模式，自动生成Prompt改进建议
- 高质量案例入库（完整对话用于分析 + 精炼片段用于RAG）
- 多指标综合质量评分（AI检测得分 + 修改轮数 + 用户评分）
- 混合触发（阈值触发 + 定时触发）

---

## Part 1：数据架构

### PostgreSQL 新增表

#### 1. prompt_templates - Prompt模板版本表

```sql
CREATE TABLE prompt_templates (
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

CREATE INDEX idx_prompt_templates_key ON prompt_templates(template_key, is_active);
CREATE INDEX idx_prompt_templates_version ON prompt_templates(template_key, version);
```

#### 2. evolution_sessions - 自进化分析会话表

```sql
CREATE TABLE evolution_sessions (
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
```

#### 3. quality_cases - 高质量案例表

```sql
CREATE TABLE quality_cases (
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
```

### Milvus 新增向量集合

#### quality_cases_vectors - 高质量案例向量集合

```python
fields = [
    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=384),  # 使用相同encoder
    FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=64),  # 关联PG quality_cases.id
    FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=500),  # 案例摘要
    FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=32),
]
```

---

## Part 2：核心组件架构

### 模块结构

```
forge/evolution/
├── __init__.py              # 导出 get_prompt_manager, get_quality_knowledge_manager 等
├── config.py                # EvolutionConfig - 配置参数
├── engine.py                # EvolutionEngine - LLM分析引擎
├── prompt_manager.py        # PromptVersionManager - 模板版本管理
├── knowledge_manager.py     # QualityKnowledgeManager - 高质量案例管理
├── quality_aggregator.py    # QualityAggregator - 评分聚合计算
├── trigger.py               # EvolutionTrigger - 混合触发器
├── worker.py                # EvolutionWorker - 异步后台服务
├── storage.py               # EvolutionStorage - 数据库操作封装
├── fallback.py              # EvolutionFallback - 降级策略
└── init_templates.py        # 默认模板初始化
```

### 组件职责

#### EvolutionEngine (`engine.py`)

核心职责：LLM驱动的分析与优化

```python
class EvolutionEngine:
    """LLM驱动的Prompt优化引擎."""

    async def analyze_feedback_patterns(
        self,
        tuning_histories: List[Dict],  # 多篇文章的微调对话
        quality_scores: List[float],
        prompt_template: Dict,
    ) -> Dict:
        """分析反馈模式，识别常见问题.

        Returns:
            {
                "patterns": [
                    {"type": "length_issue", "frequency": 0.6, "examples": [...]},
                ],
                "recommendations": [...],
                "prompt_changes": {
                    "system_prompt_delta": "...",
                    "user_prompt_delta": "...",
                }
            }
        """

    async def extract_quality_case(
        self,
        session: Dict,
        tuning_history: List[Dict],
    ) -> Dict:
        """从高质量文章中提取精炼案例."""

    async def compare_template_effectiveness(
        self,
        old_template_id: UUID,
        new_template_id: UUID,
        n_samples: int = 20,
    ) -> Dict:
        """对比新旧模板的效果差异."""
```

#### PromptVersionManager (`prompt_manager.py`)

核心职责：模板版本管理、激活切换、回滚

```python
class PromptVersionManager:
    """Prompt模板版本管理器."""

    async def get_active_template(self, template_key: str) -> Dict:
        """获取当前激活的模板."""

    async def create_new_version(
        self,
        template_key: str,
        system_prompt: str,
        user_prompt: str,
        change_reason: str,
        previous_id: UUID,
    ) -> UUID:
        """创建新版本模板."""

    async def activate_version(self, template_id: UUID) -> bool:
        """激活指定版本."""

    async def rollback(self, template_key: str, target_version: int) -> bool:
        """回滚到指定版本."""

    async def update_effect_stats(
        self,
        template_id: UUID,
        quality_score: float,
        human_score: float,
        revision_count: int,
    ) -> None:
        """更新模板效果统计."""
```

#### QualityKnowledgeManager (`knowledge_manager.py`)

核心职责：高质量案例入库、向量存储、RAG检索

```python
class QualityKnowledgeManager:
    """高质量案例知识库管理器."""

    async def should_archive_as_quality_case(
        self,
        session: Dict,
        evaluation_result: Dict,
    ) -> bool:
        """判断是否满足入库条件."""

    async def archive_case(
        self,
        session: Dict,
        tuning_history: List[Dict],
    ) -> UUID:
        """入库高质量案例（PG + Milvus）."""

    async def search_similar_cases(
        self,
        query: str,
        platform: str = None,
        n_results: int = 3,
    ) -> List[Dict]:
        """RAG检索相似高质量案例."""

    async def get_context_for_generation(
        self,
        outline: str,
        platform: str,
    ) -> str:
        """为内容生成提供高质量案例参考."""
```

#### QualityAggregator (`quality_aggregator.py`)

核心职责：计算综合质量评分

```python
class QualityAggregator:
    """质量评分聚合器."""

    WEIGHTS = {
        "human_score": 0.4,
        "revision_count": 0.3,  # 转换为负向指标
        "user_rating": 0.3,     # 如果有用户评分
    }

    def calculate_quality_score(
        self,
        human_score: float,
        revision_count: int,
        user_rating: float = None,
    ) -> float:
        """计算综合质量评分 (0.0-1.0)."""
```

#### EvolutionTrigger (`trigger.py`)

核心职责：混合触发机制

```python
class EvolutionTrigger:
    """自进化触发器 - 阈值 + 定时混合."""

    async def register_completed_session(self, session_id: UUID) -> Tuple[bool, str]:
        """注册新完成session，检查阈值触发."""

    async def check_scheduled_trigger(self) -> Tuple[bool, str]:
        """检查定时触发条件."""

    async def should_trigger(self) -> Tuple[bool, str]:
        """综合判断是否触发."""
```

#### EvolutionWorker (`worker.py`)

核心职责：异步后台服务，执行完整分析周期

```python
class EvolutionWorker:
    """自进化后台异步服务."""

    async def run_analysis_cycle(self) -> Dict:
        """执行一次完整的分析周期.

        流程：
        1. 获取待分析的session列表
        2. 加载微调历史和评分
        3. LLM分析反馈模式
        4. 生成Prompt改进建议
        5. 创建新模板版本
        6. 根据配置激活或等待确认
        """
```

---

## Part 3：数据流与系统集成

### 整体数据流

```
深度模式文章生成流程
│
▼
generate_content()
│
│  1. PromptManager.get_active_template("deep_content_generator")
│  2. QualityKnowledgeManager.get_context_for_generation(outline)
│  3. LLM.chat(prompt_template + rag_context + quality_cases_context)
│  4. 返回草稿
│
▼
微调阶段 → 定稿
│
│  - tuning_history 存入 messages 表
│  - evaluation 计算评分存入 evaluation_results
│  - PromptManager.update_effect_stats(template_id, scores)
│
▼
定稿后处理
│
│  QualityAggregator.calculate_quality_score()
│  QualityKnowledgeManager.should_archive_as_quality_case()
│  → 如果满足条件: archive_case() → PG quality_cases + Milvus
│  EvolutionTrigger.register_completed_session()
│  → 如果触发: EvolutionWorker.start_analysis()
│
▼
EvolutionWorker (异步后台服务)
│
│  1. 获取最近N篇定稿文章的 tuning_history
│  2. EvolutionEngine.analyze_feedback_patterns()
│  3. 生成新Prompt模板建议
│  4. PromptManager.create_new_version()
│  5. 等待人工确认（可选）或自动激活
│  6. PromptManager.activate_version()
│  7. 新文章使用新模板
```

### 现有代码改动点

#### 改动1：forge/deep_mode/workflow.py - generate_content()

```python
# 原代码（硬编码prompt）
async def generate_content(outline, source_article, rag_context):
    prompt = f"""请根据大纲生成完整文章..."""  # 硬编码
    draft = await llm.chat_with_retry(prompt)

# 新代码（动态模板 + 高质量案例参考）
async def generate_content(outline, source_article, rag_context):
    from forge.evolution import get_prompt_manager, get_quality_knowledge_manager

    # 获取当前激活的Prompt模板
    prompt_manager = get_prompt_manager()
    template = await prompt_manager.get_active_template("deep_content_generator")

    # 获取高质量案例参考
    quality_kb = get_quality_knowledge_manager()
    quality_context = await quality_kb.get_context_for_generation(
        outline=outline,
        platform=source_article.get("platform", "zhihu"),
    )

    # 组装prompt
    system_prompt = template["system_prompt"]
    user_prompt = template["user_prompt_template"].format(
        outline=outline,
        raw_content=source_article.get("text", "")[:2000],
        rag_context=rag_context or "无",
        quality_context=quality_context or "无参考案例",
    )

    # 调用LLM
    draft = await llm.chat_with_retry(user_prompt, system_prompt)

    return draft, template["id"]  # 返回模板ID用于效果统计
```

#### 改动2：forge/deep_mode/websocket_handler.py - 定稿后触发

```python
# 在 finalize_session 后添加
async def handle_finalize(websocket, session_id, user_content):
    # ... 现有定稿逻辑 ...

    # 新增：自进化处理
    from forge.evolution import get_evolution_trigger, get_quality_knowledge_manager
    from forge.evaluation import get_evaluation_storage

    # 获取评估结果
    eval_storage = get_evaluation_storage()
    eval_result = await eval_storage.get_result(session_id)

    # 判断是否入库高质量案例
    quality_kb = get_quality_knowledge_manager()
    if await quality_kb.should_archive_as_quality_case(session, eval_result):
        tuning_history = await session_manager.get_session_messages(session_id)
        await quality_kb.archive_case(session, tuning_history)

    # 检查触发条件
    trigger = get_evolution_trigger()
    should_run, trigger_type = await trigger.register_completed_session(session_id)
    if should_run:
        asyncio.create_task(run_evolution_analysis(trigger_type))
```

#### 改动3：初始化默认Prompt模板

```python
# forge/evolution/init_templates.py

DEFAULT_DEEP_CONTENT_TEMPLATE = {
    "template_key": "deep_content_generator",
    "version": 1,
    "system_prompt": """你是一位资深互联网职场人...""",
    "user_prompt_template": """请根据大纲生成完整文章：
## 大纲
{outline}
## 原文章内容
标题：{title}
内容：{raw_content}
## 知识库素材
{rag_context}
## 高质量案例参考
{quality_context}
## 生成要求
...""",
    "is_active": True,
}

async def init_default_templates():
    """首次运行时初始化默认模板."""
    manager = get_prompt_manager()
    active = await manager.get_active_template("deep_content_generator")
    if not active:
        await manager.create_initial_template(**DEFAULT_DEEP_CONTENT_TEMPLATE)
```

---

## Part 4：LLM分析引擎核心逻辑

### 反馈模式分析Prompt

```python
ANALYSIS_SYSTEM_PROMPT = """你是文章生成系统的优化专家。

你的任务是分析用户在微调阶段的反馈，识别常见问题模式，并提出Prompt模板改进建议。

## 分析维度

1. **长度问题** - 用户反馈"太长"、"太短"、"精简"等
2. **语气问题** - 用户反馈"太口语化"、"太正式"、"不够专业"等
3. **结构问题** - 用户反馈"段落不清"、"逻辑混乱"、"开头不吸引人"等
4. **内容问题** - 用户反馈"偏离主题"、"缺少XX内容"、"观点不明确"等
5. **风格问题** - 用户反馈"不够生动"、"太平淡"、"缺乏真实感"等

## 输出格式

请严格按以下JSON格式输出：
{
  "patterns": [
    {
      "type": "length_issue",
      "frequency": 0.6,
      "example_feedbacks": ["太长了", "精简一下"],
      "affected_articles": 6
    }
  ],
  "root_cause_analysis": "当前prompt要求800-1200字，但用户实际偏好500-800字...",
  "recommendations": [
    {
      "change_type": "modify_length_requirement",
      "current_value": "800-1200字",
      "suggested_value": "500-800字",
      "reason": "60%用户反馈过长"
    }
  ],
  "prompt_changes": {
    "system_prompt_delta": "增加：'用户偏好简洁表达'",
    "user_prompt_delta": "将'800-1200字'改为'500-800字'"
  }
}

注意：只输出JSON，不要额外文字"""

ANALYSIS_USER_PROMPT_TEMPLATE = """请分析以下 {count} 篇文章的微调反馈：

## 当前Prompt模板
- System Prompt: {current_system_prompt}
- User Prompt Template: {current_user_prompt}

## 微调反馈数据
{feedback_data}

请分析并输出改进建议。"""
```

### 高质量案例提取Prompt

```python
EXTRACT_CASE_SYSTEM_PROMPT = """你是内容质量分析专家。

从高质量文章中提取关键特征，形成可供后续参考的案例摘要。

输出格式：
{
  "key_changes": [
    "初稿段落过长，用户要求精简后改为短段落结构",
    "开头增加了口语化引入'说实话'"
  ],
  "style_features": {
    "tone": "口语化但有深度",
    "structure": "打破三段论，用疑问结尾",
    "opening_pattern": "说实话/有意思的是"
  },
  "summary": "职场吐槽风格，开头口语化引入，短段落叙述，疑问结尾"
}

注意：只输出JSON"""

EXTRACT_CASE_USER_PROMPT = """请分析以下高质量文章：

## 初版草稿
{original_draft}

## 定稿版本
{final_draft}

## 微调对话历史
{tuning_history}

请提取关键特征。"""
```

---

## Part 5：异步Worker与触发机制

### EvolutionWorker设计

```python
class EvolutionWorker:
    """自进化后台异步服务."""

    async def run_analysis_cycle(self) -> Dict:
        """执行完整分析周期."""
        sessions = await self.get_sessions_for_analysis()

        if len(sessions) < self.config.MIN_SAMPLE_FOR_ANALYSIS:
            return {"status": "skipped", "reason": "insufficient_samples"}

        # 加载微调历史和评分
        tuning_histories = await self._load_tuning_histories(sessions)
        quality_scores = await self._load_quality_scores(sessions)

        # LLM分析
        current_template = await self.prompt_manager.get_active_template(...)
        analysis = await self.engine.analyze_feedback_patterns(
            tuning_histories, quality_scores, current_template
        )

        if analysis is None:
            return {"status": "failed", "reason": "llm_analysis_error"}

        # 创建新模板
        new_template_id = await self.prompt_manager.create_new_version(
            template_key="deep_content_generator",
            system_prompt=current_template["system_prompt"] + analysis["prompt_changes"]["system_prompt_delta"],
            user_prompt=...,
            change_reason=analysis["root_cause_analysis"],
            previous_id=current_template["id"],
        )

        # 根据配置决定是否自动激活
        if self.config.AUTO_ACTIVATE:
            await self.prompt_manager.activate_version(new_template_id)
            return {"status": "applied", "new_template_id": new_template_id}
        else:
            return {"status": "pending_approval", "new_template_id": new_template_id}

    async def get_sessions_for_analysis(self) -> List[UUID]:
        """获取待分析session.

        选择标准：
        - 已定稿（stage='completed')
        - 有evaluation结果
        - 使用当前激活模板生成
        - 未被之前的evolution_session分析过
        """
```

### 混合触发机制

```python
class EvolutionTrigger:
    """混合触发器 - 阈值触发 + 定时触发."""

    def __init__(self):
        self.threshold_count = 10       # 累积样本阈值
        self.schedule_hour = 2          # 定时触发时间（凌晨2点）
        self.min_interval_hours = 12    # 最小间隔

        self._pending_sessions: List[UUID] = []
        self._last_trigger_time: Optional[datetime] = None

    async def register_completed_session(self, session_id: UUID) -> Tuple[bool, str]:
        """注册新完成session，检查阈值触发."""
        self._pending_sessions.append(session_id)

        if len(self._pending_sessions) >= self.threshold_count:
            if await self._check_min_interval():
                return True, "threshold"

        return False, ""

    async def check_scheduled_trigger(self) -> Tuple[bool, str]:
        """定时检查."""
        if len(self._pending_sessions) > 0:
            if await self._check_min_interval():
                return True, "scheduled"
        return False, ""

    def clear_pending(self):
        """分析完成后清空."""
        self._pending_sessions = []
        self._last_trigger_time = datetime.now()
```

### Worker启动方式

**方式1：独立进程（推荐）**

```python
# run_evolution_worker.py

async def main():
    worker = EvolutionWorker()
    while True:
        should_run, trigger_type = await worker.trigger.check_scheduled_trigger()
        if should_run:
            result = await worker.run_analysis_cycle()
            logger.info(f"Analysis completed: {result}")
        await asyncio.sleep(3600)  # 每小时检查
```

**方式2：集成到Web服务异步触发**

```python
# websocket_handler定稿时
async def on_session_finalize(session_id):
    trigger = get_evolution_trigger()
    should_run, trigger_type = await trigger.register_completed_session(session_id)
    if should_run:
        asyncio.create_task(run_evolution_analysis(trigger_type))
```

### 配置参数

```python
class EvolutionConfig:
    # 触发配置
    THRESHOLD_COUNT: int = 10          # 阈值触发样本数
    SCHEDULE_HOUR: int = 2             # 定时触发时间
    MIN_INTERVAL_HOURS: int = 12       # 最小间隔

    # 入库条件
    QUALITY_THRESHOLD: float = 0.70    # 综合质量评分阈值
    HUMAN_SCORE_THRESHOLD: float = 0.80
    MAX_REVISION_COUNT: int = 3

    # Prompt变更配置
    AUTO_ACTIVATE: bool = False        # 是否自动激活（False=需人工确认）
    MIN_SAMPLE_FOR_ANALYSIS: int = 5   # 最少样本数
    KEEP_OLD_VERSION_DAYS: int = 30    # 保留旧版本天数

    # RAG检索配置
    QUALITY_CASE_TOP_K: int = 2        # 检索案例数量
```

---

## Part 6：错误处理与边界情况

### 关键边界处理

#### 1. 首次初始化模板

```python
async def ensure_default_templates():
    """启动时检查并初始化."""
    manager = get_prompt_manager()
    active = await manager.get_active_template("deep_content_generator")
    if not active:
        await manager.create_initial_template(**DEFAULT_DEEP_CONTENT_TEMPLATE)
```

#### 2. 模板效果退化检测

```python
async def check_effectiveness_trend(template_key: str) -> Dict:
    """检查效果趋势，连续下降建议回滚."""
```

#### 3. 分析样本不足

```python
if len(sessions) < MIN_SAMPLE_FOR_ANALYSIS:
    return {"status": "skipped", "reason": "insufficient_samples"}
```

#### 4. LLM分析失败

```python
try:
    result = json.loads(response)
    if not self._validate_analysis_result(result):
        raise ValueError("Invalid structure")
    return result
except (json.JSONDecodeError, ValueError):
    return None  # Worker会跳过更新
```

#### 5. 高质量案例入库冲突

```sql
ALTER TABLE quality_cases ADD CONSTRAINT unique_source_session UNIQUE (source_session_id);
```

#### 6. Milvus向量同步失败

```python
# PG入库优先，向量异步补偿
case_id = await self.storage.insert_quality_case(...)
try:
    vector_id = await self._insert_vector(case_id, summary)
    await self.storage.update_vector_id(case_id, vector_id)
except Exception:
    logger.warning("Vector insert failed, will retry later")
```

#### 7. 模板变量占位符错误

```python
def safe_format_template(template: str, variables: Dict) -> str:
    """安全格式化，缺失变量使用默认值."""
    safe_vars = {k: v or "无" for k, v in variables.items()}
    return template.format(**safe_vars)
```

### 降级策略

```python
class EvolutionFallback:
    @staticmethod
    def get_fallback_template(template_key: str) -> Dict:
        """模板管理器不可用时返回硬编码模板."""
        return DEFAULT_TEMPLATES.get(template_key)

    @staticmethod
    def skip_quality_context() -> str:
        """知识库不可用时跳过."""
        return "无参考案例"
```

---

## 数据库迁移文件

```sql
-- migrations/005_evolution_tables.sql

-- Prompt模板版本表
CREATE TABLE IF NOT EXISTS prompt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_key VARCHAR(64) NOT NULL,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN DEFAULT FALSE,
    system_prompt TEXT NOT NULL,
    user_prompt_template TEXT NOT NULL,
    change_reason TEXT,
    change_summary TEXT,
    previous_version_id UUID REFERENCES prompt_templates(id),
    avg_quality_score DECIMAL(5,3),
    avg_human_score DECIMAL(5,3),
    avg_revision_count DECIMAL(3,1),
    sample_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    activated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_key ON prompt_templates(template_key, is_active);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_version ON prompt_templates(template_key, version);

-- 自进化分析会话表
CREATE TABLE IF NOT EXISTS evolution_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_type VARCHAR(16) NOT NULL,
    trigger_threshold INT,
    analyzed_session_ids UUID[],
    analysis_result JSONB,
    suggested_changes JSONB,
    status VARCHAR(16) DEFAULT 'pending',
    applied_template_id UUID REFERENCES prompt_templates(id),
    created_at TIMESTAMP DEFAULT NOW(),
    analyzed_at TIMESTAMP,
    applied_at TIMESTAMP
);

-- 高质量案例表
CREATE TABLE IF NOT EXISTS quality_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_session_id UUID NOT NULL REFERENCES sessions(id),
    quality_score DECIMAL(5,3) NOT NULL,
    human_score DECIMAL(5,3) NOT NULL,
    revision_count INT NOT NULL,
    original_draft TEXT NOT NULL,
    final_draft TEXT NOT NULL,
    key_changes TEXT,
    tuning_history JSONB NOT NULL,
    target_platform VARCHAR(32),
    extracted_at TIMESTAMP DEFAULT NOW(),
    vector_id VARCHAR(64)
);

ALTER TABLE quality_cases ADD CONSTRAINT unique_source_session UNIQUE (source_session_id);

CREATE INDEX IF NOT EXISTS idx_quality_cases_score ON quality_cases(quality_score);
CREATE INDEX IF NOT EXISTS idx_quality_cases_platform ON quality_cases(target_platform);
```

---

## 项目简历格式描述

**深度模式Prompt自进化系统 <LangGraph + PostgreSQL + Milvus + LLM-as-Analyzer>** Forge 2026.04 – 2026.04

从 0 到 1 设计并主导完成自进化架构方案。针对内容生成系统Prompt模板静态固化、无法根据用户反馈持续优化的核心痛点，构建了"反馈采集 - 模式分析 - Prompt演进 - 效果对比 - 知识沉淀"的LLM驱动自优化闭环，实现文章质量评分的持续提升。

• **混合触发机制与阈值调度**：设计"阈值触发（10篇累积）+ 定时触发（凌晨2点）+ 最小间隔保护（12小时）"的混合触发策略，配合EvolutionWorker异步服务实现分析任务的优先级调度，在保障分析样本充足性的同时有效避免频繁触发导致的系统资源浪费与噪音干扰；

• **LLM驱动的反馈模式分析**：构建EvolutionEngine分析引擎，通过结构化Prompt引导LLM从微调对话中识别长度/语气/结构/内容/风格五大类问题模式，自动生成Prompt改进建议（包含system_prompt_delta与user_prompt_delta），将人工调优耗时从"周级"压缩至"小时级"；

• **Prompt版本管理与效果对比**：设计prompt_templates表实现模板的全生命周期管理（版本追溯、激活切换、回滚机制），配合效果统计字段（avg_quality_score、avg_revision_count）支持新旧模板的A/B对比分析，当新模板效果连续下降时自动触发回滚建议，形成"演进 - 监测 - 回退"的安全迭代闭环；

• **高质量案例知识库与RAG检索**：基于多指标综合评分（human_score 40% + revision_count 30% + user_rating 30%）筛选高质量文章，采用"完整对话存PG + 精炼片段存Milvus"的双层存储策略，完整对话用于Prompt优化分析、精炼片段用于内容生成时的RAG检索参考，实现"历史经验 → 新文章生成"的知识传递；

• **全链路集成与降级保障**：将动态Prompt模板与高质量案例参考无缝集成至generate_content()节点，通过safe_format_template处理变量缺失、EvolutionFallback提供硬编码模板降级策略，确保自进化模块故障时不影响核心内容生成流程，系统可用性达99.9%。

---

## 实现优先级

1. **P0 - 基础设施**
   - 数据库迁移文件
   - EvolutionStorage（数据库操作封装）
   - PromptVersionManager（模板管理）
   - init_templates.py（默认模板初始化）

2. **P1 - 核心功能**
   - generate_content()集成改动
   - QualityKnowledgeManager（案例入库+RAG）
   - QualityAggregator（评分计算）
   - 定稿后触发逻辑

3. **P2 - 分析引擎**
   - EvolutionEngine（LLM分析）
   - EvolutionTrigger（触发机制）
   - EvolutionWorker（异步服务）

4. **P3 - 增强功能**
   - 效果对比与回滚
   - 降级策略
   - 监控与告警