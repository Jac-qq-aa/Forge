---
name: Real Node Implementation
created: 2026-04-01
status: approved
---

# Forge 真实节点实现设计

## 概述

将 Forge 项目的 placeholder 节点替换为真实实现，支持小红书和知乎双平台：
- **scout_node**: Playwright 爬取小红书/知乎内容（URL自动识别，关键词可指定平台）
- **editor_node**: Qwen3.5-plus AI 重写
- **reviewer_node**: Qwen3.5-plus AI 审核
- **director_node**: Edge TTS + FFmpeg 视频生成
- **publisher_node**: 发布到小红书视频/知乎文章/知乎视频（用户选择目标平台）

## 技术选型

| 节点 | 技术方案 |
|------|----------|
| LLM | Qwen3.5-plus via OpenAI-compatible API |
| 小红书爬虫 | Playwright 浏览器自动化 |
| 知乎爬虫 | Playwright 浏览器自动化 |
| TTS | Edge TTS (Microsoft 免费) |
| 视频合成 | FFmpeg |
| 小红书发布 | Playwright 自动化（视频） |
| 知乎发布 | Playwright 自动化（文章/视频） |

## 项目结构

```
forge/
├── config.py                 # 配置管理
├── tools/
│   ├── llm_client.py         # Qwen LLM 封装
│   ├── xhs_scraper.py        # 小红书爬虫
│   ├── zhihu_scraper.py      # 知乎爬虫
│   ├── tts_generator.py      # Edge TTS
│   ├── video_composer.py     # FFmpeg 合成
│   ├── xhs_publisher.py      # 小红书发布
│   └── zhihu_publisher.py    # 知乎发布（文章/视频）
├── agents/
│   ├── scout.py              # scout_node（支持双平台爬取）
│   ├── editor.py             # editor_node
│   ├── reviewer.py           # reviewer_node
│   ├── director.py           # director_node
│   ├── publisher.py          # publisher_node（支持双平台发布）
│   └── nodes.py              # 统一导出
└── graph/
    ├── state.py              # 新增 source_platform, target_platform
    └── workflow.py           # 保持不变
```

## 模块设计

### config.py

```python
import os
from dotenv import load_dotenv

load_dotenv()

QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
QWEN_MODEL = "qwen3.5-plus"

# 平台配置
XHS_BASE_URL = "https://www.xiaohongshu.com"
ZHIHU_BASE_URL = "https://www.zhihu.com"
VIDEO_OUTPUT_DIR = "/tmp/forge_videos"
MAX_REVISIONS = 3

# 支持的目标平台
TARGET_PLATFORMS = ["xhs_video", "zhihu_article", "zhihu_video"]
```

### graph/state.py (更新)

```python
class GraphState(TypedDict, total=False):
    # 输入
    topic: str                          # URL 或关键词
    source_platform: str                # 来源平台: "xhs" 或 "zhihu"（自动识别或指定）
    target_platform: str                # 目标平台: "xhs_video" / "zhihu_article" / "zhihu_video"

    # Scout node output
    raw_content: dict                   # {title, text, images, likes, comments, source_url}

    # ... 其他字段保持不变
```

### tools/llm_client.py

```python
from openai import AsyncOpenAI
from forge.config import QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL

class LLMClient:
    def __init__(self):
        self.client = AsyncOpenAI(base_url=QWEN_API_URL, api_key=QWEN_API_KEY)

    async def chat(self, prompt: str, system_prompt: str = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self.client.chat.completions.create(
            model=QWEN_MODEL,
            messages=messages
        )
        return response.choices[0].message.content

    async def chat_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        for i in range(max_retries):
            try:
                return await self.chat(prompt)
            except Exception as e:
                if i == max_retries - 1:
                    return f"LLM调用失败: {e}"
                await asyncio.sleep(2 ** i)
```

### tools/xhs_scraper.py

