"""Configuration management for Forge workflow."""

import os
from dotenv import load_dotenv

load_dotenv()

# Qwen LLM Configuration
QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = "qwen2.5-plus"

# Platform URLs
XHS_BASE_URL = "https://www.xiaohongshu.com"
ZHIHU_BASE_URL = "https://www.zhihu.com"

# Output paths
VIDEO_OUTPUT_DIR = "/tmp/forge_videos"
IMAGE_OUTPUT_DIR = "/tmp/forge_images"

# Control parameters
MAX_REVISIONS = 3
LLM_TIMEOUT = 60.0
PLAYWRIGHT_TIMEOUT = 30000  # ms

# Supported target platforms
TARGET_PLATFORMS = ["xhs_video", "zhihu_article", "zhihu_video"]