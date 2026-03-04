"""Fashion Video Automation – FastAPI Application

Endpoints:
  POST /api/generate      – Upload images & start the pipeline
  GET  /api/status/{id}   – Poll job progress
  GET  /outputs/{file}    – Serve generated videos
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from models import GenerationRequest, JobResponse, JobStatus, LocationPreset
from pipeline import jobs, run_pipeline, _load_history

# ─── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
)
logger = logging.getLogger(__name__)

# ─── App ───────────────────────────────────────────────────────────
app = FastAPI(
    title="Fashion Video Automation",
    description="Elbise fotoğraflarından profesyonel moda videoları üretin.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated outputs
app.mount("/outputs", StaticFiles(directory=settings.OUTPUT_DIR), name="outputs")

# Serve uploaded files (so Claid can fetch them via public URL)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Serve model assets (full-body model photos for Claid)
_assets_dir = os.path.join(os.path.dirname(__file__), "assets")
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


# ─── Helpers ───────────────────────────────────────────────────────
async def _save_upload(upload: UploadFile) -> str:
    """Save an uploaded file and return its local path."""
    ext = os.path.splitext(upload.filename or "img.jpg")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, filename)
    content = await upload.read()
    with open(path, "wb") as f:
        f.write(content)
    return path


def _file_to_url(path: str) -> str:
    """Convert a local file path to a public URL that external APIs can fetch.

    Uses BASE_URL + /uploads/filename so Claid and other services can
    download the file via HTTP instead of receiving a huge data URI.
    """
    filename = os.path.basename(path)
    base = settings.BASE_URL.rstrip("/")
    return f"{base}/uploads/{filename}"


# ─── Endpoints ─────────────────────────────────────────────────────
@app.post("/api/generate", response_model=JobResponse)
async def generate_video_endpoint(
    front_image: UploadFile = File(..., description="Elbise ön fotoğrafı"),
    back_image: Optional[UploadFile] = File(None, description="Elbise arka fotoğrafı (opsiyonel)"),
    reference_image: Optional[UploadFile] = File(None, description="Referans stil/poz resmi (opsiyonel)"),
    reference_video: Optional[UploadFile] = File(None, description="Referans hareket videosu (opsiyonel)"),
    location: str = Form("studio"),
    model_preset: str = Form("default"),
    custom_location: Optional[str] = Form(None),
    camera_style: Optional[str] = Form(None),
    model_action: Optional[str] = Form(None),
    mood: Optional[str] = Form(None),
    duration: str = Form("10"),
    scene_count: str = Form("2"),
    aspect_ratio: str = Form("9:16"),
    video_description: Optional[str] = Form(None),
    watermark_image: Optional[UploadFile] = File(None, description="Watermark/logo PNG"),
):
    """Start a new fashion video generation job."""

    # Save uploads
    front_path = await _save_upload(front_image)
    back_path = await _save_upload(back_image) if back_image else None

    reference_image_url = None
    reference_image_path = None
    if reference_image:
        reference_image_path = await _save_upload(reference_image)
        reference_image_url = _file_to_url(reference_image_path)

    reference_video_url = None
    if reference_video:
        ref_path = await _save_upload(reference_video)
        reference_video_url = _file_to_url(ref_path)

    # Convert local files to URLs for external APIs
    front_url = _file_to_url(front_path)
    back_url = _file_to_url(back_path) if back_path else None

    # Create job
    job_id = uuid.uuid4().hex[:12]
    request = GenerationRequest(
        location=LocationPreset(location),
        custom_location=custom_location,
        camera_style=camera_style,
        model_action=model_action,
        mood=mood,
    )

    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="İş kuyruğa alındı.",
    )

    # Launch pipeline in background
    asyncio.create_task(
        run_pipeline(
            job_id=job_id,
            front_path=front_path,
            back_path=back_path,
            reference_image_url=reference_image_url,
            reference_image_path=reference_image_path,
            reference_video_url=reference_video_url,
            request=request,
            front_url=front_url,
            back_url=back_url,
            duration=int(duration),
            scene_count=int(scene_count),
            video_description=video_description,
            model_preset=model_preset,
            aspect_ratio=aspect_ratio,
            watermark_path=await _save_upload(watermark_image) if watermark_image else None,
        )
    )

    return jobs[job_id]


@app.get("/api/status/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """Poll the status of a generation job."""
    if job_id not in jobs:
        return JobResponse(
            job_id=job_id,
            status=JobStatus.FAILED,
            message="Job bulunamadı.",
        )
    return jobs[job_id]


@app.get("/api/gallery")
async def get_gallery():
    """Return job history for the gallery page."""
    history = _load_history()
    return {"items": history}


@app.get("/gallery")
async def gallery_page():
    """Serve the gallery page."""
    gallery_path = os.path.join(_get_frontend_dir(), "gallery.html")
    return FileResponse(gallery_path, media_type="text/html")


@app.get("/")
async def root():
    """Serve the frontend index.html."""
    index_path = os.path.join(_get_frontend_dir(), "index.html")
    return FileResponse(index_path, media_type="text/html")


def _get_frontend_dir() -> str:
    """Resolve the frontend directory – works in both Docker and local dev."""
    # Docker: frontend is at /app/frontend
    docker_path = os.path.join(os.path.dirname(__file__), "frontend")
    if os.path.isdir(docker_path):
        return docker_path
    # Local dev: frontend is at ../frontend
    local_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
    if os.path.isdir(local_path):
        return local_path
    raise RuntimeError(f"Frontend directory not found at {docker_path} or {local_path}")


# Serve frontend static files (CSS, JS) – must be LAST mount
app.mount("/", StaticFiles(directory=_get_frontend_dir()), name="frontend")
