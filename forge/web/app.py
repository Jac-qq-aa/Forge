"""FastAPI web application for Forge content workflow."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
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

# Setup logging (must be before lifespan)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("ForgeWeb")

from forge.storage.redis_client import close_redis_pool
from forge.storage.pg_client import close_pg_pool, get_pg_pool, is_valid_uuid


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - 初始化和清理连接池。"""
    # 启动时初始化 PG 连接池
    try:
        await get_pg_pool()
        logger.info("[Lifespan] PG connection pool initialized")
    except Exception as e:
        logger.warning(f"[Lifespan] PG connection pool init failed (will use fallback): {e}")

    # 初始化 LangGraph checkpointer（官方 AsyncPostgresSaver）
    try:
        from forge.graph.checkpointer import get_checkpointer
        await get_checkpointer()
        logger.info("[Lifespan] LangGraph checkpointer initialized")
    except Exception as e:
        logger.warning(f"[Lifespan] Checkpointer init failed: {e}")

    yield

    # 关闭时清理 checkpointer
    try:
        from forge.graph.checkpointer import close_checkpointer
        await close_checkpointer()
        logger.info("[Lifespan] Checkpointer closed")
    except Exception as e:
        logger.warning(f"[Lifespan] Checkpointer close error: {e}")

    # 关闭时清理连接池
    try:
        await close_redis_pool()
        logger.info("[Lifespan] Redis connection pool closed")
    except Exception as e:
        logger.warning(f"[Lifespan] Redis close error: {e}")

    try:
        await close_pg_pool()
        logger.info("[Lifespan] PG connection pool closed")
    except Exception as e:
        logger.warning(f"[Lifespan] PG close error: {e}")


from forge.graph import workflow, create_initial_state
from forge.graph.state import UnifiedState, create_unified_state
# unified_workflow 现在是异步初始化，使用 get_unified_workflow
from forge.graph.unified_workflow import get_unified_workflow
from forge.deep_mode import (
    DeepModeSession,
    SessionNotFoundError,
    InvalidStageError,
    OutlineRevisionLimitError,
)
from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.workflow import run_plan_execute
from forge.deep_mode.websocket_handler import handle_websocket_connection
from forge.evaluation.storage import (
    get_evaluation_result,
    get_session_probe_logs,
    get_evaluation_stats,
)

# Create FastAPI app with lifespan
app = FastAPI(title="Forge 内容转换工作流", lifespan=lifespan)

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
    content: str = None  # 用户编辑后的内容（可选）


