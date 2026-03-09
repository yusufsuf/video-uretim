"""Video Service – Kling 3.0 Pro via kie.ai API.

Generates fashion videos with multishot prompts and garment elements.
"""

import asyncio
import json as _json
import logging
import os
import subprocess
import uuid
from typing import List, Optional

import fal_client
import httpx

from config import settings

logger = logging.getLogger(__name__)

# Ensure FAL_KEY is set as environment variable for the fal-client SDK (used by NB2)
os.environ["FAL_KEY"] = settings.FAL_KEY

_KIE_CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
_KIE_POLL_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"


async def generate_multishot_video(
    start_image_url: str,
    multi_prompt: List[dict],
    elements: Optional[List[dict]] = None,
    duration: str = "10",
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    negative_prompt: str = "blur, distort, and low quality, deformed hands, deformed face",
) -> str:
    """Generate a fashion video using Kling 3.0 Pro multishot via kie.ai API.

    Args:
        start_image_url: URL of the start frame (NB2 scene composition output).
        multi_prompt: List of shot dicts, each with 'duration' (str/int) and 'prompt' (str).
        elements: List of element dicts for garment consistency.
                  Each dict: { frontal_image_url: str, reference_image_urls: [str] }
        duration: Total video duration in seconds (str, "3"-"15").
        aspect_ratio: "16:9", "9:16", or "1:1".
        generate_audio: Whether to generate audio.
        negative_prompt: Unused (kie.ai does not support this param — kept for API compat).

    Returns:
        URL of the generated video.
    """
    logger.info("Starting Kling 3.0 Pro multishot via kie.ai – %d shot(s), %ss",
                len(multi_prompt), duration)

    # ── Build kling_elements from fal.ai-style elements ──────────────────
    kling_elements: list = []
    has_elements = False
    if elements:
        all_urls: list = []
        for elem in elements:
            if elem.get("frontal_image_url"):
                all_urls.append(elem["frontal_image_url"])
            for ref in elem.get("reference_image_urls", []):
                all_urls.append(ref)
        if all_urls:
            has_elements = True
            # kie.ai requires minimum 2 images in element_input_urls
            if len(all_urls) == 1:
                all_urls = all_urls * 2
            capped_urls = [u for i, u in enumerate(all_urls) if i < 50]
            kling_elements.append({
                "name": "garment",
                "description": "fashion garment reference images",
                "element_input_urls": capped_urls,
            })

    # ── Build multi_prompt — append @garment tag if elements present ──────
    kie_multi_prompt = []
    for shot in multi_prompt:
        prompt_text = shot.get("prompt", "")
        if has_elements:
            prompt_text = f"{prompt_text} @garment"
        kie_multi_prompt.append({
            "prompt": prompt_text,
            "duration": int(shot.get("duration", 5)),
        })

    input_data: dict = {
        "image_urls": [start_image_url],
        "multi_shots": True,
        "multi_prompt": kie_multi_prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "mode": "pro",
        "sound": "on",  # kie.ai requires string "on" when multi_shots is true
    }
    if kling_elements:
        input_data["kling_elements"] = kling_elements

    payload = {"model": "kling-3.0/video", "input": input_data}
    headers = {
        "Authorization": f"Bearer {settings.KIE_API_KEY}",
        "Content-Type": "application/json",
    }

    logger.info("  kie.ai payload: shots=%d, duration=%s, aspect=%s, audio=%s, elements=%d",
                len(kie_multi_prompt), duration, aspect_ratio, generate_audio, len(kling_elements))

    # ── Create task (retry up to 3x on 5xx server errors) ────────────────
    data = None
    for _attempt in range(3):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_KIE_CREATE_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        code = data.get("code")
        if code == 200:
            break
        if isinstance(code, int) and code >= 500:
            logger.warning("kie.ai createTask server error (code=%s), retrying in 10s…", code)
            await asyncio.sleep(10)
            continue
        # 4xx or other non-retryable error
        raise RuntimeError(f"kie.ai createTask failed: {data.get('msg')} (code={code})")
    else:
        raise RuntimeError(f"kie.ai createTask failed after 3 attempts: {data.get('msg') if data else 'no response'}")

    task_id = data["data"]["taskId"]
    logger.info("kie.ai task created: %s", task_id)

    # ── Poll until complete (up to ~12 minutes) ───────────────────────────
    for attempt in range(120):
        await asyncio.sleep(6)
        async with httpx.AsyncClient(timeout=30) as client:
            poll_resp = await client.get(
                _KIE_POLL_URL, params={"taskId": task_id}, headers=headers
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()

        task = poll_data.get("data", {})
        state = task.get("state", "")
        logger.info("kie.ai %s → %s (attempt %d)", task_id, state, attempt + 1)

        if state == "success":
            result_json = _json.loads(task.get("resultJson", "{}"))
            result_urls = result_json.get("resultUrls", [])
            if not result_urls:
                raise RuntimeError(f"kie.ai task {task_id} succeeded but resultUrls is empty")
            video_url = result_urls[0]
            logger.info("Kling 3.0 video completed: %s", video_url[:100])
            return video_url

        if state == "fail":
            fail_msg = task.get("failMsg", "unknown error")
            raise RuntimeError(f"kie.ai task {task_id} failed: {fail_msg}")

        # waiting / queuing / generating → keep polling

    raise RuntimeError(f"kie.ai task {task_id} timed out after 120 polling attempts (~12 min)")


# ─── Last-frame chaining helpers ─────────────────────────────────

def extract_last_frame(video_path: str, output_dir: str) -> str:
    """Extract the last frame of a video as a PNG file using FFmpeg."""
    frame_path = os.path.join(output_dir, f"{uuid.uuid4().hex}_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.5", "-i", video_path,
         "-vframes", "1", "-q:v", "2", frame_path],
        check=True, capture_output=True, timeout=30,
    )
    logger.info("Extracted last frame: %s", frame_path)
    return frame_path


async def upload_to_fal(file_path: str) -> str:
    """Upload a local file to fal.ai CDN and return the public URL."""
    url = await asyncio.to_thread(fal_client.upload_file, file_path)
    logger.info("Uploaded to fal.ai: %s", url[:80])
    return url


def concatenate_clips(clip_paths: list, output_path: str) -> str:
    """Concatenate video clips in order using FFmpeg concat demuxer."""
    list_file = output_path.replace(".mp4", "_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", output_path],
        check=True, capture_output=True, timeout=300,
    )
    os.remove(list_file)
    logger.info("Concatenated %d clips → %s", len(clip_paths), output_path)
    return output_path


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

    logger.info("Downloaded %s -> %s", url, filepath)
    return filepath
