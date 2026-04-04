"""Workflow API — step-by-step video generation with user approval gates.

Endpoints:
  POST /workflow/scenario     – Generate scenario (GPT) for a single outfit
  POST /workflow/scene-frame  – Generate NB2 scene frame for approval
  POST /workflow/generate     – Start video generation with approved scenario + frame
"""

import asyncio
import logging
import os
import shutil
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import settings
from dependencies import get_current_user
from models import DefileShotConfig, JobResponse, JobStatus

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Request Models ─────────────────────────────────────────────────

class WfOutfit(BaseModel):
    front_url: str
    side_url: Optional[str] = None
    back_url: Optional[str] = None
    extra_urls: Optional[List[str]] = None
    name: Optional[str] = None


class WfShotConfig(BaseModel):
    duration: int = 5


class ScenarioRequest(BaseModel):
    outfit: WfOutfit
    shot_configs: List[WfShotConfig]
    background_url: Optional[str] = None
    aspect_ratio: str = "9:16"
    director_note: Optional[str] = None


class SceneFrameRequest(BaseModel):
    outfit: WfOutfit
    background_url: Optional[str] = None
    background_extra_urls: Optional[List[str]] = None
    aspect_ratio: str = "9:16"


class GenerateRequest(BaseModel):
    outfit: WfOutfit
    scene_frame_url: str
    shots: List[dict]   # [{duration, prompt}]
    aspect_ratio: str = "9:16"
    generate_audio: bool = False
    provider: str = "kling"


# ─── Jobs store (shared with main pipeline) ─────────────────────────
from pipeline import jobs, _update_job


# ─── Endpoint 1: Generate Scenario ──────────────────────────────────

@router.post("/scenario")
async def generate_scenario(
    body: ScenarioRequest,
    _user: dict = Depends(get_current_user),
):
    """Generate multishot prompts via GPT for a single outfit.

    Returns {shots: [{duration, prompt}], scene_frame_url: null}
    """
    from services.analysis_service import generate_defile_multishot_prompt
    from services.nano_banana_service import generate_background, generate_scene_frame
    from pipeline import _to_fal_url

    # Build a simple NB2 scene frame for GPT to analyze
    # First, get background
    if body.background_url:
        bg_url = body.background_url
    else:
        bg_url = await generate_background(
            prompt="high-end fashion runway, empty catwalk, dramatic stage lighting, luxury fashion show venue, no people, architectural interior",
            aspect_ratio=body.aspect_ratio,
        )

    # Upload outfit images to fal CDN
    fal_front = await _to_fal_url(body.outfit.front_url)
    garment_refs = [fal_front]
    if body.outfit.side_url:
        garment_refs.append(await _to_fal_url(body.outfit.side_url))
    if body.outfit.back_url:
        garment_refs.append(await _to_fal_url(body.outfit.back_url))
    for eu in (body.outfit.extra_urls or []):
        if eu and eu not in garment_refs:
            garment_refs.append(await _to_fal_url(eu))

    fal_bg = await _to_fal_url(bg_url)

    # NB2 compose
    nb2_prompt = (
        "Fashion editorial photo: the first image is the location/scene — "
        "preserve it EXACTLY as-is: architecture, lighting, floor, walls, all structural elements unchanged. "
        "Do NOT add spectators, audience, crowd, cameramen, photographers, trees, flowers, plants, or any props not already in the scene. "
        "Place a tall fashion model in this space wearing the garment from the reference images (images 2 onward). "
        "Full body visible, frontal medium-wide shot, confident stance. "
        "Preserve all garment details: exact colors, fabric, cut, silhouette, length. "
        "CRITICAL: the garment hem must touch and rest exactly on the floor — "
        "the bottom of the garment grazes the floor surface. "
        "Shoes and feet must NOT be visible — the hem completely covers the feet. "
        "Professional fashion photography, sharp focus, editorial quality."
    )

    scene_frame_url = await generate_scene_frame(
        image_urls=[fal_bg] + garment_refs,
        prompt=nb2_prompt,
        aspect_ratio=body.aspect_ratio,
    )
    logger.info("Workflow scenario: NB2 scene frame: %s", scene_frame_url[:80] if scene_frame_url else "N/A")

    # GPT scenario generation
    shot_configs_typed = [DefileShotConfig(duration=s.duration) for s in body.shot_configs]
    shots = await generate_defile_multishot_prompt(
        scene_frame_url=scene_frame_url,
        shot_configs=shot_configs_typed,
        outfit_name=body.outfit.name or "garment",
        video_description=body.director_note,
    )

    logger.info("Workflow scenario: %d shots generated", len(shots))

    return {
        "shots": shots,
        "scene_frame_url": scene_frame_url,
    }


