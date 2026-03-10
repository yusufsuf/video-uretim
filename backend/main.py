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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import json

from config import settings
from dependencies import get_current_user
from limiter import limiter
from models import DefileCollectionRequest, GenerationRequest, JobResponse, JobStatus, LocationPreset, RefineShotRequest, ShotConfig, SuggestShotsRequest
from pipeline import jobs, run_pipeline, run_defile_collection_pipeline, _load_history
from services.analysis_service import refine_shot_description, suggest_shot_descriptions
from pydantic import BaseModel

from routes.auth_router import router as auth_router
from routes.admin_router import router as admin_router
from routes.library_router import router as library_router
from services.order_service import save_order, lookup_order

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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth")
app.include_router(admin_router, prefix="/admin")
app.include_router(library_router, prefix="/library")

# Serve generated outputs
app.mount("/outputs", StaticFiles(directory=settings.OUTPUT_DIR), name="outputs")

# Serve uploaded files (so fal.ai can fetch them via public URL)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Serve model/asset files
_assets_dir = os.path.join(os.path.dirname(__file__), "assets")
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


# ─── Helpers ───────────────────────────────────────────────────────
MAX_IMAGE_BYTES = 9 * 1024 * 1024  # 9 MB target (Kling limit is 10 MB)


def _optimize_image(path: str) -> str:
    """Ensure an image file is under MAX_IMAGE_BYTES.

    Strategy:
    1. If already under limit → return as-is
    2. Try reducing JPEG quality progressively (95 → 80 → 65 → 50)
    3. If still too large, scale dimensions down by 25% and retry
    Saves the optimized file as JPEG, returns the (possibly new) path.
    """
    from PIL import Image

    file_size = os.path.getsize(path)
    if file_size <= MAX_IMAGE_BYTES:
        return path  # Already within limits

    logger.info("Optimizing image %s (%d bytes → target <%d)", path, file_size, MAX_IMAGE_BYTES)

    img = Image.open(path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Build optimized output path (always .jpg)
    base = os.path.splitext(path)[0]
    out_path = base + "_opt.jpg"

    # Try progressively lower quality
    for quality in [92, 85, 75, 65, 50]:
        img.save(out_path, "JPEG", quality=quality, optimize=True)
        if os.path.getsize(out_path) <= MAX_IMAGE_BYTES:
            logger.info("Optimized to %d bytes (quality=%d)", os.path.getsize(out_path), quality)
            os.replace(out_path, path.rsplit(".", 1)[0] + ".jpg")
            return path.rsplit(".", 1)[0] + ".jpg"

    # Quality alone not enough — scale down dimensions progressively
    for scale in [0.75, 0.5, 0.35, 0.25]:
        w, h = img.size
        new_w, new_h = int(w * scale), int(h * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        resized.save(out_path, "JPEG", quality=80, optimize=True)
        if os.path.getsize(out_path) <= MAX_IMAGE_BYTES:
            logger.info("Optimized to %d bytes (scale=%.0f%%, %dx%d)", os.path.getsize(out_path), scale * 100, new_w, new_h)
            final_path = path.rsplit(".", 1)[0] + ".jpg"
            os.replace(out_path, final_path)
            return final_path

    # Last resort — return whatever we have
    final_path = path.rsplit(".", 1)[0] + ".jpg"
    os.replace(out_path, final_path)
    logger.warning("Could not optimize %s below %d bytes, final size: %d", path, MAX_IMAGE_BYTES, os.path.getsize(final_path))
    return final_path


async def _save_upload(upload: UploadFile) -> str:
    """Save an uploaded file, optimize images to stay under Kling's 10MB limit."""
    ext = os.path.splitext(upload.filename or "img.jpg")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, filename)
    content = await upload.read()
    with open(path, "wb") as f:
        f.write(content)

    # Optimize images (skip video files)
    if ext.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
        path = _optimize_image(path)

    return path


def _file_to_url(path: str) -> str:
    """Convert a local file path to a public URL that external APIs can fetch.

    Uses BASE_URL + /uploads/filename so fal.ai and other services can
    download the file via HTTP instead of receiving a huge data URI.
    """
    filename = os.path.basename(path)
    base = settings.BASE_URL.rstrip("/")
    return f"{base}/uploads/{filename}"


# ─── Endpoints ─────────────────────────────────────────────────────
@app.post("/api/generate", response_model=JobResponse)
@limiter.limit("10/hour")
async def generate_video_endpoint(
    request: Request,
    _user: dict = Depends(get_current_user),
    front_image: Optional[UploadFile] = File(None, description="Elbise ön fotoğrafı"),
    side_image: Optional[UploadFile] = File(None, description="Elbise yan fotoğrafı (opsiyonel)"),
    back_image: Optional[UploadFile] = File(None, description="Elbise arka fotoğrafı (opsiyonel)"),
    reference_image: Optional[UploadFile] = File(None, description="Referans stil/poz resmi (opsiyonel)"),
    reference_video: Optional[UploadFile] = File(None, description="Referans hareket videosu (opsiyonel)"),
    location: str = Form("studio"),
    custom_location: Optional[str] = Form(None),
    mood: Optional[str] = Form(None),
    generate_audio: str = Form("true"),
    duration: str = Form("10"),
    scene_count: str = Form("2"),
    aspect_ratio: str = Form("9:16"),
    video_description: Optional[str] = Form(None),
    shots: Optional[str] = Form(None),
    library_front_url: Optional[str] = Form(None),
    library_side_url: Optional[str] = Form(None),
    library_back_url: Optional[str] = Form(None),
    library_background_url: Optional[str] = Form(None),
    library_background_extra_urls: Optional[str] = Form(None),
    library_style_url: Optional[str] = Form(None),
    watermark_image: Optional[UploadFile] = File(None, description="Watermark/logo PNG"),
    generation_mode: str = Form("classic"),
):
    """Start a new fashion video generation job."""

    # Parse shots JSON from frontend multishot designer
    shots_list: Optional[list] = None
    if shots:
        try:
            raw = json.loads(shots)
            shots_list = [ShotConfig(**s) for s in raw]
        except Exception:
            shots_list = None

    # When shots are provided, derive duration and scene_count from them
    effective_duration = sum(s.duration for s in shots_list) if shots_list else int(duration)
    effective_scene_count = len(shots_list) if shots_list else int(scene_count)

    # Save uploads — library URLs bypass local file handling
    if front_image and not library_front_url:
        front_path = await _save_upload(front_image)
        front_url = _file_to_url(front_path)
    elif library_front_url:
        front_path = library_front_url   # URL passed directly to analysis / Kling
        front_url = library_front_url
    else:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=400, detail="Ön fotoğraf zorunludur.")

    side_path = None
    side_url = None
    if side_image:
        side_path = await _save_upload(side_image)
        side_url = _file_to_url(side_path)
    elif library_side_url:
        side_path = library_side_url
        side_url = library_side_url

    back_path = None
    back_url = None
    if back_image:
        back_path = await _save_upload(back_image)
        back_url = _file_to_url(back_path)
    elif library_back_url:
        back_path = library_back_url
        back_url = library_back_url

    reference_image_path = None
    reference_image_url = None
    if reference_image:
        reference_image_path = await _save_upload(reference_image)
        reference_image_url = _file_to_url(reference_image_path)
    elif library_background_url:
        # Library background bypasses Nano Banana — use directly
        reference_image_url = library_background_url

    # Parse extra background URLs (for per-shot cycling)
    bg_extra_urls: list = []
    if library_background_extra_urls:
        try:
            bg_extra_urls = json.loads(library_background_extra_urls)
        except Exception:
            bg_extra_urls = []

    # Create job
    job_id = uuid.uuid4().hex[:12]
    request = GenerationRequest(
        location=LocationPreset(location),
        custom_location=custom_location,
        mood=mood,
        generate_audio=generate_audio.lower() == "true",
        shots=shots_list,
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
            side_path=side_path,
            reference_image_path=reference_image_path,
            reference_image_url=reference_image_url,
            request=request,
            front_url=front_url,
            side_url=side_url,
            back_url=back_url,
            duration=effective_duration,
            scene_count=effective_scene_count,
            video_description=video_description,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio.lower() == "true",
            library_style_url=library_style_url or None,
            background_extra_urls=bg_extra_urls or None,
            watermark_path=await _save_upload(watermark_image) if watermark_image else None,
            generation_mode=generation_mode,
        )
    )

    return jobs[job_id]


@app.post("/api/suggest-shots")
async def suggest_shots_endpoint(
    request: SuggestShotsRequest,
    _user: dict = Depends(get_current_user),
):
    """Return AI-generated cinematic descriptions for each shot."""
    descriptions = await suggest_shot_descriptions(request)
    return {"descriptions": descriptions}


@app.post("/api/refine-shot")
async def refine_shot_endpoint(
    request: RefineShotRequest,
    _user: dict = Depends(get_current_user),
):
    """Convert a casual user description into a cinematic shot prompt."""
    description = await refine_shot_description(request)
    return {"description": description}


@app.post("/api/defile/collection", response_model=JobResponse)
@limiter.limit("10/hour")
async def defile_collection_endpoint(
    request: Request,
    body: DefileCollectionRequest,
    _user: dict = Depends(get_current_user),
):
    """Start a defile collection video generation job."""
    if not body.outfits:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="En az bir kıyafet gereklidir.")

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Defile kuyruğa alındı.",
    )

    asyncio.create_task(run_defile_collection_pipeline(job_id=job_id, request=body))
    return jobs[job_id]


@app.get("/api/status/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str, _user: dict = Depends(get_current_user)):
    """Poll the status of a generation job."""
    if job_id not in jobs:
        return JobResponse(
            job_id=job_id,
            status=JobStatus.FAILED,
            message="Job bulunamadı.",
        )
    return jobs[job_id]


@app.get("/api/gallery")
async def get_gallery(_user: dict = Depends(get_current_user)):
    """Return job history for the gallery page."""
    history = _load_history()
    return {"items": history}


@app.get("/gallery")
async def gallery_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "gallery.html"), media_type="text/html")


@app.get("/library")
async def library_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "library.html"), media_type="text/html")


@app.get("/defile")
async def defile_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "index.html"), media_type="text/html")


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "login.html"), media_type="text/html")


@app.get("/register")
async def register_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "register.html"), media_type="text/html")


@app.get("/order")
async def order_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "order-preview.html"), media_type="text/html")


# ─── Order code endpoints ───────────────────────────────────────────

class OrderCreate(BaseModel):
    code: str
    shot_configs: list


@app.post("/api/orders")
async def create_order(body: OrderCreate):
    await save_order(body.code, body.shot_configs)
    return {"ok": True}


@app.get("/api/orders/{code}")
async def get_order(code: str):
    row = await lookup_order(code)
    if not row:
        raise HTTPException(status_code=404, detail="Kod bulunamadı.")
    return {"shot_configs": row["shot_configs"]}


@app.get("/admin-panel")
async def admin_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "admin.html"), media_type="text/html")


@app.get("/")
async def root():
    return FileResponse(os.path.join(_get_frontend_dir(), "index.html"), media_type="text/html")


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
