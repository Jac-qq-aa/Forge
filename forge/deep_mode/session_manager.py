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
from forge.deep_mode.errors import SessionNotFoundError

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
        session_id: Optional[str] = None
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
            if session:
                messages = await self.pg.get_messages(session_id)
                session["tuning_history"] = messages

                # 只有活跃会话才恢复到 Redis 缓存
                if session.get("is_active"):
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
            raise SessionNotFoundError(f"Session not found: {session_id}")

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
        metadata: Optional[Dict[str, Any]] = None
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

    async def finalize_session(self, session_id: str, final_draft: Optional[str] = None) -> Dict[str, Any]:
        """定稿会话。

        Args:
            session_id: 会话ID
            final_draft: 最终稿件内容（可选）。如果不提供，从 session 中读取 current_draft 或 draft_v1。
        """
        session = await self.load_session(session_id)
        if not session:
            raise SessionNotFoundError(f"Session not found: {session_id}")

        # 优先使用传入的 final_draft，否则从 session 中读取
        if final_draft is None:
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
            "current_draft": final_draft,
            "status": "completed",
            "stage": STAGE_COMPLETED,
            "finalized_at": datetime.now().isoformat(),
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
        try:
            return await self.pg.get_history_sessions(limit, offset)
        except Exception as e:
            logger.error(f"[SessionManager] PG history query failed: {e}")
            return []

    async def get_session_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话完整消息历史。"""
        try:
            return await self.pg.get_messages(session_id)
        except Exception as e:
            logger.error(f"[SessionManager] PG messages query failed: {e}")
            return []

    # ---- 大纲版本 ----

    async def increment_outline_version(self, session_id: str) -> int:
        """增加大纲版本号。"""
        session = await self.load_session(session_id)
        if not session:
            raise SessionNotFoundError(f"Session not found: {session_id}")

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
        article_id: Optional[str] = None,
        stage: Optional[str] = None
    ) -> List[DeepModeSession]:
        """列出会话（从 PG 获取）。"""
        try:
            sessions = await self.pg.get_history_sessions(limit=100)
            result = []
            for s in sessions:
                if article_id and s.get("article_id") != article_id:
                    continue
                if stage and s.get("stage") != stage:
                    continue
                result.append(s)
            return result
        except Exception as e:
            logger.error(f"[SessionManager] PG history sessions query failed: {e}")
            return []

    # ---- 取消会话 ----

    async def cancel_session(self, session_id: str) -> DeepModeSession:
        """取消会话。"""
        return await self.update_session(session_id, stage="cancelled")

    # ---- 软删除操作 ----

    async def soft_delete_session(self, session_id: str) -> bool:
        """软删除单条会话（双写清理）。"""
        # PG 软删除
        try:
            success = await self.pg.soft_delete_session(session_id)
            if not success:
                logger.warning(f"[SessionManager] Session not found: {session_id}")
                return False
        except Exception as e:
            logger.error(f"[SessionManager] PG soft delete failed: {e}")
            raise

        # Redis 清理（失败可忽略）
        try:
            await self.redis.delete_session(session_id)
        except Exception as e:
            logger.warning(f"[SessionManager] Redis cleanup failed: {e}")

        logger.info(f"[SessionManager] Session soft deleted: {session_id}")
        return True

    async def soft_delete_sessions(self, session_ids: List[str]) -> int:
        """批量软删除会话。"""
        if not session_ids:
            return 0

        # PG 批量软删除
        try:
            count = await self.pg.soft_delete_sessions(session_ids)
        except Exception as e:
            logger.error(f"[SessionManager] PG batch delete failed: {e}")
            raise

        # Redis 批量清理
        for sid in session_ids:
            try:
                await self.redis.delete_session(sid)
            except Exception as e:
                logger.warning(f"[SessionManager] Redis cleanup failed for {sid}: {e}")

        logger.info(f"[SessionManager] Batch soft deleted {count} sessions")
        return count

    async def soft_delete_all_sessions(self) -> int:
        """软删除所有会话（清空历史）。"""
        # PG 清空
        try:
            count = await self.pg.soft_delete_all_sessions()
        except Exception as e:
            logger.error(f"[SessionManager] PG clear all failed: {e}")
            raise

        # Redis 清空（可选，清理所有缓存）
        try:
            # 注意：这里不清理所有 Redis key，只清理已知的 session 缓存
            # Redis 使用 TTL 自动过期，不需要主动清理
            logger.info("[SessionManager] Redis sessions will expire by TTL")
        except Exception as e:
            logger.warning(f"[SessionManager] Redis cleanup skipped: {e}")

        logger.info(f"[SessionManager] All sessions soft deleted: {count}")
        return count

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
