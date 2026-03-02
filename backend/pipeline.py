"""Pipeline – orchestrates the full fashion video generation workflow.

Steps:
1. Analyse the garment (GPT-4o Vision)
2. Preprocess images (Claid API – background removal + enhance)
3. Generate multi-scene prompts (GPT-4o)
4. Virtual Try-On (Fal.ai IDM-VTON)
5. Generate video clips per scene (Fal.ai Kling v3 Pro)
6. Merge clips into final video (FFmpeg)
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
    PhotoType,
)
from services.analysis_service import analyse_dress, generate_multi_scene_prompt
from services.claid_service import preprocess_garment
from services.video_service import (
    download_file,
    generate_video,
    virtual_try_on,
)

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
    # Remove large nested objects to keep history lean
    if entry.get("analysis"):
        entry["analysis_summary"] = f"{entry['analysis'].get('garment_type', '')} - {entry['analysis'].get('color', '')}"
    entry.pop("analysis", None)
    entry.pop("scene_prompt", None)

    history.insert(0, entry)  # newest first
    # Keep last 100 entries
    history = history[:100]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _update_job(job_id: str, **kwargs):
    if job_id in jobs:
        for k, v in kwargs.items():
            setattr(jobs[job_id], k, v)
        # Auto-save to history on completion or failure
        if jobs[job_id].status in (JobStatus.COMPLETED, JobStatus.FAILED):
            try:
                _save_to_history(jobs[job_id])
            except Exception as e:
                logger.error("Failed to save job history: %s", e)


def _merge_videos_ffmpeg(clip_paths: list[str], output_path: str) -> str:
    """Concatenate multiple video clips into one using FFmpeg."""
    # Create concat list file
    list_path = output_path + ".txt"
    with open(list_path, "w") as f:
        for clip in clip_paths:
            # Use forward slashes and escape single quotes
            safe = clip.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ]

    logger.info("FFmpeg merge: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr)
        # Fallback: try re-encoding
        cmd_reencode = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ]
        result2 = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=180)
        if result2.returncode != 0:
            logger.error("FFmpeg reencode stderr: %s", result2.stderr)
            raise RuntimeError(f"FFmpeg failed: {result2.stderr[:200]}")

    # Cleanup
    try:
        os.remove(list_path)
    except OSError:
        pass

    return output_path


async def run_pipeline(
    job_id: str,
    front_path: str,
    back_path: Optional[str],
    reference_image_url: Optional[str],
    reference_image_path: Optional[str],
    reference_video_url: Optional[str],
    request: GenerationRequest,
    front_url: str,
    back_url: Optional[str] = None,
    duration: int = 10,
    scene_count: int = 2,
    video_description: Optional[str] = None,
):
    """Execute the full pipeline asynchronously."""
    try:
        # Clamp values
        duration = max(3, min(60, duration))
        scene_count = max(1, min(10, scene_count))

        # ── Step 1: Analyse the garment ─────────────────────────
        _update_job(job_id, status=JobStatus.ANALYZING, progress=5, message="Elbise analiz ediliyor...")
        logger.info("[%s] Step 1 – Analysing garment", job_id)

        analysis = await analyse_dress(front_path, back_path)
        _update_job(job_id, analysis=analysis, progress=15, message="Elbise analizi tamamlandı.")
        logger.info("[%s] Analysis result: %s", job_id, analysis.garment_type)

        # ── Step 2: Preprocess images via Claid ─────────────────
        _update_job(job_id, status=JobStatus.PREPROCESSING, progress=20, message="Görseller işleniyor...")
        logger.info("[%s] Step 2 – Preprocessing images", job_id)

        is_ghost = analysis.photo_type in (PhotoType.GHOST, PhotoType.FLATLAY)
        processed_front = await preprocess_garment(front_url, local_path=front_path, is_ghost=is_ghost)

        _update_job(job_id, progress=25, message="Görseller hazırlandı.")

        # ── Step 3: Generate multi-scene prompts ────────────────
        _update_job(job_id, progress=30, message="Sahneler planlanıyor...")
        logger.info("[%s] Step 3 – Generating multi-scene prompts (duration=%ds)", job_id, duration)

        scene_prompt = await generate_multi_scene_prompt(
            analysis=analysis,
            request=request,
            total_duration=duration,
            scene_count=scene_count,
            video_description=video_description,
            location_image_path=reference_image_path,
        )
        _update_job(job_id, scene_prompt=scene_prompt, progress=35, message=f"{scene_prompt.scene_count} sahne planlandı.")
        logger.info("[%s] Planned %d scenes", job_id, scene_prompt.scene_count)

        # ── Step 4: Virtual Try-On ──────────────────────────────
        _update_job(job_id, status=JobStatus.GENERATING_VTO, progress=40, message="Elbise mankene giydiriliyor...")
        logger.info("[%s] Step 4 – Virtual Try-On", job_id)

        vto_result = await virtual_try_on(
            garment_image_url=processed_front,
            model_image_url=reference_image_url if not reference_image_path else None,
        )
        _update_job(job_id, progress=50, message="Virtual try-on tamamlandı.")

        # ── Step 5: Generate video clips per scene ──────────────
        _update_job(job_id, status=JobStatus.GENERATING_VIDEO, progress=55, message="Video sahneleri üretiliyor...")
        logger.info("[%s] Step 5 – Generating %d video clips", job_id, scene_prompt.scene_count)

        clip_paths = []
        for i, scene in enumerate(scene_prompt.scenes):
            scene_num = i + 1
            progress = 55 + int((scene_num / scene_prompt.scene_count) * 25)
            _update_job(
                job_id,
                progress=progress,
                message=f"Sahne {scene_num}/{scene_prompt.scene_count} üretiliyor...",
            )
            logger.info("[%s] Generating scene %d/%d (%ds)", job_id, scene_num, scene_prompt.scene_count, scene.duration_seconds)

            # Kling supports "5" or "10" as duration strings
            kling_duration = "10" if scene.duration_seconds > 7 else "5"

            video_url = await generate_video(
                image_url=vto_result,
                prompt=scene.full_scene_prompt,
                duration=kling_duration,
            )

            clip_path = await download_file(video_url, settings.OUTPUT_DIR, extension=f"_scene{scene_num}.mp4")
            clip_paths.append(clip_path)
            logger.info("[%s] Scene %d downloaded: %s", job_id, scene_num, clip_path)

        # ── Step 6: Merge clips ─────────────────────────────────
        if len(clip_paths) > 1:
            _update_job(job_id, status=JobStatus.MERGING, progress=85, message="Sahneler birleştiriliyor...")
            logger.info("[%s] Step 6 – Merging %d clips", job_id, len(clip_paths))

            final_filename = f"{uuid.uuid4().hex}_final.mp4"
            final_path = os.path.join(settings.OUTPUT_DIR, final_filename)
            _merge_videos_ffmpeg(clip_paths, final_path)

            # Clean up individual clips
            for cp in clip_paths:
                try:
                    os.remove(cp)
                except OSError:
                    pass
        else:
            final_path = clip_paths[0]

        relative = final_path.replace("\\", "/")

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Video başarıyla üretildi!",
            result_url=f"/outputs/{relative.split('/outputs/')[-1]}",
        )
        logger.info("[%s] Pipeline completed – %s", job_id, final_path)

    except Exception as exc:
        logger.exception("[%s] Pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=f"Hata: {exc}",
        )
