"""外部平台采集执行池。"""

from .zhihu_executor import execute_zhihu_job
from .zhihu_pool import ZhihuScraperPool

__all__ = ["ZhihuScraperPool", "execute_zhihu_job"]
