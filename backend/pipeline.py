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
from services.video_service import (
    download_file,
    generate_multishot_video,
    extract_last_frame,
    upload_to_fal,
    concatenate_clips,
)
import shutil

logger = logging.getLogger(__name__)

# Scenes per Kling API call — smaller chunks = higher consistency via last-frame chaining
CHUNK_SIZE = 2

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
    reference_image_url: Optional[str],
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
        scene_count = max(1, min(8, scene_count))

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

        # ── Step 3: Background image ─────────────────────────────
        if reference_image_url:
            # User uploaded a reference background — use it directly, skip Nano Banana
            background_url = reference_image_url
            logger.info("[%s] Step 3 – Using uploaded reference image as background: %s", job_id, background_url[:100])
            _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=50, message="Yüklenen arka plan kullanılıyor...")
        else:
            # No reference — generate background via Nano Banana 2
            _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=35, message="Arka plan üretiliyor...")
            logger.info("[%s] Step 3 – Generating background image via Nano Banana 2", job_id)

            bg_prompt = scene_prompt.background_image_prompt
            logger.info("[%s] Background prompt: %s", job_id, bg_prompt[:120])

            background_url = await generate_background(
                prompt=bg_prompt,
                aspect_ratio=aspect_ratio,
            )
            logger.info("[%s] Background generated: %s", job_id, background_url[:100])
            _update_job(job_id, progress=50, message="Arka plan hazır. Video üretiliyor...")

        # ── Step 4: Build elements + generate multishot video (chained) ─
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

        # Split scenes into chunks for last-frame chaining
        all_scenes = scene_prompt.scenes
        chunks = [all_scenes[i:i+CHUNK_SIZE] for i in range(0, len(all_scenes), CHUNK_SIZE)]
        n_chunks = len(chunks)
        logger.info("[%s] %d scenes → %d chunk(s) of max %d", job_id, len(all_scenes), n_chunks, CHUNK_SIZE)

        clip_paths = []
        current_start_image = background_url

        for chunk_idx, chunk in enumerate(chunks):
            chunk_duration = sum(int(s.duration) for s in chunk)
            chunk_multi_prompt = [{"duration": s.duration, "prompt": s.prompt} for s in chunk]
            chunk_progress = 55 + int((chunk_idx / n_chunks) * 28)
            chunk_msg = (
                f"Sahne grubu {chunk_idx + 1}/{n_chunks} üretiliyor..."
                if n_chunks > 1 else "Video üretiliyor..."
            )
            _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                        progress=chunk_progress, message=chunk_msg)
            logger.info("[%s] Chunk %d/%d: %d shots, %ds, start=%s...",
                        job_id, chunk_idx + 1, n_chunks, len(chunk),
                        chunk_duration, current_start_image[:60])

            clip_url = await generate_multishot_video(
                start_image_url=current_start_image,
                multi_prompt=chunk_multi_prompt,
                elements=elements,
                duration=str(chunk_duration),
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
            )

            clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
            clip_paths.append(clip_path)
            logger.info("[%s] Chunk %d downloaded: %s", job_id, chunk_idx + 1, clip_path)

            # Extract last frame for next chunk (not needed after the last chunk)
            if chunk_idx < n_chunks - 1:
                logger.info("[%s] Extracting last frame for chaining...", job_id)
                last_frame_path = extract_last_frame(clip_path, settings.TEMP_DIR)
                current_start_image = await upload_to_fal(last_frame_path)
                try:
                    os.remove(last_frame_path)
                except Exception:
                    pass
                logger.info("[%s] Chain: next clip starts from %s", job_id, current_start_image[:80])

        # Merge clips or move single clip to OUTPUT_DIR
        merge_msg = "Sahneler birleştiriliyor..." if n_chunks > 1 else "Video indiriliyor..."
        _update_job(job_id, progress=85, message=merge_msg)

        final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
        if n_chunks > 1:
            concatenate_clips(clip_paths, final_path)
            for p in clip_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
        else:
            shutil.move(clip_paths[0], final_path)

        logger.info("[%s] Final video: %s", job_id, final_path)

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
