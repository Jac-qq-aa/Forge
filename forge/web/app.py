"""FastAPI web application for Forge content workflow."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from forge.graph import workflow, create_initial_state
from forge.deep_mode import (
    DeepModeSession,
    SessionNotFoundError,
    InvalidStageError,
    OutlineRevisionLimitError,
)
from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.workflow import run_plan_execute
from forge.deep_mode.websocket_handler import handle_websocket_connection

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("ForgeWeb")

# Create FastAPI app
app = FastAPI(title="Forge 内容转换工作流")

# CORS configuration for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative dev port
        "http://localhost:8000",  # Same origin
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files and templates
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Create Jinja2 environment manually (avoid compatibility issues)
templates_env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))


def render_template(name: str, context: dict) -> str:
    """Render a template with context."""
    template = templates_env.get_template(name)
    return template.render(**context)


# Pydantic models for API
class SearchRequest(BaseModel):
    source: str
    source_platform: str = "zhihu"
    max_results: int = 5
    search_mode: str = "keyword"  # "keyword" or "blogger"


class ProcessRequest(BaseModel):
    source_url: str
    source_platform: str
    target_platform: str
    generate_video: bool = False


class ProcessManualRequest(BaseModel):
    title: str
    text: str
    source_platform: str = "manual"
    target_platform: str
    generate_video: bool = False


class ProcessMultiRequest(BaseModel):
    question_url: str
    answer_urls: list[str]
    source_platform: str
    target_platform: str
    generate_video: bool = False


# Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render main page."""
    html = render_template("index.html", {"request": request})
    return HTMLResponse(content=html)


@app.post("/api/search")
async def search_articles(request: SearchRequest):
    """Search articles from platform."""
    logger.info(f"[API] Search request: {request}")

    try:
        articles = []

        if request.source_platform == "zhihu":
            from forge.tools.zhihu_scraper_persistent import ZhihuScraper
            async with ZhihuScraper() as scraper:
                if request.search_mode == "blogger":
                    articles = await scraper.get_user_articles(request.source, request.max_results)
                else:
                    articles = await scraper.search_articles(request.source, request.max_results)

        elif request.source_platform == "wechat":
            from forge.tools.wechat_scraper import WechatScraper
            async with WechatScraper() as scraper:
                # 微信公众号只支持关键词搜索
                articles = await scraper.search_articles(request.source, request.max_results)

        else:
            raise HTTPException(status_code=400, detail=f"不支持的来源平台: {request.source_platform}")

        # Format response
        result = []
        for i, article in enumerate(articles, 1):
            type_label = {
                "question": "知乎问答",
                "article": "知乎文章",
                "wechat_article": "微信文章",
            }.get(article.get("type", ""), article.get("type", "未知"))

            result.append({
                "id": i,
                "title": article.get("title", "无标题"),
                "summary": article.get("summary", "")[:100],
                "source_url": article.get("source_url", ""),
                "type": type_label,
                "author": article.get("author", ""),
            })

        logger.info(f"[API] Found {len(result)} articles")
        return {"success": True, "articles": result, "count": len(result)}

    except Exception as e:
        logger.error(f"[API] Search error: {e}")
        return {"success": False, "error": str(e), "articles": []}


@app.post("/api/process")
async def process_article(request: ProcessRequest):
    """Process selected article through workflow."""
    logger.info(f"[API] Process request: {request}")

    try:
        # Create state
        state = create_initial_state(request.source_url)
        state["source_platform"] = request.source_platform
        state["target_platform"] = request.target_platform
        state["generate_video"] = request.generate_video
        state["skip_publish"] = True

        # Run workflow
        result = await workflow.ainvoke(state)

        # Debug: log all keys in result
        logger.info(f"[API] Result keys: {list(result.keys())}")

        # Extract result
        script_path = result.get("script_path", "")
        video_path = result.get("video_path", "")
        final_script = result.get("final_script", "")

        # Extract original content
        raw_content = result.get("raw_content", {})
        logger.info(f"[API] Raw content keys: {list(raw_content.keys()) if raw_content else 'None'}")
        original_title = raw_content.get("title", "")
        original_text = raw_content.get("text", "")
        original_author = raw_content.get("author", "")
        logger.info(f"[API] Original title: {original_title[:30] if original_title else 'None'}...")
        logger.info(f"[API] Original text length: {len(original_text) if original_text else 0}")

        # Read script content
        script_content = ""
        if script_path:
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    script_content = f.read()
            except Exception as e:
                logger.warning(f"[API] Failed to read script: {e}")

        logger.info(f"[API] Process complete: {script_path}")

        response_data = {
            "success": True,
            "script_path": script_path,
            "video_path": video_path,
            "script_content": script_content,
            "final_script": final_script[:500] + "..." if len(final_script) > 500 else final_script,
            "original_title": original_title,
            "original_text": original_text,
            "original_author": original_author,
        }

        logger.info(f"[API] Response data keys: {list(response_data.keys())}")
        logger.info(f"[API] Response original_title: {original_title[:30] if original_title else 'None'}")
        logger.info(f"[API] Response original_text_len: {len(original_text) if original_text else 0}")

        return response_data

    except Exception as e:
        logger.error(f"[API] Process error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/process_manual")
