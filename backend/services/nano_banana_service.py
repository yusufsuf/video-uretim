"""Nano Banana 2 Service – generates background images and composes scene frames using fal.ai."""

import logging
import os
from typing import List

import fal_client

from config import settings

logger = logging.getLogger(__name__)

# Ensure FAL_KEY is set
os.environ["FAL_KEY"] = settings.FAL_KEY


async def generate_background(
    prompt: str,
    aspect_ratio: str = "9:16",
    resolution: str = "2K",
) -> str:
    """Generate a background/scene image using Nano Banana 2.

    Args:
        prompt: Text description of the background scene (no people/model).
        aspect_ratio: One of auto, 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16.
        resolution: Image resolution: 0.5K, 1K, 2K, 4K.

    Returns:
        URL of the generated background image.
    """
    logger.info("Generating background via Nano Banana 2 – prompt: %s", prompt[:100])

    result = await fal_client.run_async(
        "fal-ai/nano-banana-2",
        arguments={
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "resolution": resolution,
            "limit_generations": True,
        },
    )

    images = result.get("images", [])
    if not images:
        raise RuntimeError("Nano Banana 2 returned no images")

    image_url = images[0].get("url", "")
    logger.info("Background generated: %s", image_url[:100])
    return image_url


async def generate_scene_frame(
    image_urls: List[str],
    prompt: str,
    aspect_ratio: str = "9:16",
) -> str:
    """Compose a per-shot scene frame using Nano Banana 2 Edit.

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
    logger.info("Nano Banana 2 Edit – composing scene frame, refs=%d", len(image_urls))

    result = await fal_client.run_async(
        "fal-ai/nano-banana-2/edit",
        arguments={
            "prompt": prompt,
            "image_urls": image_urls,
            "num_images": 1,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "resolution": "1K",
            "safety_tolerance": "4",
            "limit_generations": True,
        },
    )

    images = result.get("images", [])
    if not images:
        raise RuntimeError("Nano Banana 2 Edit returned no images for scene frame")

    image_url = images[0].get("url", "")
    logger.info("Scene frame composed: %s", image_url[:100])
    return image_url
