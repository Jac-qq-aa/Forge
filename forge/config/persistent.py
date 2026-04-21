"""Persistent browser configuration for Playwright.

This module provides a browser context that persists cookies and login state
across sessions, avoiding repeated security verifications.
"""

import os
from pathlib import Path

# Persistent browser data directory
BROWSER_DATA_DIR = os.path.expanduser("~/.forge/browser_data")

# Ensure directory exists
Path(BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

# Platform-specific cookie files
XHS_COOKIES_FILE = os.path.join(BROWSER_DATA_DIR, "xhs_cookies.json")
ZHIHU_COOKIES_FILE = os.path.join(BROWSER_DATA_DIR, "zhihu_cookies.json")

# Output directories
VIDEO_OUTPUT_DIR = "/tmp/forge_videos"
IMAGE_OUTPUT_DIR = "/tmp/forge_images"

# Ensure output directories exist
os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)
os.makedirs(IMAGE_OUTPUT_DIR, exist_ok=True)