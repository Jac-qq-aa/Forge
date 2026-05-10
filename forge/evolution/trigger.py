# forge/evolution/trigger.py

"""自进化触发器 - 混合触发机制。

核心职责：
- 阈值触发：累积样本达到阈值时触发
- 定时触发：凌晨指定时间触发
- 最小间隔保护：防止频繁触发
"""

import logging
from typing import Tuple, Optional, List
from datetime import datetime, timedelta
from uuid import UUID

from .config import get_evolution_config
from .storage import get_evolution_storage

logger = logging.getLogger(__name__)


class EvolutionTrigger:
    """混合触发器 - 阈值 + 定时."""

    def __init__(self):
        self.config = get_evolution_config()
        self.storage = get_evolution_storage()

        # 待分析session队列（内存缓存）
        self._pending_sessions: List[str] = []

        # 上次触发时间
        self._last_trigger_time: Optional[datetime] = None

    async def register_completed_session(self, session_id: str) -> Tuple[bool, str]:
        """注册新完成session，检查阈值触发.

        Args:
            session_id: 完成的session ID

        Returns:
            (should_trigger, trigger_type) 元组
            - should_trigger: True 表示应该触发分析
            - trigger_type: "threshold" / "" (空字符串表示不触发)
        """
        # 添加到待分析队列
        if session_id not in self._pending_sessions:
            self._pending_sessions.append(session_id)
            logger.debug(f"[EvolutionTrigger] Session registered: {session_id}, pending={len(self._pending_sessions)}")

        # 检查阈值触发
        threshold = self.config.THRESHOLD_COUNT

        if len(self._pending_sessions) >= threshold:
            # 检查最小间隔
            if await self._check_min_interval():
                logger.info(
                    f"[EvolutionTrigger] Threshold triggered: "
                    f"pending={len(self._pending_sessions)} >= threshold={threshold}"
                )
                return True, "threshold"

        return False, ""

    async def check_scheduled_trigger(self) -> Tuple[bool, str]:
        """检查定时触发条件.

        定时触发在指定时间点（如凌晨2点）触发，
        但需要满足最小间隔和最小样本数。

        Returns:
            (should_trigger, trigger_type) 元组
        """
        # 检查当前时间是否接近触发时间
        now = datetime.now()

        # 计算今天的触发时间
        trigger_hour = self.config.SCHEDULE_HOUR
        trigger_time_today = now.replace(hour=trigger_hour, minute=0, second=0, microsecond=0)

        # 判断是否在触发时间窗口内（前后30分钟）
        time_diff = abs((now - trigger_time_today).total_seconds())

        if time_diff > 1800:  # 30分钟窗口
            logger.debug(f"[EvolutionTrigger] Not in schedule window, time_diff={time_diff}s")
            return False, ""

        # 检查最小间隔
        if not await self._check_min_interval():
            logger.debug("[EvolutionTrigger] Min interval not satisfied")
            return False, ""

        # 检查最小样本数
        if len(self._pending_sessions) < self.config.MIN_SAMPLE_FOR_ANALYSIS:
            logger.debug(
                f"[EvolutionTrigger] Insufficient samples: "
                f"pending={len(self._pending_sessions)} < min={self.config.MIN_SAMPLE_FOR_ANALYSIS}"
            )
            return False, ""

        logger.info(
            f"[EvolutionTrigger] Scheduled triggered at hour {trigger_hour}, "
            f"pending={len(self._pending_sessions)}"
        )
        return True, "scheduled"

    async def should_trigger(self) -> Tuple[bool, str]:
        """综合判断是否触发.

        优先级：
        1. 阈值触发（更高优先级）
        2. 定时触发

        Returns:
            (should_trigger, trigger_type) 元组
        """
        # 先检查阈值触发
        threshold = self.config.THRESHOLD_COUNT
        if len(self._pending_sessions) >= threshold:
            if await self._check_min_interval():
                return True, "threshold"

        # 再检查定时触发
        scheduled, _ = await self.check_scheduled_trigger()
        if scheduled:
            return True, "scheduled"

        return False, ""

    async def _check_min_interval(self) -> bool:
        """检查最小间隔是否满足.

        Returns:
            True 如果距离上次触发已超过最小间隔
        """
        if self._last_trigger_time is None:
            return True

        min_interval_hours = self.config.MIN_INTERVAL_HOURS
        elapsed = datetime.now() - self._last_trigger_time

        if elapsed.total_seconds() < min_interval_hours * 3600:
            logger.debug(
                f"[EvolutionTrigger] Min interval not elapsed: "
                f"{elapsed.total_seconds()/3600:.1f}h < {min_interval_hours}h"
            )
            return False

        return True

    def get_pending_sessions(self) -> List[str]:
        """获取待分析session列表.

        Returns:
            session ID列表
        """
        return self._pending_sessions.copy()

    def clear_pending(self, trigger_type: str = None):
        """清空待分析队列（分析完成后调用）.

        Args:
            trigger_type: 触发类型（用于日志）
        """
        count = len(self._pending_sessions)
        self._pending_sessions = []
        self._last_trigger_time = datetime.now()

        logger.info(
            f"[EvolutionTrigger] Pending cleared: {count} sessions, "
            f"trigger_type={trigger_type}, last_trigger={self._last_trigger_time}"
        )

    async def get_trigger_stats(self) -> dict:
        """获取触发统计信息.

        Returns:
            统计数据字典
        """
        return {
            "pending_count": len(self._pending_sessions),
            "threshold": self.config.THRESHOLD_COUNT,
            "min_sample": self.config.MIN_SAMPLE_FOR_ANALYSIS,
            "schedule_hour": self.config.SCHEDULE_HOUR,
            "min_interval_hours": self.config.MIN_INTERVAL_HOURS,
            "last_trigger_time": self._last_trigger_time.isoformat() if self._last_trigger_time else None,
            "threshold_progress": len(self._pending_sessions) / self.config.THRESHOLD_COUNT,
        }

    async def load_pending_from_db(self) -> int:
        """从数据库加载待分析session.

        用于服务重启后恢复待分析队列。

        Returns:
            加载的session数量
        """
        sessions = await self.storage.get_sessions_for_analysis(
            min_samples=self.config.MIN_SAMPLE_FOR_ANALYSIS,
            limit=self.config.THRESHOLD_COUNT * 2,  # 加载更多以备后续分析
        )

        for sid in sessions:
            if sid not in self._pending_sessions:
                self._pending_sessions.append(sid)

        logger.info(f"[EvolutionTrigger] Loaded {len(sessions)} pending sessions from DB")
        return len(sessions)


# 全局实例
_evolution_trigger: Optional[EvolutionTrigger] = None


def get_evolution_trigger() -> EvolutionTrigger:
    """获取触发器实例。"""
    global _evolution_trigger
    if _evolution_trigger is None:
        _evolution_trigger = EvolutionTrigger()
    return _evolution_trigger