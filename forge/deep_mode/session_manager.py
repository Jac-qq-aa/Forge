# forge/deep_mode/session_manager.py

"""会话管理器 - Redis + PostgreSQL 双存储架构。"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from forge.storage.redis_client import get_redis_session_manager
from forge.storage.pg_client import get_pg_session_manager
from forge.deep_mode.session_state import (
    DeepModeSession,
    create_session_id,
    create_initial_session,
    STAGE_WAITING_OUTLINE,
    STAGE_TUNING,
    STAGE_COMPLETED,
)

logger = logging.getLogger(__name__)


class SessionManager:
    """双存储会话管理器。

    Redis：活跃会话缓存（30分钟 TTL）
    PostgreSQL：持久化存储（历史记录）
    """

    def __init__(self):
        self.redis = get_redis_session_manager()
        self.pg = get_pg_session_manager()

    # ---- 创建会话 ----

    async def create_session(
        self,
        source_article: Dict[str, str],
        user_input: str = "",
        session_id: str = None
    ) -> Dict[str, Any]:
        """创建新会话（双写）。"""
        if session_id is None:
            session_id = create_session_id()

        session_data = create_initial_session(
            source_article=source_article,
            user_input=user_input,
            session_id=session_id,
        )

        # PG 作为持久层必须成功
        try:
            await self.pg.create_session(session_id, session_data)
            logger.info(f"[SessionManager] Session saved to PG: {session_id}")
        except Exception as e:
            logger.error(f"[SessionManager] PG create failed: {e}")
            raise RuntimeError(f"Failed to create session: {e}")

        # Redis 缓存层失败可降级
        try:
            await self.redis.create_session(session_id, session_data)
            logger.info(f"[SessionManager] Session cached to Redis: {session_id}")
        except Exception as e:
            logger.warning(f"[SessionManager] Redis unavailable, PG-only mode: {e}")

        logger.info(f"[SessionManager] Session created: {session_id}")
        return session_data

    # ---- 加载会话 ----

    async def load_session(self, session_id: str) -> Optional[DeepModeSession]:
        """加载会话（优先 Redis，降级 PG）。"""
        # 优先从 Redis 获取
        try:
            session = await self.redis.get_session(session_id)
            if session:
                messages = await self.redis.get_messages(session_id)
                session["tuning_history"] = messages
                logger.info(f"[SessionManager] Session loaded from Redis: {session_id}")
                return session
        except Exception as e:
            logger.warning(f"[SessionManager] Redis read failed, trying PG: {e}")

        # Redis 无数据或失败，从 PG 恢复
        try:
            session = await self.pg.get_session(session_id)
            if session and session.get("is_active"):
                messages = await self.pg.get_messages(session_id)
                session["tuning_history"] = messages

                # 尝试恢复到 Redis（失败不影响）
                try:
                    await self.redis.create_session(session_id, session)
                    for msg in messages:
                        await self.redis.append_message(session_id, msg)
                    logger.info(f"[SessionManager] Session restored to Redis: {session_id}")
                except Exception as e:
                    logger.warning(f"[SessionManager] Redis restore failed: {e}")

                logger.info(f"[SessionManager] Session loaded from PG: {session_id}")
                return session
        except Exception as e:
            logger.error(f"[SessionManager] PG read failed: {e}")

        logger.warning(f"[SessionManager] Session not found: {session_id}")
        return None

    # ---- 更新会话 ----

    async def update_session(
        self,
        session_id: str,
        **updates
    ) -> Dict[str, Any]:
        """更新会话（双写）。"""
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        updated_data = {**session, **updates}
        updated_data["updated_at"] = datetime.now().isoformat()

        # 关键节点：PG 优先
        stage = updates.get("stage")
        if stage in (STAGE_WAITING_OUTLINE, STAGE_TUNING, STAGE_COMPLETED):
            try:
                await self.pg.update_session(session_id, updated_data)
                logger.info(f"[SessionManager] Session synced to PG: {session_id}")
            except Exception as e:
                logger.error(f"[SessionManager] PG update failed: {e}")
                raise

        # Redis 缓存更新
        try:
            await self.redis.update_session(session_id, updates)
        except Exception as e:
            logger.warning(f"[SessionManager] Redis update failed: {e}")

        # 保存版本
        if "current_draft" in updates and updates["current_draft"]:
            history = session.get("tuning_history", [])
            version = len(history) + 1
            try:
                await self.pg.save_version(session_id, version=version, draft=updates["current_draft"])
            except Exception as e:
                logger.warning(f"[SessionManager] Version save failed: {e}")

        logger.info(f"[SessionManager] Session updated: {session_id}")
        return updated_data

    # ---- 消息操作 ----

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        is_question: bool = False,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """追加消息（双写）。"""
        message = {
            "role": role,
            "content": content,
            "is_question": is_question,
            "metadata": metadata,
            "timestamp": datetime.now().isoformat(),
        }

        # PG 持久化必须成功
        try:
            await self.pg.append_message(session_id, message)
        except Exception as e:
            logger.error(f"[SessionManager] PG message append failed: {e}")
            raise

        # Redis 缓存失败可降级
        try:
            await self.redis.append_message(session_id, message)
            await self.redis.refresh_ttl(session_id)
        except Exception as e:
            logger.warning(f"[SessionManager] Redis message append failed: {e}")

        return True

    # ---- 定稿 ----

    async def finalize_session(self, session_id: str) -> Dict[str, Any]:
        """定稿会话。"""
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        final_draft = session.get("current_draft", "") or session.get("draft_v1", "")

        # PG 定稿 - 必须成功
        try:
            await self.pg.finalize_session(session_id, final_draft)
            logger.info(f"[SessionManager] Session finalized in PG: {session_id}")
        except Exception as e:
            logger.error(f"[SessionManager] PG finalize failed: {e}")
            raise RuntimeError(f"Failed to finalize session: {e}")

        # Redis 清理 - 失败可忽略
        try:
            await self.redis.delete_session(session_id)
        except Exception as e:
            logger.warning(f"[SessionManager] Redis cleanup failed (non-critical): {e}")

        return {
            "session_id": session_id,
            "final_draft": final_draft,
            "status": "completed",
        }

    # ---- 心跳 ----

    async def heartbeat(self, session_id: str) -> bool:
        """更新心跳时间。"""
        try:
            await self.redis.refresh_ttl(session_id)
        except Exception as e:
            logger.warning(f"[SessionManager] Heartbeat failed (Redis unavailable): {e}")
        return True

    # ---- 断开保存 ----

    async def save_on_disconnect(self, session_id: str) -> bool:
        """WebSocket 断开时保存状态。"""
        try:
            session = await self.redis.get_session(session_id)
            if session:
                try:
                    messages = await self.redis.get_messages(session_id)
                    session["tuning_history"] = messages
                except Exception as e:
                    logger.warning(f"[SessionManager] Redis message fetch failed: {e}")

                try:
                    await self.pg.update_session(
                        session_id,
                        {
                            "stage": session.get("stage"),
                            "outline": session.get("outline"),
                            "outline_version": session.get("outline_version"),
                            "current_draft": session.get("current_draft"),
                            "rag_context": session.get("rag_context"),
                            "user_input": session.get("user_input"),
                            "is_active": False,
                        }
                    )
                    logger.info(f"[SessionManager] Session saved on disconnect: {session_id}")
                except Exception as e:
                    logger.error(f"[SessionManager] PG save on disconnect failed: {e}")
        except Exception as e:
            logger.warning(f"[SessionManager] Redis read failed during disconnect save: {e}")
        return True

    # ---- 历史列表 ----

    async def get_history_sessions(
        self,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取历史会话列表。"""
        return await self.pg.get_history_sessions(limit, offset)

    async def get_session_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话完整消息历史。"""
        return await self.pg.get_messages(session_id)

    # ---- 大纲版本 ----

    async def increment_outline_version(self, session_id: str) -> int:
        """增加大纲版本号。"""
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        new_version = session.get("outline_version", 0) + 1

        # PG 优先持久化
        try:
            await self.pg.update_session(session_id, {"outline_version": new_version})
        except Exception as e:
            logger.error(f"[SessionManager] PG version update failed: {e}")
            raise

        # Redis 缓存更新
        try:
            await self.redis.update_session(session_id, {"outline_version": new_version})
        except Exception as e:
            logger.warning(f"[SessionManager] Redis version update failed: {e}")

        logger.info(f"[SessionManager] Outline version incremented: {session_id} -> {new_version}")
        return new_version

    # ---- 阶段更新 ----

    async def update_stage(self, session_id: str, stage: str) -> DeepModeSession:
        """更新会话阶段。"""
        return await self.update_session(session_id, stage=stage)

    # ---- 列表查询 ----

    async def list_sessions(
        self,
        article_id: str = None,
        stage: str = None
    ) -> List[DeepModeSession]:
        """列出会话（从 PG 获取）。"""
        # 这里简化实现，从 PG 获取历史会话
        sessions = await self.pg.get_history_sessions(limit=100)
        result = []
        for s in sessions:
            if article_id and s.get("article_id") != article_id:
                continue
            if stage and s.get("stage") != stage:
                continue
            result.append(s)
        return result

    # ---- 取消会话 ----

    async def cancel_session(self, session_id: str) -> DeepModeSession:
        """取消会话。"""
        return await self.update_session(session_id, stage="cancelled")

    # ---- 清理过期会话 ----

    async def cleanup_expired_sessions(self):
        """清理过期会话（由 PG 定期任务处理）。"""
        # PostgreSQL 持久化存储，不需要主动清理
        # Redis 自动过期
        logger.info("[SessionManager] Cleanup handled by Redis TTL and PG maintenance")


# 全局实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取会话管理器实例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager