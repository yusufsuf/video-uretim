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
                f"{KIE_BASE}/jobs/recordInfo",
                params={"taskId": task_id},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        task_data = data.get("data") or {}
        state: str = (task_data.get("state") or "").lower()
        logger.info("Kie.ai task %s [%d/%d]: state=%s", task_id, i + 1, attempts, state)

        if state == "success":
            video_url = _extract_video_url(task_data)
            if video_url:
                logger.info("Kie.ai task %s done: %s", task_id, str(video_url)[:80])  # type: ignore[index]
                return video_url
            logger.error("Kie.ai task complete but no video URL. Full response: %s", data)
            raise RuntimeError(f"Kie.ai task completed but no video URL in response: {data}")

        if state == "fail":
            logger.error("Kie.ai task %s failed: %s", task_id, data)
            raise RuntimeError(f"Kie.ai task failed: {task_data.get('failMsg', '')}")

    raise RuntimeError(f"Kie.ai task {task_id} timed out after {_MAX_WAIT}s")


def _extract_video_url(task_data: dict) -> str | None:
    """Extract video URL from kie.ai task response.

    kie.ai returns: resultJson = '{"resultUrls":["https://...mp4"]}'
    """
    import json as _json

    # Primary: resultJson string → parse → resultUrls[0]
    result_json_str = task_data.get("resultJson")
    if result_json_str and isinstance(result_json_str, str):
        try:
            parsed = _json.loads(result_json_str)
            urls = parsed.get("resultUrls") or []
            if urls and isinstance(urls[0], str) and urls[0].startswith("http"):
                return urls[0]
        except Exception:
            pass

    # Fallback: direct url fields
    for key in ("video_url", "url", "videoUrl"):
        val = task_data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val

    return None
