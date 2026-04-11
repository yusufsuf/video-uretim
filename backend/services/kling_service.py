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
_ELEM_INTERVAL = 5    # seconds between element creation polls
_ELEM_TIMEOUT  = 300  # max wait for element: 5 minutes


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


# ─── Element API ────────────────────────────────────────────────────────────

async def create_element(
    frontal_image_url: str,
    reference_image_urls: List[str],
    name: str = "garment",
    description: str = "fashion garment",
) -> int:
    """Create a Kling element from images. Returns element_id (int).

    Per Kling API docs: element_image_list requires one frontal_image plus
    1–3 refer_images that MUST differ from the frontal_image. Passing the
    same URL in both fields breaks element quality / causes API errors.
    """
    # Dedupe: remove any refer_image identical to the frontal_image
    refer_imgs = [u for u in (reference_image_urls or []) if u and u != frontal_image_url]
    # Also drop duplicates within refer_images while preserving order
    seen: set = set()
    refer_imgs = [u for u in refer_imgs if not (u in seen or seen.add(u))]
    refer_imgs = refer_imgs[:3]

    if not refer_imgs:
        raise RuntimeError(
            "Kling element requires at least 1 reference image that differs "
            "from the frontal image (side/back/detail view). Only the frontal "
            "image was provided."
        )

    body = {
        "element_name": name[:20],  # type: ignore[index]
        "element_description": description[:100],  # type: ignore[index]
        "reference_type": "image_refer",
        "element_image_list": {
            "frontal_image": frontal_image_url,
            "refer_images": [{"image_url": u} for u in refer_imgs],
        },
        "tag_list": [{"tag_id": "o_105"}],  # Costume
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_BASE_URL}/v1/general/advanced-custom-elements",
            json=body,
            headers=_headers(),
        )
        if resp.status_code != 200:
            _err = resp.text; raise RuntimeError(f"Kling element creation HTTP {resp.status_code}: {_err[:300]}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Kling element error {data.get('code')}: {data.get('message')}")
        task_id: str = data["data"]["task_id"]

    logger.info("Kling element task created: %s (name=%s)", task_id, name[:20])
    return await _poll_element(task_id)


async def _poll_element(task_id: str) -> int:
    """Poll element creation task until complete. Returns element_id."""
    elapsed = 0
    while elapsed < _ELEM_TIMEOUT:
        await asyncio.sleep(_ELEM_INTERVAL)
        elapsed += _ELEM_INTERVAL

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_BASE_URL}/v1/general/advanced-custom-elements/{task_id}",
                headers=_headers(),
            )
            if resp.status_code != 200:
                _warn = resp.text; logger.warning("Element poll HTTP %s: %s", resp.status_code, _warn[:200])
                continue
            data = resp.json()

        status = data["data"]["task_status"]
        logger.debug("Element task %s status: %s (elapsed %ds)", task_id, status, elapsed)

        if status == "succeed":
            element_id: int = data["data"]["task_result"]["elements"][0]["element_id"]
            logger.info("Kling element ready: id=%d (task=%s)", element_id, task_id)
            return element_id

        if status == "failed":
            msg = data["data"].get("task_status_msg", "unknown error")
            raise RuntimeError(f"Kling element task {task_id} failed: {msg}")

    raise TimeoutError(f"Kling element task {task_id} timed out after {_ELEM_TIMEOUT}s")


# ─── Video API ───────────────────────────────────────────────────────────────

async def generate_multishot_video(
    start_image_url: str,
    multi_prompt: List[dict],
    duration: str = "10",
    aspect_ratio: str = "9:16",
    generate_audio: bool = False,
    element_list: Optional[List[dict]] = None,  # [{"element_id": int}, ...]
    model_name: str = "kling-v3",  # "kling-v3" | "kling-v3-omni"
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
    """Generate a video via Kling Direct API."""
    if not settings.KLING_ACCESS_KEY or not settings.KLING_SECRET_KEY:
        raise RuntimeError("KLING_ACCESS_KEY / KLING_SECRET_KEY not configured")

    # Build multi_prompt with 1-based index (Kling API requirement)
    shots = [
        {
            "index": i + 1,
            "prompt": s["prompt"][:512],
            "duration": str(s["duration"]),
        }
        for i, s in enumerate(multi_prompt)
    ]
    total_dur = sum(int(s["duration"]) for s in multi_prompt)

    # Kling multi_shot hard cap: 15 seconds total across all shots.
    if total_dur > 15:
        raise ValueError(
            f"Kling multi_shot total duration must be ≤ 15s — got {total_dur}s "
            f"across {len(shots)} shots. Reduce per-shot duration or shot count."
        )
    if total_dur < 5:
        raise ValueError(
            f"Kling multi_shot total duration must be ≥ 5s — got {total_dur}s."
        )

    body: dict = {
        "model_name": model_name,
        "image": start_image_url,
        "multi_shot": True,
        "shot_type": "customize",
        "multi_prompt": shots,
        "negative_prompt": negative_prompt[:2500],
        "duration": str(total_dur),
        "aspect_ratio": aspect_ratio,
        "sound": "on" if generate_audio else "off",
        "mode": "pro",
        # cfg_scale: how strictly the model follows the prompt.
        # 0.7 is the sweet spot for fashion + element binding per Kling docs —
        # high enough to lock garment/shot semantics, low enough to keep motion natural.
        "cfg_scale": 0.7,
    }

    if element_list:
        body["element_list"] = element_list
        logger.info("Kling Direct: using element_list=%s", element_list)

    logger.info(
        "Kling Direct: %d shots, total=%ss, aspect=%s, audio=%s, elements=%d",
        len(shots), total_dur, aspect_ratio, generate_audio, len(element_list or []),
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
            _err = resp.text; raise RuntimeError(f"Kling API HTTP {resp.status_code}: {_err[:300]}")
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
                _warn = resp.text; logger.warning("Kling poll HTTP %s: %s", resp.status_code, _warn[:200])
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
