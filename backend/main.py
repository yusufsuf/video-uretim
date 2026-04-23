"""Fashion Video Automation – FastAPI Application

Endpoints:
  POST /api/generate            – Studio mode: start video generation pipeline
  POST /api/defile/collection   – Defile mode: start collection pipeline
  GET  /api/status/{id}         – Poll job progress
  GET  /outputs/{file}          – Serve generated videos
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
from models import DefileCollectionRequest, GenerationRequest, JobResponse, JobStatus, ShotConfig
from pipeline import jobs, job_owners, run_pipeline, run_defile_collection_pipeline, _load_history
from pydantic import BaseModel, Field

from routes.auth_router import router as auth_router
from routes.admin_router import router as admin_router
from routes.library_router import router as library_router
from routes.workflow_router import router as workflow_router
from routes.whatsapp_router import router as whatsapp_router
from routes.seedance_router import router as seedance_router
from routes.kling_prompt_router import router as kling_prompt_router
from routes.photo_montage_router import router as photo_montage_router
from routes.seedance_prompt_router import router as seedance_prompt_router
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
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/auth")
app.include_router(admin_router, prefix="/admin")
app.include_router(library_router, prefix="/library")
app.include_router(workflow_router, prefix="/api/workflow")
app.include_router(whatsapp_router)
app.include_router(seedance_router)
app.include_router(kling_prompt_router)
app.include_router(photo_montage_router)
app.include_router(seedance_prompt_router)

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


_ALLOWED_UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".mp4", ".mov", ".webm"}
_ALLOWED_UPLOAD_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff",
    "video/mp4", "video/quicktime", "video/webm",
}
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB hard cap (images optimized down after)


async def _save_upload(upload: UploadFile) -> str:
    """Save an uploaded file, optimize images to stay under Kling's 10MB limit."""
    ext = os.path.splitext(upload.filename or "img.jpg")[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Desteklenmeyen dosya türü: {ext}")
    if upload.content_type and upload.content_type not in _ALLOWED_UPLOAD_MIMES:
        raise HTTPException(status_code=400, detail=f"Desteklenmeyen MIME: {upload.content_type}")

    content = await upload.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Dosya boyutu 50 MB sınırını aşıyor.")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Boş dosya.")

    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(content)

    # Optimize images (skip video files)
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
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
    generate_audio: str = Form("false"),  # Fashion: sessiz video default
    duration: str = Form("10"),
    scene_count: str = Form("2"),
    aspect_ratio: str = Form("9:16"),
    video_description: Optional[str] = Form(None),
    custom_scene_count: Optional[int] = Form(None),
    custom_total_duration: Optional[int] = Form(None),
    shots: Optional[str] = Form(None),
    library_front_url: Optional[str] = Form(None),
    library_side_url: Optional[str] = Form(None),
    library_back_url: Optional[str] = Form(None),
    elements_json: Optional[str] = Form(None),  # JSON array of {front_url, extra_urls, name}
    library_background_url: Optional[str] = Form(None),
    library_background_extra_urls: Optional[str] = Form(None),
    library_style_url: Optional[str] = Form(None),
    watermark_image: Optional[UploadFile] = File(None, description="Watermark/logo PNG"),
    generation_mode: str = Form("classic"),
    ozel_start_frame: Optional[UploadFile] = File(None, description="Özel mod başlangıç karesi"),
    provider: str = Form("fal"),  # "fal" = fal.ai | "kling" = Kling Direct
    kling_model: str = Form("kling-v3"),  # "kling-v3" | "kling-v3-omni"
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

    reference_video_url = None
    if reference_video:
        ref_video_path = await _save_upload(reference_video)
        reference_video_url = _file_to_url(ref_video_path)

    start_frame_url = None
    if ozel_start_frame:
        sf_path = await _save_upload(ozel_start_frame)
        start_frame_url = _file_to_url(sf_path)

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
        generate_audio=generate_audio.lower() == "true",
        shots=shots_list,
    )

    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="İş kuyruğa alındı.",
    )
    job_owners[job_id] = _user["id"]

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
            custom_scene_count=custom_scene_count,
            custom_total_duration=custom_total_duration,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio.lower() == "true",
            library_style_url=library_style_url or None,
            background_extra_urls=bg_extra_urls or None,
            watermark_path=await _save_upload(watermark_image) if watermark_image else None,
            generation_mode=generation_mode,
            reference_video_url=reference_video_url,
            start_frame_url=start_frame_url,
            elements_json=elements_json or None,
            provider=provider,
            kling_model=kling_model,
        )
    )

    return jobs[job_id]


@app.post("/api/studio/ai-shots")
async def studio_ai_shots_endpoint(
    request: Request,
    _user: dict = Depends(get_current_user),
    element_image: Optional[UploadFile] = File(None),
    element_image_url: Optional[str] = Form(None),
    elements_json: Optional[str] = Form(None),  # JSON array of {name, front_url, extra_urls}
    start_frame: Optional[UploadFile] = File(None),
    shot_count: int = Form(2),
    user_hint: Optional[str] = Form(None),
):
    """Stüdyo modu için AI çekim açıklamaları üretir."""
    import json as _json
    from services.analysis_service import generate_studio_ai_shots

    # Parse elements if provided (multi-element)
    elem_names: list = []
    elem_image_urls: list = []
    if elements_json:
        try:
            _elems = _json.loads(elements_json)
            for _e in _elems:
                elem_names.append(f"@{_e.get('name', 'Element')}")
                elem_image_urls.append(_e.get("front_url", ""))
        except Exception:
            pass

    # Fallback to single element
    if not elem_image_urls:
        if element_image and element_image.filename:
            elem_path = await _save_upload(element_image)
            elem_image_urls = [_file_to_url(elem_path)]
        elif element_image_url:
            elem_image_urls = [element_image_url]
        else:
            raise HTTPException(status_code=400, detail="Element görseli zorunludur.")
        if not elem_names:
            elem_names = ["@Element1"]

    sf_url = None
    if start_frame and start_frame.filename:
        sf_path = await _save_upload(start_frame)
        sf_url = _file_to_url(sf_path)

    shots = await generate_studio_ai_shots(
        element_image_url=elem_image_urls[0],
        start_frame_url=sf_url,
        shot_count=min(5, max(1, shot_count)),
        user_hint=user_hint,
        element_names=elem_names,
        element_image_urls=elem_image_urls,
    )
    return {"shots": shots}


class ParseScenarioRequest(BaseModel):
    text: str
    shot_count: int = 4
    total_duration: int = 15


@app.post("/api/studio/parse-scenario")
async def parse_studio_scenario_endpoint(
    body: ParseScenarioRequest,
    _user: dict = Depends(get_current_user),
):
    """Parse a free-form scenario text into studio shot configs via GPT."""
    from services.analysis_service import parse_studio_scenario_text
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Senaryo metni boş olamaz.")
    shots = await parse_studio_scenario_text(
        text=body.text,
        shot_count=max(1, min(5, body.shot_count)),
        total_duration=max(3, min(15, body.total_duration)),
    )
    return {"shots": shots}


@app.get("/api/defile/shot-arcs")
async def defile_shot_arcs(_user: dict = Depends(get_current_user)):
    """Return available narrative arc templates for the Defile shot picker."""
    from services.analysis_service import list_defile_shot_arcs
    return {"arcs": list_defile_shot_arcs()}


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
    job_owners[job_id] = _user["id"]

    asyncio.create_task(run_defile_collection_pipeline(job_id=job_id, request=body))
    return jobs[job_id]


@app.get("/api/status/{job_id}", response_model=JobResponse)
@limiter.limit("600/hour")
async def get_job_status(request: Request, job_id: str, _user: dict = Depends(get_current_user)):
    """Poll the status of a generation job.

    Enforces ownership: non-admins can only poll their own jobs.
    """
    if job_id not in jobs:
        return JobResponse(
            job_id=job_id,
            status=JobStatus.FAILED,
            message="Job bulunamadı.",
        )
    is_admin = _user.get("role") == "admin"
    owner = job_owners.get(job_id)
    if not is_admin and owner and owner != _user["id"]:
        raise HTTPException(status_code=403, detail="Bu işe erişim yetkiniz yok.")
    return jobs[job_id]


@app.get("/api/gallery")
@limiter.limit("120/hour")
async def get_gallery(request: Request, _user: dict = Depends(get_current_user)):
    """Return job history for the gallery page (only the current user's jobs).

    Admins see every job across the system.
    """
    is_admin = _user.get("role") == "admin"
    history = _load_history(user_id=_user["id"], is_admin=is_admin)
    return {"items": history}


