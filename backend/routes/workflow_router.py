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
    shot_arc: Optional[str] = None  # Narrative arc ID — None = random


class SceneFrameRequest(BaseModel):
    outfit: WfOutfit
    background_url: Optional[str] = None
    background_extra_urls: Optional[List[str]] = None
    aspect_ratio: str = "9:16"


class WfOutfitPayload(BaseModel):
    """Per-outfit data: outfit + approved scene frame + approved shots."""
    outfit: WfOutfit
    scene_frame_url: str
    shots: List[dict]   # [{duration, prompt}]


class GenerateRequest(BaseModel):
    outfits: List[WfOutfitPayload]          # one or more outfits
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

    # Build a simple NB Pro scene frame for GPT to analyze
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

    # NB Pro compose
    from pipeline import _build_nb_pro_compose_prompt
    nb_pro_prompt = _build_nb_pro_compose_prompt(analysis=None)

    scene_frame_url = await generate_scene_frame(
        image_urls=[fal_bg] + garment_refs,
        prompt=nb_pro_prompt,
        aspect_ratio=body.aspect_ratio,
    )
    logger.info("Workflow scenario: NB Pro scene frame: %s", scene_frame_url[:80] if scene_frame_url else "N/A")

    # GPT scenario generation
    shot_configs_typed = [DefileShotConfig(duration=s.duration) for s in body.shot_configs]
    shots = await generate_defile_multishot_prompt(
        scene_frame_url=scene_frame_url,
        shot_configs=shot_configs_typed,
        outfit_name=body.outfit.name or "garment",
        video_description=body.director_note,
        shot_arc_id=body.shot_arc,
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
    """(Re)generate NB Pro scene frame for user approval."""
    from services.nano_banana_service import generate_background, generate_scene_frame
    from pipeline import _to_fal_url, _build_nb_pro_compose_prompt

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

    nb_pro_prompt = _build_nb_pro_compose_prompt(analysis=None)

    scene_frame_url = await generate_scene_frame(
        image_urls=[fal_bg] + garment_refs,
        prompt=nb_pro_prompt,
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
    """Execute video generation for workflow — supports multi-outfit with concat."""
    from services.video_service import generate_multishot_video, download_file, concatenate_clips
    from pipeline import (
        _to_fal_url,
        _to_fal_url_compressed,
        _DEFILE_NEGATIVE,
        _resolve_garment_meta_from_url,
        _apply_quality_layers,
        _get_fabric_negative,
    )

    try:
        n_outfits = len(req.outfits)
        total_shots = sum(len(op.shots) for op in req.outfits)
        clip_paths: list = []

        for oi, op in enumerate(req.outfits):
            outfit_name = op.outfit.name or f"Kıyafet {oi + 1}"
            total_duration = sum(int(s["duration"]) for s in op.shots)
            base_progress = 10 + int((oi / n_outfits) * 75)

            _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                        progress=base_progress,
                        message=f"{outfit_name} — promptlar hazırlanıyor ({oi + 1}/{n_outfits})...")

            # Resolve outfit garment meta from library (fabric + user description, translated)
            _wf_meta = await _resolve_garment_meta_from_url(op.outfit.front_url, analysis=None)
            logger.info("[%s] Workflow outfit %d meta: fabric=%r desc=%s",
                        job_id, oi + 1,
                        _wf_meta.get("fabric"),
                        (_wf_meta.get("description") or "")[:60])

            # Per-outfit dynamic negative prompt — base + fabric-specific additions
            _wf_negative = _DEFILE_NEGATIVE
            _wf_fabric_neg = _get_fabric_negative(_wf_meta.get("fabric"))
            if _wf_fabric_neg:
                _wf_negative = _wf_negative + ", " + _wf_fabric_neg

            # Wrap each approved shot with quality layers (FABRIC LOCK anchor +
            # fabric physics + Style Bible + micro actions). Garment silhouette
            # is preserved by Kling element references — no positive-text hem
            # enforcement is injected.
            multi_prompt = [
                {
                    "duration": s["duration"],
                    "prompt": _apply_quality_layers(
                        core_prompt=str(s["prompt"]),
                        meta=_wf_meta,
                        max_len=512,
                    ),
                }
                for s in op.shots
            ]

            # Build element data
            elem_front = await _to_fal_url_compressed(op.outfit.front_url)
            elem_refs: list = []
            if op.outfit.side_url:
                elem_refs.append(await _to_fal_url_compressed(op.outfit.side_url))
            if op.outfit.back_url:
                elem_refs.append(await _to_fal_url_compressed(op.outfit.back_url))
            for eu in (op.outfit.extra_urls or []):
                if eu and len(elem_refs) < 3:
                    elem_refs.append(await _to_fal_url_compressed(eu))
            if not elem_refs:
                elem_refs = [elem_front]

            outfit_element = {
                "frontal_image_url": elem_front,
                "reference_image_urls": elem_refs,
            }

            scene_frame_fal = await _to_fal_url(op.scene_frame_url)

            # Build debug_payload for this outfit
            _debug_payload = {
                "outfit": outfit_name,
                "start_image_url": scene_frame_fal,
                "multi_prompt": [{"prompt": p["prompt"], "duration": p["duration"]} for p in multi_prompt],
                "duration": str(total_duration),
                "aspect_ratio": req.aspect_ratio,
                "generate_audio": req.generate_audio,
                "elements": [outfit_element],
                "provider": req.provider,
            }

            if req.provider == "kling":
                from services.kling_service import (  # type: ignore[import]
                    generate_multishot_video as kling_gen,
                )
                from pipeline import get_or_create_kling_element

                _update_job(job_id, progress=base_progress + int(15 / n_outfits),
                            message=f"{outfit_name} — Kling element oluşturuluyor ({oi + 1}/{n_outfits})...")
                kling_eid = await get_or_create_kling_element(
                    front_url=op.outfit.front_url,
                    frontal_image_url=elem_front,
                    reference_image_urls=elem_refs,
                    name=f"workflow{oi + 1}",
                    description=f"workflow garment {oi + 1}",
                )

                if kling_eid is not None:
                    logger.info("[%s] Workflow outfit %d: Kling element_id=%d", job_id, oi + 1, kling_eid)
                    kling_prompts = [
                        {"duration": p["duration"], "prompt": f"<<<element_1>>> {p['prompt']}"}
                        for p in multi_prompt
                    ]
                    element_list_param: list = [{"element_id": int(kling_eid)}]
                else:
                    logger.info("[%s] Workflow outfit %d: no Kling element (single image) — using start frame only",
                                job_id, oi + 1)
                    kling_prompts = [
                        {"duration": p["duration"], "prompt": p["prompt"]}
                        for p in multi_prompt
                    ]
                    element_list_param = []

                _debug_payload["element_list"] = element_list_param

                _update_job(job_id, progress=base_progress + int(35 / n_outfits),
                            debug_payload=_debug_payload,
                            message=f"{outfit_name} — video üretiliyor ({oi + 1}/{n_outfits})...")
                clip_url = await kling_gen(
                    start_image_url=scene_frame_fal,
                    multi_prompt=kling_prompts,
                    duration=str(total_duration),
                    aspect_ratio=req.aspect_ratio,
                    generate_audio=req.generate_audio,
                    element_list=element_list_param,
                    negative_prompt=_wf_negative,
                )
            else:
                _update_job(job_id, progress=base_progress + int(35 / n_outfits),
                            debug_payload=_debug_payload,
                            message=f"{outfit_name} — video üretiliyor ({oi + 1}/{n_outfits})...")
                clip_url = await generate_multishot_video(
                    start_image_url=scene_frame_fal,
                    multi_prompt=multi_prompt,
                    duration=str(total_duration),
                    aspect_ratio=req.aspect_ratio,
                    generate_audio=req.generate_audio,
                    elements=[outfit_element],
                    negative_prompt=_wf_negative,
                )

            clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
            clip_paths.append(clip_path)
            logger.info("[%s] Workflow outfit %d/%d clip: %s", job_id, oi + 1, n_outfits, clip_path)

        # Concatenate if multiple outfits
        _update_job(job_id, progress=88, message="Video birleştiriliyor...")
        final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
        if len(clip_paths) > 1:
            concatenate_clips(clip_paths, final_path)
            for p in clip_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
        else:
            shutil.move(clip_paths[0], final_path)

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
            message=f"Workflow tamamlandı! {n_outfits} kıyafet, {total_shots} sahne.",
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