async def process_manual_input(request: ProcessManualRequest):
    """Process manually input content through workflow."""
    logger.info(f"[API] Process manual input: title='{request.title[:30]}...', text_len={len(request.text)}, generate_video={request.generate_video}")

    try:
        # Create raw_content from user input
        raw_content = {
            "title": request.title,
            "text": request.text,
            "author": "",
            "source_url": "",
            "images": [],
        }

        # Create state
        state = create_initial_state("manual_input")
        state["source_platform"] = "manual"
        state["target_platform"] = request.target_platform
        state["raw_content"] = raw_content
        state["generate_video"] = request.generate_video
        state["skip_publish"] = True

        # Run workflow
        result = await workflow.ainvoke(state)

        # Extract result
        script_path = result.get("script_path", "")
        video_path = result.get("video_path", "")
        final_script = result.get("final_script", "")

        # Read script content
        script_content = ""
        if script_path:
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    script_content = f.read()
            except Exception as e:
                logger.warning(f"[API] Failed to read script: {e}")

        logger.info(f"[API] Manual input process complete: {script_path}")

        return {
            "success": True,
            "script_path": script_path,
            "video_path": video_path,
            "script_content": script_content,
            "final_script": final_script[:500] + "..." if len(final_script) > 500 else final_script,
            "original_title": request.title,
            "original_text": request.text,
            "original_author": "",
        }

    except Exception as e:
        logger.error(f"[API] Process manual error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/status")
async def get_status():
    """Check if service is running."""
    return {"status": "ok", "service": "Forge Web"}


class SaveRequest(BaseModel):
    script_path: str
    content: str


@app.post("/api/save")
async def save_content(request: SaveRequest):
    """Save edited content to file."""
    logger.info(f"[API] Save request: {request.script_path}")

    try:
        if not request.script_path:
            return {"success": False, "error": "没有文案路径"}

        # Save content to file
        with open(request.script_path, "w", encoding="utf-8") as f:
            f.write(request.content)

        logger.info(f"[API] Content saved to: {request.script_path}")
        return {"success": True, "script_path": request.script_path}

    except Exception as e:
        logger.error(f"[API] Save error: {e}")
        return {"success": False, "error": str(e)}


class GenerateVideoRequest(BaseModel):
    content: str
    target_platform: str = "xhs_video"


@app.post("/api/generate_video")
async def generate_video(request: GenerateVideoRequest):
    """单独生成视频（改写完成后调用）."""
    logger.info(f"[API] Generate video request: content_len={len(request.content)}")

    try:
        from forge.tools.video_generator import VideoGenerator
        from forge.config import VIDEO_OUTPUT_DIR
        import os
        import hashlib
        import time

        # 确保输出目录存在
        os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

        # 生成视频文件名
        hash_key = hashlib.md5(f"{request.content}{time.time()}".encode()).hexdigest()[:8]
        video_path = f"{VIDEO_OUTPUT_DIR}/video_{hash_key}.mp4"

        # 调用视频生成器
        generator = VideoGenerator()
        await generator.generate(request.content, video_path)

        logger.info(f"[API] Video generated: {video_path}")
        return {"success": True, "video_path": video_path}

    except Exception as e:
        logger.error(f"[API] Generate video error: {e}")
        return {"success": False, "error": str(e)}