@app.delete("/api/gallery/{job_id}")
@limiter.limit("60/hour")
async def delete_gallery_item(request: Request, job_id: str, _user: dict = Depends(get_current_user)):
    """Delete a job row from the Supabase jobs table.

    Enforces ownership: regular users can only delete their own jobs.
    Admins can delete any job.
    """
    from pipeline import _get_supabase
    try:
        db = _get_supabase()
        is_admin = _user.get("role") == "admin"

        # Verify the row exists and the caller owns it (or is admin)
        q = db.table("jobs").select("user_id").eq("job_id", job_id).limit(1).execute()
        rows = q.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı.")
        if not is_admin and rows[0].get("user_id") != _user["id"]:
            raise HTTPException(status_code=403, detail="Bu kaydı silme yetkiniz yok.")

        db.table("jobs").delete().eq("job_id", job_id).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Gallery delete error: %s", e)
        raise HTTPException(status_code=500, detail="Silme işlemi başarısız.")


@app.post("/api/upload-temp")
async def upload_temp_file(
    file: UploadFile = File(...),
    _user: dict = Depends(get_current_user),
):
    """Upload a temporary file and return its public URL."""
    path = await _save_upload(file)
    url = _file_to_url(path)
    return {"url": url}


@app.get("/gallery")
async def gallery_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "gallery.html"), media_type="text/html")


@app.get("/library")
async def library_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "library.html"), media_type="text/html")


@app.get("/defile")
async def defile_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "index.html"), media_type="text/html")


@app.get("/workflow")
async def workflow_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "workflow.html"), media_type="text/html")


@app.get("/seedance")
async def seedance_page():
    return FileResponse(os.path.join(_get_frontend_dir(), "seedance.html"), media_type="text/html")


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
    code: str = Field(min_length=4, max_length=16, pattern=r"^[A-Za-z0-9]+$")
    shot_configs: list = Field(min_length=1, max_length=30)


_ORDER_CODE_RE = __import__("re").compile(r"^[A-Za-z0-9]{4,16}$")


def _validate_code(code: str) -> str:
    if not _ORDER_CODE_RE.match(code or ""):
        raise HTTPException(status_code=400, detail="Geçersiz kod formatı.")
    return code


@app.post("/api/orders")
@limiter.limit("10/hour")
async def create_order(request: Request, body: OrderCreate):
    try:
        await save_order(body.code, body.shot_configs)
    except ValueError as e:
        if str(e) == "duplicate_code":
            raise HTTPException(status_code=409, detail="Bu kod zaten kullanımda.")
        raise HTTPException(status_code=400, detail="Geçersiz sipariş.")
    return {"ok": True}


@app.get("/api/orders/{code}")
@limiter.limit("60/hour")
async def get_order(request: Request, code: str):
    _validate_code(code)
    row = await lookup_order(code)
    if not row:
        raise HTTPException(status_code=404, detail="Kod bulunamadı.")
    return {"shot_configs": row["shot_configs"]}


@app.get("/api/orders/{code}/studio-config")
@limiter.limit("60/hour")
async def get_order_studio_config(
    request: Request,
    code: str,
    _user: dict = Depends(get_current_user),
):
    """Convert order shot_configs to studio-ready prompts.

    Returns {shots: [{description, duration, name}, ...]} — same shape
    as studioShots in the frontend, ready to be applied directly.
    Unknown shot IDs are passed through with their type as description.
    """
    from services.shot_prompts import SHOT_PROMPTS

    row = await lookup_order(code)
    if not row:
        raise HTTPException(status_code=404, detail="Kod bulunamadı.")

    shots = []
    for sc in row["shot_configs"]:
        shot_id = sc.get("type", "")
        duration = int(sc.get("duration", 5))
        mapping = SHOT_PROMPTS.get(shot_id)
        if mapping:
            shots.append({
                "description": mapping["prompt"],
                "duration": duration,
                "name": mapping["name"],
            })
        else:
            shots.append({
                "description": shot_id,
                "duration": duration,
                "name": shot_id,
            })

    return {"code": code.upper(), "shots": shots}


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
