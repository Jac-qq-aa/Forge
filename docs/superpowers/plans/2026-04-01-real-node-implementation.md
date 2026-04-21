# Real Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace placeholder nodes with real implementations for Xiaohongshu/Zhihu content automation pipeline.

**Architecture:** Playwright for scraping/publishing, Qwen3.5-plus via OpenAI-compatible API for LLM, Edge TTS + FFmpeg for video generation, dual-platform support (xhs/zhihu).

**Tech Stack:** Playwright, edge-tts, FFmpeg, OpenAI SDK (for Qwen API), aiohttp, aiofiles

---

## File Structure

| File | Purpose |
|------|---------|
| `forge/config.py` | Centralized configuration (API keys, URLs, constants) |
| `forge/tools/llm_client.py` | Async Qwen LLM client with retry logic |
| `forge/tools/xhs_scraper.py` | Xiaohongshu Playwright scraper |
| `forge/tools/zhihu_scraper.py` | Zhihu Playwright scraper |
| `forge/tools/tts_generator.py` | Edge TTS wrapper |
| `forge/tools/video_composer.py` | FFmpeg video composition |
| `forge/tools/xhs_publisher.py` | Xiaohongshu video publisher |
| `forge/tools/zhihu_publisher.py` | Zhihu article/video publisher |
| `forge/agents/scout.py` | Dual-platform scout node (refactored from nodes.py) |
| `forge/agents/editor.py` | Editor node with LLM integration |
| `forge/agents/reviewer.py` | Reviewer node with LLM integration |
| `forge/agents/director.py` | Director node with TTS + FFmpeg |
| `forge/agents/publisher.py` | Dual-platform publisher node |
| `forge/graph/state.py` | Updated state with source_platform, target_platform |
| `forge/graph/workflow.py` | Updated routing for zhihu_article (skip director) |

---

### Task 1: Configuration Module

**Files:**
- Create: `forge/config.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write config.py**

```python
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
```

- [ ] **Step 2: Update requirements.txt**

Add to existing file:
```
# New dependencies for real implementation
playwright>=1.40.0
edge-tts>=6.1.0
openai>=1.0.0
python-dotenv>=1.0.0
aiofiles>=23.0.0
aiohttp>=3.9.0
```

- [ ] **Step 3: Commit**

```bash
git add forge/config.py requirements.txt
git commit -m "feat: add configuration module and dependencies"
```

---

### Task 2: Update Graph State

**Files:**
- Modify: `forge/graph/state.py`

- [ ] **Step 1: Update GraphState TypedDict**

Replace the entire `GraphState` class definition:

```python
class GraphState(TypedDict, total=False):
    """State dictionary that flows through the LangGraph workflow.

    Attributes:
        topic: Initial input topic or URL.
        source_platform: Source platform: "xhs" or "zhihu" (auto-detected or specified).
        target_platform: Target platform: "xhs_video", "zhihu_article", "zhihu_video".
        raw_content: Raw content scraped from platform (title, text, images, likes, etc).
        rewritten_draft: AI-rewritten content draft.
        reflection_feedback: Feedback from reviewer node for revision.
        final_script: Final approved video script and narration.
        video_path: Local file path of generated video.
        publish_status: Publication result (success/failure reason).
        revision_count: Number of rewrite iterations (prevents infinite loops).
    """

    # Input
    topic: str
    source_platform: str
    target_platform: str

    # Scout node output
    raw_content: dict

    # Editor node output
    rewritten_draft: str

    # Reviewer node output
    reflection_feedback: str
    final_script: str

    # Director node output
    video_path: str

    # Publisher node output
    publish_status: str

    # Control flow
    revision_count: int
```

- [ ] **Step 2: Update create_initial_state**

Replace the function:

```python
def create_initial_state(topic: str, target_platform: str = "xhs_video") -> GraphState:
    """Create an initial state with topic and target platform.

    Args:
        topic: The input topic or URL to process.
        target_platform: Target publishing platform.

    Returns:
        Initial GraphState with topic, target_platform, and revision_count=0.
    """
    return GraphState(
        topic=topic,
        target_platform=target_platform,
        revision_count=0,
    )
```

- [ ] **Step 3: Commit**

```bash
git add forge/graph/state.py
git commit -m "feat: add source_platform and target_platform to GraphState"
```

---

### Task 3: LLM Client Tool

**Files:**
- Create: `forge/tools/llm_client.py`
- Modify: `forge/tools/__init__.py`

- [ ] **Step 1: Write llm_client.py**

```python
"""Async LLM client for Qwen API via OpenAI-compatible interface."""

import asyncio
import logging
from openai import AsyncOpenAI

from forge.config import QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)