```python
from playwright.async_api import async_playwright

class XhsScraper:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.page = await self.browser.new_page()
        return self

    async def __aexit__(self, *args):
        await self.browser.close()
        await self.playwright.stop()

    async def scrape_post(self, url: str) -> dict:
        await self.page.goto(url)
        # CSS选择器需要根据小红书实际页面结构调整
        # 主要元素：标题、正文内容、图片、点赞数、评论数
        title = await self.page.locator("#detail-title").text_content()
        text = await self.page.locator("#detail-desc").text_content()
        images = await self.page.locator(".swiper-slide img").evaluate_all("imgs => imgs.map(i => i.src)")
        likes = await self.page.locator(".like-wrapper .count").text_content()
        return {"title": title, "text": text, "images": images, "likes": likes, "source_url": url}

    async def scrape_by_topic(self, topic: str) -> dict:
        await self.page.goto(f"{XHS_BASE_URL}/search?keyword={topic}")
        # 点击第一个搜索结果并爬取
        await self.page.locator(".search-result").first.click()
        return await self.scrape_post(self.page.url)
```

### tools/zhihu_scraper.py

```python
from playwright.async_api import async_playwright

class ZhihuScraper:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.page = await self.browser.new_page()
        return self

    async def __aexit__(self, *args):
        await self.browser.close()
        await self.playwright.stop()

    async def scrape_article(self, url: str) -> dict:
        """爬取知乎文章"""
        await self.page.goto(url)
        title = await self.page.locator(".Post-Title").text_content()
        text = await self.page.locator(".Post-RichText").text_content()
        images = await self.page.locator(".Post-RichText img").evaluate_all("imgs => imgs.map(i => i.src)")
        likes = await self.page.locator(".VoteButton--up").text_content()
        comments = await self.page.locator(".ContentItem-actions span").first.text_content()
        return {"title": title, "text": text, "images": images, "likes": likes, "comments": comments, "source_url": url}

    async def scrape_question(self, url: str) -> dict:
        """爬取知乎问答（取最高赞回答）"""
        await self.page.goto(url)
        title = await self.page.locator(".QuestionHeader-title").text_content()
        # 获取最高赞回答
        top_answer = await self.page.locator(".List-item").first
        text = await top_answer.locator(".RichContent-inner").text_content()
        images = await top_answer.locator(".RichContent-inner img").evaluate_all("imgs => imgs.map(i => i.src)")
        likes = await top_answer.locator(".VoteButton--up").text_content()
        return {"title": title, "text": text, "images": images, "likes": likes, "source_url": url}

    async def scrape_by_topic(self, topic: str) -> dict:
        """知乎搜索关键词，返回热门问答/文章"""
        await self.page.goto(f"{ZHIHU_BASE_URL}/search?type=content&q={topic}")
        await self.page.locator(".ContentItem").first.click()
        return await self.scrape_article(self.page.url)
```

### tools/tts_generator.py

```python
import edge_tts

class TtsGenerator:
    async def generate(self, text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        return output_path
```

### tools/video_composer.py

```python
import asyncio
import aiofiles

class VideoComposer:
    async def compose(self, audio_path: str, images: list[str], output_path: str) -> str:
        # 1. 下载图片到临时目录
        image_dir = f"/tmp/forge_images_{uuid.uuid4()[:8]}"
        local_images = await self._download_images(images, image_dir)

        # 2. 创建图片序列文件（每张图显示3秒）
        concat_file = f"{image_dir}/concat.txt"
        async with aiofiles.open(concat_file, "w") as f:
            for img in local_images:
                await f.write(f"file '{img}'\nduration 3\n")

        # 3. FFmpeg 合成：图片序列 + 音频 -> 视频
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.wait()
        return output_path

    async def _download_images(self, urls: list[str], dir: str) -> list[str]:
        # 使用 aiohttp 下载图片
        ...
```
```

### tools/xhs_publisher.py

```python
from playwright.async_api import async_playwright

class XhsPublisher:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.page = await self.browser.new_page()
        return self

    async def __aexit__(self, *args):
        await self.browser.close()
        await self.playwright.stop()

    async def login(self) -> bool:
        await self.page.goto(f"{XHS_BASE_URL}/login")
        # 等待用户扫码登录
        await self.page.wait_for_url("**/home**", timeout=120000)
        return True

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """发布视频到小红书"""
        await self.page.goto(f"{XHS_BASE_URL}/creator/publish")
        await self.page.locator("input[type='file']").set_input_files(video_path)
        await self.page.locator(".title-input").fill(title)
        await self.page.locator(".desc-input").fill(description)
        await self.page.locator(".publish-btn").click()
        return {"success": True, "post_url": self.page.url}