class GenerateDigitalHumanRequest(BaseModel):
    content: str
    avatar_url: str = ""  # 可选，自定义头像图片 URL
    avatar_data: str = ""  # 可选，自定义头像 base64 数据
    avatar: str = ""  # 预设头像 ID (default1, default2, default3, custom)
    voice: str = "longxiaochun"  # 可选，语音风格 ID
    session_id: str = ""  # 可选，会话 ID


# ========== 深度生成模式 API ==========


class CreateSessionRequest(BaseModel):
    """创建会话请求。"""
    article_id: str
    source_article: dict
    user_input: str = None  # 用户改写需求


class OutlineActionRequest(BaseModel):
    """大纲操作请求。"""
    session_id: str
    action: str  # "accept" or "modify"
    modification: str = None  # 修改意见（modify 时需要）


class FinalizeRequest(BaseModel):
    """定稿请求。"""
    session_id: str


@app.post("/api/generate_digital_human")
async def generate_digital_human_video(request: GenerateDigitalHumanRequest):
    """创建数字人视频生成任务（异步模式）。

    立即返回 task_id，前端需要轮询查询状态。
    """
    logger.info(f"[API] Create digital human task: content_len={len(request.content)}")

    try:
        from forge.tools.task_status import save_task_status, run_generation_task, TaskStatus
        from forge.config import VIDEO_OUTPUT_DIR, QWEN_API_KEY
        import hashlib
        import time

        # 检查 API Key
        if not QWEN_API_KEY:
            return {"success": False, "error": "QWEN_API_KEY 未配置"}

        # 先生成任务 ID（自定义头像文件名需要用到）
        task_id = hashlib.md5(f"{request.content}{time.time()}".encode()).hexdigest()[:12]

        # 预设头像 URL 映射
        preset_avatars = {
            "default1": "https://img.alicdn.com/imgextra/i3/O1CN011FObkp1T7Ttowoq4F_!!6000000002335-0-tps-1440-1797.jpg",
            "default2": "https://forge-digitalhuman.oss-cn-beijing.aliyuncs.com/digital_human/avatar_default2.png",
            "default3": "https://forge-digitalhuman.oss-cn-beijing.aliyuncs.com/digital_human/avatar_default3.png",
        }

        # 处理头像 URL
        final_avatar_url = request.avatar_url

        # 1. 如果提供了预设头像 ID
        if request.avatar and request.avatar in preset_avatars:
            final_avatar_url = preset_avatars[request.avatar]

        # 2. 如果是自定义头像（base64 数据）
        elif request.avatar == "custom" and request.avatar_data:
            # 保存 base64 图片到临时文件
            import base64
            import os

            # 解析 base64 数据
            if request.avatar_data.startswith("data:image"):
                # 移除 data:image/xxx;base64, 前缀
                avatar_base64 = request.avatar_data.split(",", 1)[1]
            else:
                avatar_base64 = request.avatar_data

            # 生成文件名
            avatar_filename = f"avatar_custom_{task_id}.png"
            avatar_filepath = f"{VIDEO_OUTPUT_DIR}/{avatar_filename}"

            # 保存图片
            try:
                avatar_bytes = base64.b64decode(avatar_base64)
                with open(avatar_filepath, "wb") as f:
                    f.write(avatar_bytes)
                logger.info(f"[API] Custom avatar saved: {avatar_filepath}")

                # 使用本地文件路径
                final_avatar_url = avatar_filepath
            except Exception as e:
                logger.error(f"[API] Save custom avatar error: {e}")
                return {"success": False, "error": f"保存自定义头像失败: {e}"}

        # 视频文件名
        video_path = f"{VIDEO_OUTPUT_DIR}/digital_human_{task_id}.mp4"

        # 保存初始状态
        save_task_status(task_id, TaskStatus.PENDING)

        # 启动后台任务（传递头像 URL 和语音）
        asyncio.create_task(run_generation_task(
            task_id,
            request.content,
            video_path,
            avatar_url=final_avatar_url,
            voice=request.voice
        ))

        logger.info(f"[API] Task created: {task_id}, avatar: {request.avatar}, avatar_url: {final_avatar_url[:50] if final_avatar_url else 'default'}, voice: {request.voice}")

        return {
            "success": True,
            "task_id": task_id,
            "message": "任务已创建，请轮询查询状态",
        }

    except Exception as e:
        logger.error(f"[API] Create task error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/task_status/{task_id}")
