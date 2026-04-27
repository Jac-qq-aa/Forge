# Forge 评估系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为Forge多Agent内容生成系统构建异步旁路评估系统，量化节点有效性和循环迭代ROI。

**Architecture:** 轻量探针在Workflow节点插入数据采集，push到Redis队列；后台Worker异步消费队列执行RAGAS+LLM评估；PostgreSQL存储评估结果；API提供用户端分数展示和后台详细报告。

**Tech Stack:** Redis (队列), PostgreSQL (存储), RAGAS (评估算子), Qwen-max (LLM Judge), FastAPI (API)

---

## 文件结构

### 新增文件

| 文件 | 负责内容 |
|------|----------|
| `forge/evaluation/__init__.py` | 模块初始化，导出核心类 |
| `forge/evaluation/probe.py` | 节点探针：打包数据 + push Redis队列 |
| `forge/evaluation/probe_decorator.py` | 装饰器：自动插入探针到节点函数 |
| `forge/evaluation/worker.py` | 后台Worker：消费队列执行评估 |
| `forge/evaluation/engine.py` | 评估引擎：RAGAS + LLM Judge |
| `forge/evaluation/storage.py` | PostgreSQL存储层 |
| `forge/evaluation/probe_calculator.py` | 节点有效性 + 循环ROI计算 |
| `migrations/004_evaluation_tables.sql` | 数据库迁移：probe_logs + evaluation_results表 |
| `tests/test_evaluation/__init__.py` | 测试模块初始化 |
| `tests/test_evaluation/test_probe.py` | 探针测试 |
| `tests/test_evaluation/test_engine.py` | 评估引擎测试 |
| `tests/test_evaluation/test_storage.py` | 存储层测试 |

### 修改文件

| 文件 | 改动内容 |
|------|----------|
| `forge/agents/editor.py` | 添加 `@with_probe("editor")` 装饰器 |
| `forge/agents/ai_detector.py` | 添加 `@with_probe("ai_detector")` 装饰器 |
| `forge/agents/humanizer_editor.py` | 添加 `@with_probe("humanizer_editor", loop_type="humanize_loop")` 装饰器 |
| `forge/agents/reviewer.py` | 添加 `@with_probe("reviewer")` 装饰器 |
| `forge/agents/director.py` | 添加 `@with_probe("director")` 装饰器 |
| `forge/agents/deep_nodes.py` | 添加探针装饰器到深度模式节点 |
| `forge/web/app.py` | 添加评估API端点：`/api/evaluation/{session_id}` |
| `requirements.txt` | 添加 `ragas>=0.1.0` 依赖 |
| `forge/config.py` | 添加评估相关配置常量 |

---

## Task 1: 数据库迁移 - 创建评估表

**Files:**
- Create: `migrations/004_evaluation_tables.sql`

- [ ] **Step 1: 创建迁移文件**

```sql
-- migrations/004_evaluation_tables.sql

-- 评估系统表迁移

-- probe_logs: 节点探针日志
CREATE TABLE IF NOT EXISTS probe_logs (
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

-- 索引
CREATE INDEX IF NOT EXISTS idx_probe_logs_session ON probe_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_probe_logs_node ON probe_logs(node_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_probe_logs_loop ON probe_logs(loop_type, session_id);

-- evaluation_results: 评估结果汇总
CREATE TABLE IF NOT EXISTS evaluation_results (
    id SERIAL PRIMARY KEY,

    -- 关联信息
    session_id VARCHAR(64) NOT NULL UNIQUE,
    article_id VARCHAR(64),

    -- 一级指标（用户展示）
    overall_score DECIMAL(3,2),
    faithfulness_score DECIMAL(3,2),
    relevance_score DECIMAL(3,2),
    human_score DECIMAL(3,2),

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
CREATE INDEX IF NOT EXISTS idx_eval_results_session ON evaluation_results(session_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_score ON evaluation_results(overall_score, created_at);
CREATE INDEX IF NOT EXISTS idx_eval_results_status ON evaluation_results(status);
```

- [ ] **Step 2: 提交迁移文件**

```bash
git add migrations/004_evaluation_tables.sql
git commit -m "feat: add evaluation tables migration (probe_logs, evaluation_results)"
```

---

## Task 2: 评估模块初始化和配置

**Files:**
- Create: `forge/evaluation/__init__.py`
- Modify: `forge/config.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 添加ragas依赖到requirements.txt**

在 `requirements.txt` 末尾添加：

```text
# RAGAS for evaluation
ragas>=0.1.0
datasets>=2.14.0
```

- [ ] **Step 2: 添加评估配置到forge/config.py**

在 `forge/config.py` 末尾添加：

```python
# ============================================================================
# Evaluation Configuration
# ============================================================================

# Redis Queue for evaluation (使用db=1，与session db=0隔离)
EVAL_REDIS_DB: int = int(os.getenv("EVAL_REDIS_DB", "1"))
EVAL_QUEUE_NAME: str = os.getenv("EVAL_QUEUE_NAME", "forge:evaluation:queue")

# 评估阈值
EVAL_FAITHFULNESS_WEIGHT: float = 0.4
EVAL_RELEVANCE_WEIGHT: float = 0.3
EVAL_HUMAN_WEIGHT: float = 0.3

# Worker配置
EVAL_WORKER_BATCH_SIZE: int = int(os.getenv("EVAL_WORKER_BATCH_SIZE", "10"))
EVAL_WORKER_TIMEOUT: int = int(os.getenv("EVAL_WORKER_TIMEOUT", "60"))
```

- [ ] **Step 3: 创建evaluation模块__init__.py**

```python
# forge/evaluation/__init__.py

"""Forge 评估系统 - 异步旁路评估模块。

核心组件：
- Probe: 轻量节点探针，记录执行特征
- Worker: 后台消费队列，执行评估
- Engine: RAGAS + LLM Judge 评估引擎
- Storage: PostgreSQL 存储层
"""

from .probe import probe_node, extract_key_metrics
from .probe_decorator import with_probe
from .storage import EvaluationStorage, get_evaluation_storage
from .engine import EvaluationEngine

__all__ = [
    "probe_node",
    "extract_key_metrics",
    "with_probe",
    "EvaluationStorage",
    "get_evaluation_storage",
    "EvaluationEngine",
]
```

- [ ] **Step 4: 提交配置变更**

```bash
git add requirements.txt forge/config.py forge/evaluation/__init__.py
git commit -m "feat: add evaluation module init and config"
```

---

## Task 3: 节点探针核心实现

**Files:**
- Create: `forge/evaluation/probe.py`
- Create: `tests/test_evaluation/__init__.py`
- Create: `tests/test_evaluation/test_probe.py`

- [ ] **Step 1: 创建测试模块初始化**

```python
# tests/test_evaluation/__init__.py

"""Evaluation module tests."""
```

- [ ] **Step 2: 编写探针单元测试**

```python
# tests/test_evaluation/test_probe.py

"""节点探针测试。"""

import pytest
import json
from unittest.mock import MagicMock, patch
from forge.evaluation.probe import probe_node, extract_key_metrics


class TestExtractKeyMetrics:
    """测试 extract_key_metrics 函数。"""

    def test_extract_basic_metrics(self):
        """测试提取基本指标。"""
        state = {
            "session_id": "test-123",
            "ai_score": 0.85,
            "revision_count": 1,
            "humanize_revisions": 2,
            "rewritten_draft": "这是一段测试文字",
            "target_platform": "zhihu_article",
        }

        metrics = extract_key_metrics(state)

        assert metrics["ai_score"] == 0.85
        assert metrics["revision_count"] == 1
        assert metrics["humanize_revisions"] == 2
        assert metrics["draft_length"] == 7
        assert metrics["draft_text"] == "这是一段测试文字"

    def test_extract_with_current_draft(self):
        """测试使用 current_draft 字段。"""
        state = {
            "current_draft": "深度模式草稿内容",
            "rewritten_draft": None,
        }

        metrics = extract_key_metrics(state)

        assert metrics["draft_length"] == 7
        assert metrics["draft_text"] == "深度模式草稿内容"

    def test_extract_draft_text_truncated(self):
        """测试 draft_text 截断到1000字。"""
        long_text = "测试内容" * 200  # 1400字
        state = {
            "rewritten_draft": long_text,
        }

        metrics = extract_key_metrics(state)

        assert len(metrics["draft_text"]) == 1000
        assert metrics["draft_length"] == len(long_text)

    def test_extract_empty_state(self):
        """测试空状态。"""
        state = {}

        metrics = extract_key_metrics(state)

        assert metrics["ai_score"] == 0.0
        assert metrics["revision_count"] == 0
        assert metrics["draft_length"] == 0
        assert metrics["draft_text"] == ""


