"""Kling Direct API service — image-to-video via Kling AI native API.

Replaces fal.ai proxy for video generation.
Auth: JWT HS256 (access_key + secret_key), refreshed per request.
Endpoint: https://api-singapore.klingai.com/v1/videos/image2video
"""

import asyncio
import logging
import time
from typing import List, Optional

import httpx
import jwt

from config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-singapore.klingai.com"
_POLL_INTERVAL = 10   # seconds between status checks
_POLL_TIMEOUT  = 600  # max wait: 10 minutes


def _make_jwt() -> str:
    """Generate a short-lived JWT token for Kling API auth."""
    payload = {
        "iss": settings.KLING_ACCESS_KEY,
        "exp": int(time.time()) + 1800,  # valid 30 min
        "nbf": int(time.time()) - 5,
    }
    return jwt.encode(
        payload,
        settings.KLING_SECRET_KEY,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"},
    )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_make_jwt()}",
        "Content-Type": "application/json",
    }


async def generate_multishot_video(
    start_image_url: str,
    multi_prompt: List[dict],
    duration: str = "10",
    aspect_ratio: str = "9:16",
    generate_audio: bool = False,
    negative_prompt: str = (
        "blur, distort, low quality, deformed hands, deformed face, "
        "changed outfit, different dress, altered silhouette, different fabric, "
        "costume change, wardrobe change, morphing clothes, feet, bare feet, "
        "shoes, heels, boots, footwear, visible ankles, visible toes, "
        "floating hem, lifted skirt, hem above ground, gap between dress and floor, "
        "short dress, mini dress, midi dress, knee-length dress, calf-length dress, "
        "cropped skirt, raised hemline, above-ankle hem, shortened dress"
    ),
) -> str:
    """Generate a video via Kling Direct API.

    Matches the signature of video_service.generate_multishot_video so it can
    be used as a drop-in replacement (elements param intentionally omitted —
    element_id support will be added in a follow-up after testing).
    """
    if not settings.KLING_ACCESS_KEY or not settings.KLING_SECRET_KEY:
        raise RuntimeError("KLING_ACCESS_KEY / KLING_SECRET_KEY not configured")

    # Build multi_prompt with 1-based index (Kling API requirement)
    shots = [
        {
            "index": i + 1,
            "prompt": s["prompt"][:500],
            "duration": str(s["duration"]),
        }
        for i, s in enumerate(multi_prompt)
    ]
    total_dur = sum(int(s["duration"]) for s in multi_prompt)

    body: dict = {
        "model_name": "kling-v3",
        "image": start_image_url,
        "multi_shot": True,
        "shot_type": "customize",
        "multi_prompt": shots,
        "negative_prompt": negative_prompt[:2500],
        "duration": str(total_dur),
        "aspect_ratio": aspect_ratio,
        "sound": "on" if generate_audio else "off",
        "mode": "pro",
    }

    logger.info(
        "Kling Direct: %d shots, total=%ss, aspect=%s, audio=%s",
        len(shots), total_dur, aspect_ratio, generate_audio,
    )
    for s in shots:
        logger.info("  Shot [%d] (%ss): %s", s["index"], s["duration"], s["prompt"])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_BASE_URL}/v1/videos/image2video",
            json=body,
            headers=_headers(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Kling API HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Kling API error {data.get('code')}: {data.get('message')}")
        task_id: str = data["data"]["task_id"]

    logger.info("Kling Direct task created: %s", task_id)
    return await _poll_task(task_id)


async def _poll_task(task_id: str) -> str:
    """Poll Kling task until succeed/failed. Returns video URL."""
    elapsed = 0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_BASE_URL}/v1/videos/image2video/{task_id}",
                headers=_headers(),
            )
            if resp.status_code != 200:
                logger.warning("Kling poll HTTP %s: %s", resp.status_code, resp.text[:200])
                continue
            data = resp.json()

        status = data["data"]["task_status"]
        logger.debug("Kling task %s status: %s (elapsed %ds)", task_id, status, elapsed)

        if status == "succeed":
            url: str = data["data"]["task_result"]["videos"][0]["url"]
            logger.info("Kling Direct task %s complete: %s", task_id, url[:80])
            return url

        if status == "failed":
            msg = data["data"].get("task_status_msg", "unknown error")
            raise RuntimeError(f"Kling task {task_id} failed: {msg}")

    raise TimeoutError(f"Kling task {task_id} timed out after {_POLL_TIMEOUT}s")
