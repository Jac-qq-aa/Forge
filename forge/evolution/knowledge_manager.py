# forge/evolution/knowledge_manager.py

"""高质量案例知识库管理器。

核心职责：
- 高质量案例入库（PG + Milvus）
- RAG 检索相似案例
- 为内容生成提供高质量案例参考
"""

import os
import logging
import uuid
from typing import Dict, Any, List, Optional

from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
from sentence_transformers import SentenceTransformer

# 强制使用本地缓存
os.environ["HF_HUB_OFFLINE"] = "1"

from forge.knowledge.config import MILVUS_HOST, MILVUS_PORT
from .config import get_evolution_config
from .storage import get_evolution_storage
from .quality_aggregator import get_quality_aggregator
from .fallback import skip_quality_context

logger = logging.getLogger(__name__)


class QualityKnowledgeManager:
    """高质量案例知识库管理器。"""

    def __init__(self):
        self.storage = get_evolution_storage()
        self.aggregator = get_quality_aggregator()
        self.config = get_evolution_config()

        self.collection: Optional[Collection] = None
        self.encoder: Optional[SentenceTransformer] = None
        self._connected = False

    # ============================================================================
    # Milvus 连接管理
    # ============================================================================

    def _ensure_connection(self):
        """确保 Milvus 连接已建立。"""
        if self._connected:
            return

        try:
            connections.connect(
                alias="quality_cases",
                host=MILVUS_HOST,
                port=MILVUS_PORT,
            )
            logger.info(f"[QualityKB] Connected to Milvus: {MILVUS_HOST}:{MILVUS_PORT}")

            # 初始化 encoder
            self.encoder = SentenceTransformer(
                'all-MiniLM-L6-v2',
                local_files_only=True
            )
            logger.info("[QualityKB] Encoder loaded: all-MiniLM-L6-v2")

            # 创建 collection（如果不存在）
            collection_name = self.config.QUALITY_CASES_COLLECTION
            if not utility.has_collection(collection_name):
                self._create_collection(collection_name)
            else:
                self.collection = Collection(collection_name)
                logger.info(f"[QualityKB] Using existing collection: {collection_name}")

            self._connected = True

        except Exception as e:
            logger.error(f"[QualityKB] Failed to connect to Milvus: {e}")
            # 不抛出异常，允许降级运行

    def _create_collection(self, collection_name: str):
        """创建 Milvus collection。"""
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.config.VECTOR_DIMENSION),
            FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=64),  # 关联 PG quality_cases.id
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=500),  # 案例摘要
            FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields, description="Quality cases for content generation")

        self.collection = Collection(collection_name, schema)

        # 创建索引
        self.collection.create_index(
            field_name="vector",
            index_params={
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
        )
        logger.info(f"[QualityKB] Created collection: {collection_name}")

    def _encode(self, texts: List[str]) -> List[List[float]]:
        """编码文本为向量。"""
        if self.encoder is None:
            self.encoder = SentenceTransformer('all-MiniLM-L6-v2', local_files_only=True)
        return self.encoder.encode(texts, convert_to_numpy=True).tolist()

    # ============================================================================
    # 入库判断与操作
    # ============================================================================

    async def should_archive_as_quality_case(
        self,
        session: Dict[str, Any],
        evaluation_result: Dict[str, Any],
    ) -> bool:
        """判断是否满足入库条件。

        Args:
            session: 会话数据（包含 revision_count）
            evaluation_result: 评估结果（包含 human_score）

        Returns:
            True 如果满足入库条件
        """
        if not evaluation_result:
            logger.debug("[QualityKB] No evaluation result, skip archive check")
            return False

        # 提取评分数据
        human_score = evaluation_result.get("human_score", 0.0)

        # 从 tuning_history 计算 revision_count
        tuning_history = session.get("tuning_history", [])
        revision_count = self._count_revisions(tuning_history)

        # 调用聚合器判断
        return self.aggregator.should_archive_as_quality_case(
            human_score=human_score,
            revision_count=revision_count,
        )

    def _count_revisions(self, tuning_history: List[Dict]) -> int:
        """计算实际修改轮数（agent 响应中非回答类的数量）。

        Args:
            tuning_history: 微调对话历史

        Returns:
            修改轮数
        """
        revision_count = 0
        for msg in tuning_history:
            if msg.get("role") == "agent" and not msg.get("is_question"):
                revision_count += 1
        return revision_count

    async def archive_case(
        self,
        session: Dict[str, Any],
        tuning_history: List[Dict],
        evaluation_result: Dict[str, Any] = None,
    ) -> Optional[str]:
        """入库高质量案例（PG + Milvus）。

        Args:
            session: 会话数据
            tuning_history: 微调对话历史
            evaluation_result: 评估结果

        Returns:
            案例ID 如果成功入库，None 如果失败
        """
        session_id = session.get("session_id")
        if not session_id:
            logger.warning("[QualityKB] No session_id, skip archive")
            return None

        # 提取必要数据
        human_score = evaluation_result.get("human_score", 0.0) if evaluation_result else 0.0
        revision_count = self._count_revisions(tuning_history)
        quality_score = self.aggregator.calculate_quality_score(human_score, revision_count)

        # 获取草稿版本
        original_draft = session.get("draft_v1", "")
        final_draft = session.get("final_draft") or session.get("current_draft", "")

        if not original_draft or not final_draft:
            logger.warning("[QualityKB] Missing drafts, skip archive")
            return None

        # 提取平台信息
        source_article = session.get("source_article", {})
        target_platform = source_article.get("platform", "zhihu")

        # 1. 存入 PG
        try:
            case_id = await self.storage.insert_quality_case(
                source_session_id=session_id,
                quality_score=quality_score,
                human_score=human_score,
                revision_count=revision_count,
                original_draft=original_draft,
                final_draft=final_draft,
                tuning_history=tuning_history,
                target_platform=target_platform,
            )

            if not case_id:
                logger.warning(f"[QualityKB] Failed to insert case to PG: {session_id}")
                return None

        except Exception as e:
            logger.error(f"[QualityKB] PG insert failed: {e}")
            return None

        # 2. 存入 Milvus（异步，失败可降级）
        try:
            vector_id = await self._insert_to_milvus(
                case_id=case_id,
                final_draft=final_draft,
                platform=target_platform,
            )

            if vector_id:
                # 更新 PG 的 vector_id
                await self.storage.update_case_vector_id(case_id, vector_id)
                logger.info(f"[QualityKB] Case archived: {case_id}, vector={vector_id}")
            else:
                logger.warning(f"[QualityKB] Milvus insert failed for case: {case_id}")

        except Exception as e:
            logger.warning(f"[QualityKB] Milvus insert failed (non-critical): {e}")

        return case_id

    async def _insert_to_milvus(
        self,
        case_id: str,
        final_draft: str,
        platform: str,
    ) -> Optional[str]:
        """插入向量到 Milvus。

        Args:
            case_id: 案例ID
            final_draft: 定稿内容（用于生成摘要和向量）
            platform: 目标平台

        Returns:
            向量ID 如果成功插入
        """
        try:
            self._ensure_connection()

            if not self.collection:
                logger.warning("[QualityKB] Milvus collection not available")
                return None

            # 生成摘要（取定稿前500字）
            summary = final_draft[:500] if len(final_draft) > 500 else final_draft

            # 编码向量
            embedding = self._encode([summary])[0]

            # 插入
            vector_id = str(uuid.uuid4())
            data = [
                [vector_id],
                [embedding],
                [case_id],
                [summary],
                [platform],
            ]

            self.collection.insert(data)
            self.collection.flush()

            logger.debug(f"[QualityKB] Vector inserted: {vector_id}")
            return vector_id

        except Exception as e:
            logger.error(f"[QualityKB] Milvus insert error: {e}")
            return None

    # ============================================================================
    # RAG 检索
    # ============================================================================

    async def search_similar_cases(
        self,
        query: str,
        platform: str = None,
        n_results: int = 3,
    ) -> List[Dict[str, Any]]:
        """RAG 检索相似高质量案例。

        Args:
            query: 查询文本（如大纲）
            platform: 目标平台过滤（可选）
            n_results: 最大返回数量

        Returns:
            相似案例列表
        """
        try:
            self._ensure_connection()

            if not self.collection:
                logger.warning("[QualityKB] Milvus not available, returning empty")
                return []

            # 加载 collection
            self.collection.load()

            # 编码查询
            query_embedding = self._encode([query])[0]

            # 构建过滤表达式
            filter_expr = None
            if platform:
                filter_expr = f'platform == "{platform}"'

            # 搜索
            search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}
            results = self.collection.search(
                data=[query_embedding],
                anns_field="vector",
                param=search_params,
                limit=n_results,
                expr=filter_expr,
                output_fields=["case_id", "summary", "platform"]
            )

            # 格式化结果
            cases = []
            if results and len(results) > 0:
                for hit in results[0]:
                    case_id = hit.entity.get("case_id")
                    # 从 PG 获取完整案例信息
                    case_data = await self.storage.get_quality_case(case_id)
                    if case_data:
                        case_data["distance"] = hit.distance
                        cases.append(case_data)

            logger.info(f"[QualityKB] Search found {len(cases)} similar cases")
            return cases

        except Exception as e:
            logger.error(f"[QualityKB] Search failed: {e}")
            return []

    async def get_context_for_generation(
        self,
        outline: str,
        platform: str = None,
    ) -> str:
        """为内容生成提供高质量案例参考。

        Args:
            outline: 大纲内容
            platform: 目标平台

        Returns:
            格式化的参考文本
        """
        try:
            # 检索相似案例
            top_k = self.config.QUALITY_CASE_TOP_K
            cases = await self.search_similar_cases(
                query=outline,
                platform=platform,
                n_results=top_k,
            )

            if not cases:
                return skip_quality_context()

            # 格式化输出
            context_parts = ["以下是高质量文章参考案例：\n"]

            for i, case in enumerate(cases, 1):
                quality_score = case.get("quality_score", 0)
                final_draft_preview = case.get("final_draft", "")[:300]

                context_parts.append(f"**案例{i}** (质量评分: {quality_score:.2f})")
                context_parts.append(f"定稿片段:\n{final_draft_preview}...")
                context_parts.append("")

            context = "\n".join(context_parts)
            logger.debug(f"[QualityKB] Generated context: {len(context)} chars")
            return context

        except Exception as e:
            logger.warning(f"[QualityKB] get_context_for_generation failed: {e}")
            return skip_quality_context()


# 全局实例
_quality_knowledge_manager: Optional[QualityKnowledgeManager] = None


def get_quality_knowledge_manager() -> QualityKnowledgeManager:
    """获取知识库管理器实例。"""
    global _quality_knowledge_manager
    if _quality_knowledge_manager is None:
        _quality_knowledge_manager = QualityKnowledgeManager()
    return _quality_knowledge_manager