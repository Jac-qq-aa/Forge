"""中文文本分割器 - 将长文章分割为适合向量数据库存储的片段。

分割策略：
1. 按语义边界优先分割（段落 > 句子 > 逗号）
2. 保留上下文重叠，确保语义连贯
3. 支持上下文前缀附加，保持文章标题关联
"""

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ChineseTextSplitter:
    """中文文本分割器，针对公众号/知乎文章优化。

    分割优先级：段落 > 句子 > 逗号 > 强制切分
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        min_chunk_size: int = 100,
        preserve_context: bool = True,
    ):
        """
        Args:
            chunk_size: 单片段最大字符数（默认500，小于2000限制）
            chunk_overlap: 重叠字符数，保留上下文（默认50）
            min_chunk_size: 最小片段大小（默认100）
            preserve_context: 是否保留上下文前缀（默认True）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size
        self.preserve_context = preserve_context

        # 中文分割分隔符优先级（从大到小）
        self.separators = [
            "\n\n",      # 段落边界
            "\n",        # 行边界
            "。",        # 句号
            "！",        # 感叹号
            "？",        # 问号
            "；",        # 分号
            "，",        # 逗号
            " ",         # 空格
            ""           # 强制切分（最后手段）
        ]

    def split_text(
        self,
        text: str,
        metadata: Optional[dict] = None
    ) -> List[dict]:
        """
        分割文本并返回带元数据的片段。

        Args:
            text: 待分割的文本内容
            metadata: 文章元数据（包含title等）

        Returns:
            List of chunks with structure:
            {
                "content": str,       # 片段内容（可能带前缀）
                "chunk_index": int,   # 片段索引
                "original_length": int, # 原始长度
            }
        """
        if not text or len(text.strip()) == 0:
            logger.warning("[TextSplitter] Empty text, returning empty list")
            return []

        # 清理文本
        clean_text = self._clean_text(text)

        # 如果文本很短，不需要分割
        if len(clean_text) <= self.chunk_size:
            return [{
                "content": self._add_context_prefix(clean_text, metadata),
                "chunk_index": 0,
                "original_length": len(clean_text),
            }]

        # 递归分割
        chunks = self._recursive_split(clean_text, self.separators)

        # 合并过短的片段
        chunks = self._merge_short_chunks(chunks)

        # 添加重叠
        chunks = self._add_overlap(chunks)

        # 构建结果
        result = []
        for i, chunk in enumerate(chunks):
            # 截断过长的片段
            if len(chunk) > self.chunk_size:
                chunk = chunk[:self.chunk_size]

            result.append({
                "content": self._add_context_prefix(chunk, metadata),
                "chunk_index": i,
                "original_length": len(chunk),
            })

        logger.info(f"[TextSplitter] Split into {len(result)} chunks from {len(clean_text)} chars")
        return result

    def _clean_text(self, text: str) -> str:
        """清理文本，移除多余空白和特殊字符。"""
        # 移除连续空白
        text = re.sub(r'\s+', ' ', text)
        # 移除连续换行（保留段落边界）
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 移除前后空白
        text = text.strip()
        return text

    def _recursive_split(
        self,
        text: str,
        separators: List[str]
    ) -> List[str]:
        """递归分割文本，按分隔符优先级尝试。"""
        final_chunks = []

        # 如果文本足够短，直接返回
        if len(text) <= self.chunk_size:
            return [text]

        # 尝试按当前分隔符分割
        for separator in separators:
            if separator == "":
                # 强制切分
                chunks = self._force_split(text)
                final_chunks.extend(chunks)
                break

            if separator in text:
                # 按分隔符分割
                splits = text.split(separator)

                # 尝试合并分割后的片段
                merged_chunks = self._merge_splits(splits, separator)

                # 检查是否有片段仍然过长
                for chunk in merged_chunks:
                    if len(chunk) > self.chunk_size:
                        # 对过长片段递归使用更细粒度的分隔符
                        remaining_separators = separators[separators.index(separator) + 1:]
                        sub_chunks = self._recursive_split(chunk, remaining_separators)
                        final_chunks.extend(sub_chunks)
                    else:
                        final_chunks.append(chunk)

                break  # 找到有效分隔符后退出循环

        return final_chunks

    def _merge_splits(
        self,
        splits: List[str],
        separator: str
    ) -> List[str]:
        """合并分割后的片段，确保每个片段大小合适。"""
        merged = []
        current_chunk = ""

        for split in splits:
            # 添加分隔符（保留语义边界）
            candidate = current_chunk + separator + split if current_chunk else split

            if len(candidate) <= self.chunk_size:
                current_chunk = candidate
            else:
                # 当前片段已达到大小限制
                if current_chunk:
                    merged.append(current_chunk)
                current_chunk = split

        # 添加最后一个片段
        if current_chunk:
            merged.append(current_chunk)

        return merged

    def _force_split(self, text: str) -> List[str]:
        """强制按字符数切分（最后手段）。"""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i:i + self.chunk_size]
            chunks.append(chunk)
        return chunks

    def _merge_short_chunks(self, chunks: List[str]) -> List[str]:
        """合并过短的片段，避免碎片化。"""
        if not chunks:
            return chunks

        merged = []
        current = ""

        for chunk in chunks:
            if len(current) + len(chunk) < self.min_chunk_size:
                # 合并过短片段
                current = current + " " + chunk if current else chunk
            else:
                if current:
                    merged.append(current)
                current = chunk

        # 处理最后一个片段
        if current:
            if len(current) < self.min_chunk_size and merged:
                # 与前一个片段合并
                merged[-1] = merged[-1] + " " + current
            else:
                merged.append(current)

        return merged

    def _add_overlap(self, chunks: List[str]) -> List[str]:
        """添加重叠以保持上下文连贯。"""
        if len(chunks) <= 1 or self.chunk_overlap == 0:
            return chunks

        overlapped = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                # 从前一个片段末尾取重叠部分
                prev_chunk = chunks[i - 1]
                overlap_start = max(0, len(prev_chunk) - self.chunk_overlap)
                overlap_text = prev_chunk[overlap_start:]

                # 添加重叠到当前片段
                chunk = overlap_text + chunk

            # 截断防止过长
            if len(chunk) > self.chunk_size + self.chunk_overlap:
                chunk = chunk[:self.chunk_size + self.chunk_overlap]

            overlapped.append(chunk)

        return overlapped

    def _add_context_prefix(
        self,
        content: str,
        metadata: Optional[dict]
    ) -> str:
        """添加上下文前缀（文章标题）。"""
        if not self.preserve_context or not metadata:
            return content

        title = metadata.get('title', '')
        if not title:
            return content

        # 格式：[文章标题] {片段内容}
        prefix = f"[{title}] "
        prefixed_content = prefix + content

        # 确保不超过限制
        if len(prefixed_content) > 2000:  # Milvus content字段限制
            # 截断内容部分
            max_content_len = 2000 - len(prefix)
            prefixed_content = prefix + content[:max_content_len]

        return prefixed_content


__all__ = ["ChineseTextSplitter"]