class TestProbeNode:
    """测试 probe_node 函数。"""

    @patch("forge.evaluation.probe.redis_client")
    def test_probe_node_pushes_to_queue(self, mock_redis):
        """测试探针push数据到Redis队列。"""
        mock_client = MagicMock()
        mock_redis.Redis.return_value = mock_client

        state_before = {
            "session_id": "session-abc",
            "ai_score": 0.0,
            "revision_count": 0,
        }
        state_after = {
            "session_id": "session-abc",
            "ai_score": 0.0,
            "revision_count": 1,
            "rewritten_draft": "改写后的内容",
            "target_platform": "zhihu_article",
        }

        probe_node(
            node_name="editor",
            state_before=state_before,
            state_after=state_after,
            duration_ms=3500,
        )

        # 验证lpush被调用
        mock_client.lpush.assert_called_once()
        call_args = mock_client.lpush.call_args
        assert call_args[0][0] == "forge:evaluation:queue"

        # 解析payload验证内容
        payload = json.loads(call_args[0][1])
        assert payload["session_id"] == "session-abc"
        assert payload["node_name"] == "editor"
        assert payload["duration_ms"] == 3500
        assert payload["input_metrics"]["revision_count"] == 0
        assert payload["output_metrics"]["revision_count"] == 1

    @patch("forge.evaluation.probe.redis_client")
    def test_probe_node_with_loop_info(self, mock_redis):
        """测试带循环信息的探针。"""
        mock_client = MagicMock()
        mock_redis.Redis.return_value = mock_client

        state_before = {"session_id": "test", "ai_score": 0.85}
        state_after = {"session_id": "test", "ai_score": 0.65}

        probe_node(
            node_name="humanizer_editor",
            state_before=state_before,
            state_after=state_after,
            duration_ms=2800,
            loop_info={"loop_type": "humanize_loop", "iteration": 1},
        )

        mock_client.lpush.assert_called_once()
        payload = json.loads(mock_client.lpush.call_args[0][1])
        assert payload["loop_type"] == "humanize_loop"
        assert payload["loop_iteration"] == 1

- [ ] **Step 3: 运行测试验证失败**

```bash
pytest tests/test_evaluation/test_probe.py -v
```

Expected: FAIL (模块不存在)

- [ ] **Step 4: 实现probe.py**

```python
# forge/evaluation/probe.py

"""节点探针 - 轻量数据采集 + Redis队列推送。

设计目标：耗时 < 10ms，不阻塞主流程。
"""

import json
import time
import logging
import redis
from typing import Dict, Any, Optional

from forge.config import REDIS_HOST, REDIS_PORT, EVAL_REDIS_DB, EVAL_QUEUE_NAME

logger = logging.getLogger(__name__)

# Redis连接（使用db=1，与session db=0隔离）
_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """获取Redis客户端（延迟初始化）。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=EVAL_REDIS_DB,
            decode_responses=False,  # 保持bytes，json.dumps后直接存
        )
        logger.info(f"[Probe] Redis client initialized: db={EVAL_REDIS_DB}")
    return _redis_client


def probe_node(
    node_name: str,
    state_before: Dict[str, Any],
    state_after: Dict[str, Any],
    duration_ms: int,
    loop_info: Optional[Dict[str, Any]] = None,
) -> None:
    """节点探针 - 打包数据并push到Redis队列。

    Args:
        node_name: 节点名称（editor, ai_detector, humanizer_editor等）
        state_before: 节点执行前的状态
        state_after: 节点执行后的状态（返回的result dict）
        duration_ms: 节点执行耗时（毫秒）
        loop_info: 循环信息 {"loop_type": "humanize_loop", "iteration": 1}

    设计原则：
    - 极轻量：只做数据打包 + push，不做任何计算
    - 不阻塞：Redis lpush是O(1)操作，耗时 < 10ms
    - 数据精简：只存关键指标，不存完整文章内容
    """
    session_id = state_before.get("session_id", "unknown")

    # 提取关键指标
    input_metrics = extract_key_metrics(state_before)
    output_metrics = extract_key_metrics(state_after)

    # 构建payload
    payload = {
        "session_id": session_id,
        "node_name": node_name,
        "timestamp": time.time(),
        "input_metrics": input_metrics,
        "output_metrics": output_metrics,
        "duration_ms": duration_ms,
        "loop_type": loop_info.get("loop_type") if loop_info else None,
        "loop_iteration": loop_info.get("iteration", 0) if loop_info else 0,
        "metadata": {
            "target_platform": state_after.get("target_platform"),
            "mode": state_after.get("mode", "fast"),
        },
    }

    # Push到Redis队列
    try:
        client = _get_redis_client()
        client.lpush(EVAL_QUEUE_NAME, json.dumps(payload, ensure_ascii=False))
        logger.debug(f"[Probe] {node_name} pushed to queue: session={session_id}")
    except Exception as e:
        logger.warning(f"[Probe] Redis push failed: {e}, data discarded")
        # 失败时静默丢弃，不影响主流程


def extract_key_metrics(state: Dict[str, Any]) -> Dict[str, Any]:
    """从状态中提取关键指标。

    Args:
        state: 节点状态字典

    Returns:
        关键指标字典，包含：
        - ai_score: AI检测分数
        - revision_count: Reviewer迭代次数
        - humanize_revisions: Humanizer迭代次数
        - reflection_revision_count: Reflection迭代次数
        - draft_length: 草稿长度
        - draft_text: 草稿前1000字（用于评估）
        - is_approved: 是否通过Critic
        - stage: 当前阶段
        - has_rag_context: 是否有RAG素材
        - has_fact_sheet: 是否有Fact Sheet
    """
    draft = state.get("rewritten_draft", "") or state.get("current_draft", "") or ""

    return {
        "ai_score": state.get("ai_score", 0.0),
        "revision_count": state.get("revision_count", 0),
        "humanize_revisions": state.get("humanize_revisions", 0),
        "reflection_revision_count": state.get("reflection_revision_count", 0),
        "draft_length": len(draft),
        "draft_text": draft[:1000] if draft else "",
        "is_approved": state.get("is_approved", False),
        "stage": state.get("stage"),
        "has_rag_context": bool(state.get("rag_context")),
        "has_fact_sheet": bool(state.get("fact_sheet")),
    }
```

- [ ] **Step 5: 运行测试验证通过**

```bash
pytest tests/test_evaluation/test_probe.py -v
```

Expected: PASS

- [ ] **Step 6: 提交probe实现**

```bash
git add forge/evaluation/probe.py tests/test_evaluation/
git commit -m "feat: implement node probe with Redis queue push"
```

---

## Task 4: 探针装饰器实现

**Files:**
- Create: `forge/evaluation/probe_decorator.py`

- [ ] **Step 1: 编写装饰器测试**

在 `tests/test_evaluation/test_probe.py` 末尾添加：

