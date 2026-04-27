# Forge 评估系统设计方案

## 概述

为Forge多Agent内容生成系统设计评估方案，目标：
- **系统优化调试** - 发现瓶颈，改进改写质量
- **用户反馈展示** - 展示质量分数，增加信任度
- **A/B测试对比** - 对比不同策略/模型优劣

核心需求：
- 无金标准数据集，需适配现有数据
- 复用Qwen API作为裁判模型
- 量化节点有效性和循环迭代ROI
- 实时展示简单分数 + 后台详细分析

## 一、整体架构

### 异步旁路设计

评估不阻塞主用户流程，采用"轻量探针 + 队列 + 后台Worker"架构：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Forge 评估系统架构（异步旁路模式）                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   【主流程 - 用户请求链路】                                                    │
│                                                                              │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐              │
│   │              │      │              │      │              │              │
│   │  Workflow    │─────▶│  Node Probe  │─────▶│  继续执行     │              │
│   │  (同步执行)   │      │ (轻量探针)    │      │  下一个节点   │              │
│   │              │      │              │      │              │              │
│   └──────────────┘      └──────┬───────┘      └──────────────┘              │
│                                 │                                            │
│                                 │ 极轻量：打包数据 + push队列                  │
│                                 │ < 10ms，不阻塞                              │
│                                 ▼                                            │
│                         ┌──────────────┐                                     │
│                         │              │                                     │
│                         │ Redis Queue  │                                     │
│                         │ (消息队列)    │                                     │
│                         │              │                                     │
│                         └──────────────┘                                     │
│                                 │                                            │
│   ═══════════════════════════════════════════════════════════════════════    │
│   【评估流程 - 异步消费链路】                                                   │
│                                 │                                            │
│                                 ▼                                            │
│                         ┌──────────────┐      ┌──────────────┐              │
│                         │              │      │              │              │
│                         │ Evaluation   │─────▶│ PostgreSQL   │              │
│                         │ Worker       │      │ (评估结果表)  │              │
│                         │ (后台服务)    │      │              │              │
│                         │              │      └──────────────┘              │
│                         └──────────────┘                                     │
│                                 │                                            │
│                                 ▼                                            │
│                         ┌──────────────┐                                     │
│                         │              │                                     │
│                         │ Report API   │                                     │
│                         │ (查询展示)    │                                     │
│                         │              │                                     │
│                         └──────────────┘                                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 职责 | 耗时 |
|------|------|------|
| Node Probe | 打包节点数据 + push Redis队列 | < 10ms |
| Redis Queue | 消息队列，存储待评估数据包 | - |
| Evaluation Worker | 后台消费队列，执行RAGAS+LLM评估 | 异步，不阻塞 |
| PostgreSQL | 存储probe_logs + evaluation_results | - |
| Report API | 查询评估结果，返回用户端/后台展示 | - |

### 队列数据格式

探针push的数据包：

```json
{
  "session_id": "abc-123",
  "node_name": "editor",
  "timestamp": "2026-04-26T10:30:00Z",
  "input_metrics": {
    "revision_count": 0,
    "ai_score": 0.0,
    "draft_length": 0,
    "stage": "executing"
  },
  "output_metrics": {
    "revision_count": 1,
    "draft_length": 1200,
    "ai_score": 0.0
  },
  "duration_ms": 3500,
  "loop_type": null,
  "loop_iteration": 0,
  "metadata": {
    "target_platform": "zhihu_article"
  }
}
```

## 二、指标体系

### 优先级排序

```
事实与业务对齐度 > Agent执行效率 > 文本质量与风格
```

### 一级指标（用户端展示）

| 指标名 | 说明 | 计算方式 | 权重 |
|--------|------|----------|------|
| **综合评分** | 用户看到的总分数 | 加权平均 | 100% |
| 事实忠实度 | 文章是否"无中生有" | RAGAS Faithfulness | 40% |
| 内容相关性 | 是否跑题/答非所问 | RAGAS Answer Relevance | 30% |
| AI特征评分 | 文章是否像AI写的 | 100 - (AI_score × 100) | 30% |