class LLMClient:
    """Async client for Qwen LLM with retry logic."""

    def __init__(self):
        if not QWEN_API_KEY:
            raise ValueError("QWEN_API_KEY not set in environment")
        self.client = AsyncOpenAI(
            base_url=QWEN_API_URL,
            api_key=QWEN_API_KEY,
            timeout=LLM_TIMEOUT,
        )

    async def chat(self, prompt: str, system_prompt: str = None) -> str:
        """Send a chat request to Qwen.

        Args:
            prompt: User message.
            system_prompt: Optional system message.

        Returns:
            LLM response text.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info(f"[LLM] Sending request with {len(messages)} messages")

        response = await self.client.chat.completions.create(
            model=QWEN_MODEL,
            messages=messages,
        )
        content = response.choices[0].message.content
        logger.info(f"[LLM] Response received: {len(content)} chars")
        return content

    async def chat_with_retry(self, prompt: str, system_prompt: str = None, max_retries: int = 3) -> str:
        """Send chat request with exponential backoff retry.

        Args:
            prompt: User message.
            system_prompt: Optional system message.
            max_retries: Maximum retry attempts.

        Returns:
            LLM response text, or error message on failure.
        """
        for attempt in range(max_retries):
            try:
                return await self.chat(prompt, system_prompt)
            except Exception as e:
                logger.warning(f"[LLM] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"[LLM] All retries exhausted")
                    return f"LLM调用失败: {e}"
                await asyncio.sleep(2 ** attempt)
```

- [ ] **Step 2: Update tools/__init__.py**

```python
"""Tools and utilities for the Forge workflow."""

from .llm_client import LLMClient

__all__ = ["LLMClient"]
```

- [ ] **Step 3: Commit**

```bash
git add forge/tools/llm_client.py forge/tools/__init__.py
git commit -m "feat: add async LLM client with retry logic"
```

---

### Task 4: Xiaohongshu Scraper

**Files:**
- Create: `forge/tools/xhs_scraper.py`

- [ ] **Step 1: Write xhs_scraper.py**

```python
"""Xiaohongshu scraper using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class XhsScraper:
    """Async Xiaohongshu scraper with browser automation."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[XhsScraper] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsScraper] Closing browser")
        await self.browser.close()
        await self.playwright.stop()

    async def scrape_post(self, url: str) -> dict:
        """Scrape a specific Xiaohongshu post by URL.

        Args:
            url: Full URL to the post.

        Returns:
            dict with title, text, images, likes, comments, source_url.
        """
        logger.info(f"[XhsScraper] Scraping post: {url}")
        await self.page.goto(url)

        # Wait for content to load
        await self.page.wait_for_selector("#detail-desc", timeout=PLAYWRIGHT_TIMEOUT)

        # Extract content - selectors may need adjustment based on actual page
        try:
            title = await self.page.locator("#detail-title").text_content() or ""
        except:
            title = ""

        try:
            text = await self.page.locator("#detail-desc").text_content() or ""
        except:
            text = ""

        try:
            images = await self.page.locator(".swiper-slide img").evaluate_all(
                "imgs => imgs.map(i => i.src).filter(s => s)"
            )
        except:
            images = []

        try:
            likes_text = await self.page.locator(".like-wrapper .count").text_content() or "0"
            likes = int(likes_text.replace("+", "").replace("万", "0000"))
        except:
            likes = 0

        result = {
            "title": title.strip(),
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }
        logger.info(f"[XhsScraper] Scraped: title='{title[:30]}...', images={len(images)}")
        return result

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search and scrape a post by topic keyword.

        Args:
            topic: Search keyword.

        Returns:
            Scraped content from first search result.
        """
        logger.info(f"[XhsScraper] Searching for topic: {topic}")
        search_url = f"{XHS_BASE_URL}/search?keyword={topic}"
        await self.page.goto(search_url)

        # Click first result
        await self.page.wait_for_selector(".search-result", timeout=PLAYWRIGHT_TIMEOUT)
        await self.page.locator(".search-result").first.click()

        # Wait for navigation
        await self.page.wait_for_load_state("networkidle")

        return await self.scrape_post(self.page.url)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/xhs_scraper.py