```

### tools/zhihu_publisher.py

```python
from playwright.async_api import async_playwright

class ZhihuPublisher:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.page = await self.browser.new_page()
        return self

    async def __aexit__(self, *args):
        await self.browser.close()
        await self.playwright.stop()

    async def login(self) -> bool:
        """登录知乎（可能需要手动扫码或输入密码）"""
        await self.page.goto(f"{ZHIHU_BASE_URL}/signin")
        await self.page.wait_for_url("**/", timeout=120000)
        return True

    async def publish_article(self, title: str, content: str) -> dict:
        """发布知乎文章"""
        await self.page.goto(f"{ZHIHU_BASE_URL}/write")
        await self.page.locator(".WriteIndex-titleInput").fill(title)
        await self.page.locator(".WriteIndex-content").fill(content)
        await self.page.locator(".WriteIndex-submitBtn").click()
        return {"success": True, "post_url": self.page.url}

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """发布知乎视频"""
        await self.page.goto(f"{ZHIHU_BASE_URL}/creator/publish/video")
        await self.page.locator("input[type='file']").set_input_files(video_path)
        await self.page.locator(".VideoUpload-title").fill(title)
        await self.page.locator(".VideoUpload-desc").fill(description)
        await self.page.locator(".VideoUpload-submit").click()
        return {"success": True, "post_url": self.page.url}
```

### agents/scout.py

```python
from forge.graph.state import GraphState
from forge.tools.xhs_scraper import XhsScraper
from forge.tools.zhihu_scraper import ZhihuScraper

def detect_platform(url: str) -> str:
    """根据 URL 自动识别平台"""
    if "xiaohongshu.com" in url:
        return "xhs"
    elif "zhihu.com" in url:
        return "zhihu"
    return ""

async def scout_node(state: GraphState) -> dict:
    """爬取内容（支持小红书和知乎）"""
    topic = state["topic"]
    source_platform = state.get("source_platform", "")

    # URL 自动识别平台
    if topic.startswith("http"):
        source_platform = detect_platform(topic)

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
        raise ValueError(f"无法识别平台: {topic}")

    return {"raw_content": raw_content, "source_platform": source_platform}
```

### agents/editor.py

```python
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient

async def editor_node(state: GraphState) -> dict:
    raw_content = state.get("raw_content", {})
    feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)

    llm = LLMClient()

    if feedback:
        prompt = f"根据以下反馈优化内容：{feedback}\n原内容：{raw_content.get('text', '')}"
    else:
        prompt = f"请原创重写以下小红书内容，保持吸引力和实用性：{raw_content.get('text', '')}"

    rewritten_draft = await llm.chat_with_retry(prompt)
    return {"rewritten_draft": rewritten_draft, "revision_count": revision_count + 1}
```

### agents/reviewer.py

```python
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient

async def reviewer_node(state: GraphState) -> dict:
    draft = state.get("rewritten_draft", "")
    revision_count = state.get("revision_count", 0)

    llm = LLMClient()

    prompt = f"""请审核以下短视频脚本内容：
1. 原创度（是否抄袭）
2. 内容质量（逻辑清晰、信息有价值）
3. 吸引力（标题吸引人、开头抓眼球）

内容：
{draft}

请回复：
- 如果通过：回复"通过"并给出简要评价
- 如果不通过：回复具体改进建议"""

    response = await llm.chat_with_retry(prompt)

    if "通过" in response or revision_count >= 3:
        final_script = f"【最终脚本】\n\n{draft}\n\n[审核评价：{response}]"
        return {"final_script": final_script, "reflection_feedback": ""}
    else:
        return {"reflection_feedback": response, "final_script": ""}
```

### agents/director.py

```python
import uuid
from forge.graph.state import GraphState
from forge.tools.tts_generator import TtsGenerator
from forge.tools.video_composer import VideoComposer
from forge.config import VIDEO_OUTPUT_DIR