# ─── Endpoint 2: Generate Scene Frame ───────────────────────────────

@router.post("/scene-frame")
async def generate_scene_frame_endpoint(
    body: SceneFrameRequest,
    _user: dict = Depends(get_current_user),
):
    """(Re)generate NB2 scene frame for user approval."""
    from services.nano_banana_service import generate_background, generate_scene_frame
    from pipeline import _to_fal_url

    if body.background_url:
        bg_url = body.background_url
    else:
        bg_url = await generate_background(
            prompt="high-end fashion runway, empty catwalk, dramatic stage lighting, luxury fashion show venue, no people, architectural interior",
            aspect_ratio=body.aspect_ratio,
        )

    fal_front = await _to_fal_url(body.outfit.front_url)
    garment_refs = [fal_front]
    if body.outfit.side_url:
        garment_refs.append(await _to_fal_url(body.outfit.side_url))
    if body.outfit.back_url:
        garment_refs.append(await _to_fal_url(body.outfit.back_url))
    for eu in (body.outfit.extra_urls or []):
        if eu and eu not in garment_refs:
            garment_refs.append(await _to_fal_url(eu))

    fal_bg = await _to_fal_url(bg_url)

    nb2_prompt = (
        "Fashion editorial photo: the first image is the location/scene — "
        "preserve it EXACTLY as-is: architecture, lighting, floor, walls, all structural elements unchanged. "
        "Do NOT add spectators, audience, crowd, cameramen, photographers, trees, flowers, plants, or any props not already in the scene. "
        "Place a tall fashion model in this space wearing the garment from the reference images (images 2 onward). "
        "Full body visible, frontal medium-wide shot, confident stance. "
        "Preserve all garment details: exact colors, fabric, cut, silhouette, length. "
        "CRITICAL: the garment hem must touch and rest exactly on the floor — "
        "the bottom of the garment grazes the floor surface. "
        "Shoes and feet must NOT be visible — the hem completely covers the feet. "
        "Professional fashion photography, sharp focus, editorial quality."
    )

    scene_frame_url = await generate_scene_frame(
        image_urls=[fal_bg] + garment_refs,
        prompt=nb2_prompt,
        aspect_ratio=body.aspect_ratio,
    )

    logger.info("Workflow scene-frame: %s", scene_frame_url[:80] if scene_frame_url else "N/A")
    return {"scene_frame_url": scene_frame_url}


# ─── Endpoint 3: Generate Video ─────────────────────────────────────

@router.post("/generate", response_model=JobResponse)
async def generate_video(
    body: GenerateRequest,
    _user: dict = Depends(get_current_user),
):
    """Start video generation with user-approved scenario + scene frame."""
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Workflow video başlatılıyor...",
    )

    asyncio.create_task(_run_workflow_video(job_id, body))
    return jobs[job_id]


