"""Seedance 2.0 prompt composer — research-backed fashion prompt writer.

Üç bucket'lı upload akışı:
  - start_frame_url (zorunlu, 1 görsel) → @image1 as first-frame reference
  - character_urls (0-8)                → @imageN as character + outfit consistency
  - location_urls (0-8)                 → @imageN as location + mood reference

Toplam ≤ 9 görsel (Seedance'ın multimodal üst sınırı). Sunucu tarafında numaralandırma
kesin sırayla: start → character → location — Seedance UI'daki asset slot sırası buna
birebir uyar.

Araştırma kaynakları (higgsfield, linkedin/chatcut) birleştirilerek türetilen
hard-rule set'i:
  - Give every @asset a job (inline 'as X reference')
  - Shot structure upfront (numbered veya timed, karma YASAK)
  - Film-look preamble zorunlu (ARRI ALEXA / Kodak Portra / Fashion Editorial vs.)
  - Footer zorunlu: "Total: {T}s / {N} shots / {aspect}"
  - Physical verbs (fracture/snap/stretch); "becomes/transforms into" yasak
  - Silent fashion: "no music, no audio, raw SFX only"
  - Scene-absolute lighting (orbit/turn'de camera-relative YASAK)
  - Start frame discipline (subject/wardrobe/location drift yok)
"""

from __future__ import annotations

import base64
import json
import logging
import random
from typing import List, Optional

import httpx
from openai import AsyncOpenAI

from config import settings
from services.kling_techniques import get_technique
from services.kling_prompt_composer import (
    _ARC_CLOSERS,
    _ARC_TONE_GUIDANCE,
    _camera_lens_mm,
    _extract_json_object,
    _fetch_image_as_data_uri,
    _resolve_shot_cameras,
)

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# Seedance 2.0 hard limits (KIE.ai / ByteDance)
MIN_SHOTS = 1
MAX_SHOTS = 6
MIN_SHOT_DURATION = 4   # Seedance per-shot minimum
MAX_SHOT_DURATION = 15  # Seedance per-shot maximum
MIN_TOTAL_DURATION = 4
MAX_TOTAL_DURATION = 90  # N_shots × 15 upper bound

MAX_REFERENCE_IMAGES = 9  # total budget across all 3 buckets
MAX_CHARACTER_REFS = 8
MAX_LOCATION_REFS = 8

RENDER_MODES = ("numbered_shots", "timed_segments")
ASPECT_RATIOS = ("9:16", "16:9", "1:1", "3:4", "21:9", "4:3", "adaptive")

_GPT_MODEL = "gpt-5.4"


FILM_LOOKS: dict[str, dict] = {
    "arri_alexa": {
        "tr_label": "ARRI ALEXA (sinematik)",
        "tr_desc": "Klasik sinema look'u, profesyonel color grading, 35mm film kalitesi",
        "preamble": (
            "Photorealistic, cinematic lighting, 35mm film quality, professional color "
            "grading, sharp focus, high detail texture, fine film grain, depth of field "
            "mastery, ARRI ALEXA aesthetic"
        ),
    },
    "kodak_portra": {
        "tr_label": "Kodak Portra (sıcak / pastel)",
        "tr_desc": "Sıcak ten tonları, yumuşak highlight, hassas film grain",
        "preamble": (
            "Photorealistic, Kodak Portra 400 color palette, warm skin tones, delicate "
            "highlight rolloff, fine grain, shallow depth of field, natural light feel"
        ),
    },
    "moody_noir": {
        "tr_label": "Moody Noir (koyu kontrast)",
        "tr_desc": "Derin gölgeler, low-key ışık, yüksek kontrast",
        "preamble": (
            "Photorealistic, moody noir cinematography, deep shadows, low-key lighting, "
            "high contrast, 35mm film, controlled bloom, muted desaturated palette"
        ),
    },
    "clean_commercial": {
        "tr_label": "Clean Commercial (reklam)",
        "tr_desc": "Parlak ve dengeli ışık, ürün-film netliği, temiz zemin",
        "preamble": (
            "Photorealistic, clean commercial look, bright even lighting, high-key "
            "illumination, crisp edges, deep focus, pristine color, product-film fidelity"
        ),
    },
    "natural_documentary": {
        "tr_label": "Natural Documentary (doğal)",
        "tr_desc": "Doğal ambient ışık, handheld hafif hareket, glamour yok",
        "preamble": (
            "Photorealistic, naturalistic documentary look, available ambient light, "
            "subtle handheld motion, 35mm fine grain, honest color, no glamour filter"
        ),
    },
    "fashion_editorial": {
        "tr_label": "Fashion Editorial (dergi kapağı)",
        "tr_desc": "Stüdyo key + yumuşak fill, 85mm depth, dergi estetiği",
        "preamble": (
            "Photorealistic, high-fashion editorial, polished retouched skin quality "
            "preserved through motion, studio key with soft fill, 85mm depth, "
            "magazine-cover aesthetic"
        ),
    },
    "orange_teal": {
        "tr_label": "Orange-Teal (Hollywood)",
        "tr_desc": "Hollywood turuncu-camgöbeği grade, güçlü kontrast",
        "preamble": (
            "Photorealistic, Hollywood orange-and-teal color grade, warm skin against "
            "cool environment, strong contrast, 35mm film, ARRI aesthetic"
        ),
    },
    "vintage_super8": {
        "tr_label": "Vintage Super-8 (nostaljik)",
        "tr_desc": "Super-8 film stok, light leak, görünür grain, aged palette",
        "preamble": (
            "Photorealistic, vintage Super-8 mm film stock aesthetic, warm light leaks, "
            "visible grain, slight gate weave, saturated yet aged palette, shallow depth"
        ),
    },
}


_SYSTEM_PROMPT_BASE = """\
You are a professional Seedance 2.0 (ByteDance) prompt composer for FASHION video
generation. Your output is COPY-PASTED by the user into Seedance's UI (wavespeed.ai,
higgsfield, or ByteDance Seed). The prompts must work directly with Seedance's native
multimodal asset system.

GOAL
Produce cinematic, production-ready prompts that yield the highest-quality fashion
videos when pasted into Seedance 2.0 with the matching reference images uploaded in
the exact order specified in the ASSET MANIFEST.

CRITICAL RULES

1. ASSET MANIFEST (FIRST PARAGRAPH — ALWAYS)
   Every prompt MUST open with the asset manifest, one @imageN per line, each with
   its inline job. Use EXACTLY the numbering given to you in the input; do not
   renumber. Example:
     @image1 as first-frame reference (the literal opening frame of the video)
     @image2, @image3, @image4 as character and wardrobe consistency references — keep face, hairstyle, and garment details identical across all shots
     @image5, @image6 as location mood reference — match the color palette, lighting temperature, and spatial layout; do not force the camera framing

2. FILM-LOOK PREAMBLE (SECOND PARAGRAPH — ALWAYS)
   Immediately after the manifest, emit the film-look preamble given to you
   verbatim. This is the look anchor for the entire sequence.

3. SHOT STRUCTURE — one of two modes, NEVER MIXED
   a) numbered_shots: "Shot 1: ...", "Shot 2: ..." — each shot is one self-contained
      cinematic paragraph. Shots are visually continuous.
   b) timed_segments: "0-3s: ...", "3-6s: ..." — single continuous take broken by
      timed beats. Use when n_shots=1 or when the brief explicitly wants one take.

4. FOOTER (LAST LINE — ALWAYS)
   End the full output with: Total: {total_duration}s / {n_shots} shots / {aspect_ratio}
   No blank line after. This is Seedance's parser anchor.

5. ELEMENT DESCRIPTION
   You have reference images via @imageN. DO NOT re-describe what those images
   contain (face color, hairstyle, dress pattern, wall texture). Seedance reads the
   images directly. Only describe what HAPPENS — motion, camera, light behavior,
   fabric physics. The manifest already assigns the job.

6. FASHION GAIT
   Any walking action MUST include explicit gait physics:
   "heel-first landing, visible weight transfer, arms loose at sides". Never a bare
   "walks". Pair every rotation/turn/tilt with secondary motion: "hair follows just
   behind", "hem flutters against calves", "silk catches rim light on the turn".

7. PHYSICAL VERBS (CRITICAL — Seedance research finding)
   Prefer concrete physical verbs: "fracture", "snap", "stretch", "implode", "unspool",
   "spill", "sweep", "compress", "glide". AVOID soft transformation words:
   "becomes", "transforms into", "magically", "somehow".

8. LENS & CAMERA MOVEMENT
   Lens defaults: 24mm wide/full-body, 35mm walking, 50mm portrait/medium,
   85mm macro/texture. Only ONE camera movement per beat. No zoom-AND-orbit combos.

9. LIGHTING VOCABULARY
   Concrete named sources with angles and color temperature. Good: "soft key at 45°,
   cool rim at 120°, 5600K". Bad: "cinematic lighting", "beautiful lighting".

10. SCENE-ABSOLUTE LIGHTING (CRITICAL for rotating / moving cameras)
    Lighting angles describe where the light is relative to the SUBJECT / SCENE,
    NOT the camera. The sun, practicals, and windows do not move when the camera
    moves. Use "key on the subject's left shoulder at 45°", "backlight from the
    doorway". AVOID "warm key from camera left" during orbits, turns, whip pans,
    side-tracking, or any shot where the camera traverses more than ~30°. During a
    full orbit, note how the light role evolves: "key begins as side-light, rolls
    through as a rim at the 90° point, settles as hair-light when subject faces
    screen-right."

11. PER-SHOT LIGHTING VARIATION
    Environmental consistency (same time of day, same palette) is preserved, but
    each shot's lighting DESCRIPTION must reflect that shot's motion / framing.
    Do NOT copy the same lighting sentence across shots.

12. CLOSER / BACK-WALK PLACEMENT
    final_back_walk, ascending_pull_back, dolly_out are sequence CLOSERS. If one is
    assigned to a NON-LAST shot, end that shot with a pivot / turn so the subject
    is re-oriented for the next shot's direction.

13. LENS CONTINUITY
    A lens jump larger than ~35mm between adjacent shots (e.g. 85mm → 24mm) is
    jarring. Soften: end the tighter shot on a slight pull-out, or begin the wider
    shot with a locked hold.

14. START FRAME DISCIPLINE (CRITICAL)
    You are given a START FRAME image (the first reference in the manifest). This
    is the literal first frame. Your prompts MUST describe what is visibly in it
    and what happens AFTER. Read: subject pose, framing, environment, lighting
    direction, time of day, color palette. The first shot continues naturally from
    this exact pose. Keep lighting palette, color temperature, location, and
    wardrobe CONSISTENT across every shot. Do NOT invent contradictions
    ("sunset" for an indoor studio frame, "red dress" for a black dress).

15. SILENT FASHION (project standard)
    When silent=true, include "no music, no audio, raw SFX only" as a trailing
    line just BEFORE the footer. Never propose music, score, or dialogue cues.

16. SINGLE-SHOT MODE (n_shots == 1)
    Add "one continuous take, no cuts, no zoom, natural camera breathing" early in
    the first paragraph body. For timed_segments, this is the default expectation.

17. LANGUAGE
    English only. Do NOT write Turkish anywhere in the output. No commentary,
    no markdown, no headings — only the JSON object defined below.
"""


_MODE_RULES_NUMBERED = """\
RENDER MODE: numbered_shots
You will receive a list of shot contracts — one per shot — with shot_no,
time_range, duration, camera_type. For EACH shot, write ONE standalone cinematic
paragraph beginning with "Shot {n}: " and honoring the camera_type exactly.

The full assembled `combined_prompt` must be structured as:
  [line 1..K] Asset manifest (one @imageN line per bucket group)
  [blank line]
  [line] Film-look preamble
  [blank line]
  Shot 1: ... (one paragraph)
  [blank line]
  Shot 2: ...
  ...
  [blank line]
  (optional) no music, no audio, raw SFX only
  Total: {T}s / {N} shots / {aspect}

JSON-STRICT OUTPUT (no extra keys):
{
  "shots": [
    {
      "shot_no": 1,
      "time_range": "0-4s",
      "duration": 4,
      "camera_type": "wide_establishing",
      "prompt": "Shot 1: one cinematic paragraph..."
    }
  ],
  "combined_prompt": "the entire copy-pasteable block including manifest + preamble + all shots + footer"
}
"""


_MODE_RULES_TIMED = """\
RENDER MODE: timed_segments
Single continuous take broken into timed beats. Write one "shot" entry per time
window, but each prompt line starts with "{start}-{end}s: " (e.g., "0-3s: ...",
"3-6s: ..."). No shot numbers, no cuts — one continuous camera body.

The full assembled `combined_prompt`:
  [Asset manifest]
  [blank line]
  [Film-look preamble]
  [blank line]
  One continuous take, no cuts, no zoom, natural camera breathing.
  0-3s: ...
  3-6s: ...
  ...
  [blank line]
  (optional) no music, no audio, raw SFX only
  Total: {T}s / 1 shot / {aspect}

JSON-STRICT OUTPUT (no extra keys):
{
  "shots": [
    {
      "shot_no": 1,
      "time_range": "0-3s",
      "duration": 3,
      "camera_type": "one_continuous_take",
      "prompt": "0-3s: the first timed beat description..."
    }
  ],
  "combined_prompt": "the entire copy-pasteable block"
}
"""


# ─── Helpers ──────────────────────────────────────────────────────

def _validate_and_clamp(
    n_shots: int,
    total_duration: int,
    *,
    enforce_min_per_shot: bool = True,
) -> tuple[int, int, bool]:
    """Returns (n, total, was_adjusted). enforce_min_per_shot=False is used for
    timed_segments mode where beats inside one continuous take can be shorter
    than Seedance's 4-second per-job minimum.
    """
    n = max(MIN_SHOTS, min(MAX_SHOTS, int(n_shots)))
    orig_total = max(MIN_TOTAL_DURATION, min(MAX_TOTAL_DURATION, int(total_duration)))
    total = orig_total
    if enforce_min_per_shot:
        total = max(total, n * MIN_SHOT_DURATION)
    total = min(total, n * MAX_SHOT_DURATION)
    return n, total, (total != orig_total)


def _distribute_durations(
    total: int,
    n: int,
    *,
    min_per_segment: int = MIN_SHOT_DURATION,
) -> List[int]:
    base = total // n
    extra = total - (base * n)
    out = [base + (1 if i < extra else 0) for i in range(n)]
    lo = max(1, min_per_segment)
    return [max(lo, min(MAX_SHOT_DURATION, d)) for d in out]


def _build_time_ranges(durations: List[int]) -> List[str]:
    ranges, t = [], 0
    for d in durations:
        ranges.append(f"{t}-{t + d}s")
        t += d
    return ranges


def _build_asset_manifest(
    start_frame_url: str,
    character_count: int,
    location_count: int,
) -> tuple[str, dict]:
    """Build the @imageN manifest with inline jobs. Returns (manifest_text, numbering_map).

    Numbering: start=@image1, character=@image2..(1+C), location=@image(2+C)..(1+C+L).
    Seedance UI slot order must match; user gets instructions reflecting this order.
    """
    lines: list[str] = []
    n = 1
    lines.append(f"@image{n} as first-frame reference (the literal opening frame of the video)")

    if character_count > 0:
        tags = [f"@image{n + i + 1}" for i in range(character_count)]
        n_final = n + character_count
        joined = ", ".join(tags)
        lines.append(
            f"{joined} as character and wardrobe consistency references — keep face, "
            f"hairstyle, body proportions, and garment details IDENTICAL across all shots"
        )
        n = n_final

    if location_count > 0:
        tags = [f"@image{n + i + 1}" for i in range(location_count)]
        joined = ", ".join(tags)
        lines.append(
            f"{joined} as location mood reference — match the color palette, lighting "
            f"temperature, and spatial layout; do NOT force the reference framing onto the shot"
        )

    numbering = {
        "start_frame": [1],
        "character": list(range(2, 2 + character_count)),
        "location": list(range(2 + character_count, 2 + character_count + location_count)),
    }
    return "\n".join(lines), numbering


def _build_user_message(
    *,
    render_mode: str,
    asset_manifest: str,
    film_look_preamble: str,
    aspect_ratio: str,
    total_duration: int,
    n_shots: int,
    arc_tone: str,
    silent: bool,
    durations: List[int],
    time_ranges: List[str],
    cameras: List[str],
    director_note: Optional[str],
    previous_prompt: Optional[str],
) -> str:
    arc_guidance = _ARC_TONE_GUIDANCE.get(arc_tone, _ARC_TONE_GUIDANCE["runway"])

    parts: list[str] = []
    if previous_prompt and previous_prompt.strip():
        parts.append(
            "CONTINUATION CONTEXT\n"
            "This new sequence directly continues from a previous Seedance generation. The "
            "START FRAME you are given is the LAST FRAME of that previous video. The new "
            "shots must pick up EXACTLY where the previous sequence ended — same pose "
            "momentum, same mood, same setting, same wardrobe — and extend the narrative "
            "organically. Do not re-introduce the subject or re-establish the scene.\n\n"
            f"PREVIOUS PROMPT (for reference only, do NOT copy phrasing):\n{previous_prompt.strip()}"
        )

    parts.append("ASSET MANIFEST (emit verbatim as the first paragraph of combined_prompt):")
    parts.append(asset_manifest)

    parts.append("FILM-LOOK PREAMBLE (emit verbatim as the second paragraph):")
    parts.append(film_look_preamble)

    parts.append(f"ARC TONE: {arc_tone}")
    parts.append(f"ARC GUIDANCE: {arc_guidance}")
    parts.append(f"ASPECT RATIO: {aspect_ratio}")
    parts.append(f"TOTAL DURATION: {total_duration}s")
    parts.append(f"TOTAL SHOTS: {n_shots}")
    parts.append(f"SILENT (fashion standard): {str(bool(silent)).lower()}")
    parts.append(f"REQUIRED FOOTER: Total: {total_duration}s / {n_shots} shots / {aspect_ratio}")

    if render_mode == "numbered_shots":
        closer_ids = {"final_back_walk", "ascending_pull_back", "dolly_out"}
        shot_lines: list[str] = []
        for i, (tr, dur, cam) in enumerate(zip(time_ranges, durations, cameras), 1):
            tech = get_technique(cam)
            is_closer_nonfinal = cam in closer_ids and i != n_shots
            flag = " [CLOSER AT NON-FINAL SLOT → apply rule 12]" if is_closer_nonfinal else ""
            if tech:
                shot_lines.append(
                    f'  - shot_no={i}, time_range="{tr}", duration={dur}, camera_type="{cam}", '
                    f'camera_instruction="{tech["en_camera"]}"{flag}'
                )
            else:
                shot_lines.append(
                    f'  - shot_no={i}, time_range="{tr}", duration={dur}, camera_type="{cam}"{flag}'
                )

        # Lens jump detection
        lenses = [_camera_lens_mm(c) for c in cameras]
        jumps: list[str] = []
        for i in range(1, len(lenses)):
            prev_mm, cur_mm = lenses[i - 1], lenses[i]
            if prev_mm and cur_mm and abs(cur_mm - prev_mm) > 35:
                jumps.append(f"  - shots {i}→{i + 1}: {prev_mm}mm → {cur_mm}mm (apply rule 13)")

        parts.append("SHOT CONTRACTS (write one `Shot N:` paragraph per shot):")
        parts.append("\n".join(shot_lines))
        if jumps:
            parts.append("LENS JUMPS DETECTED:\n" + "\n".join(jumps))
    else:
        # timed_segments
        beat_lines = [
            f'  - {tr}: duration={dur}s'
            for tr, dur in zip(time_ranges, durations)
        ]
        parts.append(
            "TIMED BEATS (one continuous take, one camera body; write a `START-ENDs: …` line per beat):"
        )
        parts.append("\n".join(beat_lines))
        parts.append(
            "CAMERA DISCIPLINE: the entire take is one continuous camera body. No cuts. "
            "Describe how that single camera evolves across the beats (slow push-in, gentle "
            "orbit, controlled tilt). Do NOT invent cut-to transitions."
        )

    if director_note and director_note.strip():
        parts.append(f"DIRECTOR NOTE (shape the overall narrative around this): {director_note.strip()}")

    parts.append(
        "COMBINED_PROMPT ASSEMBLY ORDER:\n"
        "  1. Asset manifest (the exact block above, verbatim)\n"
        "  2. Blank line\n"
        "  3. Film-look preamble (the exact block above, verbatim)\n"
        "  4. Blank line\n"
        + ("  5. One continuous take reminder line\n" if render_mode == "timed_segments" else "")
        + "  " + ("5" if render_mode == "numbered_shots" else "6")
        + ". Shot paragraphs in order, each separated by a blank line\n"
        + "  " + ("6" if render_mode == "numbered_shots" else "7")
        + ". Blank line\n"
        + ("  7. \"no music, no audio, raw SFX only\" (only if SILENT is true)\n" if silent else "")
        + "  Final line: the footer — Total: {T}s / {N} shots / {aspect}"
    )

    parts.append("Return the JSON object now.")
    return "\n\n".join(parts)