综合评分公式：
```
overall_score = faithfulness × 0.4 + relevance × 0.3 + human_score × 0.3
```

### 二级指标（后台分析）

#### 事实与业务对齐类

| 指标 | 定义 | 数据来源 | 算法 |
|------|------|----------|------|
| Faithfulness | 文章claims是否能在源材料找到支撑 | 原文 + RAG知识库 + Fact Sheet | RAGAS Faithfulness算子 |
| Context Utilization | RAG检索信息有多少被写入文章 | RAG Top-K chunks vs 文章内容 | RAGAS Context Recall算子 |
| Answer Relevance | 文章是否回答用户原始需求 | 用户prompt + 大纲 vs 文章 | RAGAS Answer Relevance算子 |
| Claim Accuracy | 具体数据/事实是否准确 | Fact Sheet claims vs 联网验证 | LLM Judge + Web Search |

#### Agent执行效率类

| 指标 | 定义 | 数据来源 | 算法 |
|------|------|----------|------|
| Node Gain | 节点执行后质量分数变化 | Probe Logs前后对比 | output_score - input_score |
| Loop ROI | 循环迭代投入产出比 | 循环次数 vs 最终增益 | (final_score - initial_score) / loop_count |
| Reflection Effectiveness | Critic反馈采纳率 | Critic建议 vs 修改后文章 | LLM Judge匹配度 |
| Time Efficiency | 各节点耗时占比 | Probe Logs duration_ms | 节点耗时 / 总耗时 |

#### 风格质量类

| 指标 | 定义 | 数据来源 | 算法 |
|------|------|----------|------|
| AI Score | AI生成概率 | 文章内容 | qwen-max判断（已有） |
| Perplexity | 文本惊喜度/复杂度 | 文章内容 | 本地算法（已有fallback） |
| Burstiness | 句子长度变化度 | 文章内容 | 本地算法（已有fallback） |
| AI Word Ratio | AI常用词汇占比 | 文章内容 vs AI词汇库 | 词频统计 |
| Brand Tone Match | 品牌语气一致性 | 文章 vs 品牌风格模板 | LLM Judge |

### 节点有效性指标

计算公式：
```
Node Effectiveness = (输出质量分数 - 输入质量分数) / 节点耗时秒数
```

各节点监控指标：

| 节点 | 监控指标 | 有效性判定 |
|------|----------|------------|
| Editor | ai_score变化、faithfulness变化 | score下降 → 有效 |
| AI_Detector | 检测准确性（人工校验样本） | 误报率 < 20% → 有效 |
| Humanizer | ai_score下降幅度 | 每次迭代下降 > 0.1 → 有效 |
| Reviewer | 文章质量提升 | feedback后revision质量 > 原版 → 有效 |
| ResearchAgent | fact_sheet质量、覆盖率 | 被引用facts > 50% → 有效 |
| ReflectionWriter | draft质量、critic采纳率 | critic建议采纳 > 60% → 有效 |

### 循环ROI指标

| 循环类型 | 计算方式 | 建议阈值 |
|----------|----------|----------|
| Humanizer循环 | (initial_ai_score - final_ai_score) / iterations | ROI > 0.1 继续迭代 |
| Reviewer循环 | (final_quality - initial_quality) / iterations | ROI > 0.05 继续迭代 |
| Reflect循环 | (final_quality - initial_quality) / iterations | ROI > 0.08 继续迭代 |

## 三、数据存储设计

### PostgreSQL表结构

#### probe_logs表

```sql
CREATE TABLE probe_logs (
    id SERIAL PRIMARY KEY,

    -- 关联信息
    session_id VARCHAR(64) NOT NULL,
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

CREATE INDEX idx_probe_logs_session ON probe_logs(session_id);
CREATE INDEX idx_probe_logs_node ON probe_logs(node_name, timestamp);
CREATE INDEX idx_probe_logs_loop ON probe_logs(loop_type, session_id);
```

input_metrics字段示例：

```json
{
  "ai_score": 0.85,
  "revision_count": 1,
  "humanize_revisions": 0,
  "draft_length": 1200,
  "faithfulness": null,
  "has_knowledge_context": true
}
```

#### evaluation_results表

```sql
CREATE TABLE evaluation_results (
    id SERIAL PRIMARY KEY,

    -- 关联信息
    session_id VARCHAR(64) NOT NULL UNIQUE,
    article_id VARCHAR(64),

    -- 一级指标
    overall_score DECIMAL(3,2),
    faithfulness_score DECIMAL(3,2),
    relevance_score DECIMAL(3,2),
    human_score DECIMAL(3,2),

    -- 二级指标详情
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

CREATE INDEX idx_eval_results_session ON evaluation_results(session_id);
CREATE INDEX idx_eval_results_score ON evaluation_results(overall_score, created_at);
CREATE INDEX idx_eval_results_status ON evaluation_results(status);
```

metrics_detail字段示例：

```json
{
  "faithfulness": {
    "score": 0.85,
    "claims_total": 12,
    "claims_supported": 10,
    "claims_hallucinated": 2
  },
  "context_utilization": {
    "score": 0.65,
    "rag_chunks_retrieved": 5,
    "rag_chunks_used": 3
  },
  "answer_relevance": {
    "score": 0.92,
    "reason": "文章紧扣大纲核心观点"
  },
  "ai_detection": {
    "ai_score": 0.45,
    "perplexity": "中",
    "burstiness": "高",
    "ai_word_count": 2
  },
  "reflection_effectiveness": {
    "critic_suggestions": 4,
    "suggestions_adopted": 3,
    "adoption_rate": 0.75
  }
}
```

node_effectiveness字段示例：

```json
{
  "editor": {
    "gain": 0.15,
    "duration_ms": 3500,
    "effectiveness": 0.043
  },
  "humanizer_loop": {
    "iterations": 3,
    "initial_ai_score": 0.85,
    "final_ai_score": 0.45,
    "gain": 0.40,
    "roi": 0.133
  },
  "reviewer": {
    "iterations": 1,
    "gain": 0.10,
    "effectiveness": 0.025
  }
}
```

## 四、评估流程

### 流程一：探针插入

#### 探针核心代码

```python
# forge/evaluation/probe.py

import json
import time
import redis
from forge.config import REDIS_HOST, REDIS_PORT

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1)
QUEUE_NAME = "forge:evaluation:queue"

def probe_node(node_name: str, state_before: dict, state_after: dict,
               duration_ms: int, loop_info: dict = None):
    """节点探针 - 轻量数据打包 + push队列，耗时 < 10ms"""
    session_id = state_before.get("session_id", "unknown")

    input_metrics = extract_key_metrics(state_before)
    output_metrics = extract_key_metrics(state_after)

    payload = {
        "session_id": session_id,
        "node_name": node_name,
        "timestamp": time.time(),
        "input_metrics": input_metrics,
        "output_metrics": output_metrics,
        "duration_ms": duration_ms,
        "loop_type": loop_info.get("loop_type") if loop_info else None,
        "loop_iteration": loop_info.get("iteration", 0) if loop_info else 0,
        "metadata": {"target_platform": state_after.get("target_platform")}
    }

    redis_client.lpush(QUEUE_NAME, json.dumps(payload))


def extract_key_metrics(state: dict) -> dict:
    """提取关键指标，包含文章摘要用于评估"""
    draft = state.get("rewritten_draft", "") or state.get("current_draft", "")
    return {
        "ai_score": state.get("ai_score", 0.0),
        "revision_count": state.get("revision_count", 0),
        "humanize_revisions": state.get("humanize_revisions", 0),
        "reflection_revision_count": state.get("reflection_revision_count", 0),
        "draft_length": len(draft),
        "draft_text": draft[:1000] if draft else "",  # 存储前1000字摘要用于评估
        "is_approved": state.get("is_approved", False),
        "stage": state.get("stage"),
        "has_rag_context": bool(state.get("rag_context")),
        "has_fact_sheet": bool(state.get("fact_sheet")),
    }
```

#### 装饰器方式插入

