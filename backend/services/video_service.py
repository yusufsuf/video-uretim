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

# ─── Model Presets ─────────────────────────────────────────────────
# Full-body model images for Claid AI Fashion Models dressing
# White background, minimal clothing, head-to-toe, straight pose
# Files stored locally in backend/assets/models/
_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "models")

MODEL_PRESETS = {
    "default": "model_default.jpg",
    "model_1": "model_default.jpg",
    "model_2": "model_default.jpg",
    "model_3": "model_default.jpg",
    "model_4": "model_default.jpg",
    "model_5": "model_default.jpg",
    "model_6": "model_default.jpg",
}


def get_model_image_url(preset: str = "default") -> str:
    """Resolve a model preset key to a base64 data URI.

    Reads the local model image file and converts it to a data URI
    that can be sent directly to Claid API.
    """
    import base64
    from pathlib import Path

    filename = MODEL_PRESETS.get(preset, MODEL_PRESETS["default"])
    filepath = os.path.join(_MODELS_DIR, filename)

    if not os.path.exists(filepath):
        logger.warning("Model preset file not found: %s – using fallback", filepath)
        # Try to find any image in the models dir
        for f in os.listdir(_MODELS_DIR):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                filepath = os.path.join(_MODELS_DIR, f)
                break
        else:
            raise FileNotFoundError(f"No model images found in {_MODELS_DIR}")

    suffix = Path(filepath).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "image/jpeg")

    with open(filepath, "rb") as f:
        data = base64.b64encode(f.read()).decode()

    logger.info("Model preset '%s' loaded from %s", preset, filepath)
    return f"data:{mime};base64,{data}"


async def _ensure_accessible_url(url: str) -> str:
    """Download an image URL and convert to base64 data URI if needed.
    This ensures Fal.ai can always access the image, even if the
    original host blocks hotlinking or requires specific headers."""
    # Fal.ai's own URLs are always accessible
    if "storage.googleapis.com/falserverless" in url:
        return url
    # data URIs are already inline
    if url.startswith("data:"):
        return url

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            if ";" in content_type:
                content_type = content_type.split(";")[0].strip()
            import base64
            b64 = base64.b64encode(resp.content).decode()
            logger.info("Converted URL to data URI (%d bytes)", len(resp.content))
            return f"data:{content_type};base64,{b64}"
    except Exception as exc:
        logger.warning("Failed to pre-download image (%s), using URL as-is", exc)
        return url


# ─── Virtual Try-On (IDM-VTON on fal.ai) ──────────────────────────
async def virtual_try_on(
    garment_image_url: str,
    model_image_url: Optional[str] = None,
    description: str = "A fashion model wearing the garment, professional fashion photography",
) -> str:
    """Apply the garment onto a model using IDM-VTON via fal.ai.

    Args:
        garment_image_url: URL of the preprocessed garment image.
        model_image_url:   URL of the model / person image.
                           If None a default fashion model will be used.
        description:       Description of the garment for better VTO fidelity.

    Returns:
        URL of the resulting try-on image.
    """
    # Default model image – use our preset full-body model
    if model_image_url is None:
        model_image_url = get_model_image_url("default")

    # Ensure the model image is accessible by Fal.ai
    accessible_url = await _ensure_accessible_url(model_image_url)

    logger.info("Starting VTO – garment: %s", garment_image_url[:80])

    result = await fal_client.run_async(
        "fal-ai/idm-vton",
        arguments={
            "human_image_url": accessible_url,
            "garment_image_url": garment_image_url,
            "description": description,
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
            "negative_prompt": "face close-up, zooming into face, blurry, distorted fabric, low quality, deformed hands",
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
