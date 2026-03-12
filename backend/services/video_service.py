"""Video Service – Kling 3.0 Pro via fal.ai API.

Generates fashion videos with prompts and optional garment elements.
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

_FAL_KLING_ENDPOINT = "fal-ai/kling-video/v3/pro/image-to-video"


async def generate_multishot_video(
    start_image_url: str,
    multi_prompt: List[dict],
    elements: Optional[List[dict]] = None,
    duration: str = "10",
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    negative_prompt: str = "blur, distort, low quality, deformed hands, deformed face, changed outfit, different dress, altered silhouette, different fabric, costume change, wardrobe change, morphing clothes",
) -> str:
    """Generate a fashion video using Kling 3.0 Pro via fal.ai.

    Args:
        start_image_url: URL of the start frame (NB2 scene composition output).
        multi_prompt: List of shot dicts, each with 'duration' (str/int) and 'prompt' (str).
                      Always called with a single shot from our pipelines.
        elements: Optional list of garment element dicts for consistency.
                  Each dict: { frontal_image_url: str, reference_image_urls: [str] }
        duration: Video duration in seconds (str, "3"–"15").
        aspect_ratio: "16:9", "9:16", or "1:1".
        generate_audio: Whether to generate audio.
        negative_prompt: Negative prompt for guidance.

    Returns:
        URL of the generated video.
    """
    # Multishot mode: more than one prompt segment → use multi_prompt API
    if multi_prompt and len(multi_prompt) > 1:
        total_duration = sum(int(s.get("duration", 5)) for s in multi_prompt)
        normalized = [{"prompt": s.get("prompt", ""), "duration": str(s.get("duration", 5))} for s in multi_prompt]
        logger.info("Starting Kling 3.0 Pro MULTISHOT via fal.ai – %d shots, total=%ds, aspect=%s, audio=%s",
                    len(normalized), total_duration, aspect_ratio, generate_audio)
        arguments: dict = {
            "start_image_url": start_image_url,
            "multi_prompt": normalized,
            "duration": str(total_duration),
            "aspect_ratio": aspect_ratio,
            "generate_audio": generate_audio,
            "negative_prompt": negative_prompt,
        }
    else:
        # Single shot — use simple prompt
        shot = multi_prompt[0] if multi_prompt else {}
        prompt = shot.get("prompt", "")
        shot_duration = str(shot.get("duration", duration))
        logger.info("Starting Kling 3.0 Pro via fal.ai – duration=%ss, aspect=%s, audio=%s",
                    shot_duration, aspect_ratio, generate_audio)
        arguments = {
            "start_image_url": start_image_url,
            "prompt": prompt,
            "duration": shot_duration,
            "aspect_ratio": aspect_ratio,
            "generate_audio": generate_audio,
            "negative_prompt": negative_prompt,
        }

    if elements:
        arguments["elements"] = elements

    result = await fal_client.run_async(
        _FAL_KLING_ENDPOINT,
        arguments=arguments,
    )

    video_url: str = str(result["video"]["url"])
    logger.info("Kling 3.0 video completed: %s", video_url)
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
    logger.info("Uploaded to fal.ai: %s", url)
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

    logger.info("Downloaded %s -> %s", url, filepath)
    return filepath
