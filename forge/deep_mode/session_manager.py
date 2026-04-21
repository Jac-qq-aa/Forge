# forge/deep_mode/session_manager.py

"""Session Manager - SQLite 存储和状态注入。"""

import json
import logging
import os
import asyncio
from datetime import datetime
from typing import Optional, List
import aiosqlite

from forge.deep_mode.session_state import (
    DeepModeSession,
    ProfileInfo,
    SessionStage,
    create_session_id,
    create_initial_session,
)
from forge.deep_mode.errors import SessionNotFoundError, OutlineRevisionLimitError
from forge.config import DEEP_MODE_SESSION_TTL, OUTLINE_MAX_REVISIONS

logger = logging.getLogger(__name__)

# SQLite 数据库路径
SESSION_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sessions.db")


class SessionManager:
    """深度生成会话管理器。"""

    def __init__(self, db_path: str = SESSION_DB_PATH):
        self.db_path = db_path
        self._initialized = False

    async def _ensure_db(self):
        """确保数据库和表已创建。"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS deep_mode_sessions (
                    session_id TEXT PRIMARY KEY,
                    article_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    outline TEXT NOT NULL,
                    outline_version INTEGER NOT NULL,
                    draft_v1 TEXT NOT NULL,
                    current_draft TEXT NOT NULL,
                    tuning_history TEXT NOT NULL,
                    source_article TEXT NOT NULL,
                    rag_context TEXT NOT NULL,
                    final_draft TEXT NOT NULL,
                    finalized_at TEXT
                )
            """)
            await db.commit()

        self._initialized = True
        logger.info(f"[SessionManager] Database initialized: {self.db_path}")

    async def create_session(
        self,
        article_id: str,
        source_article: dict,
        profile: ProfileInfo = None
    ) -> DeepModeSession:
        """创建新会话。"""
        await self._ensure_db()

        session = create_initial_session(article_id, source_article, profile)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO deep_mode_sessions (
                    session_id, article_id, created_at, updated_at, stage,
                    profile, outline, outline_version, draft_v1, current_draft,
                    tuning_history, source_article, rag_context, final_draft, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session["session_id"],
                session["article_id"],
                session["created_at"],
                session["updated_at"],
                session["stage"],
                json.dumps(session["profile"]),
                session["outline"],
                session["outline_version"],
                session["draft_v1"],
                session["current_draft"],
                json.dumps(session["tuning_history"]),
                json.dumps(session["source_article"]),
                session["rag_context"],
                session["final_draft"],
                session["finalized_at"],
            ))
            await db.commit()

        logger.info(f"[SessionManager] Session created: {session['session_id']}")
        return session

    async def load_session(self, session_id: str) -> DeepModeSession:
        """加载会话。"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM deep_mode_sessions WHERE session_id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()

            if row is None:
                raise SessionNotFoundError(session_id)

            return DeepModeSession(
                session_id=row["session_id"],
                article_id=row["article_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                stage=row["stage"],
                profile=json.loads(row["profile"]),
                outline=row["outline"],
                outline_version=row["outline_version"],
                draft_v1=row["draft_v1"],
                current_draft=row["current_draft"],
                tuning_history=json.loads(row["tuning_history"]),
                source_article=json.loads(row["source_article"]),
                rag_context=row["rag_context"],
                final_draft=row["final_draft"],
                finalized_at=row["finalized_at"],
            )

    async def update_session(self, session_id: str, **updates) -> DeepModeSession:
        """更新会话字段。"""
        await self._ensure_db()

        session = await self.load_session(session_id)

        # 更新字段
        for key, value in updates.items():
            if key in session:
                session[key] = value

        session["updated_at"] = datetime.now().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE deep_mode_sessions SET
                    updated_at = ?, stage = ?, profile = ?, outline = ?,
                    outline_version = ?, draft_v1 = ?, current_draft = ?,
                    tuning_history = ?, rag_context = ?, final_draft = ?, finalized_at = ?
                WHERE session_id = ?
            """, (
                session["updated_at"],
                session["stage"],
                json.dumps(session["profile"]),
                session["outline"],
                session["outline_version"],
                session["draft_v1"],
                session["current_draft"],
                json.dumps(session["tuning_history"]),
                session["rag_context"],
                session["final_draft"],
                session["finalized_at"],
                session_id,
            ))
            await db.commit()

        logger.info(f"[SessionManager] Session updated: {session_id}, stage={session['stage']}")
        return session

    async def update_stage(self, session_id: str, stage: SessionStage) -> DeepModeSession:
        """更新会话阶段。"""
        return await self.update_session(session_id, stage=stage)

    async def increment_outline_version(self, session_id: str) -> int:
        """增加大纲版本号，检查上限。"""
        session = await self.load_session(session_id)

        if session["outline_version"] >= OUTLINE_MAX_REVISIONS:
            raise OutlineRevisionLimitError(OUTLINE_MAX_REVISIONS)

        new_version = session["outline_version"] + 1
        await self.update_session(session_id, outline_version=new_version)
        return new_version

    async def finalize_session(self, session_id: str) -> DeepModeSession:
        """定稿会话。"""
        session = await self.load_session(session_id)
        now = datetime.now().isoformat()

        return await self.update_session(
            session_id,
            stage="completed",
            final_draft=session["current_draft"] or session["draft_v1"],
            finalized_at=now,
        )

    async def cancel_session(self, session_id: str) -> DeepModeSession:
        """取消会话。"""
        return await self.update_session(session_id, stage="cancelled")

    async def list_sessions(self, article_id: str = None, stage: str = None) -> List[DeepModeSession]:
        """列出会话。"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if article_id and stage:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE article_id = ? AND stage = ?",
                    (article_id, stage)
                )
            elif article_id:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE article_id = ?",
                    (article_id,)
                )
            elif stage:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE stage = ?",
                    (stage,)
                )
            else:
                cursor = await db.execute("SELECT * FROM deep_mode_sessions")

            rows = await cursor.fetchall()

            sessions = []
            for row in rows:
                sessions.append(DeepModeSession(
                    session_id=row["session_id"],
                    article_id=row["article_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    stage=row["stage"],
                    profile=json.loads(row["profile"]),
                    outline=row["outline"],
                    outline_version=row["outline_version"],
                    draft_v1=row["draft_v1"],
                    current_draft=row["current_draft"],
                    tuning_history=json.loads(row["tuning_history"]),
                    source_article=json.loads(row["source_article"]),
                    rag_context=row["rag_context"],
                    final_draft=row["final_draft"],
                    finalized_at=row["finalized_at"],
                ))

            return sessions

    async def cleanup_expired_sessions(self):
        """清理过期会话。"""
        await self._ensure_db()

        cutoff = datetime.now().timestamp() - DEEP_MODE_SESSION_TTL
        cutoff_dt = datetime.fromtimestamp(cutoff).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM deep_mode_sessions WHERE created_at < ? AND stage NOT IN ('completed', 'cancelled')",
                (cutoff_dt,)
            )
            deleted = db.total_changes
            await db.commit()

        if deleted > 0:
            logger.info(f"[SessionManager] Cleaned up {deleted} expired sessions")


# 全局实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取 Session Manager 单例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager