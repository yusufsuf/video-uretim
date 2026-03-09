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
from services.analysis_service import analyse_dress, generate_multi_scene_prompt
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

DEFILE_PROMPTS = [

    # ── WIDE / ESTABLISHING ───────────────────────────────────────────────────
    # Full runway in frame, architectural grandeur, audience rows visible both sides

    "wide establishing shot, fashion model walks from far backstage end of runway toward camera, white catwalk stretches full length, audience seated both sides, smooth cinematic dolly-in, full body tracking",

    "ultra-wide runway shot, model is a small silhouette at far end and walks closer, enormous luxury fashion show venue, high arched ceiling, dramatic stage lighting, architectural grandeur",

    "overhead bird's-eye crane shot, fashion model walks white runway from above, geometric symmetry of catwalk, audience rows on both sides create framing, pure graphic composition",

    "wide telephoto compressed shot, long lens flattens runway perspective, fashion model sharp in foreground walking toward camera, audience rows stacked abstractly behind, cinematic compression",

    "wide shot from elevated balcony angle, fashion model walks runway below at diagonal, strong geometric composition, venue interior fills frame, grand architectural scale",

    # ── MEDIUM FRONTAL ────────────────────────────────────────────────────────
    # Model approaching camera, fills most of frame, confident stride

    "medium full-body tracking shot, fashion model walks confidently toward camera on white runway, eye level, white barriers frame both sides, sharp focus, 24fps cinematic movement",

    "medium shot with slight low angle, fashion model walks toward camera, angle accentuates height and posture, audience soft blur behind model, white floor reflects runway lights",

    "medium-close shot, fashion model fills frame from knees to head, walks directly toward camera, runway perspective lines converge behind, confident purposeful stride",

    "medium tracking shot, fashion model in three-quarter angle walks toward camera, face and front-side of outfit both visible, dynamic forward movement, shallow depth of field on background",

    "medium shot, fashion model walks runway toward camera at golden-hour style dramatic lighting, garment silhouette crisp against bright venue backdrop, editorial fashion film look",

    # ── CLOSE-UP DETAILS ─────────────────────────────────────────────────────
    # Garment fabric, construction, drape — texture as subject

    "close-up on jacket and upper outfit as fashion model walks, camera tracks at torso height, fabric weave texture visible, slight motion blur from forward movement, shallow depth of field",

    "extreme close-up on garment fabric flowing with movement, textile drape and material texture in sharp detail, model's body rhythmic walking motion, kinetic fashion photography",

    "close-up on collar and neckline as fashion model walks, craftsmanship and garment construction visible, runway lights create fabric highlights, bokeh background",

    "close-up on hem and skirt movement as model walks, fabric swings with each step, polished white runway floor reflects garment, kinetic motion captured",

    "tight shot on jacket lapels and chest detail, camera tracks model torso, fabric surface and stitching visible, cinematic depth of field, editorial fashion detail",

    "close-up on garment back details as model walks away, fabric drape and construction from behind, audience blur peripheral, runway light catches material surface",

    # ── ACCESSORY EXTREME CLOSE-UPS ──────────────────────────────────────────
    # Bag, shoes, jewellery — luxury object as protagonist

    "extreme close-up on handbag swinging as fashion model walks runway, leather texture and metal hardware in sharp detail, motion blur background, luxury accessory focus",

    "extreme close-up on footwear as model steps on white runway floor, shoe sole and heel craftsmanship visible, ground-level perspective, each step sharp and deliberate",

    "close-up on wrist and jewellery as model's arm swings walking, metal catches runway light, shallow depth of field, luxury accessory detail shot",

    "tight close-up on belt and waist construction as model walks, garment materials and stitching visible, cinematic focus, editorial craftsmanship detail",

    # ── LOW ANGLE ────────────────────────────────────────────────────────────
    # Ground level, dramatic upward perspective, legs dominant

    "extreme low angle shot, camera at floor level, fashion model's legs and feet walking toward camera on white runway, pants hem in motion, dynamic footstep rhythm",

    "low angle shot looking up at fashion model, camera slightly below waist height, model towers against bright venue ceiling, powerful editorial perspective, confident silhouette",

    "ground level rear low angle, model's feet and lower legs walking away, white runway floor stretches ahead, audience feet visible at both sides, rhythmic stepping captured",

    # ── REAR / FOLLOWING ─────────────────────────────────────────────────────
    # Behind the model, back of outfit, following movement

    "rear tracking shot, camera directly behind fashion model walking down runway, full back view of outfit, audience rows blur on both sides, smooth following movement",

    "over-shoulder rear tracking, camera slightly to side and behind model, three-quarter back view, runway perspective lines visible ahead, audience in soft periphery",

    "low rear angle tracking shot, camera follows model from low behind, emphasises silhouette and posture, back of garment and collar against bright venue, editorial angle",

    # ── LATERAL / SIDE PROFILE ───────────────────────────────────────────────
    # Model walks parallel to camera, full silhouette visible

    "side profile tracking shot, fashion model walks parallel to camera along runway, full outfit silhouette from side, arms swinging, audience soft blur, smooth lateral camera",

    "three-quarter front-side tracking, camera at 45 degrees to model's path, face and front-side of outfit both visible, dynamic angle, smooth follow movement",

    "side close-up tracking at torso level, camera follows model laterally, outfit side profile and construction visible from side, shallow depth of field, editorial",

    "wide lateral shot, camera perpendicular to runway, fashion model walks through frame, audience visible on near side, full silhouette passes, wide cinematic composition",

    # ── END OF RUNWAY TURN ───────────────────────────────────────────────────
    # Pivot, pause, back-to-front reveal — classic runway moment

    "fashion model reaches end of runway, dramatic pause then slow deliberate pivot, camera holds and circles slightly, full outfit front-to-back reveal, editorial runway moment",

    "model at runway end, slow-motion pivot, camera frontal holds as model turns, face composition changes through the turn, fabric swings with the movement, confident expression",

    "end of runway turn from side angle, model pivots away from far end and begins walk back, profile to three-quarter reveal, audience beyond in soft focus",

    # ── SLOW MOTION ──────────────────────────────────────────────────────────
    # Cinematic slow-mo, fabric kinetics, hair movement

    "slow motion medium shot, fashion model walks runway in cinematic slow-mo, fabric ripples and flows with each step, hair movement visible, editorial fashion film quality",

    "slow motion close-up, garment fabric and drape in slow motion, material waves and swings captured in fine detail, kinetic beauty of fashion in motion",

    "slow motion low angle, model's legs and garment hem in slow motion, fabric swings dramatically, white runway floor in sharp foreground, editorial slow-mo moment",

    # ── FACE / EXPRESSION ────────────────────────────────────────────────────
    # Stoic editorial expression, eyes forward, Prada intensity

    "close-up on fashion model's face while walking runway, stoic focused editorial expression, eyes straight ahead, venue lights sculpt face, garment collar visible below",

    "medium-close shot centered on face and upper body, model walks toward camera, expression powerful and composed, garment neckline prominent, runway recedes behind in bokeh",

    "tight face tracking shot, camera ahead of walking model, dramatic side lighting from runway lights, editorial intensity, background of audience out of focus",

    # ── ARCHITECTURAL / ENVIRONMENTAL ────────────────────────────────────────
    # Venue as character, model within grand space

    "wide architectural shot, grand fashion show venue with model as small figure, dramatic columns or ceiling structure visible, model walks runway through monumental interior",

    "medium shot emphasising venue geometry, white runway lines converge to vanishing point behind model, symmetrical composition, model centered walking toward camera",

    "long-lens medium shot, fashion model isolated against abstract soft-focus audience, telephoto compression renders background as warm blurred mass, model crisp and sharp",
]


