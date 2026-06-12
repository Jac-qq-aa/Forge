"""段落生成缓存 - 避免重复调用API。

参考：LongWriter agentwrite/write.py 的 cache_dict 机制
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 默认缓存目录
CACHE_DIR = Path.home() / ".forge" / "paragraph_cache"
CACHE_FILE = CACHE_DIR / "paragraph_cache.jsonl"


class ParagraphCache:
    """段落生成缓存管理器"""

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or CACHE_FILE
        self.cache: Dict[str, Dict[str, str]] = {}
        self._ensure_cache_dir()
        self._load_cache()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if not self.cache_file.parent.exists():
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"[ParagraphCache] Created cache directory: {self.cache_file.parent}")

    def _load_cache(self):
        """加载现有缓存"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            item = json.loads(line.strip())
                            session_id = item.get("session_id", "")
                            paragraph_id = item.get("paragraph_id", "")
                            content = item.get("content", "")

                            if session_id and paragraph_id:
                                if session_id not in self.cache:
                                    self.cache[session_id] = {}
                                self.cache[session_id][paragraph_id] = content
                        except json.JSONDecodeError:
                            continue

                logger.info(f"[ParagraphCache] Loaded {len(self.cache)} sessions from cache")
            except Exception as e:
                logger.warning(f"[ParagraphCache] Failed to load cache: {e}")

    def get(self, session_id: str, paragraph_id: str) -> Optional[str]:
        """获取已生成的段落内容。

        Args:
            session_id: 会话ID
            paragraph_id: 段落ID（如 "1", "2"）

        Returns:
            缓存的段落内容，如果不存在返回 None
        """
        return self.cache.get(session_id, {}).get(paragraph_id)

    def set(self, session_id: str, paragraph_id: str, content: str):
        """缓存段落生成结果。

        Args:
            session_id: 会话ID
            paragraph_id: 段落ID
            content: 段落内容
        """
        if session_id not in self.cache:
            self.cache[session_id] = {}
        self.cache[session_id][paragraph_id] = content

        # 写入文件
        try:
            with open(self.cache_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "session_id": session_id,
                    "paragraph_id": paragraph_id,
                    "content": content,
                    "timestamp": datetime.now().isoformat(),
                }, ensure_ascii=False) + '\n')

            logger.info(f"[ParagraphCache] Cached paragraph {paragraph_id} for session {session_id}")
        except Exception as e:
            logger.warning(f"[ParagraphCache] Failed to write cache: {e}")

    def get_session_cache(self, session_id: str) -> Dict[str, str]:
        """获取整个会话的缓存内容。

        Args:
            session_id: 会话ID

        Returns:
            {paragraph_id: content} 字典
        """
        return self.cache.get(session_id, {})

    def clear_session(self, session_id: str):
        """清除指定会话的缓存。

        Args:
            session_id: 会话ID
        """
        if session_id in self.cache:
            del self.cache[session_id]
            logger.info(f"[ParagraphCache] Cleared cache for session {session_id}")

    def clear_all(self):
        """清除所有缓存"""
        self.cache.clear()
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
                logger.info("[ParagraphCache] Cleared all cache")
            except Exception as e:
                logger.warning(f"[ParagraphCache] Failed to delete cache file: {e}")

    def get_stats(self) -> Dict:
        """获取缓存统计信息"""
        total_paragraphs = sum(len(session) for session in self.cache.values())
        return {
            "sessions": len(self.cache),
            "total_paragraphs": total_paragraphs,
            "cache_file": str(self.cache_file),
        }


# ============================================================================
# 全局实例
# ============================================================================

_paragraph_cache = None


def get_paragraph_cache() -> ParagraphCache:
    """获取缓存实例"""
    global _paragraph_cache
    if _paragraph_cache is None:
        _paragraph_cache = ParagraphCache()
    return _paragraph_cache