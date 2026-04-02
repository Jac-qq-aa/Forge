"""FFmpeg video composer combining images and audio."""

import asyncio
import logging
import shutil
import tempfile
import aiofiles
import aiohttp
from aiohttp import ClientError, ClientTimeout
from asyncio import TimeoutError as AsyncTimeoutError

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
        image_dir = tempfile.mkdtemp(prefix="forge_images_")

        try:
            # Download images
            local_images = await self._download_images(images, image_dir)
            logger.info(f"[VideoComposer] Downloaded {len(local_images)} images")

            if not local_images:
                logger.warning("[VideoComposer] No images, creating video with audio only")
                # Create video from audio only (black background)
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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown FFmpeg error"
                logger.error(f"[VideoComposer] FFmpeg failed: {error_msg}")
                raise RuntimeError(f"FFmpeg failed with code {proc.returncode}: {error_msg}")

            logger.info(f"[VideoComposer] Video saved to: {output_path}")
            return output_path
        finally:
            # Cleanup temp directory
            shutil.rmtree(image_dir, ignore_errors=True)

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
                except (ClientError, AsyncTimeoutError) as e:
                    logger.debug(f"[VideoComposer] Failed to download image {i}: {e}")

        return local_paths