async def run_defile_collection_pipeline(
    job_id: str,
    request: DefileCollectionRequest,
):
    """Execute the defile collection pipeline — one Kling call per outfit shot, concatenated."""
    try:
        n_outfits = len(request.outfits)
        shots_per = max(1, min(3, request.shots_per_outfit))
        total_shots = n_outfits * shots_per

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

        _update_job(job_id, progress=15, message="Arka plan hazır. Defile başlıyor...")

        # ── Step 2: Upload background + all outfit images to fal.ai CDN ──────
        _update_job(job_id, progress=16, message="Görseller hazırlanıyor...")
        fal_background_url = await _to_fal_url(background_url)

        fal_outfits: list = []
        for outfit in request.outfits:
            fal_front = await _to_fal_url(outfit.front_url)
            fal_side = await _to_fal_url(outfit.side_url) if outfit.side_url else None
            fal_back = await _to_fal_url(outfit.back_url) if outfit.back_url else None
            fal_outfits.append((fal_front, fal_side, fal_back))
        logger.info("[%s] Defile: bg + %d outfits on fal.ai CDN", job_id, len(fal_outfits))

        # ── Step 3: Per-outfit, per-shot: NB2 compose → Kling animate ────────
        clip_paths: list = []

        for outfit_idx, outfit in enumerate(request.outfits):
            outfit_name = outfit.name or f"Kıyafet {outfit_idx + 1}"
            fal_front, fal_side, fal_back = fal_outfits[outfit_idx]

            # Garment reference list for NB2 (front always present, side/back optional)
            garment_refs = [fal_front] + ([fal_side] if fal_side else []) + ([fal_back] if fal_back else [])

            # Each outfit starts fresh from the runway background
            current_start_image = fal_background_url

            for shot_idx in range(shots_per):
                global_shot = outfit_idx * shots_per + shot_idx
                base_progress = 20 + int((global_shot / total_shots) * 65)

                # ── 3a: NB2 scene composition ─────────────────────────────
                _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                            progress=base_progress,
                            message=f"{outfit_name} — sahne {shot_idx + 1}/{shots_per} kompoze ediliyor...")

                nb2_prompt = (
                    "Fashion runway show editorial photo: the first image is the runway scene — "
                    "preserve it exactly including architecture, lighting, floor, and audience. "
                    "Place a tall fashion model wearing the garment from the reference images "
                    "(images 2 onward) walking on the runway catwalk, centered, full body visible. "
                    "Preserve all garment details: exact colors, fabric, cut, silhouette, length. "
                    "Professional fashion show photography, sharp focus, elegant confident pose."
                )

                scene_frame_url = await generate_scene_frame(
                    image_urls=[current_start_image] + garment_refs,
                    prompt=nb2_prompt,
                    aspect_ratio=request.aspect_ratio,
                )
                logger.info("[%s] Defile shot %d/%d scene frame: %s",
                            job_id, global_shot + 1, total_shots, scene_frame_url[:80])

                # ── 3b: Kling animation ───────────────────────────────────
                _update_job(job_id, progress=base_progress + max(1, int(32 / total_shots)),
                            message=f"{outfit_name} — sahne {shot_idx + 1}/{shots_per} animate ediliyor...")

                prompt_text = DEFILE_PROMPTS[global_shot % len(DEFILE_PROMPTS)]
                logger.info("[%s] Defile shot %d/%d: outfit=%s, prompt=%.60s",
                            job_id, global_shot + 1, total_shots, outfit_name, prompt_text)

                clip_url = await generate_multishot_video(
                    start_image_url=scene_frame_url,
                    multi_prompt=[{"duration": "5", "prompt": prompt_text}],
                    elements=None,  # outfit already baked into start frame via NB2
                    duration="5",
                    aspect_ratio=request.aspect_ratio,
                    generate_audio=request.generate_audio,
                )

                clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
                clip_paths.append(clip_path)
                logger.info("[%s] Defile shot %d downloaded: %s", job_id, global_shot + 1, clip_path)

                # Chain: last frame of this clip feeds next NB2 compose (within outfit)
                if shot_idx < shots_per - 1:
                    last_frame_path = extract_last_frame(clip_path, settings.TEMP_DIR)
                    current_start_image = await upload_to_fal(last_frame_path)
                    try:
                        os.remove(last_frame_path)
                    except Exception:
                        pass
                    logger.info("[%s] Chain: next shot starts from last frame", job_id)

        # ── Step 3: Concatenate all clips ─────────────────────────────────
        _update_job(job_id, progress=87, message=f"{total_shots} sahne birleştiriliyor...")

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

        # ── Step 4: Upload to Supabase ────────────────────────────────────
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
            message=f"Defile videosu hazır! {n_outfits} kıyafet, {total_shots} sahne.",
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
