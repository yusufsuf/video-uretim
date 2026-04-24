"""Kling 3.0 Omni prompt writer + direct-generate bridge.

Endpoints:
  GET  /api/kling-prompt/techniques        → technique library
  POST /api/kling-prompt/compose           → GPT prompt generation
  POST /api/kling-prompt/generate          → bridge: prompts → Kling Omni API job
  GET  /api/kling-prompt/bindable-items    → library items with a cached kling_element_id

NOTE: Do not add `from __future__ import annotations` here — FastAPI + Pydantic v2
resolve the request-body model as a forward ref using module globals, and that
fails under deferred annotations. Keep annotations concrete.
"""

import asyncio
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from dependencies import get_current_user
from limiter import limiter
from models import JobResponse, JobStatus
from services import kling_service, library_service
from services.kling_prompt_composer import (
    MAX_SHOTS,
    MAX_TOTAL_DURATION,
    MIN_SHOTS,
    MIN_TOTAL_DURATION,
    compose_kling_prompts,
)
from services.kling_techniques import TECHNIQUES

router = APIRouter(prefix="/api/kling-prompt", tags=["kling-prompt"])
logger = logging.getLogger(__name__)


class ComposeRequest(BaseModel):
    start_frame_url: str = Field(min_length=5)
    element_tags: List[str] = Field(default_factory=list, max_length=6)
    n_shots: int = Field(ge=MIN_SHOTS, le=MAX_SHOTS)
    total_duration: int = Field(ge=MIN_TOTAL_DURATION, le=MAX_TOTAL_DURATION)
    arc_tone: str = Field(default="runway")
    mode: str = Field(default="custom_multi_shot")  # "multi_shot" | "custom_multi_shot"
    director_note: Optional[str] = Field(default=None, max_length=500)
    shot_techniques: Optional[List[Optional[str]]] = Field(default=None, max_length=MAX_SHOTS)
    previous_prompt: Optional[str] = Field(default=None, max_length=8000)
    structured_garment: bool = Field(default=False)


@router.get("/techniques")
async def list_techniques(_user: dict = Depends(get_current_user)):
    """Return the technique library for the frontend picker."""
    return {"techniques": TECHNIQUES}


@router.post("/compose")
@limiter.limit("30/hour")
async def compose(
    request: Request,
    body: ComposeRequest,
    _user: dict = Depends(get_current_user),
):
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY tanımlı değil.")
    try:
        return await compose_kling_prompts(
            start_frame_url=body.start_frame_url,
            element_tags=body.element_tags,
            n_shots=body.n_shots,
            total_duration=body.total_duration,
            arc_tone=body.arc_tone,
            director_note=body.director_note,
            mode=body.mode,
            shot_techniques=body.shot_techniques,
            previous_prompt=body.previous_prompt,
            structured_garment=body.structured_garment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("kling-prompt compose failed")
        raise HTTPException(status_code=500, detail=f"Prompt üretilemedi: {e}") from e


# ─── Bridge: prompt → Kling Omni video job ────────────────────────────────

@router.get("/bindable-items")
async def list_bindable_items(user: dict = Depends(get_current_user)):
    """Library items that already have a cached kling_element_id — i.e. can be
    bound as an @element directly without re-creating."""
    items = await library_service.get_items(user["id"])
    out = []
    for it in items:
        eid = it.get("kling_element_id")
        if not eid:
            continue
        out.append({
            "item_id": it.get("id"),
            "name": it.get("name"),
            "category": it.get("category"),
            "primary_url": it.get("primary_url") or it.get("front_url"),
            "kling_element_id": int(eid),
        })
    return {"items": out}


class GenerateShot(BaseModel):
    prompt: str
    duration: int = Field(ge=1, le=15)


class GenerateRequest(BaseModel):
    start_frame_url: str = Field(min_length=5)
    shots: List[GenerateShot] = Field(min_length=1, max_length=MAX_SHOTS)
    tag_element_map: dict = Field(default_factory=dict)  # {"dress": 1234, "jacket": 5678}
    aspect_ratio: str = Field(default="9:16")
    generate_audio: bool = Field(default=False)
    negative_prompt: Optional[str] = Field(default=None, max_length=500)


def _rewrite_tags_to_tokens(prompt: str, tag_to_index: dict) -> str:
    """Replace each `@tag` occurrence with `<<<element_N>>>` where N is the
    tag's 1-based index in tag_to_index. Tags not in the map are left alone
    (kling_service auto-prefixes remaining element tokens)."""
    if not tag_to_index:
        return prompt
    out = prompt
    for tag, idx in tag_to_index.items():
        pattern = re.compile(r"@" + re.escape(tag) + r"\b")
        out = pattern.sub(f"<<<element_{idx}>>>", out)
    return out


@router.post("/generate")
@limiter.limit("15/hour")
async def generate_from_prompts(
    request: Request,
    body: GenerateRequest,
    user: dict = Depends(get_current_user),
):
    """Bridge endpoint: takes composed prompts + tag→library_element_id mapping,
    substitutes `@tag` → `<<<element_N>>>` tokens, and fires a Kling Omni job.
    Registers the job in the global `jobs` registry so existing /api/status
    polling works transparently.
    """
    if not settings.KLING_ACCESS_KEY or not settings.KLING_SECRET_KEY:
        raise HTTPException(status_code=503, detail="KLING_ACCESS_KEY/SECRET tanımlı değil.")

    total_dur = sum(int(s.duration) for s in body.shots)
    if total_dur < 3 or total_dur > 15:
        raise HTTPException(
            status_code=400,
            detail=f"Toplam süre 3-15 sn aralığında olmalı (şu an {total_dur}s).",
        )

    # Build ordered tag→index map (1-based, preserving dict order)
    ordered_tags = list(body.tag_element_map.keys())
    tag_to_index = {tag: i + 1 for i, tag in enumerate(ordered_tags)}
    element_list = [{"element_id": int(body.tag_element_map[t])} for t in ordered_tags]

    # Rewrite each shot's prompt
    multi_prompt = []
    for s in body.shots:
        rewritten = _rewrite_tags_to_tokens(s.prompt, tag_to_index)
        multi_prompt.append({"prompt": rewritten, "duration": int(s.duration)})

    # Register job in the global registry (pipeline.py owns it)
    from pipeline import job_owners, jobs

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Kling Omni kuyruğa alındı.",
    )
    job_owners[job_id] = user["id"]

    async def _run():
        try:
            jobs[job_id] = jobs[job_id].model_copy(update={
                "status": JobStatus.GENERATING_VIDEO,
                "message": "Kling 3.0 Omni üretimi başladı…",
                "progress": 5,
            })
            video_url = await kling_service.generate_omni_video(
                start_image_url=body.start_frame_url,
                multi_prompt=multi_prompt,
                duration=str(total_dur),
                aspect_ratio=body.aspect_ratio,
                generate_audio=bool(body.generate_audio),
                element_list=element_list or None,
                model_name="kling-v3-omni",
            )
            jobs[job_id] = jobs[job_id].model_copy(update={
                "status": JobStatus.COMPLETED,
                "message": "Video hazır.",
                "progress": 100,
                "result_url": video_url,
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("kling-prompt generate failed")
            jobs[job_id] = jobs[job_id].model_copy(update={
                "status": JobStatus.FAILED,
                "message": f"Üretim başarısız: {e}",
            })

    asyncio.create_task(_run())
    return jobs[job_id]