```python
# forge/evaluation/probe_decorator.py

import functools
import time
from forge.evaluation.probe import probe_node

def with_probe(node_name: str, loop_type: str = None):
    """节点探针装饰器"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(state):
            start_time = time.time()
            state_before = dict(state)

            result = await func(state)

            duration_ms = int((time.time() - start_time) * 1000)

            loop_info = None
            if loop_type:
                iteration_key = f"{loop_type}_iterations"
                loop_info = {
                    "loop_type": loop_type,
                    "iteration": state.get(iteration_key, 0) + 1
                }

            probe_node(node_name, state_before, result, duration_ms, loop_info)

            return result
        return wrapper
    return decorator
```

#### 节点改造示例

```python
@with_probe("editor")
async def editor_node(state: GraphState) -> dict:
    # ...原有逻辑不变...

@with_probe("humanizer_editor", loop_type="humanize_loop")
async def humanizer_editor_node(state: GraphState) -> dict:
    # ...原有逻辑不变...
```

### 流程二：Evaluation Worker

```python
# forge/evaluation/worker.py

import json
import asyncio
import redis
import logging
from forge.config import REDIS_HOST, REDIS_PORT
from forge.evaluation.engine import EvaluationEngine
from forge.evaluation.storage import save_probe_log, save_evaluation_result, get_session_probe_logs

logger = logging.getLogger(__name__)
QUEUE_NAME = "forge:evaluation:queue"

async def evaluation_worker():
    """后台Worker - 消费队列，执行评估"""
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1)
    engine = EvaluationEngine()

    logger.info("[EvalWorker] Started, listening to queue...")

    while True:
        result = redis_client.brpop(QUEUE_NAME, timeout=30)

        if result is None:
            continue

        _, payload_json = result
        payload = json.loads(payload_json)

        logger.info(f"[EvalWorker] Processing: {payload['session_id']} / {payload['node_name']}")

        try:
            # 1. 保存probe log
            save_probe_log(payload)

            # 2. 判断是否是session最后节点（触发完整评估）
            if payload["node_name"] in ["director", "finalize"]:
                session_logs = get_session_probe_logs(payload["session_id"])
                eval_result = await engine.evaluate_session(session_logs)
                save_evaluation_result(payload["session_id"], eval_result)

                logger.info(f"[EvalWorker] Evaluation completed: {payload['session_id']}")

        except Exception as e:
            logger.error(f"[EvalWorker] Failed: {e}")
```

### 流程三：Evaluation Engine

