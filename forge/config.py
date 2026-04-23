"""Configuration management for Forge workflow."""

import os
from dotenv import load_dotenv

load_dotenv()

# Qwen LLM Configuration
QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = "qwen-plus"  # 改写模型，可选: qwen-max, qwen-turbo

# Judge LLM Configuration (用于 AI 检测，使用不同模型实现隔离)
# 使用同一个 API Key，但指定更强的模型进行判断
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY", QWEN_API_KEY)  # 默认使用 Qwen API Key
JUDGE_API_URL = os.getenv("JUDGE_API_URL", QWEN_API_URL)  # 默认使用 Qwen API URL
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "qwen-max")  # 判断模型，默认用更强的 qwen-max

# LangSmith Configuration (工作流追踪和可视化)
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "true")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "Forge-Content-Workflow")

# HeyGen Configuration (数字人视频生成)
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")

# Platform URLs
XHS_BASE_URL = "https://www.xiaohongshu.com"
ZHIHU_BASE_URL = "https://www.zhihu.com"

# Output paths
VIDEO_OUTPUT_DIR = "/tmp/forge_videos"
IMAGE_OUTPUT_DIR = "/tmp/forge_images"
COOKIES_FILE = "/tmp/forge_cookies/xhs_cookies.json"  # Persist login state

# Control parameters
MAX_REVISIONS = 3
MAX_HUMANIZE_REVISIONS = 3  # 去AI化最大迭代次数
AI_THRESHOLD = 0.7  # AI率阈值，超过此值需要人性化改写
LLM_TIMEOUT = 60.0
PLAYWRIGHT_TIMEOUT = 30000  # ms

# Supported target platforms
TARGET_PLATFORMS = ["xhs_video", "zhihu_article", "zhihu_video"]

# ========== 深度生成模式配置 ==========

# Session 管理
DEEP_MODE_SESSION_TTL = int(os.getenv("DEEP_MODE_SESSION_TTL", "86400"))  # 24小时
OUTLINE_MAX_REVISIONS = int(os.getenv("OUTLINE_MAX_REVISIONS", "3"))      # 大纲最多修改 3 次
AGENT_EXECUTION_TIMEOUT = int(os.getenv("AGENT_EXECUTION_TIMEOUT", "60"))  # Agent 超时 60s

# 目标平台选项
TARGET_PLATFORM_OPTIONS = ["zhihu_article", "xhs_video", "wechat_article"]

# ============================================================================
# Redis Configuration
# ============================================================================

REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

# Session TTL (30 minutes)
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))

# ============================================================================
# PostgreSQL Configuration
# ============================================================================

PG_HOST: str = os.getenv("PG_HOST", "localhost")
PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
PG_USER: str = os.getenv("PG_USER", "forge")
PG_PASSWORD: str = os.getenv("PG_PASSWORD", "forge123")
PG_DATABASE: str = os.getenv("PG_DATABASE", "forge")