async def director_node(state: GraphState) -> dict:
    script = state.get("final_script", "")
    images = state.get("raw_content", {}).get("images", [])

    tts = TtsGenerator()
    composer = VideoComposer()

    video_id = str(uuid.uuid4())[:8]
    audio_path = f"{VIDEO_OUTPUT_DIR}/audio_{video_id}.mp3"
    video_path = f"{VIDEO_OUTPUT_DIR}/output_{video_id}.mp4"

    # 生成语音
    await tts.generate(script, audio_path)

    # 合成视频
    await composer.compose(audio_path, images, video_path)

    return {"video_path": video_path}
```

### agents/publisher.py

```python
from forge.graph.state import GraphState
from forge.tools.xhs_publisher import XhsPublisher
from forge.tools.zhihu_publisher import ZhihuPublisher

async def publisher_node(state: GraphState) -> dict:
    """发布内容（支持小红书视频、知乎文章、知乎视频）"""
    target_platform = state.get("target_platform", "xhs_video")
    video_path = state.get("video_path", "")
    script = state.get("final_script", "")

    if target_platform == "xhs_video":
        async with XhsPublisher() as publisher:
            await publisher.login()
            result = await publisher.publish_video(video_path, script[:50], script)
    elif target_platform == "zhihu_article":
        async with ZhihuPublisher() as publisher:
            await publisher.login()
            result = await publisher.publish_article(script[:50], script)
    elif target_platform == "zhihu_video":
        async with ZhihuPublisher() as publisher:
            await publisher.login()
            result = await publisher.publish_video(video_path, script[:50], script)
    else:
        return {"publish_status": f"FAILED: 未知目标平台 {target_platform}"}

    if result["success"]:
        return {"publish_status": f"SUCCESS: {result['post_url']}"}
    else:
        return {"publish_status": f"FAILED: {result.get('error', '未知错误')}"}
```

## 新增依赖

```
playwright>=1.40.0
edge-tts>=6.1.0
openai>=1.0.0
python-dotenv>=1.0.0
aiofiles>=23.0.0
aiohttp>=3.9.0          # 异步下载图片
```

## 错误处理

| 错误类型 | 处理方式 |
|----------|----------|
| LLM API 失败 | 重试3次，失败返回默认响应 |
| Playwright 崩溃 | 自动重启，最多2次 |
| Edge TTS 超时 | 重试2次，失败跳过 |
| FFmpeg 失败 | 日志记录，返回错误状态 |
| 登录过期 | 提示重新扫码 |

## 配置文件

创建 `.env` 文件：
```
QWEN_API_KEY=sk-your-api-key
```

## 实现顺序

1. config.py - 配置管理（添加知乎配置）
2. forge/graph/state.py - 新增 source_platform, target_platform 字段
3. tools/llm_client.py - LLM 封装
4. agents/editor.py + agents/reviewer.py - 文本处理节点
5. tools/xhs_scraper.py + tools/zhihu_scraper.py + agents/scout.py - 双平台爬虫
6. tools/tts_generator.py + tools/video_composer.py + agents/director.py - 视频生成
7. tools/xhs_publisher.py + tools/zhihu_publisher.py + agents/publisher.py - 双平台发布
8. agents/nodes.py - 统一导出，更新 __init__.py

## 注意事项

1. **平台 CSS 选择器**：
   - 小红书和知乎的页面结构可能变化，需要根据最新页面调整选择器
   - 建议使用 Playwright 的 `page.wait_for_selector()` 确保元素加载完成

2. **反爬机制**：
   - 小红书可能有反爬检测，建议使用 headless=False 模拟真实用户
   - 知乎可能需要登录才能查看完整内容
   - 设置合理的请求间隔

3. **知乎发布文章的特殊路由**：
   - 发布知乎文章不需要生成视频，应跳过 director_node
   - 需要在 workflow.py 中添加条件路由：
   ```python
   def route_after_review_for_platform(state: GraphState) -> str:
       target = state.get("target_platform", "xhs_video")
       if target == "zhihu_article":
           return "publisher"  # 直接发布，跳过 director
       return "director"
   ```

4. **视频输出目录**：需要确保 `VIDEO_OUTPUT_DIR` 目录存在且有写入权限。

5. **异步节点兼容**：LangGraph 支持异步节点，需要将 workflow 改为使用 `astream()` 执行。