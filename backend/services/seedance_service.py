"""Seedance 2.0 service (via KIE.ai marketplace).

Handles:
  - Single job creation (POST /api/v1/jobs/createTask)
  - Status polling (GET /api/v1/jobs/recordInfo?taskId=...)
  - Multi-shot chain: sequential jobs where shot N+1's first_frame_url is
    derived from shot N's generated video's last frame. (KIE returns the
    video URL; we extract the last frame with ffmpeg locally and re-upload
    it as the next shot's first_frame_url.)

Model: bytedance/seedance-2
Duration per shot: 4-15s
Reference images: max 9 (shared across all shots for char/garment consistency)
Aspect: 1:1 / 4:3 / 3:4 / 16:9 / 9:16 / 21:9 / adaptive
Resolution: 480p / 720p / 1080p
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

CREATE_URL = "/api/v1/jobs/createTask"
STATUS_URL = "/api/v1/jobs/recordInfo"
MODEL_NAME = "bytedance/seedance-2"

# Per-task poll ceiling — KIE usually returns success within ~3-6 min
POLL_MAX_SECONDS = 15 * 60
POLL_INTERVAL_SECONDS = 8


class SeedanceError(Exception):
    pass


def _headers() -> dict:
    if not settings.KIE_API_KEY:
        raise SeedanceError("KIE_API_KEY tanımlı değil.")
    return {
        "Authorization": f"Bearer {settings.KIE_API_KEY}",
        "Content-Type": "application/json",
    }


async def create_task(
    prompt: str,
    *,
    first_frame_url: Optional[str] = None,
    last_frame_url: Optional[str] = None,
    reference_image_urls: Optional[list[str]] = None,
    reference_video_urls: Optional[list[str]] = None,
    duration: int = 10,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
    generate_audio: bool = False,
) -> str:
    """Submit a Seedance 2.0 job. Returns the KIE taskId."""
    payload = {
        "model": MODEL_NAME,
        "input": {
            "prompt": prompt.strip(),
            "duration": max(4, min(15, int(duration))),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "generate_audio": bool(generate_audio),
            "web_search": False,
            "nsfw_checker": False,
        },
    }
    if first_frame_url:
        payload["input"]["first_frame_url"] = first_frame_url
    if last_frame_url:
        payload["input"]["last_frame_url"] = last_frame_url
    if reference_image_urls:
        payload["input"]["reference_image_urls"] = reference_image_urls[:9]
    if reference_video_urls:
        payload["input"]["reference_video_urls"] = reference_video_urls[:3]

    url = settings.KIE_BASE_URL.rstrip("/") + CREATE_URL
    logger.info("[seedance] createTask prompt=%r duration=%s aspect=%s res=%s imgs=%d vids=%d first_frame=%s",
                prompt[:80], duration, aspect_ratio, resolution,
                len(reference_image_urls or []), len(reference_video_urls or []),
                bool(first_frame_url))

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=_headers(), json=payload)

    if resp.status_code >= 400:
        raise SeedanceError(f"KIE createTask HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    if data.get("code") != 200:
        raise SeedanceError(f"KIE createTask error: {data.get('msg')} ({data.get('code')})")

    task_id = data.get("data", {}).get("taskId")
    if not task_id:
        raise SeedanceError(f"KIE createTask returned no taskId: {data}")
    return task_id


async def get_task_status(task_id: str) -> dict:
    """Query KIE for a task's current state. Returns a normalized dict:
        {state, progress, video_url, fail_code, fail_msg, raw}
    """
    url = settings.KIE_BASE_URL.rstrip("/") + STATUS_URL
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers(), params={"taskId": task_id})

    if resp.status_code >= 400:
        raise SeedanceError(f"KIE recordInfo HTTP {resp.status_code}: {resp.text[:500]}")

    body = resp.json()
    if body.get("code") != 200:
        raise SeedanceError(f"KIE recordInfo error: {body.get('msg')}")

    data = body.get("data") or {}
    state = data.get("state", "waiting")
    video_url = None
    result_json = data.get("resultJson")
    if result_json:
        try:
            parsed = json.loads(result_json) if isinstance(result_json, str) else result_json
            urls = parsed.get("resultUrls") or []
            if urls:
                video_url = urls[0]
        except Exception as e:  # noqa: BLE001
            logger.warning("[seedance] failed to parse resultJson: %s", e)

    return {
        "state": state,
        "progress": data.get("progress") or 0,
        "video_url": video_url,
        "fail_code": data.get("failCode") or "",
        "fail_msg": data.get("failMsg") or "",
        "raw": data,
    }


async def wait_for_task(task_id: str, *, on_progress=None) -> str:
    """Poll until task succeeds or fails. Returns the video URL on success."""
    start = time.time()
    last_progress = -1
    while True:
        info = await get_task_status(task_id)
        state = info["state"]
        progress = info.get("progress") or 0

        if on_progress and progress != last_progress:
            try:
                await on_progress(state, progress)
            except Exception:  # noqa: BLE001
                pass
            last_progress = progress

        if state == "success":
            if not info["video_url"]:
                raise SeedanceError(f"KIE task {task_id} succeeded but has no video URL.")
            return info["video_url"]
        if state == "fail":
            raise SeedanceError(
                f"KIE task {task_id} failed: {info['fail_code']} {info['fail_msg']}"
            )

        if time.time() - start > POLL_MAX_SECONDS:
            raise SeedanceError(f"KIE task {task_id} polling timed out after {POLL_MAX_SECONDS}s")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ─── Multi-shot frame extraction ─────────────────────────────────────

def _extract_last_frame_sync(video_path: str, out_path: str) -> None:
    """Use ffmpeg to extract the last frame of a video file."""
    cmd = [
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
        "-vframes", "1", "-q:v", "2", out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


async def extract_last_frame(video_url: str, upload_dir: str) -> str:
    """Download video, extract last frame as JPG. Returns local image path."""
    video_tmp = os.path.join(upload_dir, f"seedance_tmp_{uuid.uuid4().hex[:8]}.mp4")
    frame_out = os.path.join(upload_dir, f"seedance_lastframe_{uuid.uuid4().hex[:8]}.jpg")

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        r = await client.get(video_url)
        r.raise_for_status()
        with open(video_tmp, "wb") as f:
            f.write(r.content)

    try:
        await asyncio.to_thread(_extract_last_frame_sync, video_tmp, frame_out)
    finally:
        try:
            os.remove(video_tmp)
        except OSError:
            pass

    if not os.path.exists(frame_out) or os.path.getsize(frame_out) < 1024:
        raise SeedanceError("Son kare çıkarılamadı (ffmpeg sonucu boş).")
    return frame_out
