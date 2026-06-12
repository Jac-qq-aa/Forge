"""分段生成器 - 参考 LongWriter AgentWrite 设计。

核心功能：
1. 生成段落级大纲（每段有主点、字数、素材引用）
2. 逐段生成全文（保持上下文连贯）
3. 缓存机制（避免重复调用）

参考：LongWriter agentwrite/plan.py + write.py
"""

import logging
import re
import json
import os
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ============================================================================
# 段落大纲结构
# ============================================================================

class ParagraphTask:
    """单个段落的生成任务"""

    def __init__(
        self,
        id: int,
        main_point: str,
        word_count: int,
        rag_reference: str = "",
    ):
        self.id = id
        self.main_point = main_point
        self.word_count = word_count
        self.rag_reference = rag_reference

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "main_point": self.main_point,
            "word_count": self.word_count,
            "rag_reference": self.rag_reference,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ParagraphTask":
        return cls(
            id=data.get("id", 0),
            main_point=data.get("main_point", ""),
            word_count=data.get("word_count", 500),
            rag_reference=data.get("rag_reference", ""),
        )


# ============================================================================
# 段落大纲解析
# ============================================================================

def parse_paragraph_outline(outline_text: str) -> List[ParagraphTask]:
    """解析LLM输出的段落大纲。

    支持格式：
    - 段落1 | 主点：xxx | 字数：400 | 素材：xxx
    - 段落1 - 主点：xxx - 字数：400 - 素材：xxx
    - Paragraph 1 - Main Point: xxx - Word Count: 400

    Returns:
        段落任务列表
    """
    paragraphs = []

    lines = outline_text.strip().split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # 尝试多种格式解析
        paragraph = None

        # 格式1: 段落1 | 主点：xxx | 字数：400 | 素材：xxx
        if '|' in line:
            paragraph = _parse_pipe_format(line, i + 1)
        # 格式2: 段落1 - 主点：xxx - 字数：400
        elif '-' in line or '：' in line:
            paragraph = _parse_dash_format(line, i + 1)
        # 格式3: Paragraph 1 - Main Point: xxx - Word Count: 400
        elif 'Paragraph' in line or 'Main Point' in line:
            paragraph = _parse_english_format(line, i + 1)

        if paragraph:
            paragraphs.append(paragraph)

    # 如果解析失败，降级为简单分割
    if not paragraphs:
        logger.warning("[ParagraphParser] Failed to parse outline, using fallback")
        paragraphs = _fallback_parse(outline_text)

    logger.info(f"[ParagraphParser] Parsed {len(paragraphs)} paragraphs")
    return paragraphs


def _parse_pipe_format(line: str, default_id: int) -> Optional[ParagraphTask]:
    """解析 | 分隔格式"""
    parts = line.split('|')

    main_point = ""
    word_count = 500
    rag_reference = ""

    for part in parts:
        part = part.strip()
        if '主点' in part or '要点' in part:
            main_point = part.split('：')[-1].strip() if '：' in part else part.split(':')[-1].strip()
        elif '字数' in part:
            word_match = re.search(r'\d+', part)
            if word_match:
                word_count = int(word_match.group())
        elif '素材' in part:
            rag_reference = part.split('：')[-1].strip() if '：' in part else part.split(':')[-1].strip()

    if main_point:
        return ParagraphTask(
            id=default_id,
            main_point=main_point,
            word_count=word_count,
            rag_reference=rag_reference,
        )
    return None


def _parse_dash_format(line: str, default_id: int) -> Optional[ParagraphTask]:
    """解析 - 分隔格式"""
    parts = line.replace('：', ':').split('-')

    main_point = ""
    word_count = 500
    rag_reference = ""

    for part in parts:
        part = part.strip()
        if '主点' in part or '要点' in part or 'main' in part.lower():
            main_point = part.split(':')[-1].strip()
        elif '字数' in part or 'word' in part.lower():
            word_match = re.search(r'\d+', part)
            if word_match:
                word_count = int(word_match.group())
        elif '素材' in part:
            rag_reference = part.split(':')[-1].strip()

    if main_point:
        return ParagraphTask(
            id=default_id,
            main_point=main_point,
            word_count=word_count,
            rag_reference=rag_reference,
        )
    return None


def _parse_english_format(line: str, default_id: int) -> Optional[ParagraphTask]:
    """解析英文格式"""
    # Paragraph 1 - Main Point: xxx - Word Count: 400
    main_match = re.search(r'Main Point:\s*([^-\n]+)', line)
    word_match = re.search(r'Word Count:\s*(\d+)', line)

    main_point = main_match.group(1).strip() if main_match else ""
    word_count = int(word_match.group(1)) if word_match else 500

    if main_point:
        return ParagraphTask(
            id=default_id,
            main_point=main_point,
            word_count=word_count,
            rag_reference="",
        )
    return None