git commit -m "feat: add Xiaohongshu Playwright scraper"
```

---

### Task 5: Zhihu Scraper

**Files:**
- Create: `forge/tools/zhihu_scraper.py`

- [ ] **Step 1: Write zhihu_scraper.py**

```python
"""Zhihu scraper using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import ZHIHU_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class ZhihuScraper:
    """Async Zhihu scraper with browser automation."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[ZhihuScraper] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[ZhihuScraper] Closing browser")
        await self.browser.close()
        await self.playwright.stop()

    async def scrape_article(self, url: str) -> dict:
        """Scrape a Zhihu article by URL.

        Args:
            url: Full URL to the article.

        Returns:
            dict with title, text, images, likes, comments, source_url.
        """
        logger.info(f"[ZhihuScraper] Scraping article: {url}")
        await self.page.goto(url)

        await self.page.wait_for_selector(".Post-RichText", timeout=PLAYWRIGHT_TIMEOUT)

        try:
            title = await self.page.locator(".Post-Title").text_content() or ""
        except:
            title = ""

        try:
            text = await self.page.locator(".Post-RichText").text_content() or ""
        except:
            text = ""

        try:
            images = await self.page.locator(".Post-RichText img").evaluate_all(
                "imgs => imgs.map(i => i.src).filter(s => s)"
            )
        except:
            images = []

        try:
            likes_text = await self.page.locator(".VoteButton--up").text_content() or "0"
            likes = int(likes_text.replace("赞同", "").strip())
        except:
            likes = 0

        result = {
            "title": title.strip(),
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped: title='{title[:30]}...'")
        return result

    async def scrape_question(self, url: str) -> dict:
        """Scrape a Zhihu question page (get top answer).

        Args:
            url: Full URL to the question.

        Returns:
            dict with title, text (from top answer), images, likes, source_url.
        """
        logger.info(f"[ZhihuScraper] Scraping question: {url}")
        await self.page.goto(url)

        await self.page.wait_for_selector(".List-item", timeout=PLAYWRIGHT_TIMEOUT)

        try:
            title = await self.page.locator(".QuestionHeader-title").text_content() or ""
        except:
            title = ""

        # Get first (top) answer
        top_answer = self.page.locator(".List-item").first
        try:
            text = await top_answer.locator(".RichContent-inner").text_content() or ""
        except:
            text = ""

        try:
            images = await top_answer.locator(".RichContent-inner img").evaluate_all(
                "imgs => imgs.map(i => i.src).filter(s => s)"
            )
        except:
            images = []

        try:
            likes_text = await top_answer.locator(".VoteButton--up").text_content() or "0"
            likes = int(likes_text.replace("赞同", "").strip())
        except:
            likes = 0

        result = {
            "title": title.strip(),
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped question: title='{title[:30]}...'")
        return result

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search Zhihu and scrape first result.

        Args:
            topic: Search keyword.

        Returns:
            Scraped content.
        """
        logger.info(f"[ZhihuScraper] Searching for topic: {topic}")
        search_url = f"{ZHIHU_BASE_URL}/search?type=content&q={topic}"
        await self.page.goto(search_url)

        await self.page.wait_for_selector(".ContentItem", timeout=PLAYWRIGHT_TIMEOUT)
        await self.page.locator(".ContentItem").first.click()
        await self.page.wait_for_load_state("networkidle")

        return await self.scrape_article(self.page.url)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/zhihu_scraper.py
git commit -m "feat: add Zhihu Playwright scraper"
```

---

### Task 6: TTS Generator

**Files:**
- Create: `forge/tools/tts_generator.py`

- [ ] **Step 1: Write tts_generator.py**

```python
"""Edge TTS wrapper for text-to-speech generation."""

import logging
import edge_tts

logger = logging.getLogger(__name__)


class TtsGenerator:
    """Async TTS generator using Microsoft Edge TTS."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def generate(self, text: str, output_path: str) -> str:
        """Generate audio from text.

        Args:
            text: Text to convert to speech.
            output_path: Path to save MP3 file.

        Returns:
            Path to generated audio file.
        """
        logger.info(f"[TTS] Generating audio for {len(text)} chars")
        logger.info(f"[TTS] Voice: {self.voice}")

        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_path)

        logger.info(f"[TTS] Audio saved to: {output_path}")
        return output_path
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/tts_generator.py
git commit -m "feat: add Edge TTS generator wrapper"
```

---

### Task 7: Video Composer

**Files:**
- Create: `forge/tools/video_composer.py`

- [ ] **Step 1: Write video_composer.py**

```python
"""FFmpeg video composer combining images and audio."""

import asyncio
import logging
import os
import uuid
import aiofiles
import aiohttp

logger = logging.getLogger(__name__)


class VideoComposer:
    """Async video composer using FFmpeg."""

    async def compose(self, audio_path: str, images: list[str], output_path: str) -> str:
        """Compose video from audio and images.

        Args:
            audio_path: Path to MP3 audio file.
            images: List of image URLs.
            output_path: Path to save MP4 video.

        Returns:
            Path to generated video file.
        """
        logger.info(f"[VideoComposer] Starting composition")
        logger.info(f"[VideoComposer] Audio: {audio_path}")
        logger.info(f"[VideoComposer] Images: {len(images)}")

        # Create temp directory for images
        image_dir = f"/tmp/forge_images_{uuid.uuid4().hex[:8]}"
        os.makedirs(image_dir, exist_ok=True)

        # Download images
        local_images = await self._download_images(images, image_dir)
        logger.info(f"[VideoComposer] Downloaded {len(local_images)} images")

        if not local_images:
            logger.warning("[VideoComposer] No images available, creating video with audio only")
            # Create video from audio only (single frame)
            cmd = [
                "ffmpeg", "-y",
                "-i", audio_path,
                "-vf", "color=c=black:s=1920x1080:d=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-shortest",
                output_path
            ]
        else:
            # Create concat file for image sequence
            concat_file = f"{image_dir}/concat.txt"
            async with aiofiles.open(concat_file, "w") as f:
                for img in local_images:
                    await f.write(f"file '{img}'\nduration 3\n")
                # FFmpeg requires last image without duration
                await f.write(f"file '{local_images[-1]}'\n")

            # Run FFmpeg
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_file,
                "-i", audio_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-shortest",
                output_path
            ]

        logger.info(f"[VideoComposer] Running FFmpeg")
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.wait()

        if proc.returncode == 0:
            logger.info(f"[VideoComposer] Video saved to: {output_path}")
        else:
            logger.error(f"[VideoComposer] FFmpeg failed with code {proc.returncode}")

        return output_path

    async def _download_images(self, urls: list[str], dir: str) -> list[str]:
        """Download images from URLs to local directory.

        Args:
            urls: List of image URLs.
            dir: Local directory to save images.

        Returns:
            List of local file paths.
        """
        local_paths = []

        async with aiohttp.ClientSession() as session:
            for i, url in enumerate(urls[:10]):  # Limit to 10 images
                if not url.startswith("http"):
                    continue
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            ext = url.split(".")[-1][:4] or "jpg"
                            path = f"{dir}/image_{i}.{ext}"
                            content = await resp.read()
                            async with aiofiles.open(path, "wb") as f:
                                await f.write(content)
                            local_paths.append(path)
                            logger.debug(f"[VideoComposer] Downloaded image {i}")
                except Exception as e:
                    logger.warning(f"[VideoComposer] Failed to download image {i}: {e}")

        return local_paths
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/video_composer.py
git commit -m "feat: add FFmpeg video composer with image download"
```

---

### Task 8: Xiaohongshu Publisher

**Files:**
- Create: `forge/tools/xhs_publisher.py`

- [ ] **Step 1: Write xhs_publisher.py**

```python
"""Xiaohongshu video publisher using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class XhsPublisher:
    """Async Xiaohongshu video publisher."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[XhsPublisher] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsPublisher] Closing browser")
        await self.browser.close()
        await self.playwright.stop()

    async def login(self) -> bool:
        """Wait for user to login via QR code.

        Returns:
            True if login successful.
        """
        logger.info("[XhsPublisher] Opening login page")
        await self.page.goto(f"{XHS_BASE_URL}/login")

        # Wait for redirect to home page after login
        try:
            await self.page.wait_for_url("**/home**", timeout=120000)
            logger.info("[XhsPublisher] Login successful")
            return True
        except:
            logger.warning("[XhsPublisher] Login timeout")
            return False

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """Publish video to Xiaohongshu.

        Args:
            video_path: Path to video file.
            title: Video title.
            description: Video description.

        Returns:
            dict with success status and post URL.
        """
        logger.info(f"[XhsPublisher] Publishing video: {video_path}")

        await self.page.goto(f"{XHS_BASE_URL}/creator/publish")

        # Upload video file
        await self.page.locator("input[type='file']").set_input_files(video_path)
        await self.page.wait_for_load_state("networkidle")

        # Fill in title and description - selectors may need adjustment
        try:
            await self.page.locator(".title-input, [placeholder*='标题']").fill(title[:50])
        except Exception as e:
            logger.warning(f"[XhsPublisher] Could not fill title: {e}")

        try:
            await self.page.locator(".desc-input, [placeholder*='描述']").fill(description)
        except Exception as e:
            logger.warning(f"[XhsPublisher] Could not fill description: {e}")

        # Click publish button
        try:
            await self.page.locator(".publish-btn, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[XhsPublisher] Publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[XhsPublisher] Published: {self.page.url}")
        return result
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/xhs_publisher.py
git commit -m "feat: add Xiaohongshu video publisher"
```

---

### Task 9: Zhihu Publisher

**Files:**
- Create: `forge/tools/zhihu_publisher.py`

- [ ] **Step 1: Write zhihu_publisher.py**

```python
"""Zhihu article/video publisher using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import ZHIHU_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class ZhihuPublisher:
    """Async Zhihu publisher for articles and videos."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[ZhihuPublisher] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[ZhihuPublisher] Closing browser")
        await self.browser.close()
        await self.playwright.stop()

    async def login(self) -> bool:
        """Wait for user to login.

        Returns:
            True if login successful.
        """
        logger.info("[ZhihuPublisher] Opening login page")
        await self.page.goto(f"{ZHIHU_BASE_URL}/signin")

        try:
            # Wait for main page after login
            await self.page.wait_for_url("https://www.zhihu.com/", timeout=120000)
            logger.info("[ZhihuPublisher] Login successful")
            return True
        except:
            logger.warning("[ZhihuPublisher] Login timeout")
            return False

    async def publish_article(self, title: str, content: str) -> dict:
        """Publish article to Zhihu.

        Args:
            title: Article title.
            content: Article content.

        Returns:
            dict with success status and post URL.
        """
        logger.info(f"[ZhihuPublisher] Publishing article: {title[:30]}")

        await self.page.goto(f"{ZHIHU_BASE_URL}/write")

        try:
            await self.page.locator(".WriteIndex-titleInput, input[placeholder*='标题']").fill(title)
            await self.page.locator(".WriteIndex-content, .editor").fill(content)
            await self.page.locator(".WriteIndex-submitBtn, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[ZhihuPublisher] Article publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[ZhihuPublisher] Article published: {self.page.url}")
        return result

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """Publish video to Zhihu.

        Args:
            video_path: Path to video file.
            title: Video title.
            description: Video description.

        Returns:
            dict with success status and post URL.
        """
        logger.info(f"[ZhihuPublisher] Publishing video: {video_path}")

        await self.page.goto(f"{ZHIHU_BASE_URL}/creator/publish/video")

        try:
            await self.page.locator("input[type='file']").set_input_files(video_path)
            await self.page.wait_for_load_state("networkidle")

            await self.page.locator(".VideoUpload-title, input[placeholder*='标题']").fill(title)
            await self.page.locator(".VideoUpload-desc, textarea").fill(description)
            await self.page.locator(".VideoUpload-submit, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[ZhihuPublisher] Video publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[ZhihuPublisher] Video published: {self.page.url}")
        return result
