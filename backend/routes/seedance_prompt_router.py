"""Seedance 2.0 prompt writer — standalone composer (copy-paste to Seedance UI).

Endpoints:
  GET  /api/seedance-prompt/film-looks  → film-look preset list for frontend
  POST /api/seedance-prompt/compose     → generate prompts from 3 upload buckets

NOTE: Do not add `from __future__ import annotations` here — FastAPI + Pydantic v2
resolve the request-body model as a forward ref using module globals, and that
fails under deferred annotations. Keep annotations concrete. (Same as kling router.)
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from dependencies import get_current_user
from limiter import limiter
from services.seedance_prompt_composer import (
    FILM_LOOKS,
    MAX_CHARACTER_REFS,
    MAX_LOCATION_REFS,
    MAX_REFERENCE_IMAGES,
    MAX_SHOTS,
    MAX_TOTAL_DURATION,
    MIN_SHOTS,
    MIN_TOTAL_DURATION,
    compose_seedance_prompts,
)

router = APIRouter(prefix="/api/seedance-prompt", tags=["seedance-prompt"])
logger = logging.getLogger(__name__)


class ComposeRequest(BaseModel):
    start_frame_url: str = Field(min_length=5)
    character_urls: List[str] = Field(default_factory=list, max_length=MAX_CHARACTER_REFS)
    location_urls: List[str] = Field(default_factory=list, max_length=MAX_LOCATION_REFS)
    n_shots: int = Field(ge=MIN_SHOTS, le=MAX_SHOTS)
    total_duration: int = Field(ge=MIN_TOTAL_DURATION, le=MAX_TOTAL_DURATION)
    aspect_ratio: str = Field(default="9:16")
    arc_tone: str = Field(default="runway")
    render_mode: str = Field(default="numbered_shots")  # "numbered_shots" | "timed_segments"
    film_look: str = Field(default="arri_alexa")
    silent: bool = Field(default=True)
    director_note: Optional[str] = Field(default=None, max_length=500)
    shot_techniques: Optional[List[Optional[str]]] = Field(default=None, max_length=MAX_SHOTS)
    previous_prompt: Optional[str] = Field(default=None, max_length=8000)


@router.get("/film-looks")
async def list_film_looks(_user: dict = Depends(get_current_user)):
    """Return the film-look preset library for the frontend picker."""
    return {
        "film_looks": [
            {"id": k, **{kk: vv for kk, vv in v.items() if kk != "preamble"}}
            for k, v in FILM_LOOKS.items()
        ],
        "max_reference_images": MAX_REFERENCE_IMAGES,
        "max_character_refs": MAX_CHARACTER_REFS,
        "max_location_refs": MAX_LOCATION_REFS,
    }


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
        return await compose_seedance_prompts(
            start_frame_url=body.start_frame_url,
            character_urls=body.character_urls,
            location_urls=body.location_urls,
            n_shots=body.n_shots,
            total_duration=body.total_duration,
            aspect_ratio=body.aspect_ratio,
            arc_tone=body.arc_tone,
            render_mode=body.render_mode,
            film_look=body.film_look,
            silent=body.silent,
            director_note=body.director_note,
            shot_techniques=body.shot_techniques,
            previous_prompt=body.previous_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("seedance-prompt compose failed")
        raise HTTPException(status_code=500, detail=f"Prompt üretilemedi: {e}") from e
