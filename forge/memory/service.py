"""从大纲和文章编辑事件中学习稳定的用户偏好。"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from forge.storage.pg_client import get_pg_pool, is_valid_uuid

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_OWNER_ID = "local-user"
GLOBAL_SCOPE = "*"

_EXPLICIT_SCOPE_MARKERS = (
    "以后",
    "今后",
    "每次",
    "始终",
    "一直",
    "记住",
    "默认",
)

_INTENT_PATTERNS = (
    (
        "shorter",
        re.compile(r"精简|简短|短一点|太长|压缩|删减|减少字数"),
        "表达更精简，删除重复、空泛和非必要内容。",
    ),
    (
        "longer",
        re.compile(r"扩写|展开|详细|太短|增加字数|丰富"),
        "在不重复的前提下补充细节、论据或例子。",
    ),
    (
        "remove_background",
        re.compile(r"删掉?.{0,8}(背景|行业介绍)|不要.{0,8}(背景|行业介绍)"),
        "避免泛化背景铺垫，直接进入具体问题。",
    ),
    (
        "direct_opening",
        re.compile(r"直接.{0,8}(切入|进入|开门见山)|开头.{0,8}(直接|别绕)"),
        "开头直接呈现核心问题或矛盾。",
    ),
    (
        "fewer_sections",
        re.compile(r"减少.{0,6}(章节|部分|标题)|大纲.{0,8}(太多|精简)|合并.{0,6}(章节|部分)"),
        "减少一级章节数量，合并重复或相近部分。",
    ),
    (
        "short_paragraphs",
        re.compile(r"短段落|段落.{0,6}(短|太长)|拆分.{0,6}段落"),
        "使用较短段落，避免单段承载过多信息。",
    ),
    (
        "natural_tone",
        re.compile(r"自然|口语|人话|不像人|生硬|机器味|AI味"),
        "表达自然、具体，避免模板化和机器化措辞。",
    ),
    (
        "stronger_opinion",
        re.compile(r"观点.{0,6}(明确|鲜明|加强)|态度.{0,6}(明确|鲜明)|更有观点"),
        "明确表达核心判断，并用事实或例子支撑。",
    ),
    (
        "add_examples",
        re.compile(r"增加?.{0,6}(案例|例子)|补充.{0,6}(案例|例子)|举例"),
        "在关键观点后补充具体案例或例子。",
    ),
    (
        "avoid_summary",
        re.compile(r"不要.{0,8}(总结|结语)|删掉?.{0,8}(总结|结语)|取消.{0,8}(总结|结语)"),
        "不设置模板化的总结或结语。",
    ),
)

_CONTRADICTORY_INTENTS = {
    "shorter": "longer",
    "longer": "shorter",
}


@dataclass(frozen=True)
class ClassifiedInstruction:
    intent_key: str
    instruction: str
    explicit_scope: bool


def _normalize_instruction(instruction: str) -> str:
    normalized = re.sub(r"\s+", "", instruction.strip().lower())
    return re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()]", "", normalized)


def classify_instruction(instruction: str) -> ClassifiedInstruction:
    """把常见自然语言要求归一为稳定、可聚合的偏好键。"""
    normalized = _normalize_instruction(instruction)
    explicit_scope = any(marker in normalized for marker in _EXPLICIT_SCOPE_MARKERS)

    for intent_key, pattern, canonical_instruction in _INTENT_PATTERNS:
        if pattern.search(normalized):
            return ClassifiedInstruction(
                intent_key=intent_key,
                instruction=canonical_instruction,
                explicit_scope=explicit_scope,
            )

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return ClassifiedInstruction(
        intent_key=f"custom_{digest}",
        instruction=instruction.strip(),
        explicit_scope=explicit_scope,
    )


def _make_diff_summary(before_text: str, after_text: str, limit: int = 2000) -> str:
    diff = difflib.unified_diff(
        before_text.splitlines(),
        after_text.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
        n=1,
    )
    return "\n".join(diff)[:limit]


def _calculate_confidence(
    evidence_count: int,
    success_count: int,
    contradiction_count: int,
    explicit_scope: bool,
) -> float:
    if evidence_count <= 0:
        return 0.0
    repeat_score = min(1.0, evidence_count / 3)
    success_rate = success_count / evidence_count
    explicit_score = 1.0 if explicit_scope else 0.0
    contradiction_penalty = min(0.6, contradiction_count * 0.2)
    confidence = (
        0.35 * repeat_score
        + 0.30 * success_rate
        + 0.20
        + 0.15 * explicit_score
        - contradiction_penalty
    )
    return round(max(0.0, min(1.0, confidence)), 4)


class PreferenceMemoryService:
    """编辑事件写入、偏好聚合与生成时检索。"""

    async def record_edit_event(
        self,
        *,
        memory_owner_id: str,
        session_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
        instruction: str,
        before_text: str,
        after_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not instruction.strip() or before_text == after_text:
            return None
        if not is_valid_uuid(session_id):
            logger.warning("[PreferenceMemory] Skip non-UUID session: %s", session_id)
            return None

        classified = classify_instruction(instruction)
        pool = await get_pg_pool()
        if pool is None:
            logger.warning("[PreferenceMemory] PostgreSQL unavailable; edit event not recorded")
            return None

        async with pool.acquire() as conn:
            event_id = await conn.fetchval(
                """
                INSERT INTO edit_events (
                    memory_owner_id, session_id, stage, target_platform,
                    content_type, instruction, intent_key, before_text,
                    after_text, diff_summary, metadata, explicit_scope
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                RETURNING id
                """,
                memory_owner_id,
                UUID(session_id),
                stage,
                target_platform or GLOBAL_SCOPE,
                content_type or GLOBAL_SCOPE,
                instruction.strip(),
                classified.intent_key,
                before_text,
                after_text,
                _make_diff_summary(before_text, after_text),
                json.dumps(metadata or {}),
                classified.explicit_scope,
            )
        return str(event_id)

    async def learn_from_finalized_session(self, session_id: str) -> int:
        """将定稿前尚未学习的编辑视为已接受，并更新候选规则。"""
        if not is_valid_uuid(session_id):
            return 0
        pool = await get_pg_pool()
        if pool is None:
            return 0

        learned_count = 0
        cases: List[Dict[str, Any]] = []
        async with pool.acquire() as conn:
            async with conn.transaction():
                events = await conn.fetch(
                    """
                    SELECT *
                    FROM edit_events
                    WHERE session_id = $1 AND learned_at IS NULL
                    ORDER BY created_at
                    FOR UPDATE
                    """,
                    UUID(session_id),
                )
                for event in events:
                    await self._upsert_rule(conn, dict(event))
                    case = await self._create_experience_case(conn, dict(event))
                    if case:
                        cases.append(case)
                    learned_count += 1

                if events:
                    await conn.execute(
                        """
                        UPDATE edit_events
                        SET accepted = TRUE, learned_at = NOW()
                        WHERE session_id = $1 AND learned_at IS NULL
                        """,
                        UUID(session_id),
                    )

        if cases:
            try:
                from .experience import get_experience_memory_manager

                manager = get_experience_memory_manager()
                for case in cases:
                    await manager.index_case(case)
            except Exception as exc:
                logger.warning("[PreferenceMemory] Experience indexing skipped: %s", exc)
        return learned_count

    async def _create_experience_case(
        self,
        conn: Any,
        event: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """从已接受编辑事件创建一条可检索经验。"""
        instruction = str(event["instruction"]).strip()
        before_excerpt = str(event["before_text"])[:1200]
        after_excerpt = str(event["after_text"])[:1200]
        diff_summary = str(event["diff_summary"])[:2000]
        retrieval_text = "\n".join(
            [
                f"阶段：{event['stage']}",
                f"平台：{event['target_platform']}",
                f"内容类型：{event['content_type']}",
                f"用户要求：{instruction}",
                f"修改摘要：{diff_summary}",
                f"修改后示例：{after_excerpt}",
            ]
        )
        row = await conn.fetchrow(
            """
            INSERT INTO memory_experience_cases (
                source_event_id, memory_owner_id, session_id, stage,
                target_platform, content_type, instruction, before_excerpt,
                after_excerpt, diff_summary, retrieval_text
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
            )
            ON CONFLICT (source_event_id) DO NOTHING
            RETURNING id
            """,
            event["id"],
            event["memory_owner_id"],
            event["session_id"],
            event["stage"],
            event["target_platform"],
            event["content_type"],
            instruction,
            before_excerpt,
            after_excerpt,
            diff_summary,
            retrieval_text,
        )
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "memory_owner_id": event["memory_owner_id"],
            "stage": event["stage"],
            "target_platform": event["target_platform"],
            "content_type": event["content_type"],
            "retrieval_text": retrieval_text,
        }

    async def _upsert_rule(self, conn: Any, event: Dict[str, Any]) -> None:
        classified_event = classify_instruction(event["instruction"])
        opposite_intent = _CONTRADICTORY_INTENTS.get(classified_event.intent_key)
        if opposite_intent:
            await conn.execute(
                """
                UPDATE user_preference_rules
                SET contradiction_count = contradiction_count + 1,
                    confidence = GREATEST(0, confidence - 0.2),
                    status = CASE
                        WHEN confidence - 0.2 < 0.45 THEN 'deprecated'
                        ELSE 'candidate'
                    END,
                    updated_at = NOW()
                WHERE memory_owner_id = $1
                  AND stage = $2
                  AND target_platform = $3
                  AND content_type = $4
                  AND intent_key = $5
                """,
                event["memory_owner_id"],
                event["stage"],
                event["target_platform"],
                event["content_type"],
                opposite_intent,
            )

        current = await conn.fetchrow(
            """
            SELECT *
            FROM user_preference_rules
            WHERE memory_owner_id = $1
              AND stage = $2
              AND target_platform = $3
              AND content_type = $4
              AND intent_key = $5
            FOR UPDATE
            """,
            event["memory_owner_id"],
            event["stage"],
            event["target_platform"],
            event["content_type"],
            event["intent_key"],
        )

        evidence_count = int(current["evidence_count"]) + 1 if current else 1
        success_count = int(current["success_count"]) + 1 if current else 1
        contradiction_count = int(current["contradiction_count"]) if current else 0
        explicit_scope = bool(event["explicit_scope"]) or bool(
            current and float(current["confidence"]) >= 0.7
        )
        confidence = _calculate_confidence(
            evidence_count,
            success_count,
            contradiction_count,
            explicit_scope,
        )
        status = "active" if confidence >= 0.7 else "candidate"

        await conn.execute(
            """
            INSERT INTO user_preference_rules (
                memory_owner_id, stage, target_platform, content_type,
                intent_key, instruction, evidence_count, success_count,
                contradiction_count, confidence, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (
                memory_owner_id, stage, target_platform, content_type, intent_key
            ) DO UPDATE SET
                instruction = EXCLUDED.instruction,
                evidence_count = EXCLUDED.evidence_count,
                success_count = EXCLUDED.success_count,
                contradiction_count = EXCLUDED.contradiction_count,
                confidence = EXCLUDED.confidence,
                status = EXCLUDED.status,
                updated_at = NOW(),
                last_seen_at = NOW()
            """,
            event["memory_owner_id"],
            event["stage"],
            event["target_platform"],
            event["content_type"],
            event["intent_key"],
            classified_event.instruction,
            evidence_count,
            success_count,
            contradiction_count,
            confidence,
            status,
        )

    async def get_preference_context(
        self,
        *,
        memory_owner_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
        limit: int = 8,
    ) -> str:
        pool = await get_pg_pool()
        if pool is None:
            return ""

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
            SELECT instruction, confidence
            FROM user_preference_rules
                WHERE memory_owner_id = $1
                  AND stage = $2
                  AND target_platform IN ($3, '*')
                  AND content_type IN ($4, '*')
                  AND status = 'active'
                  AND confidence * CASE
                      WHEN last_seen_at >= NOW() - INTERVAL '90 days' THEN 1
                      WHEN last_seen_at >= NOW() - INTERVAL '180 days' THEN 0.85
                      ELSE 0.7
                  END >= 0.7
            ORDER BY
                    (target_platform = $3) DESC,
                    (content_type = $4) DESC,
                    confidence * CASE
                        WHEN last_seen_at >= NOW() - INTERVAL '90 days' THEN 1
                        WHEN last_seen_at >= NOW() - INTERVAL '180 days' THEN 0.85
                        ELSE 0.7
                    END DESC,
                    last_seen_at DESC
                LIMIT $5
                """,
                memory_owner_id,
                stage,
                target_platform or GLOBAL_SCOPE,
                content_type or GLOBAL_SCOPE,
                limit,
            )

        if not rows:
            return ""
        rules = "\n".join(
            f"{index}. {row['instruction']}"
            for index, row in enumerate(rows, 1)
        )
        return (
            "以下是本次适用的用户长期偏好：\n"
            f"{rules}\n"
            "若本次明确要求与长期偏好冲突，以本次明确要求为准。"
        )


_service: Optional[PreferenceMemoryService] = None


def get_preference_memory_service() -> PreferenceMemoryService:
    global _service
    if _service is None:
        _service = PreferenceMemoryService()
    return _service