```

- [ ] **Step 2: Commit**

```bash
git add forge/tools/zhihu_publisher.py
git commit -m "feat: add Zhihu article/video publisher"
```

---

### Task 10: Scout Node (Dual-Platform)

**Files:**
- Create: `forge/agents/scout.py`

- [ ] **Step 1: Write scout.py**

```python
"""Scout node - dual-platform content scraper."""

import logging
from forge.graph.state import GraphState
from forge.tools.xhs_scraper import XhsScraper
from forge.tools.zhihu_scraper import ZhihuScraper

logger = logging.getLogger(__name__)


def detect_platform(url: str) -> str:
    """Auto-detect platform from URL.

    Args:
        url: URL to analyze.

    Returns:
        Platform identifier: "xhs", "zhihu", or empty string.
    """
    if "xiaohongshu.com" in url:
        return "xhs"
    elif "zhihu.com" in url:
        return "zhihu"
    return ""


async def scout_node(state: GraphState) -> dict:
    """Scrape content from Xiaohongshu or Zhihu.

    Args:
        state: Workflow state with 'topic' (URL or keyword).
               May have 'source_platform' if specified.

    Returns:
        dict with 'raw_content' and 'source_platform'.
    """
    topic = state.get("topic", "")
    source_platform = state.get("source_platform", "")

    logger.info(f"[Scout] Starting scrape for: {topic}")
    logger.info(f"[Scout] Specified platform: {source_platform}")

    # Auto-detect platform from URL
    if topic.startswith("http"):
        detected = detect_platform(topic)
        if detected:
            source_platform = detected
            logger.info(f"[Scout] Auto-detected platform: {source_platform}")

    if source_platform == "xhs":
        async with XhsScraper() as scraper:
            if topic.startswith("http"):
                raw_content = await scraper.scrape_post(topic)
            else:
                raw_content = await scraper.scrape_by_topic(topic)
    elif source_platform == "zhihu":
        async with ZhihuScraper() as scraper:
            if topic.startswith("http"):
                if "question" in topic:
                    raw_content = await scraper.scrape_question(topic)
                else:
                    raw_content = await scraper.scrape_article(topic)
            else:
                raw_content = await scraper.scrape_by_topic(topic)
    else:
        raise ValueError(f"无法识别平台: {topic}。请指定 source_platform 或使用有效的 URL。")

    logger.info(f"[Scout] Scraped content: title='{raw_content.get('title', '')[:30]}...'")
    logger.info("[Scout] Node completed")

    return {"raw_content": raw_content, "source_platform": source_platform}
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/scout.py
git commit -m "feat: add dual-platform scout node"
```

---

### Task 11: Editor Node (LLM Integration)

**Files:**
- Create: `forge/agents/editor.py`

- [ ] **Step 1: Write editor.py**

```python
"""Editor node - LLM-based content rewriting."""