async def get_task_status(task_id: str):
    """查询数字人视频生成任务状态。"""
    from forge.tools.task_status import load_task_status

    status_data = load_task_status(task_id)

    if not status_data:
        return {"success": False, "error": "任务不存在"}

    return {"success": True, **status_data}


@app.post("/api/upload_image")
async def upload_image(request: Request):
    """上传图片到 OSS，返回公网 URL（用于数字人头像）。"""
    logger.info(f"[API] Upload image request")

    try:
        import oss2
        from datetime import datetime
        from forge.config import VIDEO_OUTPUT_DIR
        import os

        # OSS 配置
        oss_bucket = os.getenv("OSS_BUCKET", "")
        oss_endpoint = os.getenv("OSS_ENDPOINT", "")
        oss_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
        oss_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")

        if not all([oss_bucket, oss_endpoint, oss_key_id, oss_key_secret]):
            return {"success": False, "error": "OSS 配置不完整"}

        # 获取上传的文件
        form = await request.form()
        file = form.get("file")

        if not file:
            return {"success": False, "error": "没有上传文件"}

        # 读取文件内容
        content = await file.read()
        filename = file.filename or "upload.jpg"

        # 验证文件类型
        allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/webp", "image/bmp"]
        content_type = file.content_type or ""
        if not any(t in content_type for t in allowed_types):
            return {"success": False, "error": f"不支持文件类型: {content_type}"}

        # 上传到 OSS
        auth = oss2.Auth(oss_key_id, oss_key_secret)
        bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket)

        # 确保 Bucket 是公开读
        try:
            bucket.put_bucket_acl(oss2.BUCKET_ACL_PUBLIC_READ)
        except:
            pass

        # 生成对象名称
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        object_key = f"digital_human/avatar_{timestamp}_{filename}"

        # 上传
        result = bucket.put_object(object_key, content)

        if result.status == 200:
            url = f"https://{oss_bucket}.{oss_endpoint}/{object_key}"
            logger.info(f"[API] Image uploaded: {url}")
            return {"success": True, "url": url}
        else:
            return {"success": False, "error": f"上传失败: HTTP {result.status}"}

    except Exception as e:
        logger.error(f"[API] Upload image error: {e}")
        return {"success": False, "error": str(e)}


class GetAnswersRequest(BaseModel):
    question_url: str
    max_answers: int = 10


@app.post("/api/get_answers")
async def get_answers(request: GetAnswersRequest):
    """获取知乎问题下的回答列表，筛选后返回。"""
    logger.info(f"[API] Get answers request: {request.question_url}")

    try:
        from forge.tools.zhihu_scraper_persistent import ZhihuScraper
        async with ZhihuScraper() as scraper:
            result = await scraper.get_question_answers(
                request.question_url,
                request.max_answers
            )

        logger.info(f"[API] Found {len(result.get('answers', []))} answers")
        return {"success": True, **result}

    except Exception as e:
        logger.error(f"[API] Get answers error: {e}")
        return {"success": False, "error": str(e), "answers": []}


@app.post("/api/process_multi")
async def process_multi_answers(request: ProcessMultiRequest):
    """处理多个回答，合并后洗稿。"""
    logger.info(f"[API] Process multi request: {len(request.answer_urls)} answers, generate_video={request.generate_video}")

    try:
        from forge.tools.zhihu_scraper_persistent import ZhihuScraper

        # 获取所有回答内容
        all_answers = []
        async with ZhihuScraper() as scraper:
            for answer_url in request.answer_urls:
                content = await scraper.scrape_answer(answer_url)
                if content.get("text"):
                    all_answers.append(content)

        # 合并内容
        combined_text = "\n\n---\n\n".join([a.get("text", "") for a in all_answers])
        combined_title = all_answers[0].get("title", "") if all_answers else ""

        # 创建合并后的 raw_content
        combined_content = {
            "title": combined_title,
            "text": combined_text,
            "likes": sum(a.get("likes", 0) for a in all_answers),
            "images": [],
            "source_url": request.question_url,
        }

        # 创建 state 并处理
        state = create_initial_state(request.question_url)
        state["source_platform"] = request.source_platform
        state["target_platform"] = request.target_platform
        state["raw_content"] = combined_content  # 直接传入合并内容
        state["generate_video"] = request.generate_video
        state["skip_publish"] = True

        # Run workflow
        result = await workflow.ainvoke(state)

        # 提取结果
        script_path = result.get("script_path", "")
        final_script = result.get("final_script", "")

        # 读取文案
        script_content = ""
        if script_path:
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    script_content = f.read()
            except Exception as e:
                logger.warning(f"[API] Failed to read script: {e}")

        return {
            "success": True,
            "script_path": script_path,
            "script_content": script_content,
            "final_script": final_script,
            "original_title": combined_title,
            "original_text": combined_text,  # 返回完整原文，不截断
            "answer_count": len(all_answers),
        }

    except Exception as e:
        logger.error(f"[API] Process multi error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/download_file")
