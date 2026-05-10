# forge/evolution/prompt_manager.py

"""Prompt模板版本管理器。

核心职责：
- 模板版本管理、激活切换、回滚
- 效果统计更新
- 安全的模板格式化
"""

import logging
from typing import Dict, Any, Optional

from .storage import get_evolution_storage
from .fallback import get_fallback_template

logger = logging.getLogger(__name__)


class PromptVersionManager:
    """Prompt模板版本管理器。"""

    def __init__(self):
        self.storage = get_evolution_storage()

    async def get_active_template(self, template_key: str) -> Optional[Dict[str, Any]]:
        """获取当前激活的模板。

        如果数据库中没有激活模板，返回硬编码降级模板。

        Args:
            template_key: 模板标识（如 "deep_content_generator")

        Returns:
            模板数据字典，包含:
            - id: 模板ID
            - template_key: 模板标识
            - version: 版本号
            - system_prompt: 系统提示词
            - user_prompt_template: 用户提示词模板
        """
        try:
            template = await self.storage.get_active_template(template_key)

            if template is None:
                logger.warning(f"[PromptManager] No active template for: {template_key}, using fallback")
                return get_fallback_template(template_key)

            return template

        except Exception as e:
            logger.error(f"[PromptManager] get_active_template failed: {e}, using fallback")
            return get_fallback_template(template_key)

    async def create_new_version(
        self,
        template_key: str,
        system_prompt: str,
        user_prompt_template: str,
        change_reason: str,
        previous_id: str = None,
        change_summary: str = None,
        activate: bool = False,
    ) -> str:
        """创建新版本模板。

        Args:
            template_key: 模板标识
            system_prompt: 新的系统提示词
            user_prompt_template: 新的用户提示词模板
            change_reason: 修改原因（LLM生成的分析）
            previous_id: 前一版本ID
            change_summary: 修改摘要
            activate: 是否立即激活

        Returns:
            新创建的模板ID
        """
        template_id = await self.storage.create_template(
            template_key=template_key,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            change_reason=change_reason,
            change_summary=change_summary,
            previous_version_id=previous_id,
            is_active=False,  # 先不激活
        )

        if activate and template_id:
            await self.activate_version(template_id)

        logger.info(f"[PromptManager] New template created: {template_key} (activate={activate})")
        return template_id

    async def activate_version(self, template_id: str) -> bool:
        """激活指定版本。

        Args:
            template_id: 模板ID

        Returns:
            True 如果成功激活
        """
        success = await self.storage.activate_template(template_id)

        if success:
            logger.info(f"[PromptManager] Template activated: {template_id}")
        else:
            logger.warning(f"[PromptManager] Failed to activate template: {template_id}")

        return success

    async def rollback(self, template_key: str, target_version: int) -> bool:
        """回滚到指定版本。

        Args:
            template_key: 模板标识
            target_version: 目标版本号

        Returns:
            True 如果成功回滚
        """
        history = await self.storage.get_template_history(template_key, limit=50)

        # 找到目标版本
        target_template = None
        for t in history:
            if t.get("version") == target_version:
                target_template = t
                break

        if not target_template:
            logger.warning(f"[PromptManager] Target version not found: {template_key} v{target_version}")
            return False

        # 激活目标版本
        return await self.activate_version(target_template["id"])

    async def update_effect_stats(
        self,
        template_id: str,
        quality_score: float,
        human_score: float,
        revision_count: int,
    ) -> bool:
        """更新模板效果统计。

        Args:
            template_id: 模板ID
            quality_score: 本次质量评分
            human_score: 本次人性化评分
            revision_count: 本次修改轮数

        Returns:
            True 如果成功更新
        """
        return await self.storage.update_template_stats(
            template_id=template_id,
            quality_score=quality_score,
            human_score=human_score,
            revision_count=revision_count,
        )

    async def get_template_history(
        self,
        template_key: str,
        limit: int = 10,
    ) -> list:
        """获取模板版本历史。

        Args:
            template_key: 模板标识
            limit: 最大返回数量

        Returns:
            版本列表
        """
        return await self.storage.get_template_history(template_key, limit)

    def safe_format_template(self, template: str, variables: Dict[str, Any]) -> str:
        """安全格式化模板，缺失变量使用默认值。

        Args:
            template: 模板字符串（包含 {var} 占位符）
            variables: 变量字典

        Returns:
            格式化后的字符串
        """
        # 为缺失变量提供默认值
        safe_vars = {}
        for key, value in variables.items():
            if value is None:
                safe_vars[key] = "无"
            elif isinstance(value, str) and not value.strip():
                safe_vars[key] = "无"
            else:
                safe_vars[key] = value

        try:
            return template.format(**safe_vars)
        except KeyError as e:
            # 如果模板中有未提供的变量，使用空字符串
            logger.warning(f"[PromptManager] Missing template variable: {e}")
            missing_key = str(e).strip("'")
            safe_vars[missing_key] = ""
            return template.format(**safe_vars)


# 全局实例
_prompt_manager: Optional[PromptVersionManager] = None


def get_prompt_manager() -> PromptVersionManager:
    """获取模板管理器实例。"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptVersionManager()
    return _prompt_manager