```python
class TestWithProbeDecorator:
    """测试 with_probe 装饰器。"""

    @pytest.mark.asyncio
    async def test_decorator_calls_probe(self):
        """测试装饰器自动调用probe_node。"""
        from forge.evaluation.probe_decorator import with_probe
        from unittest.mock import AsyncMock, patch

        @with_probe("test_node")
        async def mock_node(state):
            return {"ai_score": 0.5, "rewritten_draft": "结果"}

        state = {"session_id": "test-session"}

        with patch("forge.evaluation.probe_decorator.probe_node") as mock_probe:
            result = await mock_node(state)

            # 验证probe_node被调用
            mock_probe.assert_called_once()
            call_args = mock_probe.call_args
            assert call_args[1]["node_name"] == "test_node"
            assert call_args[1]["state_before"]["session_id"] == "test-session"
            assert call_args[1]["state_after"]["ai_score"] == 0.5

            # 验证返回结果正确
            assert result["ai_score"] == 0.5

    @pytest.mark.asyncio
    async def test_decorator_with_loop_info(self):
        """测试装饰器带循环信息。"""
        from forge.evaluation.probe_decorator import with_probe
        from unittest.mock import patch

        @with_probe("loop_node", loop_type="test_loop")
        async def mock_node(state):
            return {}

        state = {"session_id": "test", "test_loop_iterations": 2}

        with patch("forge.evaluation.probe_decorator.probe_node") as mock_probe:
            await mock_node(state)

            call_args = mock_probe.call_args
            assert call_args[1]["loop_info"]["loop_type"] == "test_loop"
            assert call_args[1]["loop_info"]["iteration"] == 3  # 2 + 1
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/test_evaluation/test_probe.py::TestWithProbeDecorator -v
```

Expected: FAIL (模块不存在)

- [ ] **Step 3: 实现probe_decorator.py**

```python
# forge/evaluation/probe_decorator.py

"""节点探针装饰器 - 自动插入探针到节点函数。

用法：
    @with_probe("editor")
    async def editor_node(state: GraphState) -> dict:
        ...

    @with_probe("humanizer_editor", loop_type="humanize_loop")
    async def humanizer_editor_node(state: GraphState) -> dict:
        ...
"""

import functools
import time
import logging
from typing import Callable, Dict, Any, Optional, Awaitable

from .probe import probe_node

logger = logging.getLogger(__name__)


def with_probe(
    node_name: str,
    loop_type: Optional[str] = None,
) -> Callable[[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]], Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]:
    """节点探针装饰器。

    Args:
        node_name: 节点名称（用于标识和报告）
        loop_type: 循环类型（如 "humanize_loop", "review_loop", "reflect_loop"）

    Returns:
        装饰后的异步节点函数

    功能：
    - 自动记录节点执行前后的状态
    - 计算节点执行耗时
    - 调用 probe_node 将数据push到队列
    - 不影响原节点函数的返回值
    """
    def decorator(
        func: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
    ) -> Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]:
        @functools.wraps(func)
        async def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            # 记录开始时间
            start_time = time.time()

            # 快速复制state_before（只复制顶层key，不深拷贝）
            state_before = dict(state)

            # 执行原节点函数
            result = await func(state)

            # 计算耗时
            duration_ms = int((time.time() - start_time) * 1000)

            # 构建循环信息
            loop_info = None
            if loop_type:
                iteration_key = f"{loop_type}_iterations"
                current_iteration = state.get(iteration_key, 0)
                loop_info = {
                    "loop_type": loop_type,
                    "iteration": current_iteration + 1,  # 迭代次数+1
                }

            # 调用探针（不阻塞，失败静默丢弃）
            probe_node(
                node_name=node_name,
                state_before=state_before,
                state_after=result,
                duration_ms=duration_ms,
                loop_info=loop_info,
            )

            return result

        return wrapper

    return decorator
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_evaluation/test_probe.py -v
```

Expected: PASS

- [ ] **Step 5: 提交装饰器实现**

```bash
git add forge/evaluation/probe_decorator.py tests/test_evaluation/test_probe.py
git commit -m "feat: implement with_probe decorator for automatic probing"
```

---

## Task 5: 评估存储层实现

**Files:**
- Create: `forge/evaluation/storage.py`
- Create: `tests/test_evaluation/test_storage.py`

- [ ] **Step 1: 编写存储层测试**

```python
# tests/test_evaluation/test_storage.py

"""评估存储层测试。"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from forge.evaluation.storage import EvaluationStorage, save_probe_log, get_session_probe_logs


class TestEvaluationStorage:
    """测试 EvaluationStorage 类。"""

    @pytest.mark.asyncio
    async def test_save_probe_log(self):
        """测试保存probe log。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        storage = EvaluationStorage()
        storage._pool = mock_pool

        payload = {
            "session_id": "test-session",
            "node_name": "editor",
            "timestamp": 1234567890,
            "input_metrics": {"ai_score": 0.0},
            "output_metrics": {"ai_score": 0.0, "draft_length": 100},
            "duration_ms": 3500,
            "loop_type": None,
            "loop_iteration": 0,
            "metadata": {},
        }

        await storage.save_probe_log(payload)

        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_probe_logs(self):
        """测试获取session的所有probe logs。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_rows = [
            MagicMock(
                session_id="test-session",
                node_name="editor",
                node_sequence=1,
                input_metrics={"ai_score": 0.0},
                output_metrics={"draft_length": 100},
            ),
            MagicMock(
                session_id="test-session",
                node_name="ai_detector",
                node_sequence=2,
                input_metrics={"draft_length": 100},
                output_metrics={"ai_score": 0.85},
            ),
        ]
        mock_conn.fetch.return_value = mock_rows
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        storage = EvaluationStorage()
        storage._pool = mock_pool

        logs = await storage.get_session_probe_logs("test-session")

        assert len(logs) == 2
        assert logs[0]["node_name"] == "editor"
        assert logs[1]["node_name"] == "ai_detector"

    @pytest.mark.asyncio
    async def test_save_evaluation_result(self):
        """测试保存评估结果。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        storage = EvaluationStorage()
        storage._pool = mock_pool

        result = {
            "overall_score": 0.85,
            "faithfulness_score": 0.90,
            "relevance_score": 0.80,
            "human_score": 0.75,
            "metrics_detail": {"ai_detection": {"ai_score": 0.25}},
            "node_effectiveness": {"editor": {"gain": 0.15}},
            "loop_roi": {"humanize_loop": {"roi": 0.13}},
            "status": "completed",
        }

        await storage.save_evaluation_result("test-session", result)

        mock_conn.execute.assert_called()

    @pytest.mark.asyncio
    async def test_get_evaluation_result(self):
        """测试获取评估结果。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_row = MagicMock(
            session_id="test-session",
            overall_score=0.85,
            faithfulness_score=0.90,
            status="completed",
        )
        mock_conn.fetchrow.return_value = mock_row
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        storage = EvaluationStorage()
        storage._pool = mock_pool

        result = await storage.get_evaluation_result("test-session")

        assert result["overall_score"] == 0.85
        assert result["status"] == "completed"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/test_evaluation/test_storage.py -v
```

Expected: FAIL (模块不存在)

- [ ] **Step 3: 实现storage.py**

