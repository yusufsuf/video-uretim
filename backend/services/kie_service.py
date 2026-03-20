"""kie.ai Kling 3.0 video generation service.

API docs: https://api.kie.ai/api/v1
- POST /jobs/createTask  — submit a video generation task
- GET  /jobs/getTaskDetail?taskId=xxx — poll for status/result
"""

import asyncio
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

KIE_BASE = "https://api.kie.ai/api/v1"
_POLL_INTERVAL = 10   # seconds between polls
_MAX_WAIT      = 600  # seconds before timeout


async def generate_kie_video(
    start_image_url: str,
    multi_prompt: list,   # [{"prompt": str, "duration": int}, ...]
    kling_elements: list, # [{"name": str, "description": str, "element_input_urls": [str, ...]}, ...]
    aspect_ratio: str = "9:16",
    generate_audio: bool = False,
) -> str:
    """Submit a Kling 3.0 multi-shot job to kie.ai and return the video URL."""

    total_duration = sum(int(p["duration"]) for p in multi_prompt)
    # kie.ai top-level duration must be 3-15; use clamped sum
    top_duration = max(3, min(total_duration, 15))

    payload: dict = {
        "model": "kling-3.0/video",
        "input": {
            "multi_shots": True,
            "image_urls": [start_image_url],
            "duration": str(top_duration),
            "aspect_ratio": aspect_ratio,
            "mode": "pro",
            "sound": generate_audio,
            "multi_prompt": multi_prompt,
            "kling_elements": kling_elements,
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.KIE_API_KEY}",
        "Content-Type": "application/json",
    }

    logger.info("Kie.ai: submitting task — %d shots, %ds, ratio=%s, elements=%d",
                len(multi_prompt), total_duration, aspect_ratio, len(kling_elements))

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{KIE_BASE}/jobs/createTask", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Kie.ai createTask full response: %s", data)

    task_data = data.get("data") or {}
    task_id: str = (
        task_data.get("taskId")
        or task_data.get("task_id")
        or task_data.get("id")
        or data.get("taskId")
        or data.get("task_id")
    )
    if not task_id:
        raise RuntimeError(f"Kie.ai createTask: no taskId in response: {data}")

    logger.info("Kie.ai task created: %s", task_id)
    return await _poll_task(task_id)


async def _poll_task(task_id: str) -> str:
    """Poll kie.ai until task completes; return video URL."""
    headers = {"Authorization": f"Bearer {settings.KIE_API_KEY}"}
    attempts = _MAX_WAIT // _POLL_INTERVAL

    for i in range(attempts):
        await asyncio.sleep(_POLL_INTERVAL)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{KIE_BASE}/jobs/getTaskDetail/{task_id}",
                headers=headers,
            )
            logger.info("Kie.ai poll status: %s, url: %s", resp.status_code, resp.url)
            resp.raise_for_status()
            data = resp.json()
        logger.info("Kie.ai poll response: %s", data)

        task_data = data.get("data") or {}
        status: str = (task_data.get("status") or "").lower()
        logger.info("Kie.ai task %s [%d/%d]: %s", task_id, i + 1, attempts, status)

        if status in ("succeed", "success", "completed", "finished"):
            video_url = _extract_video_url(task_data)
            if video_url:
                logger.info("Kie.ai task %s done: %s", task_id, video_url[:80])
                return video_url
            # Log full response for debugging unknown response shapes
            logger.error("Kie.ai task complete but no video URL found. Full response: %s", data)
            raise RuntimeError(f"Kie.ai task completed but no video URL in response: {data}")

        if status in ("failed", "error", "cancelled"):
            logger.error("Kie.ai task %s failed: %s", task_id, data)
            raise RuntimeError(f"Kie.ai task failed (status={status}): {task_data.get('message', '')}")

    raise RuntimeError(f"Kie.ai task {task_id} timed out after {_MAX_WAIT}s")


def _extract_video_url(task_data: dict) -> str | None:
    """Try known response shapes to find the video URL."""
    # Shape 1: task_data.output.video_url
    output = task_data.get("output") or {}
    if isinstance(output, dict):
        for key in ("video_url", "url", "video"):
            val = output.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
            if isinstance(val, dict):
                inner = val.get("url") or val.get("video_url")
                if isinstance(inner, str) and inner.startswith("http"):
                    return inner

    # Shape 2: task_data.result.video_url
    result = task_data.get("result") or {}
    if isinstance(result, dict):
        for key in ("video_url", "url"):
            val = result.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val

    # Shape 3: task_data.videos[0]
    videos = task_data.get("videos") or []
    if videos and isinstance(videos[0], dict):
        return videos[0].get("url") or videos[0].get("video_url")

    return None