async def download_file(path: str, type: str = "video"):
    """下载单个文件（视频或音频）。"""
    from fastapi.responses import FileResponse

    if not path or not os.path.exists(path):
        return {"success": False, "error": "文件不存在"}

    # 安全检查：确保路径在允许的目录内
    from forge.config import VIDEO_OUTPUT_DIR
    allowed_dirs = [VIDEO_OUTPUT_DIR, "/tmp/forge_videos"]
    is_allowed = any(path.startswith(d) for d in allowed_dirs)

    if not is_allowed:
        return {"success": False, "error": "路径不在允许范围内"}

    filename = os.path.basename(path)
    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/octet-stream"
    )


@app.get("/api/download_task_dir")
async def download_task_dir(task_dir: str):
    """打包任务目录并返回下载链接。"""
    import shutil

    if not task_dir or not os.path.exists(task_dir):
        return {"success": False, "error": "任务目录不存在"}

    # 安全检查
    from forge.config import VIDEO_OUTPUT_DIR
    allowed_dirs = [VIDEO_OUTPUT_DIR, "/tmp/forge_videos"]
    is_allowed = any(task_dir.startswith(d) for d in allowed_dirs)

    if not is_allowed:
        return {"success": False, "error": "路径不在允许范围内"}

    try:
        # 创建 zip 文件
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"task_{timestamp}.zip"
        zip_path = f"{VIDEO_OUTPUT_DIR}/{zip_filename}"

        # 打包目录
        shutil.make_archive(zip_path.replace('.zip', ''), 'zip', task_dir)

        # 返回下载链接
        download_url = f"/api/download_file?path={zip_path}&type=zip"
        logger.info(f"[API] Task directory packed: {zip_path}")

        return {
            "success": True,
            "download_url": download_url,
            "filename": zip_filename
        }

    except Exception as e:
        logger.error(f"[API] Pack task directory error: {e}")
        return {"success": False, "error": str(e)}


# ========== 深度生成模式端点 ==========


@app.post("/api/deep_mode/create_session")
async def api_create_deep_mode_session(request: CreateSessionRequest):
    """创建深度生成会话，直接生成大纲。"""
    logger.info(f"[API] Create deep mode session: article_id={request.article_id}")

    session_manager = get_session_manager()

    # 创建会话
    session = await session_manager.create_session(
        article_id=request.article_id,
        source_article=request.source_article,
        profile=None
    )

    # 如果有用户输入，直接生成大纲
    if request.user_input:
        try:
            session = await run_plan_execute(
                session["session_id"],
                "outline_generation",
                user_input=request.user_input
            )
            return {
                "session_id": session["session_id"],
                "stage": session["stage"],
                "outline": session["outline"],
                "outline_version": session["outline_version"],
            }
        except Exception as e:
            logger.error(f"[API] Outline generation failed: {e}")
            return {
                "session_id": session["session_id"],
                "stage": "waiting_profile",
                "error": str(e),
            }

    return {
        "session_id": session["session_id"],
        "stage": session["stage"],
    }