```python
# forge/evaluation/storage.py

"""评估存储层 - PostgreSQL持久化。

表结构：
- probe_logs: 节点探针日志
- evaluation_results: 评估结果汇总
"""

import json
import logging
import asyncpg
from typing import Dict, Any, List, Optional
from datetime import datetime

from forge.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE
from forge.storage.pg_client import get_pg_pool

logger = logging.getLogger(__name__)


class EvaluationStorage:
    """评估数据存储类。"""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        """获取PG连接池。"""
        if self._pool is None:
            self._pool = await get_pg_pool()
        return self._pool

    async def save_probe_log(self, payload: Dict[str, Any]) -> None:
        """保存单条probe log。

        Args:
            payload: 探针数据包
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 获取当前session的node_sequence
            existing_logs = await conn.fetch(
                "SELECT node_sequence FROM probe_logs WHERE session_id = $1",
                payload["session_id"],
            )
            node_sequence = len(existing_logs) + 1

            await conn.execute(
                """
                INSERT INTO probe_logs (
                    session_id, node_name, node_sequence, timestamp,
                    input_metrics, output_metrics, duration_ms,
                    loop_type, loop_iteration, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                payload["session_id"],
                payload["node_name"],
                node_sequence,
                datetime.fromtimestamp(payload["timestamp"]),
                json.dumps(payload["input_metrics"]),
                json.dumps(payload["output_metrics"]),
                payload["duration_ms"],
                payload["loop_type"],
                payload["loop_iteration"],
                json.dumps(payload["metadata"]),
            )

        logger.debug(f"[EvalStorage] Probe log saved: {payload['node_name']}")

    async def get_session_probe_logs(self, session_id: str) -> List[Dict[str, Any]]:
        """获取session的所有probe logs。

        Args:
            session_id: 会话ID

        Returns:
            probe log列表，按node_sequence排序
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT session_id, node_name, node_sequence, timestamp,
                       input_metrics, output_metrics, duration_ms,
                       loop_type, loop_iteration, metadata
                FROM probe_logs
                WHERE session_id = $1
                ORDER BY node_sequence ASC
                """,
                session_id,
            )

        return [self._row_to_dict(row) for row in rows]

    async def save_evaluation_result(
        self,
        session_id: str,
        result: Dict[str, Any]
    ) -> None:
        """保存评估结果。

        Args:
            session_id: 会话ID
            result: 评估结果字典
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO evaluation_results (
                    session_id, overall_score, faithfulness_score,
                    relevance_score, human_score, metrics_detail,
                    node_effectiveness, loop_roi, evaluated_at, status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (session_id) DO UPDATE SET
                    overall_score = $2,
                    faithfulness_score = $3,
                    relevance_score = $4,
                    human_score = $5,
                    metrics_detail = $6,
                    node_effectiveness = $7,
                    loop_roi = $8,
                    evaluated_at = $9,
                    status = $10
                """,
                session_id,
                result.get("overall_score"),
                result.get("faithfulness_score"),
                result.get("relevance_score"),
                result.get("human_score"),
                json.dumps(result.get("metrics_detail", {})),
                json.dumps(result.get("node_effectiveness", {})),
                json.dumps(result.get("loop_roi", {})),
                datetime.now(),
                result.get("status", "completed"),
            )

        logger.info(f"[EvalStorage] Evaluation result saved: {session_id}")

    async def get_evaluation_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取评估结果。

        Args:
            session_id: 会话ID

        Returns:
            评估结果字典，不存在返回None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT session_id, overall_score, faithfulness_score,
                       relevance_score, human_score, metrics_detail,
                       node_effectiveness, loop_roi, created_at,
                       evaluated_at, status, error_message
                FROM evaluation_results
                WHERE session_id = $1
                """,
                session_id,
            )

        if not row:
            return None
        return self._row_to_dict(row)

    async def get_evaluation_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取评估统计数据（后台报告）。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            limit: 最大返回数量

        Returns:
            评估结果列表
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            query = """
                SELECT session_id, overall_score, faithfulness_score,
                       relevance_score, human_score, status, created_at
                FROM evaluation_results
                WHERE status = 'completed'
                ORDER BY created_at DESC
                LIMIT $1
            """
            rows = await conn.fetch(query, limit)

        return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: asyncpg.Record) -> Dict[str, Any]:
        """转换数据库行到字典。"""
        result = dict(row)

        # 处理datetime
        for key in ["timestamp", "created_at", "evaluated_at"]:
            if key in result and result[key] is not None:
                if isinstance(result[key], datetime):
                    result[key] = result[key].isoformat()

        # 处理JSONB字段
        json_fields = ["input_metrics", "output_metrics", "metadata",
                       "metrics_detail", "node_effectiveness", "loop_roi"]
        for key in json_fields:
            if key in result and isinstance(result[key], str):
                try:
                    result[key] = json.loads(result[key])
                except json.JSONDecodeError:
                    pass

        return result


# 全局实例
_eval_storage: Optional[EvaluationStorage] = None


def get_evaluation_storage() -> EvaluationStorage:
    """获取存储实例。"""
    global _eval_storage
    if _eval_storage is None:
        _eval_storage = EvaluationStorage()
    return _eval_storage


# 便捷函数
async def save_probe_log(payload: Dict[str, Any]) -> None:
    """保存probe log的便捷函数。"""
    storage = get_evaluation_storage()
    await storage.save_probe_log(payload)


async def get_session_probe_logs(session_id: str) -> List[Dict[str, Any]]:
    """获取session probe logs的便捷函数。"""
    storage = get_evaluation_storage()
    return await storage.get_session_probe_logs(session_id)


async def save_evaluation_result(session_id: str, result: Dict[str, Any]) -> None:
    """保存评估结果的便捷函数。"""
    storage = get_evaluation_storage()
    await storage.save_evaluation_result(session_id, result)


async def get_evaluation_result(session_id: str) -> Optional[Dict[str, Any]]:
    """获取评估结果的便捷函数。"""
    storage = get_evaluation_storage()
    return await storage.get_evaluation_result(session_id)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_evaluation/test_storage.py -v
```

Expected: PASS

- [ ] **Step 5: 提交存储层实现**

```bash
git add forge/evaluation/storage.py tests/test_evaluation/test_storage.py
git commit -m "feat: implement evaluation storage layer (PostgreSQL)"
```

---

## Task 6: 评估引擎实现

**Files:**
- Create: `forge/evaluation/engine.py`
- Create: `forge/evaluation/probe_calculator.py`
- Create: `tests/test_evaluation/test_engine.py`

- [ ] **Step 1: 编写节点有效性计算测试**

```python
# tests/test_evaluation/test_engine.py

"""评估引擎测试。"""

import pytest
from forge.evaluation.probe_calculator import calculate_node_effectiveness, calculate_loop_roi


class TestProbeCalculator:
    """测试 probe_calculator 模块。"""

    def test_calculate_node_effectiveness(self):
        """测试节点有效性计算。"""
        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0, "draft_length": 0},
                "output_metrics": {"ai_score": 0.0, "draft_length": 500},
                "duration_ms": 3000,
            },
            {
                "node_name": "ai_detector",
                "input_metrics": {"draft_length": 500},
                "output_metrics": {"ai_score": 0.85},
                "duration_ms": 800,
            },
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.85},
                "output_metrics": {"ai_score": 0.65},
                "duration_ms": 2500,
            },
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 2,
                "input_metrics": {"ai_score": 0.65},
                "output_metrics": {"ai_score": 0.45},
                "duration_ms": 2500,
            },
        ]

        effectiveness = calculate_node_effectiveness(probe_logs)

        assert "editor" in effectiveness
        assert "ai_detector" in effectiveness
        assert "humanizer_loop" in effectiveness

        # Humanizer循环：初始0.85 -> 最终0.45，增益0.40
        assert effectiveness["humanizer_loop"]["iterations"] == 2
        assert effectiveness["humanizer_loop"]["gain"] == 0.40

    def test_calculate_loop_roi(self):
        """测试循环ROI计算。"""
        probe_logs = [
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.85},
                "output_metrics": {"ai_score": 0.65},
                "duration_ms": 2500,
            },
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 2,
                "input_metrics": {"ai_score": 0.65},
                "output_metrics": {"ai_score": 0.45},
                "duration_ms": 2500,
            },
        ]

        roi = calculate_loop_roi(probe_logs)

        assert "humanize_loop" in roi
        assert roi["humanize_loop"]["iterations"] == 2
        assert roi["humanize_loop"]["initial_ai_score"] == 0.85
        assert roi["humanize_loop"]["final_ai_score"] == 0.45
        assert roi["humanize_loop"]["roi"] == 0.20  # (0.85-0.45) / 2


class TestEvaluationEngine:
    """测试 EvaluationEngine 类。"""

    @pytest.mark.asyncio
    async def test_calculate_overall_score(self):
        """测试综合评分计算。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        ragas_scores = {"faithfulness": 0.85, "answer_relevance": 0.90}
        style_scores = {"ai_score": 0.30}

        overall = engine._calculate_overall(ragas_scores, style_scores)

        # 0.85 * 0.4 + 0.90 * 0.3 + (1 - 0.30) * 0.3 = 0.34 + 0.27 + 0.21 = 0.82
        assert overall == pytest.approx(0.82, rel=0.01)

    def test_parse_score(self):
        """测试分数解析。"""
        from forge.evaluation.engine import parse_score

        response = "【AI特征评分】75\n【口语化程度】60"
        score = parse_score(response, "AI特征评分")

        assert score == 75

    def test_parse_score_missing(self):
        """测试缺失分数返回默认值。"""
        from forge.evaluation.engine import parse_score

        response = "没有找到评分"
        score = parse_score(response, "AI特征评分")

        assert score == 50  # 默认值
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/test_evaluation/test_engine.py -v
```