import logging
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


async def editor_node(state: GraphState) -> dict:
    """Rewrite content using Qwen LLM.

    Args:
        state: Workflow state with 'raw_content' or 'reflection_feedback'.

    Returns:
        dict with 'rewritten_draft' and updated 'revision_count'.
    """
    raw_content = state.get("raw_content", {})
    reflection_feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)

    logger.info(f"[Editor] Starting rewrite, revision count: {revision_count}")

    llm = LLMClient()

    original_text = raw_content.get("text", "")
    title = raw_content.get("title", "")

    if reflection_feedback:
        prompt = f"""请根据以下反馈意见优化内容：

反馈意见：
{reflection_feedback}

原始内容：
{original_text}

请改进内容，解决反馈中指出的问题。"""
        system_prompt = "你是一个专业的内容编辑，擅长根据反馈改进文章。"
    else:
        prompt = f"""请原创重写以下内容，保持吸引力和实用性，但要确保原创性：

标题：{title}
内容：{original_text}

要求：
1. 保持核心信息价值
2. 使用新的表达方式
3. 增加吸引人的开头
4. 控制篇幅在300-500字"""
        system_prompt = "你是一个短视频脚本创作专家，擅长创作原创、吸引人的内容。"

    rewritten_draft = await llm.chat_with_retry(prompt, system_prompt)

    new_revision_count = revision_count + 1
    logger.info(f"[Editor] Generated draft ({len(rewritten_draft)} chars)")
    logger.info(f"[Editor] New revision count: {new_revision_count}")
    logger.info("[Editor] Node completed")

    return {
        "rewritten_draft": rewritten_draft,
        "revision_count": new_revision_count,
    }
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/editor.py
git commit -m "feat: add editor node with LLM integration"
```

---

### Task 12: Reviewer Node (LLM Integration)

**Files:**
- Create: `forge/agents/reviewer.py`

- [ ] **Step 1: Write reviewer.py**

```python
"""Reviewer node - LLM-based quality review."""