class BatchDeleteRequest(BaseModel):
    """批量删除请求。"""
    session_ids: list[str]


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

        # 2. 如果是自定义头像且有 URL（优先使用 OSS URL）
        elif request.avatar == "custom" and request.avatar_url:
            final_avatar_url = request.avatar_url
            logger.info(f"[API] Custom avatar URL from OSS: {request.avatar_url[:60]}...")

        # 3. 如果是自定义头像只有 base64 数据（备用方案）
        elif request.avatar == "custom" and request.avatar_data:
            # 保存 base64 图片到 OSS（而不是本地文件）
            import base64
            import os

            # 解析 base64 数据
            if request.avatar_data.startswith("data:image"):
                avatar_base64 = request.avatar_data.split(",", 1)[1]
            else:
                avatar_base64 = request.avatar_data

            # 尝试上传到 OSS
            try:
                import oss2
                oss_bucket = os.getenv("OSS_BUCKET", "")
                oss_endpoint = os.getenv("OSS_ENDPOINT", "")
                oss_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
                oss_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")

                if all([oss_bucket, oss_endpoint, oss_key_id, oss_key_secret]):
                    auth = oss2.Auth(oss_key_id, oss_key_secret)
                    bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket)

                    avatar_bytes = base64.b64decode(avatar_base64)
                    object_key = f"digital_human/avatar_base64_{task_id}.png"
                    result = bucket.put_object(object_key, avatar_bytes)

                    if result.status == 200:
                        final_avatar_url = f"https://{oss_bucket}.{oss_endpoint}/{object_key}"
                        logger.info(f"[API] Custom avatar uploaded to OSS: {final_avatar_url}")
                    else:
                        logger.warning(f"[API] OSS upload failed, using fallback")
                else:
                    logger.warning("[API] OSS not configured, cannot use base64 avatar")
                    return {"success": False, "error": "OSS 配置不完整，无法上传自定义头像"}
            except Exception as e:
                logger.error(f"[API] Upload custom avatar error: {e}")
                return {"success": False, "error": f"上传自定义头像失败: {e}"}

        # 视频文件名
        video_path = f"{VIDEO_OUTPUT_DIR}/digital_human_{task_id}.mp4"

        # 保存初始状态
        save_task_status(task_id, TaskStatus.PENDING)

        # 如果有 session_id，保存视频任务信息到 session
        if request.session_id:
            try:
                session_manager = get_session_manager()
                await session_manager.update_session(
                    request.session_id,
                    video_task_id=task_id,
                    video_status="pending",
                    video_path=video_path  # 预设路径，完成后更新
                )
                logger.info(f"[API] Video task linked to session: {request.session_id}")
            except Exception as e:
                logger.warning(f"[API] Failed to link video to session: {e}")

        # 启动后台任务（传递头像 URL 和语音）
        asyncio.create_task(run_generation_task(
            task_id,
            request.content,
            video_path,
            avatar_url=final_avatar_url,
            voice=request.voice,
            session_id=request.session_id  # 传递 session_id 用于完成时更新
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

    # 创建会话（适配新 SessionManager）
    session = await session_manager.create_session(
        source_article=request.source_article,
        user_input=request.user_input or ""
    )

    # 保存 article_id 到会话（兼容字段）
    session["article_id"] = request.article_id

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
    """定稿会话（支持用户编辑后的内容）。"""
    logger.info(f"[API] Finalize session: {request.session_id}")

    session_manager = get_session_manager()

    try:
        # 如果用户提供了编辑后的内容，先更新 session
        if request.content:
            logger.info(f"[API] User provided edited content: {len(request.content)} chars")
            await session_manager.update_session(
                request.session_id,
                current_draft=request.content
            )

        session = await session_manager.finalize_session(request.session_id)
        return {
            "status": "completed",
            "session_id": session["session_id"],
            "final_draft": session["final_draft"],
            "current_draft": request.content or session["final_draft"],  # 返回用户编辑的内容
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


@app.delete("/api/deep_mode/history/{session_id}")
async def api_delete_history_session(session_id: str):
    """软删除历史记录。"""
    logger.info(f"[API] Delete history session: {session_id}")

    session_manager = get_session_manager()

    try:
        success = await session_manager.soft_delete_session(session_id)
        if not success:
            return {"success": False, "error": "会话不存在或已删除"}, 404
        return {"success": True, "status": "deleted", "session_id": session_id}
    except Exception as e:
        logger.error(f"[API] Delete error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/deep_mode/history/batch_delete")
async def api_batch_delete_history(request: BatchDeleteRequest):
    """批量软删除历史记录。"""
    logger.info(f"[API] Batch delete {len(request.session_ids)} sessions")

    session_manager = get_session_manager()

    try:
        count = await session_manager.soft_delete_sessions(request.session_ids)
        return {"success": True, "status": "deleted", "count": count}
    except Exception as e:
        logger.error(f"[API] Batch delete error: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/api/deep_mode/history/clear_all")
async def api_clear_all_history():
    """清空所有历史记录。"""
    logger.info(f"[API] Clear all history")

    session_manager = get_session_manager()

    try:
        count = await session_manager.soft_delete_all_sessions()
        return {"success": True, "status": "deleted", "count": count}
    except Exception as e:
        logger.error(f"[API] Clear all error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/deep_mode/sessions")
async def api_list_sessions(article_id: str = None, stage: str = None):
    """列出会话。"""
    session_manager = get_session_manager()
    sessions = await session_manager.list_sessions(article_id=article_id, stage=stage)

    return {
        "sessions": [
            {
                "session_id": s.get("session_id") or s.get("id"),
                "article_id": s.get("article_id", ""),
                "stage": s.get("stage"),
                "created_at": s.get("created_at"),
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


# ========== 统一 Workflow API（融合快速/深度模式） ==========


class UnifiedProcessRequest(BaseModel):
    """统一处理请求 - 支持快速/深度两种模式。"""
    mode: str = "fast"  # "fast" or "deep"
    source_url: str = ""
    source_platform: str = "zhihu"
    target_platform: str = "xhs_video"
    raw_content: dict = None  # 用于深度模式或手动输入
    user_input: str = ""  # 深度模式的改写需求
    generate_video: bool = False


class UnifiedContinueRequest(BaseModel):
    """继续执行请求 - 用于 interrupt 后恢复。"""
    session_id: str
    human_decision: str  # "accept" / "modify:xxx" / "finalize" / 用户微调消息


@app.post("/api/unified/start")
async def unified_start(request: UnifiedProcessRequest):
    """启动统一 workflow - 快速模式或深度模式。

    快速模式（mode="fast"）：
    - 从 URL 抓取内容，自动改写，生成输出
    - 无需用户干预，一次性完成

    深度模式（mode="deep"）：
    - 需要提供 raw_content 和 user_input
    - 生成大纲后暂停，等待用户确认
    - 返回 outline，需要调用 /api/unified/continue 继续

    Returns:
        快速模式：完整结果
        深度模式：大纲和 session_id，需要继续
    """
    logger.info(f"[API/Unified] Start: mode={request.mode}, target={request.target_platform}")

    # 创建初始状态
    state = create_unified_state(
        mode=request.mode,
        topic=request.source_url,
        target_platform=request.target_platform,
        source_platform=request.source_platform,
        raw_content=request.raw_content,
        user_input=request.user_input,
        generate_video=request.generate_video,
    )
    state["skip_publish"] = True  # 默认不实际发布

    session_id = state["session_id"]

    # 执行 workflow（延迟初始化）
    try:
        workflow = await get_unified_workflow()
        result = await workflow.ainvoke(
            state,
            config={"configurable": {"thread_id": session_id}}
        )
        logger.info(f"[API/Unified] Workflow executed: stage={result.get('stage', 'completed')}")

        # 根据模式返回不同结果
        if request.mode == "fast":
            return {
                "success": True,
                "session_id": session_id,
                "script_path": result.get("script_path", ""),
                "video_path": result.get("video_path", ""),
                "final_script": result.get("final_script", ""),
            }
        else:
            # 深度模式：在 human_review 前暂停
            return {
                "success": True,
                "session_id": session_id,
                "stage": result.get("stage", "waiting_outline"),
                "outline": result.get("outline", ""),
                "outline_version": result.get("outline_version", 0),
                "need_continue": result.get("stage") == "waiting_outline",
            }

    except Exception as e:
        logger.error(f"[API/Unified] Start failed: {e}")
        return {"success": False, "error": str(e), "session_id": session_id}


@app.post("/api/unified/continue")
async def unified_continue(request: UnifiedContinueRequest):
    """继续执行 workflow - 用于 interrupt 后恢复。

    用于深度模式的大纲确认和微调阶段：
    - 大纲确认：human_decision = "accept" / "modify:xxx" / "finalize"
    - 微调阶段：human_decision = 用户修改请求 / "finalize"

    Returns:
        当前状态，可能需要继续或已完成
    """
    logger.info(f"[API/Unified] Continue: session={request.session_id}, decision={request.human_decision[:30]}...")

    try:
        # 使用 Command 来恢复执行，同时更新状态
        # LangGraph resume 的正确用法：
        # - 传入 None: 可以 resume，但不能传入新数据
        # - 传入 dict: 会重新开始（从 START），不会 resume
        # - 使用 Command: 可以 resume 并同时传入新数据
        from langgraph.types import Command

        workflow = await get_unified_workflow()
        result = await workflow.ainvoke(
            Command(update={"human_decision": request.human_decision}),
            config={"configurable": {"thread_id": request.session_id}},
        )

        stage = result.get("stage", "")
        logger.info(f"[API/Unified] Continue result: stage={stage}")

        # 返回当前状态
        return {
            "success": True,
            "session_id": request.session_id,
            "stage": stage,
            "outline": result.get("outline", ""),
            "outline_version": result.get("outline_version", 0),
            "current_draft": result.get("current_draft", ""),
            "final_script": result.get("final_script", ""),
            "script_path": result.get("script_path", ""),
            "video_path": result.get("video_path", ""),
            "need_continue": stage in ["waiting_outline", "tuning"],
        }

    except Exception as e:
        logger.error(f"[API/Unified] Continue failed: {e}")
        return {"success": False, "error": str(e), "session_id": request.session_id}


@app.get("/api/unified/status/{session_id}")
async def unified_status(session_id: str):
    """获取统一 workflow 状态。"""
    try:
        # 从 checkpointer 加载状态（延迟初始化）
        workflow = await get_unified_workflow()
        result = await workflow.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )

        if not result:
            return {"success": False, "error": "Session not found"}

        state = result.get("channel_values", {})
        return {
            "success": True,
            "session_id": session_id,
            "mode": state.get("mode", "unknown"),
            "stage": state.get("stage", ""),
            "outline": state.get("outline", ""),
            "current_draft": state.get("current_draft", ""),
            "final_script": state.get("final_script", ""),
        }

    except Exception as e:
        logger.error(f"[API/Unified] Status failed: {e}")
        return {"success": False, "error": str(e)}


@app.websocket("/ws/deep_mode/{session_id}")
async def deep_mode_websocket(websocket: WebSocket, session_id: str):
    """深度生成实时对话通道（Phase 2）。"""
    await handle_websocket_connection(websocket, session_id)


# ========== 评估 API ==========


def generate_evaluation_tip(result: dict) -> str:
    """根据评估结果生成改进提示。

    Args:
        result: 评估结果字典

    Returns:
        改进提示字符串
    """
    if not result:
        return "暂无评估数据"

    tips = []
    overall = result.get("overall_score", 0) or 0
    faithfulness = result.get("faithfulness_score", 0) or 0
    relevance = result.get("relevance_score", 0) or 0
    human_score = result.get("human_score", 0) or 0

    # 根据各项指标生成提示
    if faithfulness < 0.7:
        tips.append("内容事实一致性较低，建议检查信息来源准确性")
    if relevance < 0.7:
        tips.append("内容相关性较低，建议更聚焦目标主题")
    if human_score < 0.7:
        tips.append("内容人性化程度较低，建议增加自然表达和情感元素")

    if not tips:
        if overall >= 0.9:
            return "内容质量优秀，保持当前风格"
        elif overall >= 0.7:
            return "内容质量良好，可适当优化细节"
        else:
            return "建议全面提升内容质量"
    else:
        return "；".join(tips)


@app.get("/api/evaluation/{session_id}")
async def api_get_evaluation(session_id: str):
    """获取评估结果（用户端简单分数）。"""
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=400, detail="无效的session_id格式")
    result = await get_evaluation_result(session_id)
    if result is None:
        return {"status": "pending"}
    return {
        "overall_score": round(result.get("overall_score", 0) * 100),
        "faithfulness": round(result.get("faithfulness_score", 0) * 100),
        "relevance": round(result.get("relevance_score", 0) * 100),
        "human_score": round(result.get("human_score", 0) * 100),
        "tip": generate_evaluation_tip(result),
    }


@app.get("/api/admin/evaluation/{session_id}/detail")
async def api_get_evaluation_detail(session_id: str):
    """获取详细评估结果（后台分析）。"""
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=400, detail="无效的session_id格式")
    result = await get_evaluation_result(session_id)
    logs = await get_session_probe_logs(session_id)
    if result is None:
        return {"status": "not_found", "evaluation": None, "probe_logs": logs}
    return {"status": "completed", "evaluation": result, "probe_logs": logs}


@app.get("/api/admin/evaluation/stats")
async def api_get_evaluation_stats(limit: int = 100):
    """获取评估统计数据。"""
    limit = min(max(limit, 1), 1000)  # 范围限制1-1000
    stats = await get_evaluation_stats(limit)

    # 计算分布
    distribution = {
        "excellent": 0,  # >= 90
        "good": 0,       # 70-89
        "fair": 0,       # 50-69
        "poor": 0,       # < 50
    }

    avg_scores = {
        "overall": 0,
        "faithfulness": 0,
        "relevance": 0,
        "human_score": 0,
    }

    if stats:
        for item in stats:
            overall = item.get("overall_score", 0) or 0
            overall_pct = overall * 100

            if overall_pct >= 90:
                distribution["excellent"] += 1
            elif overall_pct >= 70:
                distribution["good"] += 1
            elif overall_pct >= 50:
                distribution["fair"] += 1
            else:
                distribution["poor"] += 1

            avg_scores["overall"] += item.get("overall_score", 0) or 0
            avg_scores["faithfulness"] += item.get("faithfulness_score", 0) or 0
            avg_scores["relevance"] += item.get("relevance_score", 0) or 0
            avg_scores["human_score"] += item.get("human_score", 0) or 0

        count = len(stats)
        avg_scores = {k: round(v / count * 100, 1) for k, v in avg_scores.items()}

    return {
        "distribution": distribution,
        "averages": avg_scores,
        "total_count": len(stats),
        "results": stats,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)