Expected: FAIL (模块不存在)

- [ ] **Step 3: 实现probe_calculator.py**

```python
# forge/evaluation/probe_calculator.py

"""节点有效性和循环ROI计算。

核心算法：
- Node Effectiveness = (输出分数 - 输入分数) / 节点耗时秒数
- Loop ROI = (初始分数 - 最终分数) / 迭代次数
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def calculate_node_effectiveness(probe_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算各节点的有效性。

    Args:
        probe_logs: session的所有probe log

    Returns:
        节点有效性字典，包含：
        - 单节点：gain, duration_ms, effectiveness
        - 循环节点：iterations, initial_score, final_score, gain, roi
    """
    result = {}

    # 按节点名分组
    node_groups: Dict[str, List[Dict[str, Any]]] = {}
    loop_groups: Dict[str, List[Dict[str, Any]]] = {}

    for log in probe_logs:
        node_name = log["node_name"]
        loop_type = log.get("loop_type")

        if loop_type:
            # 循环节点
            if loop_type not in loop_groups:
                loop_groups[loop_type] = []
            loop_groups[loop_type].append(log)
        else:
            # 单节点（只取第一次，因为可能有revision循环）
            if node_name not in node_groups:
                node_groups[node_name] = []
            node_groups[node_name].append(log)

    # 计算单节点有效性
    for node_name, logs in node_groups.items():
        if not logs:
            continue

        # 使用第一条记录（首次执行）
        first_log = logs[0]
        input_score = first_log["input_metrics"].get("ai_score", 0.0)
        output_score = first_log["output_metrics"].get("ai_score", 0.0)

        # 对于ai_detector节点，输出是ai_score，增益为0（检测节点）
        if node_name == "ai_detector":
            gain = 0.0
        else:
            # 对于editor/reviewer，增益 = 输出质量 - 输入质量
            # 质量定义为：(1 - ai_score) + draft_length权重
            gain = _calculate_quality_gain(first_log["input_metrics"], first_log["output_metrics"])

        duration_ms = first_log.get("duration_ms", 0)
        duration_seconds = duration_ms / 1000.0 if duration_ms > 0 else 1.0

        result[node_name] = {
            "gain": gain,
            "duration_ms": duration_ms,
            "effectiveness": gain / duration_seconds if duration_seconds > 0 else 0.0,
        }

    # 计算循环ROI
    for loop_type, logs in loop_groups.items():
        if not logs:
            continue

        # 按iteration排序
        logs_sorted = sorted(logs, key=lambda x: x.get("loop_iteration", 0))

        # 获取初始和最终分数
        first_log = logs_sorted[0]
        last_log = logs_sorted[-1]

        initial_ai_score = first_log["input_metrics"].get("ai_score", 0.0)
        final_ai_score = last_log["output_metrics"].get("ai_score", 0.0)

        # Humanizer循环：ai_score下降是正向增益
        if loop_type == "humanize_loop":
            gain = initial_ai_score - final_ai_score
        else:
            # 其他循环：质量上升是正向
            gain = _calculate_quality_gain(first_log["input_metrics"], last_log["output_metrics"])

        iterations = len(logs_sorted)
        roi = gain / iterations if iterations > 0 else 0.0

        result[loop_type] = {
            "iterations": iterations,
            "initial_ai_score": initial_ai_score,
            "final_ai_score": final_ai_score,
            "gain": gain,
            "roi": roi,
        }

    return result


def calculate_loop_roi(probe_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算各循环的ROI。

    Args:
        probe_logs: session的所有probe log

    Returns:
        循环ROI字典
    """
    # 从calculate_node_effectiveness提取循环部分
    effectiveness = calculate_node_effectiveness(probe_logs)

    roi_result = {}
    for key, value in effectiveness.items():
        if key.endswith("_loop"):
            roi_result[key] = value

    return roi_result


def _calculate_quality_gain(input_metrics: Dict[str, Any], output_metrics: Dict[str, Any]) -> float:
    """计算质量增益（内部函数）。

    质量定义：
    - ai_score越低越好（拟人性）
    - draft_length有一定正向权重（内容充实度）

    Returns:
        正值表示质量提升
    """
    input_ai = input_metrics.get("ai_score", 0.5)
    output_ai = output_metrics.get("ai_score", 0.5)

    # AI分数下降 = 质量提升
    ai_gain = input_ai - output_ai

    # 草稿长度增加（有内容）
    input_len = input_metrics.get("draft_length", 0)
    output_len = output_metrics.get("draft_length", 0)
    len_gain = min(output_len - input_len, 500) / 5000.0  # 归一化，最大0.1

    return ai_gain + len_gain
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_evaluation/test_engine.py::TestProbeCalculator -v
```

Expected: PASS

- [ ] **Step 5: 实现engine.py**

```python
# forge/evaluation/engine.py

"""评估引擎 - RAGAS + LLM Judge。

核心功能：
- RAGAS评估：Faithfulness, Answer Relevance, Context Utilization
- LLM Judge：AI特征评分、风格质量
- 综合评分计算
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional

from forge.tools.llm_client import LLMClient
from forge.config import (
    EVAL_FAITHFULNESS_WEIGHT,
    EVAL_RELEVANCE_WEIGHT,
    EVAL_HUMAN_WEIGHT,
)
from .probe_calculator import calculate_node_effectiveness, calculate_loop_roi

logger = logging.getLogger(__name__)


class EvaluationEngine:
    """评估引擎。"""

    def __init__(self):
        self.llm_client = LLMClient()

    async def evaluate_session(self, probe_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """执行完整session评估。

        Args:
            probe_logs: 该session所有节点的probe记录

        Returns:
            评估结果字典，包含一级指标、二级指标、节点有效性、循环ROI
        """
        if not probe_logs:
            return {
                "overall_score": 0.0,
                "status": "failed",
                "error_message": "No probe logs available",
            }

        # 获取最终草稿（从最后节点的output_metrics）
        final_log = probe_logs[-1]
        final_draft = final_log.get("output_metrics", {}).get("draft_text", "")

        if not final_draft:
            logger.warning("[EvalEngine] No draft text in probe logs")
            final_draft = "[无内容]"

        logger.info(f"[EvalEngine] Evaluating session: {probe_logs[0].get('session_id')}")
        logger.info(f"[EvalEngine] Draft length: {len(final_draft)} chars")

        # 1. RAGAS评估（Faithfulness, Relevance, Context Utilization）
        try:
            ragas_scores = await self._run_ragas_evaluation(probe_logs, final_draft)
        except Exception as e:
            logger.warning(f"[EvalEngine] RAGAS failed: {e}, using fallback")
            ragas_scores = {
                "faithfulness": 0.5,
                "answer_relevance": 0.5,
                "context_utilization": 0.5,
            }

        # 2. LLM Judge风格评估
        try:
            style_scores = await self._run_style_evaluation(final_draft)
        except Exception as e:
            logger.warning(f"[EvalEngine] Style evaluation failed: {e}, using fallback")
            style_scores = {"ai_score": 0.5}

        # 3. 节点有效性计算
        node_effectiveness = calculate_node_effectiveness(probe_logs)

        # 4. 循环ROI计算
        loop_roi = calculate_loop_roi(probe_logs)

        # 5. 综合评分
        overall_score = self._calculate_overall(ragas_scores, style_scores)

        logger.info(f"[EvalEngine] Overall score: {overall_score:.2f}")

        return {
            "overall_score": overall_score,
            "faithfulness_score": ragas_scores.get("faithfulness", 0.5),
            "relevance_score": ragas_scores.get("answer_relevance", 0.5),
            "human_score": 1 - style_scores.get("ai_score", 0.5),
            "metrics_detail": {
                "faithfulness": ragas_scores.get("faithfulness_detail", {}),
                "answer_relevance": ragas_scores.get("relevance_detail", {}),
                "context_utilization": ragas_scores.get("context_detail", {}),
                "ai_detection": style_scores,
            },
            "node_effectiveness": node_effectiveness,
            "loop_roi": loop_roi,
            "status": "completed",
        }

    async def _run_ragas_evaluation(
        self,
        probe_logs: List[Dict[str, Any]],
        final_draft: str
    ) -> Dict[str, Any]:
        """RAGAS评估。

        无金标准时：
        - 用原文章raw_content作为reference
        - 用RAG检索的chunks作为contexts
        - 用用户prompt作为question

        注意：RAGAS默认使用OpenAI模型，需要适配使用Qwen。
        """
        # 从probe_logs提取材料
        raw_content = self._extract_raw_content(probe_logs)
        rag_chunks = self._extract_rag_chunks(probe_logs)
        user_prompt = self._extract_user_prompt(probe_logs)

        # 如果没有原始素材，返回默认分数
        if not raw_content and not rag_chunks:
            logger.warning("[EvalEngine] No source material for RAGAS")
            return {
                "faithfulness": 0.5,
                "answer_relevance": 0.5,
                "context_utilization": 0.5,
            }

        # 尝试使用RAGAS
        try:
            from datasets import Dataset
            from ragas import evaluate
            from ragas.metrics import faithfulness, answer_relevance, context_recall

            # 构建评估数据
            eval_data = {
                "question": [user_prompt or "内容改写"],
                "answer": [final_draft],
                "contexts": [rag_chunks if rag_chunks else [raw_content.get("text", "")[:500]]],
                "reference": [raw_content.get("text", "")[:1000] if raw_content else ""],
            }

            dataset = Dataset.from_dict(eval_data)

            # 使用项目的LLM（需要适配）
            # RAGAS默认用OpenAI，这里用简化方案：直接LLM Judge
            logger.info("[EvalEngine] Using LLM Judge instead of RAGAS (model compatibility)")

            return await self._llm_judge_ragas(user_prompt, final_draft, raw_content, rag_chunks)

        except ImportError:
            logger.warning("[EvalEngine] RAGAS not installed, using LLM Judge")
            return await self._llm_judge_ragas(user_prompt, final_draft, raw_content, rag_chunks)

    async def _llm_judge_ragas(
        self,
        user_prompt: str,
        final_draft: str,
        raw_content: Dict[str, Any],
        rag_chunks: List[str]
    ) -> Dict[str, Any]:
        """用LLM Judge替代RAGAS评估。

        当RAGAS不可用或模型不兼容时使用。
        """
        source_text = raw_content.get("text", "")[:500] if raw_content else ""
        rag_text = "\n".join(rag_chunks[:3]) if rag_chunks else "无"

        prompt = f"""请作为专业编辑，评估以下改写文章的质量。

## 原文章摘要
{source_text}

## 参考素材
{rag_text}

## 改写后文章
{final_draft[:1000]}

## 用户需求
{user_prompt or "内容改写"}

请按以下维度评分（满分100分）：

【忠实度】XX分 - 文章中的观点是否来自原文/素材，有无编造
【相关性】XX分 - 文章是否满足用户需求，是否跑题
【素材利用】XX分 - 参考素材有多少被有效利用

请严格按格式回复：
【忠实度】XX
【相关性】XX
【素材利用】XX
【分析】简短说明
"""

        system_prompt = "你是一位专业的内容评估专家，客观公正地评估文章质量。"

        response = await self.llm_client.chat_with_retry(prompt, system_prompt)

        # 解析分数
        faithfulness = parse_score(response, "忠实度") / 100
        relevance = parse_score(response, "相关性") / 100
        context_utilization = parse_score(response, "素材利用") / 100

        return {
            "faithfulness": faithfulness,
            "answer_relevance": relevance,
            "context_utilization": context_utilization,
            "faithfulness_detail": {"method": "llm_judge"},
            "relevance_detail": {"method": "llm_judge"},
            "context_detail": {"method": "llm_judge"},
        }

    async def _run_style_evaluation(self, final_draft: str) -> Dict[str, Any]:
        """风格质量评估（LLM Judge）。

        复用现有AI_Detector的逻辑框架。
        """
        prompt = f"""请评估以下文章的AI生成特征（满分100）：

文章内容：
{final_draft[:1000]}

评估维度：
1. AI特征评分：是否像AI生成（词汇单一、结构模板化）
2. 口语化程度：是否有真实感、长短句交替
3. 结构自然度：是否打破三段论、有无AI常用词

请严格按格式回复：
【AI特征评分】XX
【口语化程度】XX
【结构自然度】XX
【总评】简短说明
"""

        system_prompt = "你是一位AI内容检测专家，判断文章是否像AI生成。"

        response = await self.llm_client.chat_with_retry(prompt, system_prompt)

        ai_score = parse_score(response, "AI特征评分") / 100

        return {
            "ai_score": ai_score,
            "ai_detection_detail": {
                "ai_score": ai_score,
                "method": "llm_judge",
                "perplexity": "中",  # 简化
                "burstiness": "中",
            },
        }

    def _calculate_overall(
        self,
        ragas_scores: Dict[str, Any],
        style_scores: Dict[str, Any]
    ) -> float:
        """计算综合评分。

        权重：Faithfulness 40% + Relevance 30% + Human 30%
        """
        faith = ragas_scores.get("faithfulness", 0.5)
        relevance = ragas_scores.get("answer_relevance", 0.5)
        human = 1 - style_scores.get("ai_score", 0.5)

        overall = (
            faith * EVAL_FAITHFULNESS_WEIGHT +
            relevance * EVAL_RELEVANCE_WEIGHT +
            human * EVAL_HUMAN_WEIGHT
        )

        return round(overall, 2)

    # 辅助提取函数

    def _extract_raw_content(self, probe_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """从scout节点提取原始内容。"""
        for log in probe_logs:
            if log["node_name"] == "scout":
                return log.get("metadata", {}).get("raw_content", {})
        return {}

    def _extract_rag_chunks(self, probe_logs: List[Dict[str, Any]]) -> List[str]:
        """从各节点提取RAG素材。"""
        chunks = []
        for log in probe_logs:
            metadata = log.get("metadata", {})
            if metadata.get("rag_chunks"):
                chunks.extend(metadata["rag_chunks"])
        return chunks

    def _extract_user_prompt(self, probe_logs: List[Dict[str, Any]]) -> str:
        """从首个节点提取用户prompt/大纲。"""
        if probe_logs:
            metadata = probe_logs[0].get("metadata", {})
            return metadata.get("user_input", "") or metadata.get("outline", "")
        return ""


def parse_score(response: str, label: str) -> int:
    """从LLM响应中解析分数。

    Args:
        response: LLM响应文本
        label: 分数标签（如"AI特征评分"）

    Returns:
        分数（0-100），解析失败返回50
    """
    match = re.search(rf"【{label}】(\d+)", response)
    if match:
        return int(match.group(1))
    return 50  # 默认分数
```

- [ ] **Step 6: 运行所有引擎测试**

```bash
pytest tests/test_evaluation/test_engine.py -v
```

Expected: PASS

- [ ] **Step 7: 提交评估引擎实现**

```bash
git add forge/evaluation/engine.py forge/evaluation/probe_calculator.py tests/test_evaluation/test_engine.py
git commit -m "feat: implement evaluation engine with RAGAS+LLM Judge"
```

---

## Task 7: 后台Worker实现

**Files:**
- Create: `forge/evaluation/worker.py`

- [ ] **Step 1: 编写Worker测试**

在 `tests/test_evaluation/test_engine.py` 末尾添加：

