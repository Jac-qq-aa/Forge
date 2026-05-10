# forge/evolution/__init__.py

"""深度模式自进化系统。

核心功能：
1. Prompt模板版本管理与自动优化
2. 高质量案例知识库（PG + Milvus）
3. LLM驱动的反馈模式分析
4. 混合触发机制（阈值 + 定时）
"""

from .config import get_evolution_config, EvolutionConfig
from .storage import (
    get_evolution_storage,
    EvolutionStorage,
)
from .prompt_manager import (
    get_prompt_manager,
    PromptVersionManager,
)
from .init_templates import (
    init_default_templates,
    ensure_default_templates,
)
from .quality_aggregator import (
    get_quality_aggregator,
    QualityAggregator,
)
from .knowledge_manager import (
    get_quality_knowledge_manager,
    QualityKnowledgeManager,
)
from .fallback import (
    get_fallback_template,
    skip_quality_context,
    is_fallback_template,
)
from .engine import (
    get_evolution_engine,
    EvolutionEngine,
)
from .trigger import (
    get_evolution_trigger,
    EvolutionTrigger,
)
from .worker import (
    run_evolution_analysis,
    run_evolution_worker,
)


__all__ = [
    # 配置
    "get_evolution_config",
    "EvolutionConfig",
    # 存储
    "get_evolution_storage",
    "EvolutionStorage",
    # 模板管理
    "get_prompt_manager",
    "PromptVersionManager",
    "init_default_templates",
    "ensure_default_templates",
    # 质量评分
    "get_quality_aggregator",
    "QualityAggregator",
    # 知识库
    "get_quality_knowledge_manager",
    "QualityKnowledgeManager",
    # 降级
    "get_fallback_template",
    "skip_quality_context",
    "is_fallback_template",
    # 分析引擎
    "get_evolution_engine",
    "EvolutionEngine",
    # 触发器
    "get_evolution_trigger",
    "EvolutionTrigger",
    # Worker
    "run_evolution_analysis",
    "run_evolution_worker",
]