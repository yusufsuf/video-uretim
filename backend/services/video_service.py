"""Video Service – Kling 3.0 Pro Multishot on fal.ai.

Generates fashion videos with multishot prompts and garment elements.
"""

import asyncio
import logging
import os
import subprocess
import uuid
from typing import List, Optional

import fal_client
import httpx

from config import settings

logger = logging.getLogger(__name__)

# Ensure FAL_KEY is set as environment variable for the fal-client SDK
os.environ["FAL_KEY"] = settings.FAL_KEY


async def generate_multishot_video(
    start_image_url: str,
    multi_prompt: List[dict],
    elements: Optional[List[dict]] = None,
    duration: str = "10",
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    negative_prompt: str = "blur, distort, and low quality, deformed hands, deformed face",
) -> str:
    """Generate a fashion video using Kling 3.0 Pro multishot on fal.ai.

    Args:
        start_image_url: URL of the background/scene image (from Nano Banana).
        multi_prompt: List of shot dicts, each with 'duration' (str) and 'prompt' (str).
        elements: List of element dicts for garment consistency.
                  Each dict: { frontal_image_url: str, reference_image_urls: [str] }
        duration: Total video duration in seconds (str, "3"-"15").
        aspect_ratio: "16:9", "9:16", or "1:1".
        generate_audio: Whether to generate audio.
        negative_prompt: Things to avoid in generation.

    Returns:
        URL of the generated video.
    """
    logger.info("Starting Kling 3.0 Pro multishot – %d shots, %ss duration",
                len(multi_prompt), duration)

    payload = {
        "multi_prompt": multi_prompt,
        "start_image_url": start_image_url,
        "duration": duration,
        "shot_type": "customize",
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
        "negative_prompt": negative_prompt,
        "cfg_scale": 0.5,
    }

    if elements:
        payload["elements"] = elements
        logger.info("  Elements: %d garment references", len(elements))

    logger.info("  Payload keys: %s", list(payload.keys()))
    logger.info("  Multi-prompt shots: %s",
                [(s.get("duration"), s.get("prompt", "")[:60]) for s in multi_prompt])

    result = await fal_client.run_async(
        "fal-ai/kling-video/v3/pro/image-to-video",
        arguments=payload,
    )

    video_url = result.get("video", {}).get("url", "")
    if not video_url:
        logger.error("Kling returned no video URL. Full result: %s", result)
        raise RuntimeError("Kling 3.0 Pro returned no video URL")

    logger.info("Multishot video completed: %s", video_url[:100])
    return video_url


# ─── Last-frame chaining helpers ─────────────────────────────────

def extract_last_frame(video_path: str, output_dir: str) -> str:
    """Extract the last frame of a video as a PNG file using FFmpeg."""
    frame_path = os.path.join(output_dir, f"{uuid.uuid4().hex}_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.5", "-i", video_path,
         "-vframes", "1", "-q:v", "2", frame_path],
        check=True, capture_output=True, timeout=30,
    )
    logger.info("Extracted last frame: %s", frame_path)
    return frame_path


async def upload_to_fal(file_path: str) -> str:
    """Upload a local file to fal.ai CDN and return the public URL."""
    url = await asyncio.to_thread(fal_client.upload_file, file_path)
    logger.info("Uploaded to fal.ai: %s", url[:80])
    return url


def concatenate_clips(clip_paths: list, output_path: str) -> str:
    """Concatenate video clips in order using FFmpeg concat demuxer."""
    list_file = output_path.replace(".mp4", "_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", output_path],
        check=True, capture_output=True, timeout=300,
    )
    os.remove(list_file)
    logger.info("Concatenated %d clips → %s", len(clip_paths), output_path)
    return output_path


# ─── Download helper ──────────────────────────────────────────────
async def download_file(url: str, output_dir: str, extension: str = ".mp4") -> str:
    """Download a remote file and save it locally.

    Returns:
        The local file path.
    """
    filename = f"{uuid.uuid4().hex}{extension}"
    filepath = os.path.join(output_dir, filename)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)

    logger.info("Downloaded %s -> %s", url[:60], filepath)
    return filepath
