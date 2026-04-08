"""Nano Banana Pro Service – generates background images and composes scene frames using fal.ai."""

import asyncio
import logging
import os
from typing import List

import fal_client

from config import settings

logger = logging.getLogger(__name__)

# Ensure FAL_KEY is set
os.environ["FAL_KEY"] = settings.FAL_KEY

# ── Model IDs ────────────────────────────────────────────────────────────────
_NB_PRO_MODEL = "fal-ai/nano-banana-pro"
_NB_PRO_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"


async def generate_background(
    prompt: str,
    aspect_ratio: str = "9:16",
    resolution: str = "2K",
) -> str:
    """Generate a background/scene image using Nano Banana Pro.

    Args:
        prompt: Text description of the background scene (no people/model).
        aspect_ratio: One of auto, 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16.
        resolution: Image resolution: 1K, 2K, 4K.

    Returns:
        URL of the generated background image.
    """
    logger.info("Generating background via Nano Banana Pro – prompt: %s", prompt[:100])

    result = await fal_client.run_async(
        _NB_PRO_MODEL,
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "resolution": resolution,
            "safety_tolerance": "4",
            "limit_generations": True,
        },
    )

    images = result.get("images", [])
    if not images:
        raise RuntimeError("Nano Banana Pro returned no images")

    image_url = images[0].get("url", "")
    logger.info("Background generated: %s", image_url[:100])
    return image_url


async def generate_scene_frame(
    image_urls: List[str],
    prompt: str,
    aspect_ratio: str = "9:16",
) -> str:
    """Compose a per-shot scene frame using Nano Banana Pro Edit.

    Combines the background (image_urls[0]) with garment reference images
    (image_urls[1:]) to produce a single photorealistic frame ready to be
    animated by Kling.

    Args:
        image_urls: [background_url, front_url, (side_url), (back_url)]
        prompt: Scene-specific composition instruction.
        aspect_ratio: Output aspect ratio (must match video aspect ratio).

    Returns:
        URL of the composed scene frame.
    """
    logger.info("Nano Banana Pro Edit – composing scene frame, refs=%d", len(image_urls))

    result = await fal_client.run_async(
        _NB_PRO_EDIT_MODEL,
        arguments={
            "prompt": prompt,
            "image_urls": image_urls,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "resolution": "2K",
            "safety_tolerance": "4",
            "limit_generations": True,
        },
    )

    images = result.get("images", [])
    if not images:
        raise RuntimeError("Nano Banana Pro Edit returned no images for scene frame")

    image_url = images[0].get("url", "")
    logger.info("Scene frame composed: %s", image_url[:100])
    return image_url


# 4 camera angle prompts for venue/location variant generation
_VENUE_ANGLE_PROMPTS = [
    "Wide establishing shot of this exact venue — preserve the same architectural style, materials, lighting atmosphere, and all decor elements. Full space visible from a wide cinematic angle, the runway or catwalk centered in the composition.",
    "Medium frontal eye-level view of this exact venue — preserve architecture, materials, lighting and atmosphere exactly. Symmetrical central perspective, camera at eye level looking toward the main focal area of the space.",
    "Side lateral view of this exact venue — preserve the same architectural style, materials, lighting and atmosphere. Camera positioned perpendicular to the main axis, full side profile of the space visible.",
    "Elevated bird's-eye overhead view of this exact venue — preserve all architectural details, materials and atmosphere. Camera looking straight down from above, geometric top-down composition of the full space.",
]


async def generate_venue_variants(
    venue_image_url: str,
    count: int,
    aspect_ratio: str = "9:16",
) -> list:
    """Generate N angle variants of a venue photo using NB Pro Edit.

    Args:
        venue_image_url: fal.ai CDN URL of the source venue photo.
        count: Number of variants to generate (1-4).
        aspect_ratio: Output aspect ratio.

    Returns:
        List of generated image URLs (length = count).
    """
    count = max(1, min(4, count))
    prompts = _VENUE_ANGLE_PROMPTS[:count]

    async def _gen_one(prompt: str) -> str:
        return await generate_scene_frame(
            image_urls=[venue_image_url],
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )

    urls = await asyncio.gather(*[_gen_one(p) for p in prompts])
    logger.info("Venue variants generated: %d", len(urls))
    return list(urls)