import logging
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient
from forge.config import MAX_REVISIONS

logger = logging.getLogger(__name__)


async def reviewer_node(state: GraphState) -> dict:
    """Review rewritten content for quality.

    Args:
        state: Workflow state with 'rewritten_draft'.

    Returns:
        dict with 'final_script' (approved) or 'reflection_feedback' (needs revision).
    """
    rewritten_draft = state.get("rewritten_draft", "")
    revision_count = state.get("revision_count", 0)

    logger.info(f"[Reviewer] Starting review, revision count: {revision_count}")

    llm = LLMClient()

    prompt = f"""请审核以下短视频脚本内容：

{rewritten_draft}

审核标准：
1. 原创度：内容是否原创，无明显抄袭痕迹
2. 内容质量：逻辑清晰，信息有价值
3. 吸引力：开头吸引人，整体有吸引力

请回复：
- 如果通过审核：回复"通过"并给出简要评价
- 如果不通过：回复具体改进建议（不超过100字）"""

    system_prompt = "你是一个严格但专业的内容审核专家，确保内容质量和原创性。"

    response = await llm.chat_with_retry(prompt, system_prompt)

    approved = "通过" in response
    force_approve = revision_count >= MAX_REVISIONS

    if approved or force_approve:
        if force_approve and not approved:
            logger.info(f"[Reviewer] Force approving after {MAX_REVISIONS} revisions")
        final_script = f"【最终脚本】\n\n{rewritten_draft}\n\n[审核评价：{response}]"
        logger.info("[Reviewer] Content APPROVED")
        logger.info("[Editor] Node completed")
        return {"final_script": final_script, "reflection_feedback": ""}
    else:
        logger.info(f"[Reviewer] Content REJECTED - feedback: {response[:100]}")
        logger.info("[Reviewer] Node completed")
        return {"reflection_feedback": response, "final_script": ""}
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/reviewer.py
git commit -m "feat: add reviewer node with LLM quality check"
```

---

### Task 13: Director Node (TTS + FFmpeg)

**Files:**
- Create: `forge/agents/director.py`

- [ ] **Step 1: Write director.py**

```python
"""Director node - video generation with TTS and FFmpeg."""

import logging
import os
import uuid
from forge.graph.state import GraphState
from forge.tools.tts_generator import TtsGenerator
from forge.tools.video_composer import VideoComposer
from forge.config import VIDEO_OUTPUT_DIR

logger = logging.getLogger(__name__)


async def director_node(state: GraphState) -> dict:
    """Generate video from final script.

    Args:
        state: Workflow state with 'final_script' and 'raw_content'.

    Returns:
        dict with 'video_path'.
    """
    final_script = state.get("final_script", "")
    raw_content = state.get("raw_content", {})
    images = raw_content.get("images", [])

    logger.info("[Director] Starting video generation")
    logger.info(f"[Director] Script length: {len(final_script)} chars")
    logger.info(f"[Director] Available images: {len(images)}")

    # Ensure output directory exists
    os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

    video_id = uuid.uuid4().hex[:8]
    audio_path = f"{VIDEO_OUTPUT_DIR}/audio_{video_id}.mp3"
    video_path = f"{VIDEO_OUTPUT_DIR}/output_{video_id}.mp4"

    # Generate TTS audio
    tts = TtsGenerator()
    await tts.generate(final_script, audio_path)

    # Compose video
    composer = VideoComposer()
    await composer.compose(audio_path, images, video_path)

    logger.info(f"[Director] Video generated: {video_path}")
    logger.info("[Director] Node completed")

    return {"video_path": video_path}
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/director.py
git commit -m "feat: add director node with TTS and FFmpeg integration"
```

---

### Task 14: Publisher Node (Dual-Platform)

**Files:**
- Create: `forge/agents/publisher.py`

- [ ] **Step 1: Write publisher.py**

```python
"""Publisher node - dual-platform content publishing."""

import logging
from forge.graph.state import GraphState
from forge.tools.xhs_publisher import XhsPublisher
from forge.tools.zhihu_publisher import ZhihuPublisher

logger = logging.getLogger(__name__)


