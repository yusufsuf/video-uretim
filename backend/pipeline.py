"""Pipeline – orchestrates the full fashion video generation workflow.

New Flow (v2):
1. Analyse the garment (GPT-4o Vision)
2. Generate multi-scene prompts (GPT-4o – cinematography rules)
3. Generate background image (Nano Banana 2 via fal.ai)
4. Generate multishot video (Kling 3.0 Pro with elements + start_image)
5. (Optional) Watermark overlay
"""

import logging
import os
import subprocess
import uuid
from typing import Optional

from config import settings
from models import (
    DressAnalysisResult,
    GenerationRequest,
    JobResponse,
    JobStatus,
    MultiScenePrompt,
)
from services.analysis_service import analyse_dress, generate_multi_scene_prompt
from services.nano_banana_service import generate_background
from services.video_service import download_file, generate_multishot_video

logger = logging.getLogger(__name__)

# In-memory job store (replace with DB for production)
jobs: dict[str, JobResponse] = {}

HISTORY_FILE = os.path.join(settings.DATA_DIR, "job_history.json")


def _load_history() -> list[dict]:
    """Load job history from disk."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                import json
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_to_history(job: JobResponse):
    """Append a completed job to the persistent history file."""
    import json
    from datetime import datetime

    history = _load_history()
    entry = job.model_dump()
    entry["created_at"] = datetime.now().isoformat()
    if entry.get("analysis"):
        entry["analysis_summary"] = f"{entry['analysis'].get('garment_type', '')} - {entry['analysis'].get('color', '')}"
    entry.pop("analysis", None)
    entry.pop("scene_prompt", None)

    history.insert(0, entry)
    history = history[:100]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _update_job(job_id: str, **kwargs):
    if job_id in jobs:
        for k, v in kwargs.items():
            setattr(jobs[job_id], k, v)
        if jobs[job_id].status in (JobStatus.COMPLETED, JobStatus.FAILED):
            try:
                _save_to_history(jobs[job_id])
            except Exception as e:
                logger.error("Failed to save job history: %s", e)


async def run_pipeline(
    job_id: str,
    front_path: str,
    back_path: Optional[str],
    side_path: Optional[str],
    reference_image_path: Optional[str],
    request: GenerationRequest,
    front_url: str,
    side_url: Optional[str] = None,
    back_url: Optional[str] = None,
    duration: int = 10,
    scene_count: int = 2,
    video_description: Optional[str] = None,
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    watermark_path: Optional[str] = None,
):
    """Execute the full pipeline asynchronously."""
    try:
        # Clamp values
        duration = max(3, min(15, duration))
        scene_count = max(1, min(6, scene_count))

        # ── Step 1: Analyse the garment ─────────────────────────
        _update_job(job_id, status=JobStatus.ANALYZING, progress=5, message="Elbise analiz ediliyor...")
        logger.info("[%s] Step 1 – Analysing garment", job_id)

        analysis = await analyse_dress(front_path, back_path)
        _update_job(job_id, analysis=analysis, progress=15, message="Elbise analizi tamamlandı.")
        logger.info("[%s] Analysis result: %s", job_id, analysis.garment_type)

        # ── Step 2: Generate multi-scene prompts ────────────────
        _update_job(job_id, status=JobStatus.GENERATING_PROMPTS, progress=20, message="Sahneler planlanıyor...")
        logger.info("[%s] Step 2 – Generating multi-scene prompts (duration=%ds, scenes=%d)", job_id, duration, scene_count)

        scene_prompt = await generate_multi_scene_prompt(
            analysis=analysis,
            request=request,
            total_duration=duration,
            scene_count=scene_count,
            video_description=video_description,
            location_image_path=reference_image_path,
        )
        _update_job(job_id, scene_prompt=scene_prompt, progress=30, message=f"{scene_prompt.scene_count} sahne planlandı.")
        logger.info("[%s] Planned %d scenes", job_id, scene_prompt.scene_count)

        # ── Step 3: Generate background via Nano Banana 2 ────────
        _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=35, message="Arka plan üretiliyor...")
        logger.info("[%s] Step 3 – Generating background image", job_id)

        bg_prompt = scene_prompt.background_image_prompt
        logger.info("[%s] Background prompt: %s", job_id, bg_prompt[:120])

        background_url = await generate_background(
            prompt=bg_prompt,
            aspect_ratio=aspect_ratio,
        )
        logger.info("[%s] Background generated: %s", job_id, background_url[:100])
        _update_job(job_id, progress=50, message="Arka plan hazır. Video üretiliyor...")

        # ── Step 4: Build elements + generate multishot video ────
        _update_job(job_id, status=JobStatus.GENERATING_VIDEO, progress=55, message="Video üretiliyor...")
        logger.info("[%s] Step 4 – Generating multishot video", job_id)

        # Build element (garment photos)
        element = {
            "frontal_image_url": front_url,
            "reference_image_urls": [],
        }
        if side_url:
            element["reference_image_urls"].append(side_url)
        if back_url:
            element["reference_image_urls"].append(back_url)

        elements = [element]
        logger.info("[%s] Element: frontal=%s, refs=%d", job_id, front_url[:60], len(element["reference_image_urls"]))

        # Build multi_prompt from scene prompts
        multi_prompt = []
        for scene in scene_prompt.scenes:
            multi_prompt.append({
                "duration": scene.duration,
                "prompt": scene.prompt,
            })
        logger.info("[%s] Multi-prompt: %d shots", job_id, len(multi_prompt))

        # Generate video
        video_url = await generate_multishot_video(
            start_image_url=background_url,
            multi_prompt=multi_prompt,
            elements=elements,
            duration=str(duration),
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
        )

        _update_job(job_id, progress=85, message="Video indiriliyior...")

        # Download final video
        final_path = await download_file(video_url, settings.OUTPUT_DIR, extension=".mp4")
        logger.info("[%s] Video downloaded: %s", job_id, final_path)

        # ── Step 5 (optional): Watermark overlay ─────────────────
        if watermark_path and os.path.isfile(watermark_path):
            logger.info("[%s] Step 5 – Applying watermark", job_id)
            _update_job(job_id, progress=92, message="Watermark ekleniyor...")
            watermarked_path = final_path.replace(".mp4", "_wm.mp4")
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", final_path, "-i", watermark_path,
                    "-filter_complex",
                    "[1:v]scale=iw/6:-1,format=rgba,colorchannelmixer=aa=0.7[wm];"
                    "[0:v][wm]overlay=W-w-20:H-h-20[out]",
                    "-map", "[out]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    watermarked_path,
                ], check=True, capture_output=True, timeout=120)
                os.replace(watermarked_path, final_path)
                logger.info("[%s] Watermark applied", job_id)
            except Exception as wm_err:
                logger.warning("[%s] Watermark failed (continuing without): %s", job_id, wm_err)

        relative = final_path.replace("\\", "/")

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Video başarıyla üretildi!",
            result_url=f"/outputs/{relative.split('/outputs/')[-1]}",
        )
        logger.info("[%s] Pipeline fully completed – %s", job_id, final_path)

    except Exception as exc:
        logger.exception("[%s] Pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=f"Hata: {exc}",
        )