async def _run_workflow_video(job_id: str, req: GenerateRequest):
    """Execute video generation for workflow — reuses existing pipeline services."""
    from services.video_service import generate_multishot_video, download_file
    from pipeline import _to_fal_url, _to_fal_url_compressed

    # Import Kling-specific helpers
    _HEM_LOCK_SHORT = "NO slit anywhere. NO front slit. Sealed floor-length gown. Skirt fully closed. Legs hidden."
    _DEFILE_NEGATIVE = (
        "blur, distort, low quality, deformed hands, deformed face, "
        "changed outfit, different dress, altered silhouette, different fabric, "
        "costume change, wardrobe change, morphing clothes, feet, bare feet, "
        "shoes, heels, boots, footwear, visible ankles, visible toes, "
        "floating hem, lifted skirt, hem above ground, gap between dress and floor, "
        "short dress, mini dress, midi dress, knee-length dress, calf-length dress, "
        "cropped skirt, raised hemline, above-ankle hem, shortened dress, "
        "spectators, audience, crowd, seated guests, cameraman, photographer, crew, "
        "people in background, bystanders, onlookers, "
        "trees, flowers, plants, flower arrangements, decorative props, added accessories, "
        "extra furniture, added decor, altered background, modified scenery"
    )

    try:
        n_shots = len(req.shots)
        total_duration = sum(int(s["duration"]) for s in req.shots)

        _update_job(job_id, status=JobStatus.GENERATING_VIDEO, progress=10,
                    message="Promptlar hazırlanıyor...")

        # Prepend hem lock to each prompt
        _rem = 512 - len(_HEM_LOCK_SHORT) - 1
        multi_prompt = [
            {
                "duration": s["duration"],
                "prompt": (_HEM_LOCK_SHORT + " " + str(s["prompt"])[:_rem])[:512],
            }
            for s in req.shots
        ]

        # Build element data
        _update_job(job_id, progress=20, message="Element görselleri hazırlanıyor...")

        elem_front = await _to_fal_url_compressed(req.outfit.front_url)
        elem_refs = []
        if req.outfit.side_url:
            elem_refs.append(await _to_fal_url_compressed(req.outfit.side_url))
        if req.outfit.back_url:
            elem_refs.append(await _to_fal_url_compressed(req.outfit.back_url))
        for eu in (req.outfit.extra_urls or []):
            if eu and len(elem_refs) < 3:
                elem_refs.append(await _to_fal_url_compressed(eu))
        if not elem_refs:
            elem_refs = [elem_front]

        outfit_element = {
            "frontal_image_url": elem_front,
            "reference_image_urls": elem_refs,
        }

        # Re-upload scene frame
        scene_frame_fal = await _to_fal_url(req.scene_frame_url)

        _update_job(job_id, progress=35, message="Video üretiliyor...")

        if req.provider == "kling":
            from services.kling_service import (  # type: ignore[import]
                generate_multishot_video as kling_gen,
                create_element as kling_create_elem,
            )

            # Create Kling element
            _update_job(job_id, progress=40, message="Kling element oluşturuluyor...")
            kling_eid = await kling_create_elem(
                frontal_image_url=elem_front,
                reference_image_urls=elem_refs,
                name="workflow",
                description="workflow garment",
            )
            logger.info("[%s] Workflow: Kling element_id=%d", job_id, kling_eid)

            # Prepend <<<element_1>>> token
            kling_prompts = [
                {"duration": p["duration"], "prompt": f"<<<element_1>>> {p['prompt']}"}
                for p in multi_prompt
            ]

            _update_job(job_id, progress=55, message="Kling video üretiliyor...")
            clip_url = await kling_gen(
                start_image_url=scene_frame_fal,
                multi_prompt=kling_prompts,
                duration=str(total_duration),
                aspect_ratio=req.aspect_ratio,
                generate_audio=req.generate_audio,
                element_list=[{"element_id": int(kling_eid)}],
                negative_prompt=_DEFILE_NEGATIVE,
            )
        else:
            _update_job(job_id, progress=55, message="fal.ai video üretiliyor...")
            clip_url = await generate_multishot_video(
                start_image_url=scene_frame_fal,
                multi_prompt=multi_prompt,
                duration=str(total_duration),
                aspect_ratio=req.aspect_ratio,
                generate_audio=req.generate_audio,
                elements=[outfit_element],
                negative_prompt=_DEFILE_NEGATIVE,
            )

        _update_job(job_id, progress=85, message="Video indiriliyor...")
        final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
        clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
        shutil.move(clip_path, final_path)

        # Upload to Supabase
        result_url = None
        try:
            _update_job(job_id, progress=95, message="Yükleniyor...")
            from pipeline import _get_supabase
            db = _get_supabase()
            filename = os.path.basename(final_path)
            with open(final_path, "rb") as f:
                db.storage.from_("videos").upload(
                    path=filename,
                    file=f.read(),
                    file_options={"content-type": "video/mp4"},
                )
            result_url = db.storage.from_("videos").get_public_url(filename)
        except Exception as upload_err:
            logger.warning("[%s] Supabase upload failed: %s", job_id, upload_err)
            relative = final_path.replace("\\", "/")
            result_url = f"/outputs/{relative.split('/outputs/')[-1]}"

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message=f"Workflow tamamlandı! {n_shots} sahne, {total_duration}s.",
            result_url=result_url,
        )

        from services.telegram_service import notify_video_ready  # type: ignore[import]
        await notify_video_ready(result_url or "", job_id, mode="workflow")

    except Exception as exc:
        logger.exception("[%s] Workflow video failed", job_id)
        from pipeline import _tr_error
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=_tr_error(exc),
        )