def _fallback_parse(outline_text: str) -> List[ParagraphTask]:
    """降级解析：将大纲按换行分割"""
    lines = outline_text.strip().split('\n')
    paragraphs = []

    for i, line in enumerate(lines):
        line = line.strip()
        if line and len(line) > 10:
            paragraphs.append(ParagraphTask(
                id=i + 1,
                main_point=line[:100],  # 截取前100字作为主点
                word_count=500,
                rag_reference="",
            ))

    return paragraphs


# ============================================================================
# 字数推断
# ============================================================================

def estimate_target_length(
    source_article: Dict,
    target_platform: str,
    user_input: str = ""
) -> int:
    """智能推断目标字数。

    Args:
        source_article: 原文章数据
        target_platform: 目标平台（zhihu/wechat/baijia）
        user_input: 用户改写需求

    Returns:
        推断的目标字数
    """

    # 1. 从用户需求中提取字数提示
    if user_input:
        # 用户说"写3000字"、"控制在1500字内"等
        patterns = [
            r'控制在(\d+)字',
            r'(\d+)字左右',
            r'(\d+)字以内',
            r'写(\d+)字',
            r'简短.*(\d+)字',
        ]
        for pattern in patterns:
            match = re.search(pattern, user_input)
            if match:
                return int(match.group(1))

        # 用户说"简短版"、"短文" → 默认1500
        if any(word in user_input for word in ['简短', '短文', '精简', '简洁']):
            return 1500

        # 用户说"长文"、"详细" → 默认5000
        if any(word in user_input for word in ['长文', '详细', '深度', '全面']):
            return 5000

    # 2. 根据平台推荐字数
    platform_defaults = {
        "zhihu": 3000,      # 知乎适合长文
        "wechat": 2000,     # 公众号中篇
        "baijia": 1500,     # 百家号
        "xhs": 500,         # 小红书极短
        "other": 2000,      # 其他
    }
    base_length = platform_defaults.get(target_platform, 2000)

    # 3. 根据原文章长度调整
    source_text = source_article.get("text", "")
    if source_text:
        source_length = len(source_text)
        # 原文长 → 目标字数适当增加（最多增加50%）
        base_length = max(base_length, int(source_length * 1.2))
        # 原文极短 → 目标字数不低于800
        base_length = max(base_length, 800)

    logger.info(f"[WordEstimator] Estimated target length: {base_length} words (platform={target_platform})")
    return base_length


# ============================================================================
# 分段生成器
# ============================================================================