# ─── Public API ──────────────────────────────────────────────────

async def compose_seedance_prompts(
    *,
    start_frame_url: str,
    character_urls: List[str],
    location_urls: List[str],
    n_shots: int,
    total_duration: int,
    aspect_ratio: str = "9:16",
    arc_tone: str = "runway",
    render_mode: str = "numbered_shots",
    film_look: str = "arri_alexa",
    silent: bool = True,
    director_note: Optional[str] = None,
    shot_techniques: Optional[List[Optional[str]]] = None,
    previous_prompt: Optional[str] = None,
) -> dict:
    """Compose Seedance 2.0 prompts from three upload buckets.

    Total references across all buckets must satisfy:
        1 (start_frame) + len(character_urls) + len(location_urls) <= 9
    """
    # ── Validate ──
    if not start_frame_url or not start_frame_url.strip():
        raise ValueError("start_frame_url zorunludur.")

    chars = [u for u in (character_urls or []) if u and u.strip()]
    locs = [u for u in (location_urls or []) if u and u.strip()]
    if len(chars) > MAX_CHARACTER_REFS:
        raise ValueError(f"Karakter referansı en fazla {MAX_CHARACTER_REFS} olabilir.")
    if len(locs) > MAX_LOCATION_REFS:
        raise ValueError(f"Mekan referansı en fazla {MAX_LOCATION_REFS} olabilir.")
    total_refs = 1 + len(chars) + len(locs)
    if total_refs > MAX_REFERENCE_IMAGES:
        raise ValueError(
            f"Toplam referans {MAX_REFERENCE_IMAGES} görseli aşamaz "
            f"(1 start + {len(chars)} karakter + {len(locs)} mekan = {total_refs})."
        )

    rm = (render_mode or "numbered_shots").lower().strip()
    if rm not in RENDER_MODES:
        rm = "numbered_shots"

    ar = (aspect_ratio or "9:16").strip()
    if ar not in ASPECT_RATIOS:
        ar = "9:16"

    look_id = (film_look or "arri_alexa").lower().strip()
    look = FILM_LOOKS.get(look_id) or FILM_LOOKS["arri_alexa"]
    if look_id not in FILM_LOOKS:
        look_id = "arri_alexa"

    arc = (arc_tone or "runway").lower().strip()
    if arc not in _ARC_TONE_GUIDANCE:
        arc = "runway"

    # timed_segments is a single continuous take — beats inside it can be shorter
    # than Seedance's 4s per-job minimum. numbered_shots emits N separate jobs so
    # the 4s minimum applies there.
    enforce_min = rm == "numbered_shots"
    n, total, duration_adjusted = _validate_and_clamp(
        n_shots, total_duration, enforce_min_per_shot=enforce_min,
    )

    # In timed_segments mode, Seedance treats it as 1 continuous shot even if user
    # asked for N beats — keep n for beat count, but shot_count in footer is 1.
    footer_shots = 1 if rm == "timed_segments" else n

    min_per_segment = MIN_SHOT_DURATION if enforce_min else 1
    durations = _distribute_durations(total, n, min_per_segment=min_per_segment)
    time_ranges = _build_time_ranges(durations)

    if rm == "numbered_shots":
        cameras = _resolve_shot_cameras(n, arc, shot_techniques)
    else:
        # timed_segments: one continuous take, a single camera "body" — no per-beat
        # technique assignment. GPT writes a unified camera evolution.
        cameras = ["one_continuous_take"] * n

    # ── Asset manifest (deterministic numbering) ──
    manifest, numbering = _build_asset_manifest(start_frame_url, len(chars), len(locs))

    # ── User message ──
    user_msg = _build_user_message(
        render_mode=rm,
        asset_manifest=manifest,
        film_look_preamble=look["preamble"],
        aspect_ratio=ar,
        total_duration=total,
        n_shots=footer_shots,
        arc_tone=arc,
        silent=bool(silent),
        durations=durations,
        time_ranges=time_ranges,
        cameras=cameras,
        director_note=director_note,
        previous_prompt=previous_prompt,
    )

    system_msg = (
        _SYSTEM_PROMPT_BASE + "\n"
        + (_MODE_RULES_NUMBERED if rm == "numbered_shots" else _MODE_RULES_TIMED)
    )

    # ── Start frame vision ──
    try:
        image_data_uri = await _fetch_image_as_data_uri(start_frame_url)
    except Exception as e:
        logger.warning("seedance start frame fetch failed (%s): %s", start_frame_url, e)
        raise ValueError(f"Start frame indirilemedi: {e}") from e

    resp = await client.chat.completions.create(
        model=_GPT_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "START FRAME (this is the literal first frame of the video — "
                        "read it carefully and anchor every shot to what is visibly in it)."
                    )},
                    {"type": "image_url", "image_url": {"url": image_data_uri, "detail": "high"}},
                    {"type": "text", "text": user_msg},
                ],
            },
        ],
        max_completion_tokens=2600,
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    try:
        data = _extract_json_object(raw)
    except Exception as e:
        logger.warning("compose_seedance_prompts JSON parse failed: %s | raw=%s", e, raw[:400])
        raise

    # ── Normalize output ──
    shots_out: list[dict] = []
    gpt_shots = data.get("shots") or []
    for i, (tr, dur, cam) in enumerate(zip(time_ranges, durations, cameras)):
        gs = gpt_shots[i] if i < len(gpt_shots) else {}
        prompt_text = (gs.get("prompt") or "").strip()
        shots_out.append({
            "shot_no": i + 1,
            "time_range": tr,
            "duration": dur,
            "camera_type": cam,
            "prompt": prompt_text,
        })

    combined = (data.get("combined_prompt") or "").strip()
    if not combined:
        # Fallback: assemble from parts if GPT forgot
        body_lines: list[str] = [manifest, "", look["preamble"], ""]
        if rm == "timed_segments":
            body_lines.append(
                "One continuous take, no cuts, no zoom, natural camera breathing."
            )
        body_lines += [s["prompt"] for s in shots_out if s["prompt"]]
        body_lines.append("")
        if silent:
            body_lines.append("no music, no audio, raw SFX only")
        body_lines.append(f"Total: {total}s / {footer_shots} shots / {ar}")
        combined = "\n".join(body_lines)

    meta = {
        "render_mode": rm,
        "aspect_ratio": ar,
        "arc_tone": arc,
        "film_look": look_id,
        "n_shots": n,
        "footer_shots": footer_shots,
        "total_duration": total,
        "requested_duration": int(total_duration),
        "duration_adjusted": duration_adjusted,
        "silent": bool(silent),
        "continuation": bool(previous_prompt and previous_prompt.strip()),
        "reference_numbering": numbering,
        "total_references": total_refs,
        "model": _GPT_MODEL,
    }

    return {
        "render_mode": rm,
        "shots": shots_out,
        "combined_prompt": combined,
        "asset_manifest": manifest,
        "film_look_preamble": look["preamble"],
        "meta": meta,
    }
