"""Kling 3.0 Omni prompt writer — standalone composer for the app.klingai.com web UI.

Single endpoint: POST /api/kling-prompt/compose
Takes element tag labels + shot count + total duration + arc tone + optional director
note and returns a JSON bundle of per-shot prompts + one negative prompt.
Prompts reference elements as `@<tag>` placeholders — user swaps them for Kling's
Bind Subject element picker on the site.

NOTE: Do not add `from __future__ import annotations` here — FastAPI + Pydantic v2
resolve the request-body model (ComposeRequest) as a forward ref using module
globals, and that fails under deferred annotations. Keep annotations concrete.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from dependencies import get_current_user
from limiter import limiter
from services.kling_prompt_composer import (
    MAX_SHOTS,
    MAX_TOTAL_DURATION,
    MIN_SHOTS,
    MIN_TOTAL_DURATION,
    compose_kling_prompts,
)

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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("kling-prompt compose failed")
        raise HTTPException(status_code=500, detail=f"Prompt üretilemedi: {e}") from e