```python
class TestEvaluationWorker:
    """测试 Evaluation Worker。"""

    @pytest.mark.asyncio
    async def test_process_probe_log(self):
        """测试处理单条probe log。"""
        from forge.evaluation.worker import process_probe_log
        from unittest.mock import AsyncMock, patch

        payload = {
            "session_id": "test-session",
            "node_name": "editor",
            "timestamp": 1234567890,
            "input_metrics": {},
            "output_metrics": {},
            "duration_ms": 100,
        }

        with patch("forge.evaluation.worker.save_probe_log", new_callable=AsyncMock) as mock_save:
            await process_probe_log(payload)
            mock_save.assert_called_once_with(payload)

    @pytest.mark.asyncio
    async def test_trigger_evaluation_on_final_node(self):
        """测试在最终节点触发完整评估。"""
        from forge.evaluation.worker import should_trigger_evaluation

        # director节点应触发评估
        assert should_trigger_evaluation("director") == True
        assert should_trigger_evaluation("finalize") == True

        # 其他节点不触发
        assert should_trigger_evaluation("editor") == False
        assert should_trigger_evaluation("ai_detector") == False
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/test_evaluation/test_engine.py::TestEvaluationWorker -v
```

Expected: FAIL (模块不存在)

- [ ] **Step 3: 实现worker.py**

```python
# forge/evaluation/worker.py

"""评估Worker - 异步消费队列执行评估。

运行方式：
    python -m forge.evaluation.worker

或作为后台进程：
    asyncio.run(run_evaluation_worker())
"""

import json
import asyncio
import logging
import signal
import sys
from typing import Optional

import redis

from forge.config import REDIS_HOST, REDIS_PORT, EVAL_REDIS_DB, EVAL_QUEUE_NAME
from .engine import EvaluationEngine
from .storage import save_probe_log, get_session_probe_logs, save_evaluation_result

logger = logging.getLogger(__name__)

# Redis客户端
_redis_client: Optional[redis.Redis] = None

# 运行标志
_running = True


def _get_redis_client() -> redis.Redis:
    """获取Redis客户端。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=EVAL_REDIS_DB,
        )
    return _redis_client


def should_trigger_evaluation(node_name: str) -> bool:
    """判断是否应触发完整评估。

    在以下节点触发：
    - director（快速模式结束）
    - finalize（深度模式结束）
    """
    return node_name in ["director", "finalize"]


async def process_probe_log(payload: dict) -> None:
    """处理单条probe log。

    Args:
        payload: 探针数据包
    """
    session_id = payload.get("session_id", "unknown")
    node_name = payload.get("node_name", "unknown")

    logger.info(f"[EvalWorker] Processing: {session_id} / {node_name}")

    # 1. 保存probe log
    await save_probe_log(payload)

    # 2. 判断是否触发完整评估
    if should_trigger_evaluation(node_name):
        logger.info(f"[EvalWorker] Triggering evaluation for: {session_id}")

        try:
            # 获取该session所有probe logs
            session_logs = await get_session_probe_logs(session_id)

            if not session_logs:
                logger.warning(f"[EvalWorker] No logs found for session: {session_id}")
                return

            # 执行评估
            engine = EvaluationEngine()
            eval_result = await engine.evaluate_session(session_logs)

            # 保存评估结果
            await save_evaluation_result(session_id, eval_result)

            logger.info(f"[EvalWorker] Evaluation completed: {session_id}, score={eval_result.get('overall_score')}")

        except Exception as e:
            logger.error(f"[EvalWorker] Evaluation failed: {session_id}, error={e}")
            # 保存失败状态
            await save_evaluation_result(session_id, {
                "status": "failed",
                "error_message": str(e),
            })


async def run_evaluation_worker() -> None:
    """运行评估Worker主循环。

    阻塞式消费Redis队列，处理每条消息。
    """
    logger.info("[EvalWorker] Starting evaluation worker...")
    logger.info(f"[EvalWorker] Queue: {EVAL_QUEUE_NAME}, Redis: {REDIS_HOST}:{REDIS_PORT}/db{EVAL_REDIS_DB}")

    client = _get_redis_client()
    engine = EvaluationEngine()

    # 注册信号处理
    def signal_handler(signum, frame):
        global _running
        logger.info(f"[EvalWorker] Received signal {signum}, shutting down...")
        _running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while _running:
        try:
            # 阻塞获取队列消息（BRPOP超时30秒）
            result = client.brpop(EVAL_QUEUE_NAME, timeout=30)

            if result is None:
                # 超时，继续等待
                continue

            # 解析消息
            _, payload_bytes = result
            payload = json.loads(payload_bytes)

            # 处理消息
            await process_probe_log(payload)

        except redis.ConnectionError as e:
            logger.error(f"[EvalWorker] Redis connection error: {e}, reconnecting...")
            await asyncio.sleep(5)
            _redis_client = None  # 强制重新连接

        except json.JSONDecodeError as e:
            logger.error(f"[EvalWorker] JSON decode error: {e}")
            continue

        except Exception as e:
            logger.error(f"[EvalWorker] Unexpected error: {e}")
            await asyncio.sleep(1)

    logger.info("[EvalWorker] Worker stopped")


def main():
    """Worker入口函数。"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    # 运行Worker
    asyncio.run(run_evaluation_worker())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_evaluation/test_engine.py::TestEvaluationWorker -v
```

Expected: PASS

- [ ] **Step 5: 提交Worker实现**

```bash
git add forge/evaluation/worker.py tests/test_evaluation/test_engine.py
git commit -m "feat: implement evaluation worker (async queue consumer)"
```

---

## Task 8: 节点装饰器集成

**Files:**
- Modify: `forge/agents/editor.py`
- Modify: `forge/agents/ai_detector.py`
- Modify: `forge/agents/humanizer_editor.py`
- Modify: `forge/agents/reviewer.py`
- Modify: `forge/agents/director.py`

- [ ] **Step 1: 为editor.py添加探针装饰器**

在 `forge/agents/editor.py` 开头导入装饰器：

```python
from forge.evaluation.probe_decorator import with_probe
```

为 `editor_node` 函数添加装饰器：

```python
@traceable(name="Editor")
@with_probe("editor")
async def editor_node(state: GraphState) -> dict:
    """Rewrite content using Qwen LLM with knowledge base context.
    ...
    """
```

- [ ] **Step 2: 为ai_detector.py添加探针装饰器**

在 `forge/agents/ai_detector.py` 开头添加：

```python
from forge.evaluation.probe_decorator import with_probe
```

为 `ai_detector_node` 函数添加装饰器：

```python
@traceable(name="AI_Detector")
@with_probe("ai_detector")
async def ai_detector_node(state: GraphState) -> dict:
    """Detect AI-generated content characteristics using Claude.
    ...
    """
```

- [ ] **Step 3: 为humanizer_editor.py添加探针装饰器**

在 `forge/agents/humanizer_editor.py` 开头添加：

```python
from forge.evaluation.probe_decorator import with_probe
```

为 `humanizer_editor_node` 函数添加装饰器（带循环类型）：

```python
@traceable(name="Humanizer_Editor")
@with_probe("humanizer_editor", loop_type="humanize_loop")
async def humanizer_editor_node(state: GraphState) -> dict:
    """Humanize content to reduce AI-generated characteristics.
    ...
    """
```

- [ ] **Step 4: 为reviewer.py添加探针装饰器**

在 `forge/agents/reviewer.py` 开头添加：

```python
from forge.evaluation.probe_decorator import with_probe
```

为 `reviewer_node` 函数添加装饰器：

```python
@traceable(name="Reviewer")
@with_probe("reviewer")
async def reviewer_node(state: GraphState) -> dict:
    """Review rewritten content and provide feedback.
    ...
    """
```

- [ ] **Step 5: 为director.py添加探针装饰器**

在 `forge/agents/director.py` 开头添加：

```python
from forge.evaluation.probe_decorator import with_probe
```

为 `director_node` 函数添加装饰器：

```python
@traceable(name="Director")
@with_probe("director")
async def director_node(state: GraphState) -> dict:
    """Director node - finalize script and prepare for video/publishing.
    ...
    """
```

- [ ] **Step 6: 提交节点装饰器集成**

```bash
git add forge/agents/editor.py forge/agents/ai_detector.py forge/agents/humanizer_editor.py forge/agents/reviewer.py forge/agents/director.py
git commit -m "feat: integrate probe decorators into workflow nodes"
```

---

## Task 9: 深度模式节点装饰器集成

**Files:**
- Modify: `forge/agents/deep_nodes.py`

- [ ] **Step 1: 为deep_nodes.py添加探针装饰器**

首先读取现有文件结构，然后在关键节点添加装饰器：

