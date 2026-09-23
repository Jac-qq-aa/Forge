"""知乎采集池配置。"""

import os

from dotenv import load_dotenv

load_dotenv()


ZHIHU_BROWSER_POOL_SIZE = int(os.getenv("ZHIHU_BROWSER_POOL_SIZE", "4"))
ZHIHU_GLOBAL_REQUESTS_PER_SECOND = float(
    os.getenv("ZHIHU_GLOBAL_REQUESTS_PER_SECOND", "0.5")
)
ZHIHU_GLOBAL_BURST = int(os.getenv("ZHIHU_GLOBAL_BURST", "2"))
ZHIHU_RESULT_CACHE_SECONDS = int(os.getenv("ZHIHU_RESULT_CACHE_SECONDS", "300"))
ZHIHU_REQUEST_DEDUP_SECONDS = int(os.getenv("ZHIHU_REQUEST_DEDUP_SECONDS", "30"))
ZHIHU_PROFILE_ROOT = os.path.expanduser(
    os.getenv("ZHIHU_PROFILE_ROOT", "~/.forge/browser_data/zhihu-pool")
)
