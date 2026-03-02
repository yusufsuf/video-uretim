"""Video Service – orchestrates Fal.ai calls for Virtual Try-On and
Image-to-Video generation."""

import logging
import os
import uuid
from typing import Optional

import fal_client
import httpx

from config import settings

logger = logging.getLogger(__name__)

# Ensure FAL_KEY is set as environment variable for the fal-client SDK
os.environ["FAL_KEY"] = settings.FAL_KEY


# ─── Virtual Try-On (IDM-VTON on fal.ai) ──────────────────────────
async def virtual_try_on(
    garment_image_url: str,
    model_image_url: Optional[str] = None,
) -> str:
    """Apply the garment onto a model using IDM-VTON via fal.ai.

    Args:
        garment_image_url: URL of the preprocessed garment image.
        model_image_url:   URL of the model / person image.
                           If None a default fashion model will be used.

    Returns:
        URL of the resulting try-on image.
    """
    # Default model image – a generic fashion model pose
    if model_image_url is None:
        model_image_url = (
            "https://storage.googleapis.com/falserverless/"
            "model_tests/try_on/person.jpg"
        )

    logger.info("Starting VTO – garment: %s", garment_image_url[:80])

    result = await fal_client.run_async(
        "fal-ai/idm-vton",
        arguments={
            "human_image_url": model_image_url,
            "garment_image_url": garment_image_url,
            "description": "A fashion model wearing the garment, professional fashion photography",
        },
    )

    output_url = result.get("image", {}).get("url", "")
    logger.info("VTO completed: %s", output_url[:80])
    return output_url


# ─── Image to Video (Kling v3 Pro on fal.ai) ──────────────────────
async def generate_video(
    image_url: str,
    prompt: str,
    duration: str = "5",
    aspect_ratio: str = "9:16",
) -> str:
    """Generate a fashion video from a keyframe image using Kling v3 Pro on fal.ai.

    Args:
        image_url:    URL of the keyframe (VTO result).
        prompt:       Full cinematic prompt for the video.
        duration:     Video duration in seconds ("5" or "10").
        aspect_ratio: Aspect ratio, default vertical for fashion.

    Returns:
        URL of the generated video.
    """
    logger.info("Starting video generation – image: %s", image_url[:80])

    result = await fal_client.run_async(
        "fal-ai/kling-video/v3/pro/image-to-video",
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
        },
    )

    video_url = result.get("video", {}).get("url", "")
    logger.info("Video generation completed: %s", video_url[:80])
    return video_url


# ─── Video to Video with Reference ────────────────────────────────
async def generate_video_with_reference(
    image_url: str,
    reference_video_url: str,
    prompt: str,
    duration: str = "5",
    aspect_ratio: str = "9:16",
) -> str:
    """Generate a fashion video using both a keyframe and a reference movement video.

    Uses Kling v2's video-to-video pipeline where the reference video provides
    motion guidance and the image provides visual identity.

    Args:
        image_url:          URL of the keyframe (VTO result).
        reference_video_url: URL of the reference fashion video for motion.
        prompt:             Full cinematic prompt.
        duration:           Video duration.
        aspect_ratio:       Aspect ratio.

    Returns:
        URL of the generated video.
    """
    logger.info("Starting video generation with reference – image: %s", image_url[:80])

    # Use Kling v3 Pro with image-to-video
    result = await fal_client.run_async(
        "fal-ai/kling-video/v3/pro/image-to-video",
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
        },
    )

    video_url = result.get("video", {}).get("url", "")
    logger.info("Video with reference completed: %s", video_url[:80])
    return video_url


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