@app.get("/api/deep_mode/session/{session_id}")
async def api_get_session_status(session_id: str):
    """获取会话状态。"""
    session_manager = get_session_manager()

    try:
        session = await session_manager.load_session(session_id)
        return {
            "session_id": session["session_id"],
            "article_id": session["article_id"],
            "stage": session["stage"],
            "outline": session["outline"],
            "outline_version": session["outline_version"],
            "draft_v1": session["draft_v1"],
            "current_draft": session["current_draft"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found", "session_id": session_id}, 404


class UpdateOutlineRequest(BaseModel):
    """更新大纲请求。"""
    session_id: str
    outline: str


@app.post("/api/deep_mode/update_outline")
async def api_update_outline(request: UpdateOutlineRequest):
    """直接更新大纲内容（用户手动编辑后）。"""
    logger.info(f"[API] Update outline: session={request.session_id}")

    session_manager = get_session_manager()

    try:
        session = await session_manager.update_session(
            request.session_id,
            outline=request.outline
        )
        return {
            "status": "updated",
            "session_id": session["session_id"],
            "outline": session["outline"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found"}, 404


@app.post("/api/deep_mode/outline_action")
async def api_outline_action(request: OutlineActionRequest):
    """大纲确认或修改。"""
    logger.info(f"[API] Outline action: session={request.session_id}, action={request.action}")

    session_manager = get_session_manager()

    try:
        session = await session_manager.load_session(request.session_id)

        # 检查阶段
        if session["stage"] != "waiting_outline":
            raise InvalidStageError(session["stage"], "waiting_outline")

        if request.action == "accept":
            # 确认大纲，开始生成全文
            session = await run_plan_execute(
                request.session_id,
                "content_generation"
            )
            return {
                "status": "accepted",
                "session_id": session["session_id"],
                "stage": session["stage"],
                "draft": session["current_draft"],
            }

        elif request.action == "modify":
            if not request.modification:
                return {"error": "modification required for modify action"}, 400

            # 修改大纲
            session = await run_plan_execute(
                request.session_id,
                "outline_revision",
                user_input=request.modification
            )
            return {
                "status": "modified",
                "session_id": session["session_id"],
                "stage": session["stage"],
                "outline": session["outline"],
                "outline_version": session["outline_version"],
            }

        else:
            return {"error": "Invalid action: must be 'accept' or 'modify'"}, 400

    except SessionNotFoundError:
        return {"error": "Session not found"}, 404
    except InvalidStageError as e:
        return {"error": str(e)}, 400
    except OutlineRevisionLimitError as e:
        return {"error": str(e), "max_revisions": e.max_revisions}, 400


@app.post("/api/deep_mode/finalize")
async def api_finalize_session(request: FinalizeRequest):
    """定稿会话（Phase 1 版本，直接返回定稿内容）。"""
    logger.info(f"[API] Finalize session: {request.session_id}")

    session_manager = get_session_manager()

    try:
        session = await session_manager.finalize_session(request.session_id)
        return {
            "status": "completed",
            "session_id": session["session_id"],
            "final_draft": session["final_draft"],
            "finalized_at": session["finalized_at"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found"}, 404


@app.delete("/api/deep_mode/session/{session_id}")
async def api_cancel_session(session_id: str):
    """取消会话。"""
    logger.info(f"[API] Cancel session: {session_id}")

    session_manager = get_session_manager()

    try:
        session = await session_manager.cancel_session(session_id)
        return {
            "status": "cancelled",
            "session_id": session["session_id"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found"}, 404


@app.get("/api/deep_mode/sessions")
async def api_list_sessions(article_id: str = None, stage: str = None):
    """列出会话。"""
    session_manager = get_session_manager()
    sessions = await session_manager.list_sessions(article_id=article_id, stage=stage)

    return {
        "sessions": [
            {
                "session_id": s["session_id"],
                "article_id": s["article_id"],
                "stage": s["stage"],
                "created_at": s["created_at"],
            }
            for s in sessions
        ],
        "count": len(sessions),
    }


# ---- 历史记录 API ----


@app.get("/api/deep_mode/history")
async def get_deep_mode_history(
    limit: int = 20,
    offset: int = 0
):
    """获取历史会话列表（类似 ChatGPT）。"""
    session_manager = get_session_manager()
    sessions = await session_manager.get_history_sessions(limit, offset)
    return {"sessions": sessions, "total": len(sessions)}


@app.get("/api/deep_mode/session/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话完整消息历史。"""
    session_manager = get_session_manager()
    messages = await session_manager.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@app.post("/api/deep_mode/session/{session_id}/restore")
async def restore_session(session_id: str):
    """恢复中断的会话。"""
    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)
    if not session:
        return {"error": "Session not found"}
    return {"session": session, "restored": True}


@app.websocket("/ws/deep_mode/{session_id}")
async def deep_mode_websocket(websocket: WebSocket, session_id: str):
    """深度生成实时对话通道（Phase 2）。"""
    await handle_websocket_connection(websocket, session_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)