async def publisher_node(state: GraphState) -> dict:
    """Publish content to target platform.

    Args:
        state: Workflow state with 'target_platform', 'video_path', 'final_script'.

    Returns:
        dict with 'publish_status'.
    """
    target_platform = state.get("target_platform", "xhs_video")
    video_path = state.get("video_path", "")
    final_script = state.get("final_script", "")

    logger.info(f"[Publisher] Starting publication to: {target_platform}")
    logger.info(f"[Publisher] Video path: {video_path}")

    # Extract title from script (first line or first 50 chars)
    lines = final_script.strip().split("\n")
    title = lines[0][:50] if lines else "无标题"
    description = final_script

    result = {"success": False, "error": "Unknown platform"}

    try:
        if target_platform == "xhs_video":
            async with XhsPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_video(video_path, title, description)

        elif target_platform == "zhihu_article":
            async with ZhihuPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_article(title, description)

        elif target_platform == "zhihu_video":
            async with ZhihuPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_video(video_path, title, description)

        else:
            logger.error(f"[Publisher] Unknown target platform: {target_platform}")
            return {"publish_status": f"FAILED: 未知目标平台 {target_platform}"}

    except Exception as e:
        logger.error(f"[Publisher] Publication error: {e}")
        result = {"success": False, "error": str(e)}

    if result.get("success"):
        publish_status = f"SUCCESS: {result.get('post_url', '已发布')}"
        logger.info(f"[Publisher] {publish_status}")
    else:
        publish_status = f"FAILED: {result.get('error', '未知错误')}"
        logger.warning(f"[Publisher] {publish_status}")

    logger.info("[Publisher] Node completed")
    return {"publish_status": publish_status}
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/publisher.py
git commit -m "feat: add dual-platform publisher node"
```

---

### Task 15: Update Nodes Module Export

**Files:**
- Modify: `forge/agents/nodes.py`
- Modify: `forge/agents/__init__.py`

- [ ] **Step 1: Replace nodes.py with imports**

Replace entire file content:

```python
"""Agent nodes for the Forge LangGraph workflow.

Re-exports async node implementations from individual modules.
"""

from .scout import scout_node
from .editor import editor_node
from .reviewer import reviewer_node
from .director import director_node
from .publisher import publisher_node

__all__ = [
    "scout_node",
    "editor_node",
    "reviewer_node",
    "director_node",
    "publisher_node",
]
```

- [ ] **Step 2: Commit**

```bash
git add forge/agents/nodes.py
git commit -m "refactor: replace placeholder nodes with real implementations"
```

---

### Task 16: Update Workflow for Conditional Routing

**Files:**
- Modify: `forge/graph/workflow.py`

- [ ] **Step 1: Add zhihu_article routing**

Add new routing function after `route_after_review`:

```python
def route_after_review_for_platform(state: GraphState) -> Literal["director", "publisher"]:
    """Route based on target platform after review.

    For zhihu_article, skip director (no video needed).

    Args:
        state: Current workflow state.

    Returns:
        Next node: "director" or "publisher".
    """
    target_platform = state.get("target_platform", "xhs_video")

    logger.info(f"[Router] Platform routing - target: {target_platform}")

    if target_platform == "zhihu_article":
        logger.info("[Router] zhihu_article -> skip director, go to publisher")
        return "publisher"

    return "director"
```

- [ ] **Step 2: Update conditional edges in build_graph**

Replace the reviewer conditional edges block:

```python
    # reviewer -> conditional routing (platform-based)
    graph.add_conditional_edges(
        "reviewer",
        route_after_review_for_platform,
        {
            "director": "director",
            "publisher": "publisher",
        }
    )
```

- [ ] **Step 3: Add editor back-edge**

After the reviewer conditional edges, add:

```python
    # Back-edge: editor can be revisited by reviewer feedback
    # (handled by route_after_review returning "editor")
```

Note: The current workflow structure needs adjustment for async nodes.

- [ ] **Step 4: Update entire workflow.py for async**

Replace entire file with async-compatible version:

```python
"""LangGraph workflow assembly for the Forge multi-agent pipeline.

This module constructs the StateGraph with all nodes and edges,
implementing the conditional routing logic for the content workflow.
"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from forge.graph.state import GraphState
from forge.agents.nodes import (
    scout_node,
    editor_node,
    reviewer_node,
    director_node,
    publisher_node,
)

logger = logging.getLogger(__name__)


def route_after_review(state: GraphState) -> Literal["director", "publisher", "editor"]:
    """Determine the next node after reviewer.

    Routing logic:
    1. If revision_count >= MAX_REVISIONS -> force approve
    2. If final_script exists (approved) -> director or publisher (by platform)
    3. If reflection_feedback exists -> editor for revision

    Args:
        state: Current workflow state.

    Returns:
        Next node name.
    """
    final_script = state.get("final_script", "")
    reflection_feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)
    target_platform = state.get("target_platform", "xhs_video")

    logger.info(f"[Router] Routing after review - revision_count: {revision_count}")
    logger.info(f"[Router] Has final_script: {bool(final_script)}")
    logger.info(f"[Router] Has feedback: {bool(reflection_feedback)}")

    # Needs revision (has feedback and not max revisions)
    if reflection_feedback and revision_count < 3:
        logger.info("[Router] Needs revision -> routing to editor")
        return "editor"

    # Approved - check platform for routing
    if final_script or revision_count >= 3:
        if target_platform == "zhihu_article":
            logger.info("[Router] Approved (zhihu_article) -> routing to publisher")
            return "publisher"
        else:
            logger.info("[Router] Approved -> routing to director")
            return "director"

    # Default fallback
    return "editor"


def build_graph() -> StateGraph:
    """Build the LangGraph workflow.

    Returns:
        Compiled StateGraph ready for execution.
    """
    logger.info("[GraphBuilder] Starting graph construction")

    graph = StateGraph(GraphState)

    # Add async nodes
    logger.info("[GraphBuilder] Adding nodes...")
    graph.add_node("scout", scout_node)
    graph.add_node("editor", editor_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("director", director_node)
    graph.add_node("publisher", publisher_node)

    # Add edges
    logger.info("[GraphBuilder] Adding edges...")

    graph.add_edge(START, "scout")
    graph.add_edge("scout", "editor")
    graph.add_edge("editor", "reviewer")

    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "director": "director",
            "publisher": "publisher",
            "editor": "editor",
        }
    )

    graph.add_edge("director", "publisher")
    graph.add_edge("publisher", END)

    logger.info("[GraphBuilder] Graph construction completed")
    return graph


def create_workflow() -> CompiledStateGraph:
    """Create and compile the workflow graph.

    Returns:
        Compiled LangGraph application ready for async invocation.
    """
    graph = build_graph()
    compiled = graph.compile()
    logger.info("[Workflow] Graph compiled successfully")
    return compiled


workflow = create_workflow()


def visualize_graph(workflow: CompiledStateGraph, output_path: str | None = None) -> str:
    """Generate ASCII visualization of the workflow graph."""
    try:
        ascii_graph = workflow.get_graph().draw_ascii()
        if output_path:
            with open(output_path, "w") as f:
                f.write(ascii_graph)
            logger.info(f"[Visualize] Graph saved to {output_path}")
        return ascii_graph
    except Exception as e:
        logger.warning(f"[Visualize] Could not generate visualization: {e}")
        return "Graph visualization not available"
```

- [ ] **Step 5: Commit**

```bash
git add forge/graph/workflow.py
git commit -m "feat: update workflow for async nodes and platform routing"
```

---

### Task 17: Update main.py for Async Execution

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update main.py for async workflow**

Replace entire file:

```python
"""Main entry point for the Forge workflow.

