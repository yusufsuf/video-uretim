"""Claid Service – handles image preprocessing using the Claid.ai API.
Responsible for background removal, upscaling, and studio-quality enhancement.

Falls back to local Pillow-based preprocessing if Claid API fails."""

import base64
import io
import logging
import os
from pathlib import Path

import httpx
from PIL import Image

from config import settings

logger = logging.getLogger(__name__)

CLAID_BASE_URL = "https://api.claid.ai/v1-beta1"

# Maximum image dimension before sending to external APIs
MAX_DIMENSION = 1536


# ─── Local image helpers ──────────────────────────────────────────
def _resize_image_locally(image_path: str, max_dim: int = MAX_DIMENSION) -> str:
    """Resize a local image so its longest side is at most max_dim.
    Saves to a new file and returns the new path."""
    img = Image.open(image_path)
    w, h = img.size

    if max(w, h) <= max_dim:
        return image_path  # already small enough

    if w > h:
        new_w = max_dim
        new_h = int(h * (max_dim / w))
    else:
        new_h = max_dim
        new_w = int(w * (max_dim / h))

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Save as JPEG for smaller size
    stem = Path(image_path).stem
    out_path = os.path.join(os.path.dirname(image_path), f"{stem}_resized.jpg")
    img.convert("RGB").save(out_path, "JPEG", quality=85)
    logger.info("Resized %s -> %s (%dx%d)", image_path, out_path, new_w, new_h)
    return out_path


