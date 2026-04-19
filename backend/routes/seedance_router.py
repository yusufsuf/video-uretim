"""Seedance 2.0 (KIE.ai) video generation — separate module from Studio/Defile.

Endpoints:
  POST /api/seedance/generate      — Start a generation job (single or chained shots)
  GET  /api/seedance/status/{id}   — Poll job progress

Flow:
  Frontend submits { prompt | shots[], start_frame_url, reference_image_urls[], duration, aspect, resolution }
  Backend:
    - Single shot   → one KIE createTask, await, done
    - Multi-shot N  → for each shot i:
                        first_frame_url = start_frame (i=0) OR last frame of previous video
                        submit, poll, extract last frame (if i < N-1)
                      Concatenate via ffmpeg, publish final video URL.
"""

import asyncio
import logging
import os
import subprocess
import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from dependencies import get_current_user
from limiter import limiter
from models import JobResponse, JobStatus
from pipeline import jobs, job_owners, _update_job
from services import seedance_service

router = APIRouter(prefix="/api/seedance")
logger = logging.getLogger(__name__)


# ─── Request models ────────────────────────────────────────────────

class SeedanceShot(BaseModel):
    prompt: str = Field(min_length=2, max_length=20000)
    duration: int = Field(default=10, ge=4, le=15)


class SeedanceGenerateRequest(BaseModel):
    shots: List[SeedanceShot] = Field(min_length=1, max_length=6)
    reference_image_urls: List[str] = Field(default_factory=list, max_length=9)
    start_frame_url: Optional[str] = None
    aspect_ratio: str = "9:16"
    resolution: str = "1080p"
    generate_audio: bool = False


# ─── Helpers ────────────────────────────────────────────────────────

def _file_to_url(path: str) -> str:
    base = settings.BASE_URL.rstrip("/")
    return f"{base}/uploads/{os.path.basename(path)}"


async def _download_to(upload_dir: str, url: str, ext: str = ".mp4") -> str:
    dest = os.path.join(upload_dir, f"seedance_{uuid.uuid4().hex[:10]}{ext}")
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
    return dest


def _concat_videos_sync(video_paths: list[str], out_path: str) -> None:
    """ffmpeg concat demuxer — lossless join of same-codec mp4 files."""
    list_file = out_path + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in video_paths:
            f.write(f"file '{p.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-c", "copy", out_path],
            check=True, capture_output=True,
        )
    finally:
        try:
            os.remove(list_file)
        except OSError:
            pass


# ─── Pipeline (runs in background) ──────────────────────────────────

async def _run_seedance_pipeline(
    job_id: str,
    body: SeedanceGenerateRequest,
):
    upload_dir = settings.UPLOAD_DIR
    try:
        _update_job(job_id, status=JobStatus.GENERATING_VIDEO, message="Seedance kuyruğa alındı.", progress=5)

        shot_video_urls: list[str] = []
        current_first_frame = body.start_frame_url  # None is fine for text-to-video

        for i, shot in enumerate(body.shots):
            _update_job(
                job_id,
                status=JobStatus.GENERATING_VIDEO,
                message=f"Shot {i+1}/{len(body.shots)} üretiliyor…",
                progress=int(10 + (i / len(body.shots)) * 80),
            )

            task_id = await seedance_service.create_task(
                prompt=shot.prompt,
                first_frame_url=current_first_frame,
                reference_image_urls=body.reference_image_urls or None,
                duration=shot.duration,
                aspect_ratio=body.aspect_ratio,
                resolution=body.resolution,
                generate_audio=body.generate_audio,
            )
            logger.info("[seedance %s] shot %d/%d submitted task_id=%s",
                        job_id, i + 1, len(body.shots), task_id)

            async def _on_prog(state, p, _i=i):
                _update_job(
                    job_id,
                    message=f"Shot {_i+1}/{len(body.shots)} — {state} ({p}%)",
                )

            video_url = await seedance_service.wait_for_task(task_id, on_progress=_on_prog)
            shot_video_urls.append(video_url)

            # Extract last frame for next shot (unless this is the last shot)
            if i < len(body.shots) - 1:
                _update_job(job_id, message=f"Shot {i+1} tamamlandı, son kare çıkarılıyor…")
                frame_path = await seedance_service.extract_last_frame(video_url, upload_dir)
                current_first_frame = _file_to_url(frame_path)

        # Single shot → final URL is the remote KIE URL
        if len(shot_video_urls) == 1:
            final_url = shot_video_urls[0]
        else:
            # Multi-shot → download each, concat locally, serve from /outputs
            _update_job(job_id, message="Shotlar birleştiriliyor…", progress=92)
            local_paths = []
            for u in shot_video_urls:
                local_paths.append(await _download_to(upload_dir, u, ext=".mp4"))

            out_name = f"seedance_{job_id}.mp4"
            out_path = os.path.join(settings.OUTPUT_DIR, out_name)
            await asyncio.to_thread(_concat_videos_sync, local_paths, out_path)

            # cleanup shot clips
            for p in local_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

            base = settings.BASE_URL.rstrip("/")
            final_url = f"{base}/outputs/{out_name}"

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            message="Seedance video hazır.",
            result_url=final_url,
            progress=100,
        )

    except seedance_service.SeedanceError as e:
        logger.error("[seedance %s] failed: %s", job_id, e)
        _update_job(job_id, status=JobStatus.FAILED, message=f"Seedance hata: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("[seedance %s] unexpected error", job_id)
        _update_job(job_id, status=JobStatus.FAILED, message=f"Beklenmedik hata: {e}")


# ─── Endpoints ──────────────────────────────────────────────────────

@router.post("/generate", response_model=JobResponse)
@limiter.limit("10/hour")
async def seedance_generate(
    request: Request,
    body: SeedanceGenerateRequest,
    _user: dict = Depends(get_current_user),
):
    """Start a Seedance 2.0 video generation job (single or multi-shot chain)."""
    if not settings.KIE_API_KEY:
        raise HTTPException(status_code=503, detail="Seedance devre dışı: KIE_API_KEY tanımlı değil.")

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Seedance işi oluşturuluyor…",
    )
    job_owners[job_id] = _user["id"]

    asyncio.create_task(_run_seedance_pipeline(job_id, body))
    return jobs[job_id]


@router.get("/status/{job_id}", response_model=JobResponse)
@limiter.limit("600/hour")
async def seedance_status(request: Request, job_id: str, _user: dict = Depends(get_current_user)):
    if job_id not in jobs:
        return JobResponse(job_id=job_id, status=JobStatus.FAILED, message="İş bulunamadı.")
    is_admin = _user.get("role") == "admin"
    owner = job_owners.get(job_id)
    if not is_admin and owner and owner != _user["id"]:
        raise HTTPException(status_code=403, detail="Bu işe erişim yetkiniz yok.")
    return jobs[job_id]