class ParagraphGenerator:
    """分段生成器"""

    def __init__(self):
        self.llm = LLMClient()

    async def generate_paragraph_outline(
        self,
        source_article: Dict,
        user_input: str,
        rag_context: str,
        target_platform: str = "zhihu",
    ) -> List[ParagraphTask]:
        """生成段落级大纲。

        Args:
            source_article: 原文章 {title, text, url}
            user_input: 用户改写需求
            rag_context: RAG知识库素材
            target_platform: 目标平台

        Returns:
            段落任务列表
        """
        logger.info("[ParagraphGen] Generating paragraph outline...")

        # 推断目标字数
        target_length = estimate_target_length(source_article, target_platform, user_input)

        # 根据字数决定段落数量
        if target_length < 1500:
            paragraph_count = 3  # 短文：3段
        elif target_length < 3000:
            paragraph_count = 5  # 中篇：5段
        else:
            paragraph_count = 8  # 长文：8段

        prompt = f"""请将文章写作任务分解为段落级任务：

## 原文章
标题：{source_article.get('title', '')}
内容摘要：{source_article.get('text', '')[:500]}

## 用户需求
{user_input}

## 知识库素材（自然融入文中）
{rag_context if rag_context else "无"}

## 分解要求
1. 总字数约{target_length}字，拆分为{paragraph_count}段左右
2. 每段200-1000字
3. 根据用户需求调整风格和侧重点
4. 每段指定知识库素材引用点（如有）
5. 保持逻辑连贯，段落间有过渡

请按以下格式输出（每行一段，共{paragraph_count}段左右）：
段落1 | 主点：[详细描述这一段要写什么] | 字数：[400] | 素材：[锐博集团招聘流程/无]
段落2 | 主点：[详细描述] | 字数：[800] | 素材：[公司发展历程/无]
...

直接输出段落列表，不要添加其他内容："""

        try:
            outline_text = await self.llm.chat_with_retry(prompt)
            paragraphs = parse_paragraph_outline(outline_text)

            logger.info(f"[ParagraphGen] Generated {len(paragraphs)} paragraphs, total ~{sum(p.word_count for p in paragraphs)} words")
            return paragraphs

        except Exception as e:
            logger.error(f"[ParagraphGen] Outline generation failed: {e}")
            raise

    async def generate_paragraph(
        self,
        paragraph: ParagraphTask,
        all_paragraphs: List[ParagraphTask],
        written_text: str,
        source_article: Dict,
        user_input: str,
    ) -> str:
        """生成单个段落。

        Args:
            paragraph: 当前段落任务
            all_paragraphs: 所有段落任务列表
            written_text: 已生成的内容
            source_article: 原文章
            user_input: 用户需求

        Returns:
            生成的段落内容
        """
        # 构建全部段落摘要
        all_summary = "\n".join([
            f"段落{p.id} | {p.main_point[:50]} | {p.word_count}字"
            for p in all_paragraphs
        ])

        prompt = f"""你是写作助手。根据写作指令和已写内容，继续写下一个段落。

## 原文章标题
{source_article.get('title', '')}

## 用户需求
{user_input}

## 全文段落规划
{all_summary}

## 已写内容
{written_text if written_text else "（这是第一段，之前无内容）"}

## 当前段落任务
段落{paragraph.id} | 主点：{paragraph.main_point} | 字数：{paragraph.word_count}
素材引用：{paragraph.rag_reference if paragraph.rag_reference else "无特定素材要求"}

## 要求
1. 只输出这一段的内容，不重复已写内容
2. 字数控制在{paragraph.word_count}字左右（±20%）
3. 如果有素材引用要求，自然融入{paragraph.rag_reference}
4. 与已写内容保持连贯，有过渡衔接
5. 这是连载文章，避免在段末使用总结性结尾

请输出段落{paragraph.id}："""

        try:
            content = await self.llm.chat_with_retry(prompt)
            # 去除可能的前缀（如"段落1："）
            content = re.sub(r'^段落\d+[：:]\s*', '', content.strip())
            return content

        except Exception as e:
            logger.error(f"[ParagraphGen] Paragraph {paragraph.id} generation failed: {e}")
            raise

    async def generate_full_article(
        self,
        paragraphs: List[ParagraphTask],
        source_article: Dict,
        user_input: str,
        rag_context: str = "",
        progress_callback: Optional[callable] = None,
    ) -> str:
        """逐段生成全文。

        Args:
            paragraphs: 段落任务列表
            source_article: 原文章
            user_input: 用户需求
            rag_context: RAG素材（可选）
            progress_callback: 进度回调函数

        Returns:
            完整文章内容
        """
        logger.info(f"[ParagraphGen] Starting full article generation: {len(paragraphs)} paragraphs")

        full_text = ""

        for i, paragraph in enumerate(paragraphs):
            logger.info(f"[ParagraphGen] Generating paragraph {paragraph.id}/{len(paragraphs)}...")

            content = await self.generate_paragraph(
                paragraph=paragraph,
                all_paragraphs=paragraphs,
                written_text=full_text,
                source_article=source_article,
                user_input=user_input,
            )

            full_text += content + "\n\n"

            # 进度回调
            if progress_callback:
                progress_callback(
                    current=i + 1,
                    total=len(paragraphs),
                    paragraph_id=paragraph.id,
                    content_preview=content[:100],
                )

            # 每3段输出进度日志
            if (i + 1) % 3 == 0:
                logger.info(f"[ParagraphGen] Progress: {i + 1}/{len(paragraphs)} paragraphs, ~{len(full_text)} chars")

        logger.info(f"[ParagraphGen] Full article generated: {len(full_text)} chars")
        return full_text.strip()


# ============================================================================
# 全局实例
# ============================================================================

_paragraph_generator = None


def get_paragraph_generator() -> ParagraphGenerator:
    """获取分段生成器实例"""
    global _paragraph_generator
    if _paragraph_generator is None:
        _paragraph_generator = ParagraphGenerator()
    return _paragraph_generator


# ============================================================================
# API函数
# ============================================================================

async def generate_article_with_paragraphs(
    source_article: Dict,
    user_input: str,
    rag_context: str = "",
    target_platform: str = "zhihu",
    progress_callback: Optional[callable] = None,
) -> Dict:
    """使用分段方式生成文章（API入口）。

    Args:
        source_article: 原文章数据
        user_input: 用户改写需求
        rag_context: RAG知识库素材
        target_platform: 目标平台
        progress_callback: 进度回调

    Returns:
        {
            "outline": 段落大纲（结构化列表）,
            "outline_text": 段落大纲（文本形式）,
            "draft": 生成的全文,
            "paragraph_count": 段落数量,
            "estimated_words": 预估字数,
        }
    """
    generator = get_paragraph_generator()

    # 1. 生成段落大纲
    paragraphs = await generator.generate_paragraph_outline(
        source_article=source_article,
        user_input=user_input,
        rag_context=rag_context,
        target_platform=target_platform,
    )

    # 2. 逐段生成全文
    draft = await generator.generate_full_article(
        paragraphs=paragraphs,
        source_article=source_article,
        user_input=user_input,
        rag_context=rag_context,
        progress_callback=progress_callback,
    )

    # 3. 返回结果
    outline_text = "\n".join([
        f"段落{p.id} | 主点：{p.main_point} | 字数：{p.word_count} | 素材：{p.rag_reference}"
        for p in paragraphs
    ])

    return {
        "outline": [p.to_dict() for p in paragraphs],
        "outline_text": outline_text,
        "draft": draft,
        "paragraph_count": len(paragraphs),
        "estimated_words": sum(p.word_count for p in paragraphs),
    }