Run the complete LangGraph pipeline with async execution.
"""

import asyncio
import logging
import sys

from forge.graph import workflow, create_initial_state


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the workflow."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


async def run_workflow(topic: str, target_platform: str = "xhs_video") -> dict:
    """Run the complete Forge workflow asynchronously.

    Args:
        topic: Input topic or URL.
        target_platform: Target publishing platform.

    Returns:
        Final state after workflow completion.
    """
    logger = logging.getLogger("Forge")
    logger.info("=" * 60)
    logger.info(f"Starting Forge workflow")
    logger.info(f"  Topic: {topic}")
    logger.info(f"  Target: {target_platform}")
    logger.info("=" * 60)

    initial_state = create_initial_state(topic, target_platform)
    logger.info(f"Initial state keys: {list(initial_state.keys())}")

    # Async invocation
    result = await workflow.ainvoke(initial_state)

    logger.info("=" * 60)
    logger.info("WORKFLOW COMPLETED")
    logger.info("=" * 60)

    logger.info("Final State Summary:")
    logger.info(f"  - Topic: {result.get('topic')}")
    logger.info(f"  - Source Platform: {result.get('source_platform')}")
    logger.info(f"  - Target Platform: {result.get('target_platform')}")
    logger.info(f"  - Revision Count: {result.get('revision_count')}")
    logger.info(f"  - Final Script: {result.get('final_script', 'N/A')[:100]}...")
    logger.info(f"  - Video Path: {result.get('video_path', 'N/A')}")
    logger.info(f"  - Publish Status: {result.get('publish_status', 'N/A')}")

    return result


def main() -> None:
    """Main entry point."""
    setup_logging()

    # Example usage
    test_topic = "https://www.xiaohongshu.com/explore/example"
    test_target = "xhs_video"

    asyncio.run(run_workflow(test_topic, test_target))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: update main.py for async workflow execution"
```

---

### Task 18: Create .env.example

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Write .env.example**

```
# Forge Environment Configuration

# Qwen API Key (required for LLM nodes)
QWEN_API_KEY=sk-your-api-key-here

# Optional: Override default model
# QWEN_MODEL=qwen2.5-plus
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add .env.example template"
```

---

## Self-Review Checklist

**1. Spec Coverage:**
- [x] config.py - ✓ Task 1
- [x] state.py updates (source_platform, target_platform) - ✓ Task 2
- [x] llm_client.py - ✓ Task 3
- [x] xhs_scraper.py - ✓ Task 4
- [x] zhihu_scraper.py - ✓ Task 5
- [x] tts_generator.py - ✓ Task 6
- [x] video_composer.py - ✓ Task 7
- [x] xhs_publisher.py - ✓ Task 8
- [x] zhihu_publisher.py - ✓ Task 9
- [x] scout_node (dual-platform) - ✓ Task 10
- [x] editor_node (LLM) - ✓ Task 11
- [x] reviewer_node (LLM) - ✓ Task 12
- [x] director_node (TTS+FFmpeg) - ✓ Task 13
- [x] publisher_node (dual-platform) - ✓ Task 14
- [x] workflow routing for zhihu_article - ✓ Task 16
- [x] async execution in main.py - ✓ Task 17

**2. Placeholder Scan:**
- No TBD/TODO found
- All code blocks contain actual implementation
- No "similar to" references without code

**3. Type Consistency:**
- All nodes return dict with correct keys matching GraphState
- `source_platform: str` used consistently
- `target_platform: str` used consistently
- `raw_content: dict` structure consistent across scout/publisher

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-01-real-node-implementation.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** - Dispatch fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**