```python
# forge/evaluation/engine.py

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevance, context_recall
from forge.tools.llm_client import LLMClient
from forge.evaluation.probe_calculator import calculate_node_effectiveness, calculate_loop_roi

class EvaluationEngine:
    def __init__(self):
        self.llm_client = LLMClient()

    async def evaluate_session(self, probe_logs: list) -> dict:
        """执行完整session评估"""
        final_log = probe_logs[-1]
        final_draft = final_log.get("output_metrics", {}).get("draft_text", "")

        # 1. RAGAS评估
        ragas_scores = await self._run_ragas_evaluation(probe_logs, final_draft)

        # 2. LLM Judge风格评估
        style_scores = await self._run_style_evaluation(final_draft)

        # 3. 节点有效性计算
        node_effectiveness = calculate_node_effectiveness(probe_logs)

        # 4. 循环ROI计算
        loop_roi = calculate_loop_roi(probe_logs)

        # 5. 综合评分
        overall_score = self._calculate_overall(ragas_scores, style_scores)

        return {
            "overall_score": overall_score,
            "faithfulness_score": ragas_scores["faithfulness"],
            "relevance_score": ragas_scores["answer_relevance"],
            "human_score": 1 - style_scores["ai_score"],
            "metrics_detail": {**ragas_scores, **style_scores},
            "node_effectiveness": node_effectiveness,
            "loop_roi": loop_roi,
            "status": "completed",
        }

    async def _run_ragas_evaluation(self, probe_logs: list, final_draft: str) -> dict:
        """RAGAS评估 - 无金标准适配"""
        # 从probe_logs提取材料
        raw_content = self._extract_raw_content(probe_logs)
        rag_chunks = self._extract_rag_chunks(probe_logs)
        user_prompt = self._extract_user_prompt(probe_logs)

        eval_data = {
            "question": user_prompt,
            "answer": final_draft,
            "contexts": rag_chunks,
            "reference": raw_content.get("text", ""),
        }

        # 调用RAGAS（使用qwen作为裁判）
        from datasets import Dataset
        dataset = Dataset.from_dict({k: [v] for k, v in eval_data.items()})

        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevance, context_recall],
            llm=self.llm_client.llm,
        )

        return {
            "faithfulness": result["faithfulness"][0],
            "answer_relevance": result["answer_relevance"][0],
            "context_utilization": result["context_recall"][0],
        }

    async def _run_style_evaluation(self, final_draft: str) -> dict:
        """风格质量评估"""
        prompt = f"""请评估以下文章的AI特征评分（满分100）：

文章内容：
{final_draft[:1000]}

请按以下格式回复：
【AI特征评分】XX
【口语化程度】XX
【结构自然度】XX
"""

        response = await self.llm_client.chat_with_retry(prompt)
        ai_score = parse_score(response, "AI特征评分") / 100

        return {"ai_score": ai_score}

    def _calculate_overall(self, ragas_scores: dict, style_scores: dict) -> float:
        """综合评分：Faithfulness 40% + Relevance 30% + Human 30%"""
        faith = ragas_scores.get("faithfulness", 0.5)
        relevance = ragas_scores.get("answer_relevance", 0.5)
        human = 1 - style_scores.get("ai_score", 0.5)
        return faith * 0.4 + relevance * 0.3 + human * 0.3

    # 辅助函数
    def _extract_raw_content(self, probe_logs: list) -> dict:
        """从scout节点的probe log提取原始内容"""
        for log in probe_logs:
            if log["node_name"] == "scout":
                return log["metadata"].get("raw_content", {})
        return {}

    def _extract_rag_chunks(self, probe_logs: list) -> list:
        """从节点metadata提取RAG检索的chunks"""
        chunks = []
        for log in probe_logs:
            if log["metadata"].get("rag_chunks"):
                chunks.extend(log["metadata"]["rag_chunks"])
        return chunks

    def _extract_user_prompt(self, probe_logs: list) -> str:
        """从首个节点提取用户prompt/大纲"""
        if probe_logs:
            return probe_logs[0]["metadata"].get("user_input", "") or \
                   probe_logs[0]["metadata"].get("outline", "")
        return ""


def parse_score(response: str, label: str) -> int:
    """从LLM响应中解析分数"""
    import re
    match = re.search(rf"【{label}】(\d+)", response)
    if match:
        return int(match.group(1))
    return 50  # 默认分数

## 五、展示层设计

### 用户端展示

简单分数卡片：

```
┌─────────────────────────────────────────────────┐
│              📊 文章质量评分                        │
├─────────────────────────────────────────────────┤
│           ┌─────────────────────┐               │
│           │      综合评分        │               │
│           │       85分          │               │
│           └─────────────────────┘               │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│   │忠实度    │  │相关性   │  │拟人性    │        │
│   │  92分   │  │  88分   │  │  70分   │        │
│   └─────────┘  └─────────┘  └─────────┘        │
│   💡 提示：拟人性评分较低，建议检查AI常用词汇        │
└─────────────────────────────────────────────────┘
```

API接口：

```python
@app.get("/api/evaluation/{session_id}")
async def get_evaluation(session_id: str):
    result = get_evaluation_result(session_id)

    if result is None or result["status"] != "completed":
        return {"status": "pending", "message": "评估正在进行中"}

    return {
        "overall_score": round(result["overall_score"] * 100),
        "faithfulness": round(result["faithfulness_score"] * 100),
        "relevance": round(result["relevance_score"] * 100),
        "human_score": round(result["human_score"] * 100),
        "tip": generate_tip(result),
    }