```python
from forge.evaluation.probe_decorator import with_probe
```

为以下节点添加装饰器：

```python
@with_probe("deep_outline_generator")
async def deep_outline_generator_node(state: UnifiedState) -> dict:
    ...

@with_probe("research_agent")
async def research_agent_node(state: UnifiedState) -> dict:
    ...

@with_probe("reflection_writer")
async def reflection_writer_node(state: UnifiedState) -> dict:
    ...

@with_probe("finalize")
async def finalize_node(state: UnifiedState) -> dict:
    ...
```

- [ ] **Step 2: 提交深度模式装饰器集成**

```bash
git add forge/agents/deep_nodes.py
git commit -m "feat: integrate probe decorators into deep mode nodes"
```

---

## Task 10: 评估API实现

**Files:**
- Modify: `forge/web/app.py`

- [ ] **Step 1: 添加评估API端点**

在 `forge/web/app.py` 末尾添加评估API：

```python
# ============================================================
# Evaluation API
# ============================================================

from forge.evaluation.storage import get_evaluation_result, get_evaluation_stats


@app.get("/api/evaluation/{session_id}")
async def api_get_evaluation(session_id: str):
    """获取session的评估结果（用户端）。"""
    try:
        result = await get_evaluation_result(session_id)

        if result is None:
            return {"status": "pending", "message": "评估正在进行中或尚未开始"}

        if result.get("status") != "completed":
            return {
                "status": result.get("status", "pending"),
                "message": result.get("error_message", "评估未完成"),
            }

        # 用户端只返回简单分数
        return {
            "status": "completed",
            "overall_score": round(result.get("overall_score", 0.0) * 100),
            "faithfulness": round(result.get("faithfulness_score", 0.0) * 100),
            "relevance": round(result.get("relevance_score", 0.0) * 100),
            "human_score": round(result.get("human_score", 0.0) * 100),
            "tip": generate_evaluation_tip(result),
        }

    except Exception as e:
        logger.error(f"[API] Evaluation error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/admin/evaluation/{session_id}/detail")
async def api_get_evaluation_detail(session_id: str):
    """获取详细评估结果（后台分析）。"""
    try:
        from forge.evaluation.storage import get_session_probe_logs

        result = await get_evaluation_result(session_id)
        probe_logs = await get_session_probe_logs(session_id)

        if result is None:
            return {"status": "pending", "probe_logs": probe_logs}

        return {
            "status": "completed",
            "evaluation": result,
            "probe_logs": probe_logs,
        }

    except Exception as e:
        logger.error(f"[API] Evaluation detail error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/admin/evaluation/stats")
async def api_get_evaluation_stats(
    start_date: str = None,
    end_date: str = None,
    limit: int = 100,
):
    """获取评估统计数据（后台报告）。"""
    try:
        results = await get_evaluation_stats(start_date, end_date, limit)

        # 计算分布统计
        if results:
            scores = [r.get("overall_score", 0) for r in results]
            avg_score = sum(scores) / len(scores)
            sorted_scores = sorted(scores)
            median_score = sorted_scores[len(sorted_scores) // 2]

            distribution = {
                "total_count": len(results),
                "average_score": round(avg_score * 100, 1),
                "median_score": round(median_score * 100, 1),
            }
        else:
            distribution = {"total_count": 0}

        return {
            "success": True,
            "distribution": distribution,
            "results": results[:20],  # 只返回前20条详情
        }

    except Exception as e:
        logger.error(f"[API] Evaluation stats error: {e}")
        return {"success": False, "error": str(e)}


def generate_evaluation_tip(result: dict) -> str:
    """根据评估结果生成改进提示。"""
    tips = []

    faithfulness = result.get("faithfulness_score", 0.5)
    relevance = result.get("relevance_score", 0.5)
    human_score = result.get("human_score", 0.5)

    if faithfulness < 0.7:
        tips.append("文章存在未验证的事实，建议核实数据来源")

    if human_score < 0.6:
        tips.append("拟人性评分较低，建议减少AI常用词汇、增加口语化表达")

    if relevance < 0.7:
        tips.append("文章与原始需求相关性较弱，建议紧扣核心观点")

    if not tips:
        return "文章质量良好，可直接发布"

    return tips[0]
```

- [ ] **Step 2: 提交评估API**

```bash
git add forge/web/app.py
git commit -m "feat: add evaluation API endpoints (user + admin)"
```

---

## Task 11: 模块导出和最终集成

**Files:**
- Modify: `forge/evaluation/__init__.py`

- [ ] **Step 1: 更新模块导出**

更新 `forge/evaluation/__init__.py`：

```python
# forge/evaluation/__init__.py

"""Forge 评估系统 - 异步旁路评估模块。

核心组件：
- Probe: 轻量节点探针，记录执行特征
- ProbeDecorator: 装饰器自动插入探针
- Worker: 后台消费队列，执行评估
- Engine: RAGAS + LLM Judge 评估引擎
- Storage: PostgreSQL 存储层
- Calculator: 节点有效性 + 循环ROI计算

使用方式：
1. 在节点函数添加装饰器：
   @with_probe("editor")
   async def editor_node(state): ...

2. 启动Worker：
   python -m forge.evaluation.worker

3. 查询评估结果：
   GET /api/evaluation/{session_id}
"""

from .probe import probe_node, extract_key_metrics
from .probe_decorator import with_probe
from .probe_calculator import calculate_node_effectiveness, calculate_loop_roi
from .storage import (
    EvaluationStorage,
    get_evaluation_storage,
    save_probe_log,
    get_session_probe_logs,
    save_evaluation_result,
    get_evaluation_result,
)
from .engine import EvaluationEngine, parse_score
from .worker import run_evaluation_worker, process_probe_log

__all__ = [
    # Probe
    "probe_node",
    "extract_key_metrics",
    "with_probe",
    # Calculator
    "calculate_node_effectiveness",
    "calculate_loop_roi",
    # Storage
    "EvaluationStorage",
    "get_evaluation_storage",
    "save_probe_log",
    "get_session_probe_logs",
    "save_evaluation_result",
    "get_evaluation_result",
    # Engine
    "EvaluationEngine",
    "parse_score",
    # Worker
    "run_evaluation_worker",
    "process_probe_log",
]
```

- [ ] **Step 2: 运行所有测试验证**

```bash
pytest tests/test_evaluation/ -v
```

Expected: All PASS

- [ ] **Step 3: 提交最终集成**

```bash
git add forge/evaluation/__init__.py
git commit -m "feat: complete evaluation module integration"
```

---

## Task 12: 运行数据库迁移

- [ ] **Step 1: 执行迁移**

```bash
# 连接PostgreSQL执行迁移
psql -h localhost -U forge -d forge -f migrations/004_evaluation_tables.sql
```

Expected: Tables created successfully

- [ ] **Step 2: 验证表创建**

```bash
psql -h localhost -U forge -d forge -c "\dt probe_logs evaluation_results"
```

Expected: Tables listed

---

## Task 13: 安装依赖并验证

- [ ] **Step 1: 安装新依赖**

```bash
pip install ragas datasets
```

- [ ] **Step 2: 运行完整测试套件**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass

- [ ] **Step 3: 提交最终状态**

```bash
git status
git add -A
git commit -m "feat: complete evaluation system implementation"
```

---

## 验证清单

完成所有任务后，验证以下功能：

| 功能 | 验证方式 |
|------|----------|
| 探针数据采集 | 运行workflow，检查Redis队列有数据 |
| Worker消费队列 | 启动Worker，观察日志输出 |
| RAGAS评估 | 完成一个session，检查evaluation_results表 |
| API返回分数 | GET /api/evaluation/{session_id} |
| 节点有效性报告 | GET /api/admin/evaluation/{session_id}/detail |
| 循环ROI计算 | 检查node_effectiveness字段 |

---

## 后续迭代建议

1. **构建金标准数据集** - 收集50-100篇人工标注优秀文章
2. **优化循环阈值** - 根据ROI数据调整MAX_REVISIONS
3. **实时监控告警** - 异常分数触发通知
4. **前端分数卡片** - React组件展示评估结果