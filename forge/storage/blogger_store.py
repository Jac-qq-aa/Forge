"""博主关注存储模块 - 使用 JSON 文件存储关注的博主列表."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# 存储文件路径
BLOGGER_FILE = Path.home() / ".forge" / "followed_bloggers.json"

logger = logging.getLogger(__name__)

# 默认关注博主（与前端预设保持一致）。
# 服务器端推荐文章接口不能依赖浏览器 localStorage，否则“订阅博主”弹窗会为空。
DEFAULT_BLOGGERS = [
    {"id": "rui-bo-ji-tuan-5", "name": "锐博集团", "note": "锐博集团", "platform": "zhihu", "added_at": "preset"},
]


def _ensure_file() -> None:
    """确保存储文件存在."""
    BLOGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not BLOGGER_FILE.exists():
        with open(BLOGGER_FILE, "w", encoding="utf-8") as f:
            json.dump({"bloggers": []}, f, ensure_ascii=False)


def _normalize_blogger(blogger: Dict) -> Optional[Dict]:
    """兼容前端 localStorage(note) 和后端存储(name) 的博主结构."""
    if not blogger or not blogger.get("id"):
        return None
    blogger_id = str(blogger.get("id", "")).strip()
    if not blogger_id:
        return None
    name = blogger.get("name") or blogger.get("note") or blogger_id
    return {
        "id": blogger_id,
        "name": name,
        "note": blogger.get("note") or (name if name != blogger_id else ""),
        "platform": blogger.get("platform") or "zhihu",
        "added_at": blogger.get("added_at") or "",
    }


def load_bloggers(include_defaults: bool = True) -> List[Dict]:
    """加载关注博主列表。

    include_defaults=True 时，如果服务器端还没有保存任何博主，返回默认预设博主，
    避免推荐文章弹窗因为只依赖后端 JSON 而显示为空。
    """
    _ensure_file()
    try:
        with open(BLOGGER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            bloggers = []
            seen = set()
            for b in data.get("bloggers", []):
                nb = _normalize_blogger(b)
                if nb and nb["id"] not in seen:
                    bloggers.append(nb)
                    seen.add(nb["id"])

            if include_defaults and not bloggers:
                return [dict(b) for b in DEFAULT_BLOGGERS]
            return bloggers
    except Exception as e:
        logger.error(f"[BloggerStore] 加载博主列表失败: {e}")
        return [dict(b) for b in DEFAULT_BLOGGERS] if include_defaults else []


def save_bloggers(bloggers: List[Dict]) -> None:
    """保存关注博主列表."""
    _ensure_file()
    try:
        with open(BLOGGER_FILE, "w", encoding="utf-8") as f:
            json.dump({"bloggers": bloggers}, f, ensure_ascii=False, indent=2)
        logger.info(f"[BloggerStore] 保存了 {len(bloggers)} 个博主")
    except Exception as e:
        logger.error(f"[BloggerStore] 保存博主列表失败: {e}")
        raise


def add_blogger(blogger_id: str, name: str = "", platform: str = "zhihu") -> Dict:
    """添加关注博主.

    Args:
        blogger_id: 博主 ID（知乎用户 URL 中的 ID）
        name: 博主名称（可选）
        platform: 平台（默认 zhihu）

    Returns:
        添加的博主信息
    """
    bloggers = load_bloggers()

    # 检查是否已存在
    for b in bloggers:
        if b.get("id") == blogger_id:
            logger.warning(f"[BloggerStore] 博主已存在: {blogger_id}")
            return b

    # 添加新博主
    new_blogger = {
        "id": blogger_id,
        "name": name or blogger_id,
        "note": name or "",
        "platform": platform,
        "added_at": datetime.now().strftime("%Y-%m-%d"),
    }
    bloggers.append(new_blogger)
    save_bloggers(bloggers)
    logger.info(f"[BloggerStore] 添加博主: {blogger_id}")
    return new_blogger


def remove_blogger(blogger_id: str) -> bool:
    """取消关注博主.

    Args:
        blogger_id: 博主 ID

    Returns:
        是否成功删除
    """
    bloggers = load_bloggers()
    new_bloggers = [b for b in bloggers if b["id"] != blogger_id]

    if len(new_bloggers) == len(bloggers):
        logger.warning(f"[BloggerStore] 博主不存在: {blogger_id}")
        return False

    save_bloggers(new_bloggers)
    logger.info(f"[BloggerStore] 取消关注博主: {blogger_id}")
    return True


def get_blogger(blogger_id: str) -> Optional[Dict]:
    """获取单个博主信息."""
    bloggers = load_bloggers()
    for b in bloggers:
        if b["id"] == blogger_id:
            return b
    return None