```

### 后台分析报告

#### 节点效率报告

```
┌─────────────────────────────────────────────────────────────────────┐
│                    📈 节点效率分析报告                                 │
│                    Session: abc-123                                  │
├─────────────────────────────────────────────────────────────────────┤
│  节点           │ 输入分数 │ 输出分数 │ 增益   │ 耗时   │ 有效判定    │
│  ───────────────┼─────────┼─────────┼───────┼───────┼────────────  │
│  editor         │   0.15  │   0.30  │ +0.15 │ 3500  │ ✅ 有效      │
│  humanizer(1)   │   0.85  │   0.65  │ -0.20 │ 2800  │ ✅ 有效      │
│  humanizer(2)   │   0.65  │   0.50  │ -0.15 │ 2600  │ ✅ 有效      │
│  humanizer(3)   │   0.50  │   0.45  │ -0.05 │ 2400  │ ⚠️ 边际递减  │
│  reviewer       │   0.55  │   0.65  │ +0.10 │ 1200  │ ✅ 有效      │
│                                                                      │
│  📊 循环ROI分析                                                       │
│  Humanizer循环：迭代3次，AI_score 0.85→0.45，ROI=0.133               │
│  建议：阈值0.5可减少无效迭代                                           │
└─────────────────────────────────────────────────────────────────────┘
```

#### 批量统计报告

```
┌─────────────────────────────────────────────────────────────────────┐
│                    📊 评估统计报告                                    │
│                    时间范围：2026-04-01 ~ 2026-04-26                 │
├─────────────────────────────────────────────────────────────────────┤
│  总样本数：156篇                                                      │
│                                                                      │
│  【整体指标分布】                                                      │
│  综合评分：均值 78.5 │ 中位数 82 │ 标准差 12.3                         │
│  忠实度：  均值 85.2 │ 中位数 88 │ 标准差 8.5                          │
│  拟人性：  均值 65.3 │ 中位数 68 │ 标准差 15.8                         │
│                                                                      │
│  【节点效率统计】                                                      │
│  Humanizer循环：平均迭代2.3次，平均ROI=0.12                          │
│  建议：阈值0.6可减少30%迭代次数                                       │
│                                                                      │
│  【异常案例】                                                          │
│  - 忠实度<60：12篇（7.7%）                                            │
│  - 拟人性<50：18篇（11.5%）                                           │
└─────────────────────────────────────────────────────────────────────┘
```

API接口：

```python
@app.get("/api/admin/evaluation/{session_id}/detail")
async def get_evaluation_detail(session_id: str):
    """详细评估结果"""
    result = get_evaluation_result(session_id)
    probe_logs = get_session_probe_logs(session_id)
    return {"evaluation": result, "probe_logs": probe_logs}

@app.get("/api/admin/evaluation/stats")
async def get_evaluation_stats(start_date: str, end_date: str, group_by: str = "day"):
    """批量统计报告"""
    results = query_evaluation_results(start_date, end_date)
    return {
        "total_count": len(results),
        "score_distribution": calculate_distribution(results),
        "node_efficiency_stats": calculate_node_stats(results),
    }

@app.get("/api/admin/evaluation/compare")
async def compare_evaluations(session_ids: list[str] = None, platforms: list[str] = None):
    """A/B对比分析"""
    groups = group_for_comparison(session_ids, platforms)
    return {"groups": groups, "comparison": calculate_comparison_stats(groups)}
```

### 前端集成位置

- **用户端**：文章生成完成后，展示评估分数卡片
- **后台**：Web管理界面新增"评估分析"菜单

## 六、实施步骤

详见实现计划（使用writing-plans技能生成）。

## 七、风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| RAGAS无中文优化 | Faithfulness计算不准 | 适配中文prompt，或用LLM Judge替代 |
| 探针影响主流程性能 | 用户等待时间增加 | 探针设计<10ms，Redis db隔离 |
| 评估Worker故障 | 评估结果丢失 | 队列持久化，Worker重启自动恢复 |
| LLM Judge不稳定 | 分数波动 | 多次采样取平均，或增加本地fallback |

## 八、后续迭代方向

1. **构建金标准数据集** - 积累50-100篇人工标注优秀文章
2. **优化循环阈值** - 根据ROI数据自动调整MAX_REVISIONS
3. **增加Claim验证** - 对关键数据进行联网事实核查
4. **实时质量监控告警** - 异常分数触发告警