def _local_image_to_data_uri(image_path: str) -> str:
    """Convert a local image to a base64 data URI (smaller, resized version)."""
    resized = _resize_image_locally(image_path)
    suffix = Path(resized).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "image/jpeg")
    with open(resized, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


# ─── Claid API calls ─────────────────────────────────────────────
async def _call_claid(endpoint: str, payload: dict) -> dict:
    """Generic helper to call the Claid API."""
    headers = {
        "Authorization": f"Bearer {settings.CLAID_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{CLAID_BASE_URL}/{endpoint}",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


async def remove_background(image_url: str) -> str:
    """Remove background from a garment image using Claid.
    Returns the URL of the processed image."""
    payload = {
        "input": image_url,
        "operations": {
            "background": {
                "remove": True,
            }
        },
        "output": {
            "format": "png",
        },
    }
    result = await _call_claid("image/edit", payload)
    output_url = result.get("data", {}).get("output", {}).get("tmp_url", "")
    logger.info("Claid background removal completed: %s", output_url[:80])
    return output_url


async def enhance_image(image_url: str, target_width: int = 1024) -> str:
    """Upscale and enhance a garment image for better VTO results.
    Returns the URL of the enhanced image."""
    payload = {
        "input": image_url,
        "operations": {
            "resizing": {
                "width": target_width,
                "fit": "bounds",
            },
            "adjustments": {
                "sharpness": 50,
            },
        },
        "output": {
            "format": "png",
        },
    }
    result = await _call_claid("image/edit", payload)
    output_url = result.get("data", {}).get("output", {}).get("tmp_url", "")
    logger.info("Claid enhance completed: %s", output_url[:80])
    return output_url


# ─── Claid AI Fashion Models (per-scene photo generation) ────────
CLAID_FASHION_URL = "https://api.claid.ai/v1/image/ai-fashion-models"

async def generate_fashion_photo(
    clothing_url: str,
    pose: str = "standing elegantly, full body front view",
    background: str = "luxury fashion studio with soft lighting",
    aspect_ratio: str = "9:16",
) -> str:
    """Generate a fashion model photo wearing the garment using Claid AI Fashion Models.

    Args:
        clothing_url: URL or data URI of the garment image.
        pose:         Pose + camera angle description for the AI model.
        background:   Background/setting description for the scene.
        aspect_ratio: Output aspect ratio (9:16, 16:9, 1:1).

    Returns:
        URL of the generated fashion photo.
    """
    import asyncio

    headers = {
        "Authorization": f"Bearer {settings.CLAID_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "input": {
            "clothing": [clothing_url],
        },
        "options": {
            "pose": pose,
            "background": background,
            "aspect_ratio": aspect_ratio,
        },
        "output": {
            "number_of_images": 1,
            "format": "png",
        },
    }

    logger.info("Claid Fashion Photo – pose: %s, bg: %s", pose[:60], background[:60])

    async with httpx.AsyncClient(timeout=180) as http:
        resp = await http.post(CLAID_FASHION_URL, json=payload, headers=headers)
        logger.info("Claid response status: %d, body: %s", resp.status_code, resp.text[:500])

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Claid API error {resp.status_code}: {resp.text[:500]}"
            )

        result = resp.json()

        # Check for immediate result
        tmp_url = result.get("data", {}).get("output", {}).get("tmp_url", "")
        if tmp_url:
            logger.info("Claid Fashion Photo completed (immediate): %s", tmp_url[:80])
            return tmp_url

        # Check for async result that needs polling
        result_url = result.get("data", {}).get("result_url", "")
        output_objects = result.get("data", {}).get("result", {}).get("output_objects", [])

        if output_objects:
            photo_url = output_objects[0].get("tmp_url", "")
            if photo_url:
                logger.info("Claid Fashion Photo completed (objects): %s", photo_url[:80])
                return photo_url

        if result_url:
            logger.info("Claid Fashion Photo polling: %s", result_url[:80])
            for attempt in range(18):  # max 18 * 10s = 3 min wait
                await asyncio.sleep(10)
                poll_resp = await http.get(result_url, headers=headers)
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()

                # Check for completed result
                poll_tmp = poll_data.get("data", {}).get("output", {}).get("tmp_url", "")
                if poll_tmp:
                    logger.info("Claid Fashion Photo completed (polled): %s", poll_tmp[:80])
                    return poll_tmp

                status = poll_data.get("data", {}).get("status", "")
                if status in ("DONE", "done", "completed"):
                    objects = poll_data.get("data", {}).get("result", {}).get("output_objects", [])
                    if objects:
                        photo_url = objects[0].get("tmp_url", "")
                        if photo_url:
                            logger.info("Claid Fashion Photo completed (polled-objects): %s", photo_url[:80])
                            return photo_url
                elif status in ("FAILED", "ERROR", "failed", "error"):
                    raise RuntimeError(f"Claid Fashion Photo failed: {poll_data}")

                logger.info("Claid Fashion Photo poll %d, status: %s", attempt + 1, status)

            raise TimeoutError("Claid Fashion Photo generation timed out after 3 minutes")

        raise ValueError(f"Unexpected Claid response: {result}")


# ─── Main preprocessing entry point ──────────────────────────────
async def preprocess_garment(image_url: str, local_path: str = "", is_ghost: bool = False) -> str:
    """Full preprocessing pipeline for a garment image.

    Tries Claid API first. If it fails (e.g. 413 Payload Too Large),
    falls back to local Pillow-based resizing.

    Args:
        image_url:  Data URI or URL of the image.
        local_path: Local file path (used for fallback resizing).
        is_ghost:   Whether this is a ghost mannequin photo.

    Returns:
        URL/data-URI of the preprocessed image.
    """
    try:
        # Try resizing locally first to reduce payload size for Claid
        if local_path and os.path.exists(local_path):
            small_uri = _local_image_to_data_uri(local_path)
        else:
            small_uri = image_url

        if is_ghost:
            clean_url = await remove_background(small_uri)
        else:
            clean_url = small_uri

        enhanced_url = await enhance_image(clean_url)
        return enhanced_url

    except Exception as exc:
        logger.warning("Claid preprocessing failed (%s), using local fallback", exc)

        # Fallback: just resize locally and return a data URI
        if local_path and os.path.exists(local_path):
            return _local_image_to_data_uri(local_path)

        # If no local path, return original
        return image_url
