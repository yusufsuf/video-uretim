"""Pipeline – orchestrates the full fashion video generation workflow.

New Flow (v2):
1. Analyse the garment (GPT-4o Vision)
2. Generate multi-scene prompts (GPT-4o – cinematography rules)
3. Generate background image (Nano Banana 2 via fal.ai)
4. Generate multishot video (Kling 3.0 Pro with elements + start_image)
5. (Optional) Watermark overlay
"""

import ipaddress
import logging
import os
import socket
import subprocess
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from supabase import create_client, Client
from config import settings
from models import (
    DefileCollectionRequest,
    DefileOutfit,
    DressAnalysisResult,
    GenerationRequest,
    JobResponse,
    JobStatus,
    MultiScenePrompt,
)
from services.analysis_service import analyse_dress, generate_multi_scene_prompt, generate_defile_multishot_prompt
from services.nano_banana_service import generate_background, generate_scene_frame
from services.video_service import (
    download_file,
    generate_multishot_video,
    extract_last_frame,
    upload_to_fal,
    concatenate_clips,
)
import shutil

logger = logging.getLogger(__name__)

# In-memory job store
jobs: dict[str, JobResponse] = {}


_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_ssrf_safe(url: str) -> bool:
    """Return True only if the URL resolves to a public IP (not internal/loopback)."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        ip = ipaddress.ip_address(socket.gethostbyname(host))
        return not any(ip in net for net in _PRIVATE_NETS)
    except Exception:
        return False


async def _to_fal_url(url: str) -> str:
    """Ensure a URL is reachable from fal.ai by re-uploading to fal.ai CDN if needed.

    NB2 Edit (and some other fal.ai models) cannot access Supabase storage or
    local-server URLs. This helper downloads the file and re-uploads it so
    fal.ai can fetch it reliably.
    """
    # Already on fal.ai CDN — skip
    if any(s in url for s in ("fal.media", "fal.run", "v3.fal.media", "storage.googleapis.com/isolate")):
        return url
    clean_url: str = url.split("?")[0]  # strip trailing ?. artefacts from Supabase SDK
    if not _is_ssrf_safe(clean_url):
        raise ValueError(f"SSRF blocked: URL resolves to private/internal address: {clean_url}")
    try:
        local = await download_file(clean_url, settings.TEMP_DIR, extension=".jpg")
        fal_url = await upload_to_fal(local)
        try:
            os.remove(local)
        except Exception:
            pass
        logger.info("Re-uploaded to fal.ai CDN: %s → %s", clean_url[:60], fal_url[:60])
        return fal_url
    except Exception as e:
        logger.warning("Could not re-upload %s to fal.ai CDN (%s) — using original", clean_url[:60], e)
        return url


@lru_cache(maxsize=1)
def _get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _load_history() -> list[dict]:
    """Load job history from Supabase jobs table."""
    try:
        db = _get_supabase()
        res = db.table("jobs").select("*").order("created_at", desc=True).limit(100).execute()
        return res.data or []
    except Exception as e:
        logger.error("Failed to load history: %s", e)
        return []


def _save_to_history(job: JobResponse):
    """Save completed job to Supabase jobs table."""
    try:
        db = _get_supabase()
        entry: dict = {
            "job_id": job.job_id,
            "status": job.status.value,
            "message": job.message,
            "result_url": job.result_url,
            "created_at": datetime.now().isoformat(),
        }
        if job.analysis:
            entry["analysis_summary"] = f"{job.analysis.garment_type} - {job.analysis.color}"
        db.table("jobs").insert(entry).execute()
    except Exception as e:
        logger.error("Failed to save history: %s", e)


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
    library_style_url: Optional[str] = None,
    background_extra_urls: Optional[list] = None,
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
            style_image_url=library_style_url,
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

        # If user provided per-shot configs, override GPT's durations (safeguard)
        if request.shots and len(request.shots) == len(scene_prompt.scenes):
            for scene, shot in zip(scene_prompt.scenes, request.shots):
                scene.duration = str(shot.duration)

        # Build background pool for per-shot cycling
        # If multiple backgrounds provided: each shot gets its own background (no chaining)
        # If single background: chain via last-frame extraction
        bg_pool = [background_url] + (background_extra_urls or [])
        multi_bg = len(bg_pool) > 1
        logger.info("[%s] Background pool: %d image(s), mode=%s",
                    job_id, len(bg_pool), "cycle" if multi_bg else "chain")

        # Garment reference URLs for NB2 scene composition
        # NB2 Edit cannot access Supabase / local-server URLs — re-upload to fal.ai CDN
        garment_ref_urls = [front_url] + ([side_url] if side_url else []) + ([back_url] if back_url else [])

        _update_job(job_id, progress=52, message="Görseller hazırlanıyor...")
        fal_garment_refs: list = []
        for gurl in garment_ref_urls:
            fal_garment_refs.append(await _to_fal_url(gurl))
        logger.info("[%s] Garment refs on fal.ai CDN: %d", job_id, len(fal_garment_refs))

        # Background URLs also need fal.ai CDN upload — NB2 Edit cannot access Supabase URLs
        fal_bg_pool: list = []
        for bg_url in bg_pool:
            fal_bg_pool.append(await _to_fal_url(bg_url))
        logger.info("[%s] Background pool on fal.ai CDN: %d", job_id, len(fal_bg_pool))

        # ── Per-shot execution: NB2 compose + Kling animate ──────
        all_scenes = scene_prompt.scenes
        n_shots = len(all_scenes)
        logger.info("[%s] %d scene(s) — NB2 compose + Kling per shot", job_id, n_shots)

        clip_paths = []
        current_start_image = fal_bg_pool[0]

        for shot_idx, scene in enumerate(all_scenes):
            base_progress = 55 + int((shot_idx / n_shots) * 28)

            # Choose start image: cycle pool (multi-bg) or chain from previous clip
            start_image = fal_bg_pool[shot_idx % len(fal_bg_pool)] if multi_bg else current_start_image
            shot_duration = int(scene.duration)

            logger.info("[%s] Shot %d/%d: %ds", job_id, shot_idx + 1, n_shots, shot_duration)

            # ── 4a: Compose scene frame via Nano Banana 2 Edit ───
            _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                        progress=base_progress,
                        message=f"Sahne {shot_idx + 1}/{n_shots} kompoze ediliyor...")

            angle = (scene.camera_angle or "eye_level").replace("_", " ")
            size = (scene.shot_size or "full_body").replace("_", " ")
            garment_hint = f"{analysis.color} {analysis.garment_type}"
            nb2_prompt = (
                f"Fashion editorial photo: the first image is the background scene — keep it exactly. "
                f"Place a fashion model wearing the {garment_hint} from the garment reference images "
                f"(images 2 onward) into this background. "
                f"Preserve every garment detail: exact colors, pattern, texture, cut, length. "
                f"Camera angle: {angle}. Shot framing: {size}. "
                f"Context: {scene.prompt}. "
                f"Professional fashion photography, sharp focus, natural elegant pose."
            )

            scene_frame_url = await generate_scene_frame(
                image_urls=[start_image] + fal_garment_refs,
                prompt=nb2_prompt,
                aspect_ratio=aspect_ratio,
            )
            logger.info("[%s] Shot %d scene frame: %s", job_id, shot_idx + 1, scene_frame_url[:80])

            # ── 4b: Animate scene frame via Kling ────────────────
            _update_job(job_id, progress=base_progress + int(14 / n_shots),
                        message=f"Sahne {shot_idx + 1}/{n_shots} animate ediliyor...")

            clip_url = await generate_multishot_video(
                start_image_url=scene_frame_url,
                multi_prompt=[{"duration": scene.duration, "prompt": scene.prompt}],
                elements=elements,
                duration=str(shot_duration),
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
            )

            clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
            clip_paths.append(clip_path)
            logger.info("[%s] Shot %d downloaded: %s", job_id, shot_idx + 1, clip_path)

            # Chain via last frame only in single-background mode
            if not multi_bg and shot_idx < n_shots - 1:
                logger.info("[%s] Extracting last frame for chaining...", job_id)
                last_frame_path = extract_last_frame(clip_path, settings.TEMP_DIR)
                current_start_image = await upload_to_fal(last_frame_path)
                try:
                    os.remove(last_frame_path)
                except Exception:
                    pass
                logger.info("[%s] Chain: next shot starts from %s", job_id, current_start_image[:80])

        # Merge clips or move single clip to OUTPUT_DIR
        merge_msg = "Sahneler birleştiriliyor..." if n_shots > 1 else "Video indiriliyor..."
        _update_job(job_id, progress=85, message=merge_msg)

        final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
        if n_shots > 1:
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

        # Supabase Storage'a yükle
        result_url = None
        try:
            _update_job(job_id, progress=96, message="Video yükleniyor...")
            db = _get_supabase()
            filename = os.path.basename(final_path)
            with open(final_path, "rb") as f:
                db.storage.from_("videos").upload(
                    path=filename,
                    file=f.read(),
                    file_options={"content-type": "video/mp4"},
                )
            result_url = db.storage.from_("videos").get_public_url(filename)
            logger.info("[%s] Uploaded to Supabase Storage: %s", job_id, result_url)
        except Exception as upload_err:
            logger.warning("[%s] Supabase upload failed, falling back to local URL: %s", job_id, upload_err)
            relative = final_path.replace("\\", "/")
            result_url = f"/outputs/{relative.split('/outputs/')[-1]}"

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Video başarıyla üretildi!",
            result_url=result_url,
        )
        logger.info("[%s] Pipeline fully completed – %s", job_id, final_path)

    except Exception as exc:
        logger.exception("[%s] Pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=f"Hata: {exc}",
        )


# ── Fixed runway prompts for defile collection mode ──────────────────────────
# Rich library of Prada-style cinematography techniques.
# Organised by category; cycles across all shots so every defile gets varied angles.

DEFILE_SHOT_CONFIGS = [

    # ── WIDE / ESTABLISHING ───────────────────────────────────────────────────
    {"prompt": "wide establishing shot, fashion model walks from far backstage end of runway toward camera, white catwalk stretches full length, audience seated both sides, smooth cinematic dolly-in, full body tracking",
     "nb2_angle": "model as small full-body figure at the far end of the runway, centered, walking toward camera, wide establishing composition, full catwalk visible",
     "view": "front"},

    {"prompt": "ultra-wide runway shot, model is a small silhouette at far end and walks closer, enormous luxury fashion show venue, high arched ceiling, dramatic stage lighting, architectural grandeur",
     "nb2_angle": "model as tiny silhouette at the very far end of the runway, ultra-wide composition, full venue architecture dominates, model centered at vanishing point",
     "view": "front"},

    {"prompt": "overhead bird's-eye crane shot, fashion model walks white runway from above, geometric symmetry of catwalk, audience rows on both sides create framing, pure graphic composition",
     "nb2_angle": "overhead bird's-eye view, model seen from directly above on the runway, geometric top-down composition, catwalk lines visible",
     "view": "front"},

    {"prompt": "wide telephoto compressed shot, long lens flattens runway perspective, fashion model sharp in foreground walking toward camera, audience rows stacked abstractly behind, cinematic compression",
     "nb2_angle": "frontal full body, telephoto compression, model sharp and centered, audience blurred and stacked behind, medium-wide framing",
     "view": "front"},

    {"prompt": "wide shot from elevated balcony angle, fashion model walks runway below at diagonal, strong geometric composition, venue interior fills frame, grand architectural scale",
     "nb2_angle": "model seen from elevated high angle at diagonal, three-quarter overhead view, full body visible from above-front, wide venue composition",
     "view": "front"},

    # ── MEDIUM FRONTAL ────────────────────────────────────────────────────────
    {"prompt": "medium full-body tracking shot, fashion model walks confidently toward camera on white runway, eye level, white barriers frame both sides, sharp focus, 24fps cinematic movement",
     "nb2_angle": "full body frontal, eye level, model centered walking directly toward camera, head to feet visible, confident stride",
     "view": "front"},

    {"prompt": "medium shot with slight low angle, fashion model walks toward camera, angle accentuates height and posture, audience soft blur behind model, white floor reflects runway lights",
     "nb2_angle": "full body frontal, slight low angle accentuating height, model walking toward camera, upward perspective",
     "view": "front"},

    {"prompt": "medium-close shot, fashion model fills frame from knees to head, walks directly toward camera, runway perspective lines converge behind, confident purposeful stride",
     "nb2_angle": "frontal from knees to head, model fills frame, walking directly toward camera, upper and mid body dominant",
     "view": "front"},

    {"prompt": "medium tracking shot, fashion model in three-quarter angle walks toward camera, face and front-side of outfit both visible, dynamic forward movement, shallow depth of field on background",
     "nb2_angle": "three-quarter front-left angle, face and front-left side of outfit visible, model angled to show depth, full body",
     "view": "front"},

    {"prompt": "medium shot, fashion model walks runway toward camera at golden-hour style dramatic lighting, garment silhouette crisp against bright venue backdrop, editorial fashion film look",
     "nb2_angle": "full body frontal centered, dramatic backlighting from venue, model silhouette crisp, walking toward camera",
     "view": "front"},

    # ── CLOSE-UP DETAILS ─────────────────────────────────────────────────────
    {"prompt": "close-up on jacket and upper outfit as fashion model walks, camera tracks at torso height, fabric weave texture visible, slight motion blur from forward movement, shallow depth of field",
     "nb2_angle": "tight close-up on jacket and upper torso, frontal, garment fabric texture and lapels dominant, waist to shoulders",
     "view": "front"},

    {"prompt": "extreme close-up on garment fabric flowing with movement, textile drape and material texture in sharp detail, model's body rhythmic walking motion, kinetic fashion photography",
     "nb2_angle": "extreme close-up on garment fabric and drape at mid-torso, frontal, textile surface texture fills frame",
     "view": "front"},

    {"prompt": "close-up on collar and neckline as fashion model walks, craftsmanship and garment construction visible, runway lights create fabric highlights, bokeh background",
     "nb2_angle": "tight close-up on collar, neckline and upper chest, frontal, garment construction at neck, head partially visible above",
     "view": "front"},

    {"prompt": "close-up on hem and skirt movement as model walks, fabric swings with each step, polished white runway floor reflects garment, kinetic motion captured",
     "nb2_angle": "tight close-up on lower body and hem, frontal, skirt or pants bottom and feet visible, fabric movement at hem",
     "view": "front"},

    {"prompt": "tight shot on jacket lapels and chest detail, camera tracks model torso, fabric surface and stitching visible, cinematic depth of field, editorial fashion detail",
     "nb2_angle": "tight close-up on lapels and chest detail, frontal upper body, jacket construction and stitching visible",
     "view": "front"},

    {"prompt": "close-up on garment back details as model walks away, fabric drape and construction from behind, audience blur peripheral, runway light catches material surface",
     "nb2_angle": "close-up on garment back, model facing away from camera walking down runway, back fabric and construction visible from behind",
     "view": "back"},

    # ── ACCESSORY EXTREME CLOSE-UPS ──────────────────────────────────────────
    {"prompt": "extreme close-up on handbag swinging as fashion model walks runway, leather texture and metal hardware in sharp detail, motion blur background, luxury accessory focus",
     "nb2_angle": "tight close-up on model hand and handbag at side, frontal-side view of arm and bag, accessory detail",
     "view": "front"},

    {"prompt": "extreme close-up on footwear as model steps on white runway floor, shoe sole and heel craftsmanship visible, ground-level perspective, each step sharp and deliberate",
     "nb2_angle": "ground-level close-up on model feet and shoes on runway, frontal low angle, footwear fills frame",
     "view": "front"},

    {"prompt": "close-up on wrist and jewellery as model's arm swings walking, metal catches runway light, shallow depth of field, luxury accessory detail shot",
     "nb2_angle": "tight close-up on wrist and jewellery, model arm at side, frontal-side detail of wrist accessories",
     "view": "front"},

    {"prompt": "tight close-up on belt and waist construction as model walks, garment materials and stitching visible, cinematic focus, editorial craftsmanship detail",
     "nb2_angle": "tight close-up on waist and belt, frontal mid-section, belt hardware and garment construction at waist",
     "view": "front"},

    # ── LOW ANGLE ────────────────────────────────────────────────────────────
    {"prompt": "extreme low angle shot, camera at floor level, fashion model's legs and feet walking toward camera on white runway, pants hem in motion, dynamic footstep rhythm",
     "nb2_angle": "extreme low angle from floor level, looking up at model legs and lower body walking toward camera, dramatic upward perspective",
     "view": "front"},

    {"prompt": "low angle shot looking up at fashion model, camera slightly below waist height, model towers against bright venue ceiling, powerful editorial perspective, confident silhouette",
     "nb2_angle": "low angle looking up at model, full body from below, model towers above with venue ceiling behind, powerful upward perspective",
     "view": "front"},

    {"prompt": "ground level rear low angle, model's feet and lower legs walking away, white runway floor stretches ahead, audience feet visible at both sides, rhythmic stepping captured",
     "nb2_angle": "ground-level low angle from behind model, looking up at model walking away, lower legs and back of garment from behind",
     "view": "back"},

    # ── REAR / FOLLOWING ─────────────────────────────────────────────────────
    {"prompt": "rear tracking shot, camera directly behind fashion model walking down runway, full back view of outfit, audience rows blur on both sides, smooth following movement",
     "nb2_angle": "full back view, model facing directly away from camera, complete back of garment visible head to feet, rear tracking composition",
     "view": "back"},

    {"prompt": "over-shoulder rear tracking, camera slightly to side and behind model, three-quarter back view, runway perspective lines visible ahead, audience in soft periphery",
     "nb2_angle": "three-quarter back-right view, model right shoulder and back-side of garment visible, over-shoulder perspective, runway visible ahead",
     "view": "back"},

    {"prompt": "low rear angle tracking shot, camera follows model from low behind, emphasises silhouette and posture, back of garment and collar against bright venue, editorial angle",
     "nb2_angle": "low angle from behind model, looking up at back of garment, posture and silhouette from rear, collar and back detail visible",
     "view": "back"},

    # ── LATERAL / SIDE PROFILE ───────────────────────────────────────────────
    {"prompt": "side profile tracking shot, fashion model walks parallel to camera along runway, full outfit silhouette from side, arms swinging, audience soft blur, smooth lateral camera",
     "nb2_angle": "full left side profile, model walking parallel to camera, complete outfit silhouette from the side head to feet, lateral composition",
     "view": "side"},

    {"prompt": "three-quarter front-side tracking, camera at 45 degrees to model's path, face and front-side of outfit both visible, dynamic angle, smooth follow movement",
     "nb2_angle": "three-quarter front-right angle at 45 degrees, model face and front-right side of outfit visible, dynamic diagonal composition",
     "view": "side"},

    {"prompt": "side close-up tracking at torso level, camera follows model laterally, outfit side profile and construction visible from side, shallow depth of field, editorial",
     "nb2_angle": "tight close-up from right side at torso level, outfit side construction and silhouette from the side, lateral editorial detail",
     "view": "side"},

    {"prompt": "wide lateral shot, camera perpendicular to runway, fashion model walks through frame, audience visible on near side, full silhouette passes, wide cinematic composition",
     "nb2_angle": "wide left side profile, model perpendicular to camera, full silhouette visible from side, wide lateral composition with runway extending",
     "view": "side"},

    # ── END OF RUNWAY TURN ───────────────────────────────────────────────────
    {"prompt": "fashion model reaches end of runway, dramatic pause then slow deliberate pivot, camera holds and circles slightly, full outfit front-to-back reveal, editorial runway moment",
     "nb2_angle": "model at end of runway in pivot pose, three-quarter angle showing both front and turning side, poised confident stance",
     "view": "front"},

    {"prompt": "model at runway end, slow-motion pivot, camera frontal holds as model turns, face composition changes through the turn, fabric swings with the movement, confident expression",
     "nb2_angle": "frontal medium at runway end, model in slight pivot, face toward camera, fabric in motion from turn",
     "view": "front"},

    {"prompt": "end of runway turn from side angle, model pivots away from far end and begins walk back, profile to three-quarter reveal, audience beyond in soft focus",
     "nb2_angle": "side profile at runway end, model mid-pivot showing full side silhouette, three-quarter angle during turn",
     "view": "side"},

    # ── SLOW MOTION ──────────────────────────────────────────────────────────
    {"prompt": "slow motion medium shot, fashion model walks runway in cinematic slow-mo, fabric ripples and flows with each step, hair movement visible, editorial fashion film quality",
     "nb2_angle": "full body frontal, model centered, fabric flowing, elegant slow motion pose, head to feet visible",
     "view": "front"},

    {"prompt": "slow motion close-up, garment fabric and drape in slow motion, material waves and swings captured in fine detail, kinetic beauty of fashion in motion",
     "nb2_angle": "extreme close-up on fabric drape and movement at mid-torso, frontal, fabric flowing in motion",
     "view": "front"},

    {"prompt": "slow motion low angle, model's legs and garment hem in slow motion, fabric swings dramatically, white runway floor in sharp foreground, editorial slow-mo moment",
     "nb2_angle": "low angle frontal, legs and hem dominant, fabric at hem flowing, runway floor in sharp foreground",
     "view": "front"},

    # ── FACE / EXPRESSION ────────────────────────────────────────────────────
    {"prompt": "close-up on fashion model's face while walking runway, stoic focused editorial expression, eyes straight ahead, venue lights sculpt face, garment collar visible below",
     "nb2_angle": "close-up on face and upper chest, frontal, stoic editorial expression, garment collar and neckline visible below face",
     "view": "front"},

    {"prompt": "medium-close shot centered on face and upper body, model walks toward camera, expression powerful and composed, garment neckline prominent, runway recedes behind in bokeh",
     "nb2_angle": "medium-close frontal, face and upper body, model looking directly at camera, garment neckline prominent",
     "view": "front"},

    {"prompt": "tight face tracking shot, camera ahead of walking model, dramatic side lighting from runway lights, editorial intensity, background of audience out of focus",
     "nb2_angle": "tight face close-up, frontal with dramatic directional lighting, model face fills upper frame, garment shoulder and collar visible",
     "view": "front"},

    # ── ARCHITECTURAL / ENVIRONMENTAL ────────────────────────────────────────
    {"prompt": "wide architectural shot, grand fashion show venue with model as small figure, dramatic columns or ceiling structure visible, model walks runway through monumental interior",
     "nb2_angle": "model as small full-body figure centered in grand architectural space, wide environmental composition, venue structure dominates",
     "view": "front"},

    {"prompt": "medium shot emphasising venue geometry, white runway lines converge to vanishing point behind model, symmetrical composition, model centered walking toward camera",
     "nb2_angle": "full body frontal centered, symmetrical runway perspective lines converge behind model, geometric environmental composition",
     "view": "front"},

    {"prompt": "long-lens medium shot, fashion model isolated against abstract soft-focus audience, telephoto compression renders background as warm blurred mass, model crisp and sharp",
     "nb2_angle": "medium frontal, telephoto-style compression, model sharp against very blurred audience background, isolated editorial look",
     "view": "front"},
]


async def run_defile_collection_pipeline(
    job_id: str,
    request: DefileCollectionRequest,
):
    """Execute the defile collection pipeline.

    New flow (per outfit):
      1. NB2 compose: background + outfit images → establishing scene frame
      2. GPT-4o Vision: analyze scene frame → generate multishot prompts
      3. Single Kling call with multi_prompt list → one cohesive video per outfit
      4. Concatenate all outfit clips
    """
    try:
        n_outfits = len(request.outfits)
        shot_configs = request.shot_configs
        n_shots = len(shot_configs)
        total_duration = sum(s.duration for s in shot_configs)
        total_clips = n_outfits  # one clip per outfit

        logger.info("[%s] Defile: %d outfits, %d shots/outfit, %ds total per outfit",
                    job_id, n_outfits, n_shots, total_duration)

        # ── Step 1: Generate/fetch runway background ──────────────────────
        _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=5,
                    message="Pist arka planı hazırlanıyor...")

        if request.runway_background_url:
            background_url = request.runway_background_url
            logger.info("[%s] Defile: using provided background %s", job_id, background_url[:80])
        else:
            logger.info("[%s] Defile: generating runway background via Nano Banana", job_id)
            background_url = await generate_background(
                prompt="high-end fashion runway, empty catwalk, dramatic stage lighting, luxury fashion show venue, no people, architectural interior",
                aspect_ratio=request.aspect_ratio,
            )
            logger.info("[%s] Defile: background generated %s", job_id, background_url[:80])

        _update_job(job_id, progress=12, message="Arka plan hazır. Görseller yükleniyor...")

        # ── Step 2: Upload all images to fal.ai CDN ───────────────────────
        all_bg_urls = [background_url] + (request.runway_background_extra_urls or [])
        fal_bg_pool: list = []
        for bg_url in all_bg_urls:
            fal_bg_pool.append(await _to_fal_url(bg_url))
        logger.info("[%s] Defile: bg pool size=%d", job_id, len(fal_bg_pool))

        fal_outfits: list = []
        for outfit in request.outfits:
            fal_front = await _to_fal_url(outfit.front_url)
            fal_side = await _to_fal_url(outfit.side_url) if outfit.side_url else None
            fal_back = await _to_fal_url(outfit.back_url) if outfit.back_url else None
            fal_outfits.append((fal_front, fal_side, fal_back))
        logger.info("[%s] Defile: %d outfits on fal.ai CDN", job_id, len(fal_outfits))

        # ── Step 3: Per-outfit: NB2 compose → GPT prompts → Kling ────────
        clip_paths: list = []

        for outfit_idx, outfit in enumerate(request.outfits):
            outfit_name = outfit.name or f"Kıyafet {outfit_idx + 1}"
            fal_front, fal_side, fal_back = fal_outfits[outfit_idx]
            base_progress = 20 + int((outfit_idx / n_outfits) * 65)

            # Background for this outfit (cycle pool)
            bg_for_outfit = fal_bg_pool[outfit_idx % len(fal_bg_pool)]

            # Garment refs: front + side (best frontal overview for NB2)
            garment_refs = [fal_front] + ([fal_side] if fal_side else [])

            # ── 3a: NB2 — compose establishing scene frame ────────────────
            _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                        progress=base_progress,
                        message=f"{outfit_name} — sahne kompoze ediliyor ({outfit_idx + 1}/{n_outfits})...")

            nb2_prompt = (
                "Fashion runway show editorial photo: the first image is the runway scene — "
                "preserve it exactly including architecture, lighting, floor, and audience. "
                "Place a tall fashion model at the CENTER-BACK of the runway catwalk, "
                "roughly two-thirds of the way down the runway from the camera — "
                "a significant length of empty runway must be clearly visible between the model and the camera foreground. "
                "wearing the garment from the reference images (images 2 onward). "
                "Full body visible, frontal medium-wide shot, confident runway stance. "
                "Preserve all garment details: exact colors, fabric, cut, silhouette, length. "
                "CRITICAL: the garment hem must touch and rest exactly on the runway floor — "
                "the bottom of the garment grazes the floor surface. "
                "Shoes and feet must NOT be visible under any circumstances — "
                "the hem completely covers and conceals the feet. "
                "Professional fashion show photography, sharp focus, editorial quality."
            )

            scene_frame_url = await generate_scene_frame(
                image_urls=[bg_for_outfit] + garment_refs,
                prompt=nb2_prompt,
                aspect_ratio=request.aspect_ratio,
            )
            logger.info("[%s] Outfit %d/%d scene frame: %s",
                        job_id, outfit_idx + 1, n_outfits, scene_frame_url[:80])

            # ── 3b: GPT-4o Vision — analyze frame + generate multishot prompts
            _update_job(job_id, progress=base_progress + int(20 / n_outfits),
                        message=f"{outfit_name} — senaryo üretiliyor ({outfit_idx + 1}/{n_outfits})...")

            multi_prompt = await generate_defile_multishot_prompt(
                scene_frame_url=scene_frame_url,
                shot_configs=shot_configs,
                outfit_name=outfit_name,
            )
            logger.info("[%s] Outfit %d/%d prompts: %d shots, %ds total",
                        job_id, outfit_idx + 1, n_outfits, len(multi_prompt), total_duration)

            # ── 3c: Kling — single multishot call per outfit ──────────────
            _update_job(job_id, progress=base_progress + int(35 / n_outfits),
                        message=f"{outfit_name} — video üretiliyor ({outfit_idx + 1}/{n_outfits})...")

            clip_url = await generate_multishot_video(
                start_image_url=scene_frame_url,
                multi_prompt=multi_prompt,
                elements=None,  # outfit baked into start frame via NB2
                duration=str(total_duration),
                aspect_ratio=request.aspect_ratio,
                generate_audio=request.generate_audio,
            )

            clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
            clip_paths.append(clip_path)
            logger.info("[%s] Outfit %d/%d clip downloaded: %s",
                        job_id, outfit_idx + 1, n_outfits, clip_path)

        # ── Step 4: Concatenate all outfit clips ──────────────────────────
        _update_job(job_id, progress=87,
                    message=f"{n_outfits} kıyafet birleştiriliyor...")

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

        logger.info("[%s] Defile final video: %s", job_id, final_path)

        # ── Step 5: Upload to Supabase ────────────────────────────────────
        result_url = None
        try:
            _update_job(job_id, progress=96, message="Video yükleniyor...")
            db = _get_supabase()
            filename = os.path.basename(final_path)
            with open(final_path, "rb") as f:
                db.storage.from_("videos").upload(
                    path=filename,
                    file=f.read(),
                    file_options={"content-type": "video/mp4"},
                )
            result_url = db.storage.from_("videos").get_public_url(filename)
            logger.info("[%s] Defile uploaded: %s", job_id, result_url)
        except Exception as upload_err:
            logger.warning("[%s] Supabase upload failed: %s", job_id, upload_err)
            relative = final_path.replace("\\", "/")
            result_url = f"/outputs/{relative.split('/outputs/')[-1]}"

        _update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message=f"Defile videosu hazır! {n_outfits} kıyafet, kıyafet başına {n_shots} sahne.",
            result_url=result_url,
        )
        logger.info("[%s] Defile pipeline completed", job_id)

    except Exception as exc:
        logger.exception("[%s] Defile pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=f"Hata: {exc}",
        )
