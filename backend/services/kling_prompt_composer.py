"""Kling 3.0 Omni prompt composer — research-backed fashion prompt writer.

Üretilen prompt'lar Kling AI'nın kendi web UI'ında (app.klingai.com) Video 3.0
Omni ile kullanılmak üzere hazırlanır. Kullanıcı `@<tag>` placeholder'larını
Kling'in Bind Subject (Elements) özelliğiyle kendi referans görseline bağlar.

İki mod:
  - multi_shot         → Kling'in otomatik sahne bölme (AI-orchestrated cuts).
                         Tek bir zengin paragraf üretilir; Kling kesileri kendi
                         seçer. `n_shots` ve `total_duration` mood/tempo hint'i
                         olarak kullanılır.
  - custom_multi_shot  → Kullanıcı her shot'ı kendisi tanımlar (time_range,
                         camera type). Her shot için ayrı, bağımsız paragraf
                         prompt'u üretilir.

Araştırma kaynakları (fal.ai, klingaio.com, atlascloud, invideo, crepal, leonardo,
cliprise) birleştirilerek aşağıdaki hard-rule set'i türetildi:

  - Element bağlıyken karakter/kıyafet TEKRAR TARİF EDİLMEZ → sadece action +
    environment + camera yazılır (identity drift önlenir).
  - Her shot tek cümle sırası: [Camera] . [Action+Physics] . [Env+Lighting] .
    [Texture/Detail] .
  - Fashion için her walk'ta "heel-first landing + weight transfer + arms loose"
    gait fiziği zorunlu; her dönüş/tilt saç/kumaş ikincil hareketiyle eşlenir.
  - Kamera tipleri shot boyunca benzersiz; Python tarafında hard-assign edilir.
  - Negative prompt kısa (≤ 80 char), fashion-odaklı.
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

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# Kling / fashion hard limits
MIN_SHOTS = 1
MAX_SHOTS = 6          # Kling native multishot cap
MIN_TOTAL_DURATION = 3
MAX_TOTAL_DURATION = 60
MIN_SHOT_DURATION = 3  # Kling minimum
MAX_SHOT_DURATION = 10 # Kling maximum per shot

# OpenAI model — latest ChatGPT for maximum prompt quality (project-wide standard).
_GPT_MODEL = "gpt-5.4"

# Supported Kling UI modes
MODES = ("multi_shot", "custom_multi_shot")


# Master camera pool — each one appears at most once per sequence (no repetition).
# Order matters: earlier entries = establishing feel, later = closing feel.
_CAMERA_POOL = [
    "wide_establishing",
    "medium_tracking",
    "low_angle_hero",
    "side_tracking_profile",
    "three_quarter_turn_orbit",
    "hem_to_head_tilt_up",
    "close_up_fabric",
    "dolly_in_face",
    "over_shoulder_back",
    "descending_follow",
    "back_detail_close",
    "final_back_walk",
]

# Closing camera type per arc (last shot anchor)
_ARC_CLOSERS = {
    "runway":            "final_back_walk",
    "editorial":         "dolly_in_face",
    "street":            "over_shoulder_back",
    "cinematic":         "back_detail_close",
    "lookbook":          "three_quarter_turn_orbit",
    "couture":           "close_up_fabric",
    "bridal":            "final_back_walk",
    "athleisure":        "descending_follow",
    "resort":            "side_tracking_profile",
    "avant_garde":       "hem_to_head_tilt_up",
    "noir":              "dolly_in_face",
    "retro_70s":         "three_quarter_turn_orbit",
    "urban_night":       "over_shoulder_back",
    "fantasy_dream":     "dolly_in_face",
    "vintage_hollywood": "dolly_in_face",
}

_ARC_TONE_GUIDANCE = {
    "runway": (
        "Classic runway walk energy. Confident cadence, model owns the floor. "
        "Clean tracking + rim-lit silhouettes. Semi-gloss floor, controlled spotlight pools. "
        "Deep blacks, crushed shadows at edges. Pace: steady, deliberate."
    ),
    "editorial": (
        "High-fashion editorial cuts. Model holds intentional poses between beats, "
        "micro-movements rather than long walks. Studio key + reflector fill, warm catchlights. "
        "Seamless backdrop or minimal set. Pace: slow and composed."
    ),
    "street": (
        "Urban natural light. Handheld feel (subtle sway, not shaky). Candid movement, "
        "natural stride, wardrobe reacts to real wind. Golden hour or blue-hour city light. "
        "Pace: relaxed, documentary-leaning."
    ),
    "cinematic": (
        "Moody narrative. Dramatic contrast, named practical sources (neon, lamp, window "
        "slats, bounce). Model moves with intent, small expressive beats. Atmospheric haze. "
        "Pace: slow, scene-driven."
    ),
    "lookbook": (
        "Clean product-first aesthetic. Neutral backdrop, even studio lighting, minimal shadows. "
        "Controlled micro-motion showcasing garment silhouette and fabric. Pace: calm and balanced."
    ),
    "couture": (
        "Haute couture atelier feel. Sculptural silhouettes, museum-like negative space. "
        "Gallery lighting — single soft key at 35°, no fill, deep shadows. 3200K warm tungsten. "
        "Motion is sparse and reverent; every gesture deliberate. Pace: very slow, artful."
    ),
    "bridal": (
        "Bridal elegance. Soft diffused key with heavy bounce fill, airy highlights, 5200K. "
        "Pastel or ivory palette, delicate veil/train motion catching light. Walking is glide-like, "
        "hem sweeps the floor with visible secondary motion. Pace: graceful, ceremonial."
    ),
    "athleisure": (
        "Athletic energy. Dynamic camera, faster tracking, confident forward propulsion. "
        "Daylight 5600K with slight cool cast, natural sweat sheen. Fabric compresses and stretches "
        "with each stride; breathable weave visible on close-ups. Pace: brisk, alive."
    ),
    "resort": (
        "Resort / beachwear. Natural sunlight, warm 5000K with sky-blue fill bounce. "
        "Linen/silk reacts to real breeze, hem and hair lift with each step. Sand, pool tile, or "
        "wood deck underfoot. Pace: breezy, unhurried, sun-kissed."
    ),
    "avant_garde": (
        "Avant-garde editorial. Unconventional framing, asymmetric poses, architectural shadows. "
        "High-contrast single-source lighting from unusual angle. Color palette reduced to 2–3 "
        "saturated tones. Motion is statuesque with sudden controlled shifts. Pace: staccato."
    ),
    "noir": (
        "Film-noir mood. Hard raking side-light creating deep Rembrandt shadows, 3000K. "
        "Smoky haze, venetian-blind patterns, wet floor reflections. Model moves with silent intent, "
        "cigarette-smoke grace. Desaturated palette leaning monochrome. Pace: slow, tense."
    ),
    "retro_70s": (
        "1970s retro fashion. Warm amber tungsten (2800K), subtle film grain, soft halation on "
        "highlights. Flared fabric swings wide on turns, hair catches golden backlight. Wood, "
        "velvet, or sun-bleached exterior settings. Pace: relaxed, groovy."
    ),
    "urban_night": (
        "Urban nightlife. Neon practicals (magenta + cyan), wet asphalt reflections, street haze. "
        "Model moves through layered light pools, silhouette dominates between sources. Color "
        "separation between warm signs and cool ambient. Pace: cool, confident."
    ),
    "fantasy_dream": (
        "Dreamlike fantasy. Atmospheric haze, volumetric god-rays, soft bloom on highlights. "
        "Ethereal key light with heavy diffusion, pastel color grade. Motion feels suspended — "
        "fabric and hair float longer than physics would allow. Pace: languid, floating."
    ),
    "vintage_hollywood": (
        "Vintage Hollywood glamour (1940s–50s). Classic three-point lighting with hard edged key, "
        "soft fill, strong rim on hair. Silvery highlights, creamy blacks, slight sepia cast. "
        "Model poses with studied elegance, minimal movement. Pace: poised, iconic."
    ),
}


def _validate_and_clamp(n_shots: int, total_duration: int) -> tuple[int, int]:
    n = max(MIN_SHOTS, min(MAX_SHOTS, int(n_shots)))
    total = max(MIN_TOTAL_DURATION, min(MAX_TOTAL_DURATION, int(total_duration)))
    # Ensure total fits within per-shot bounds; otherwise clamp total.
    total = max(total, n * MIN_SHOT_DURATION)
    total = min(total, n * MAX_SHOT_DURATION)
    return n, total


def _distribute_durations(total: int, n: int) -> List[int]:
    base = total // n
    extra = total - (base * n)
    out = [base + (1 if i < extra else 0) for i in range(n)]
    return [max(MIN_SHOT_DURATION, min(MAX_SHOT_DURATION, d)) for d in out]


def _assign_cameras(n: int, arc_tone: str) -> List[str]:
    """Pick n unique camera types from the pool. Last one is the arc's closer."""
    closer = _ARC_CLOSERS.get(arc_tone, _ARC_CLOSERS["runway"])
    pool = [c for c in _CAMERA_POOL if c != closer]
    random.shuffle(pool)
    if n == 1:
        return [closer]
    chosen = pool[: n - 1] + [closer]
    return chosen


def _resolve_shot_cameras(
    n: int,
    arc_tone: str,
    shot_techniques: Optional[List[Optional[str]]],
) -> List[str]:
    """Merge user-picked technique IDs with auto-assignment.

    For shots where the user picked a technique ID (from the kling_techniques
    library), use that ID directly. For None/missing slots, auto-pick from the
    remaining pool while avoiding duplicates against user picks.
    """
    if not shot_techniques:
        return _assign_cameras(n, arc_tone)

    # Normalize
    picks = list(shot_techniques) + [None] * max(0, n - len(shot_techniques))
    picks = picks[:n]

    user_set = {p for p in picks if p}
    closer = _ARC_CLOSERS.get(arc_tone, _ARC_CLOSERS["runway"])

    # Auto pool: exclude user-chosen ones so we don't duplicate.
    remaining = [c for c in _CAMERA_POOL if c not in user_set]
    if closer not in user_set and closer in remaining:
        remaining.remove(closer)  # we'll tack the closer on if last slot is empty
    random.shuffle(remaining)

    out: list[str] = []
    for i, p in enumerate(picks):
        if p:
            out.append(p)
            continue
        if i == n - 1 and closer not in user_set:
            out.append(closer)
        elif remaining:
            out.append(remaining.pop())
        else:
            # Fallback — user over-specified duplicates, just pick anything
            out.append(random.choice(_CAMERA_POOL))
    return out


def _build_time_ranges(durations: List[int]) -> List[str]:
    ranges = []
    t = 0
    for d in durations:
        ranges.append(f"{t}-{t + d}s")
        t += d
    return ranges


_SYSTEM_PROMPT_BASE = """\
You are a professional Kling 3.0 Omni prompt composer for FASHION video generation.

GOAL
Produce cinematic, production-ready prompts that will yield the highest-quality,
most realistic fashion videos when pasted into Kling AI's Video 3.0 Omni web UI.

CRITICAL RULES (apply to every mode)

1. ELEMENT TAGS
   The user supplies element tag names (e.g., "dress", "jacket"). Refer to each
   element inside the prompt as @<name> (e.g., @dress). DO NOT describe the
   element's visual appearance — Kling's Bind Subject feature handles identity
   from the reference image. Describe only where/how the element APPEARS and
   BEHAVES (fabric motion, contact with body, light on material). Never
   re-describe face, hair color, skin tone, pattern, or outfit details.

2. SENTENCE ORDER (within any single cinematic beat):
   [Camera: shot size + movement verb + lens mm, framing height] .
   [Subject action with explicit PHYSICS: foot contact, weight transfer, fabric
    and hair secondary motion] .
   [Environment + named lighting sources with direction/angle + color temperature] .
   [One texture or detail beat] .

3. FASHION GAIT
   Any walking action MUST include explicit gait physics:
   "heel-first landing, visible weight transfer, arms loose at sides".
   Never a bare "walks". Pair every rotation/turn/tilt with secondary motion:
   "hair follows just behind", "hem flutters against calves",
   "fabric sways with each stride", "silk catches the rim light on the turn".

4. LENS & CAMERA MOVEMENT
   Lens map (default unless told otherwise): 24mm wide/full-body,
   35mm walking, 50mm portrait/medium, 85mm macro/texture.
   Only ONE camera movement per beat. No zoom-AND-orbit combos.

5. LIGHTING VOCABULARY
   Use concrete, named sources with angles and color temperature.
   Good: "soft key at 45°, cool rim at 120°, 5600K".
   Good: "tungsten practicals + amber bounce, 3200K".
   Bad:  "cinematic lighting", "beautiful lighting", "dramatic".

6. ARC TONE
   The arc_tone controls mood, pacing, and lighting palette. Stay inside it.
   You will be told the tone with a short guidance string — honor it strictly.

7. NEGATIVE PROMPT
   Produce ONE short negative prompt (<= 90 characters) for the whole sequence.
   Target the highest-risk Kling fashion artifacts only. Short beats long —
   long negatives stiffen motion.

8. LANGUAGE
   English only. Do NOT write Turkish anywhere in the output. No commentary,
   no markdown, no headings, no trailing notes — only the JSON object.

9. START FRAME DISCIPLINE (CRITICAL)
   You are given a START FRAME image. This is the literal first frame Kling will
   animate from. Your prompts MUST describe what is visibly in that frame and
   what happens AFTER it — never a contradictory model, wardrobe, location,
   time-of-day, or lighting.
   - Read the frame first: subject pose, framing, environment, lighting direction,
     time of day, color palette, atmosphere.
   - The first shot must continue naturally from this exact pose and framing.
   - Keep lighting palette, color temperature, location, and wardrobe CONSISTENT
     with the frame across every shot (unless the director note explicitly calls
     for a scene change).
   - Do NOT invent details that contradict the frame (e.g., don't write "sunset"
     if the frame is indoor studio; don't write "red dress" if it's black).
   - Element @tags still apply for identity — do not re-describe the element
     itself, just how it reacts to motion and light as seen in the frame.
"""

_MODE_RULES_CUSTOM = """\
MODE: CUSTOM MULTI-SHOT
You will receive a list of shot contracts — one per shot — with shot_no,
time_range, duration, camera_type. For EACH shot, write ONE standalone cinematic
paragraph that starts by honoring the camera_type exactly (e.g.,
wide_establishing → wide framing + slow/locked tracking; final_back_walk →
over-the-shoulder pull-back with model receding; hem_to_head_tilt_up →
vertical tilt starting at hem, ending at shoulder/face). Shots connect
emotionally through the arc tone, but each paragraph must stand alone (the user
will paste each one into a separate custom shot slot on Kling).

JSON-STRICT OUTPUT (no extra keys):
{
  "shots": [
    {
      "shot_no": 1,
      "time_range": "0-4s",
      "duration": 4,
      "camera_type": "wide_establishing",
      "prompt": "One cinematic paragraph following the sentence order above."
    }
  ],
  "negative_prompt": "short, fashion-focused negative prompt"
}
"""

_MODE_RULES_MULTI = """\
MODE: MULTI-SHOT (AI-orchestrated cuts)
Kling's Multi-Shot mode automatically decides cuts from a single rich paragraph.
Write ONE unified cinematic paragraph (strictly one paragraph, no line breaks,
no bullet lists). Inside that paragraph, imply N_SHOTS distinct beats using
connective language like "opens with…", "cuts to…", "then…", "finally…" —
but do NOT output time_range labels or shot numbers. Each beat inside the
paragraph still follows the SENTENCE ORDER rule (camera → action+physics →
environment+lighting → texture). Use the target total duration to pace the beats
(short durations = tighter, fewer beats; longer durations = more breathing room).
Vary camera framings across beats — do not repeat the same shot size twice.

Paragraph length target: ~3× the total duration in words (e.g., 10s → ~30 words
per beat × N beats). Aim for vivid but economical language.

JSON-STRICT OUTPUT (no extra keys):
{
  "prompt": "Single cinematic paragraph with implied cuts.",
  "negative_prompt": "short, fashion-focused negative prompt"
}
"""


def _build_user_message_custom(
    element_tags: List[str],
    durations: List[int],
    time_ranges: List[str],
    cameras: List[str],
    arc_tone: str,
    director_note: Optional[str],
    previous_prompt: Optional[str] = None,
) -> str:
    tag_line = ", ".join(f"@{t}" for t in element_tags) if element_tags else "(no elements — write generic fashion scene)"
    arc_guidance = _ARC_TONE_GUIDANCE.get(arc_tone, _ARC_TONE_GUIDANCE["runway"])

    shot_lines = []
    for i, (tr, dur, cam) in enumerate(zip(time_ranges, durations, cameras), 1):
        tech = get_technique(cam)
        if tech:
            # User-picked technique from library — pass the full English instruction.
            shot_lines.append(
                f'  - shot_no={i}, time_range="{tr}", duration={dur}, camera_type="{cam}", '
                f'camera_instruction="{tech["en_camera"]}"'
            )
        else:
            shot_lines.append(
                f'  - shot_no={i}, time_range="{tr}", duration={dur}, camera_type="{cam}"'
            )
    shot_block = "\n".join(shot_lines)

    parts = []
    if previous_prompt and previous_prompt.strip():
        parts.append(
            "CONTINUATION CONTEXT\n"
            "This new sequence directly continues from a previous Kling generation. The START FRAME "
            "you are given is the LAST FRAME of that previous video. Your new shots must pick up "
            "EXACTLY where the previous sequence ended — same pose momentum, same mood, same setting, "
            "same wardrobe — and extend the narrative organically. Do not re-introduce the subject "
            "or re-establish the scene; treat this as a direct continuation.\n\n"
            f"PREVIOUS PROMPT(S) (for reference only, do NOT copy phrasing):\n{previous_prompt.strip()}"
        )

    parts.extend([
        f"ELEMENT TAGS (use verbatim, with @ prefix): {tag_line}",
        f"ARC TONE: {arc_tone}",
        f"ARC GUIDANCE: {arc_guidance}",
        f"TOTAL SHOTS: {len(durations)}",
        "SHOT CONTRACTS (write one prompt per shot, honor camera_type / camera_instruction exactly):",
        shot_block,
    ])
    if director_note and director_note.strip():
        parts.append(f"DIRECTOR NOTE (shape the overall narrative around this): {director_note.strip()}")

    parts.append("Return the JSON object now.")
    return "\n\n".join(parts)


def _build_user_message_multi(
    element_tags: List[str],
    n_shots: int,
    total_duration: int,
    arc_tone: str,
    director_note: Optional[str],
    previous_prompt: Optional[str] = None,
) -> str:
    tag_line = ", ".join(f"@{t}" for t in element_tags) if element_tags else "(no elements — write generic fashion scene)"
    arc_guidance = _ARC_TONE_GUIDANCE.get(arc_tone, _ARC_TONE_GUIDANCE["runway"])

    parts = []
    if previous_prompt and previous_prompt.strip():
        parts.append(
            "CONTINUATION CONTEXT\n"
            "This new sequence directly continues from a previous Kling generation. The START FRAME "
            "you are given is the LAST FRAME of that previous video. Your new paragraph must pick "
            "up EXACTLY where the previous sequence ended — same pose momentum, same mood, same "
            "setting, same wardrobe — and extend the narrative organically. Do not re-introduce the "
            "subject or re-establish the scene; treat this as a direct continuation.\n\n"
            f"PREVIOUS PROMPT (for reference only, do NOT copy phrasing):\n{previous_prompt.strip()}"
        )

    parts.extend([
        f"ELEMENT TAGS (use verbatim, with @ prefix): {tag_line}",
        f"ARC TONE: {arc_tone}",
        f"ARC GUIDANCE: {arc_guidance}",
        f"TARGET BEATS (implied cuts inside the single paragraph): {n_shots}",
        f"TOTAL DURATION: {total_duration}s",
        "Pace the beats so they fit the total duration naturally. Do NOT output time stamps or shot numbers.",
    ])
    if director_note and director_note.strip():
        parts.append(f"DIRECTOR NOTE (shape the overall narrative around this): {director_note.strip()}")

    parts.append("Return the JSON object now.")
    return "\n\n".join(parts)


async def _fetch_image_as_data_uri(url: str) -> str:
    """Download an image URL and return a base64 data URI for OpenAI vision input."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        r = await http.get(url)
        r.raise_for_status()
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(r.content).decode('ascii')}"


def _extract_json_object(text: str) -> dict:
    """Tolerant JSON extraction — handles code fences or leading prose."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.lstrip().lower().startswith("json"):
            s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in GPT response")
    return json.loads(s[start : end + 1])


async def compose_kling_prompts(
    start_frame_url: str,
    element_tags: List[str],
    n_shots: int,
    total_duration: int,
    arc_tone: str = "runway",
    director_note: Optional[str] = None,
    mode: str = "custom_multi_shot",
    shot_techniques: Optional[List[Optional[str]]] = None,
    previous_prompt: Optional[str] = None,
) -> dict:
    """Compose Kling 3.0 Omni prompts from a start frame image.

    The start frame is passed to GPT vision so prompts are anchored to the actual
    model, wardrobe, location, lighting, and time-of-day shown in the frame.

    mode="custom_multi_shot" → per-shot paragraphs with explicit camera contracts.
        Returns {"mode", "shots":[...], "negative_prompt", "meta"}
    mode="multi_shot" → single unified paragraph (Kling auto-cuts).
        Returns {"mode", "prompt", "negative_prompt", "meta"}
    """
    if not start_frame_url or not start_frame_url.strip():
        raise ValueError("start_frame_url zorunludur.")
    arc = (arc_tone or "runway").lower().strip()
    if arc not in _ARC_TONE_GUIDANCE:
        arc = "runway"

    m = (mode or "custom_multi_shot").lower().strip()
    if m not in MODES:
        m = "custom_multi_shot"

    tags = [t.strip().lstrip("@") for t in (element_tags or []) if t and t.strip()]
    # Light sanity: allow alphanumerics, dash, underscore — strip anything else.
    tags = ["".join(ch for ch in t if ch.isalnum() or ch in "-_") for t in tags]
    tags = [t for t in tags if t]

    n, total = _validate_and_clamp(n_shots, total_duration)

    if m == "custom_multi_shot":
        durations = _distribute_durations(total, n)
        time_ranges = _build_time_ranges(durations)
        cameras = _resolve_shot_cameras(n, arc, shot_techniques)
        user_msg = _build_user_message_custom(
            tags, durations, time_ranges, cameras, arc, director_note, previous_prompt,
        )
        system_msg = _SYSTEM_PROMPT_BASE + "\n" + _MODE_RULES_CUSTOM
    else:
        durations, time_ranges, cameras = [], [], []
        user_msg = _build_user_message_multi(
            tags, n, total, arc, director_note, previous_prompt,
        )
        system_msg = _SYSTEM_PROMPT_BASE + "\n" + _MODE_RULES_MULTI

    try:
        image_data_uri = await _fetch_image_as_data_uri(start_frame_url)
    except Exception as e:
        logger.warning("start frame fetch failed (%s): %s", start_frame_url, e)
        raise ValueError(f"Start frame indirilemedi: {e}") from e

    resp = await client.chat.completions.create(
        model=_GPT_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "START FRAME (this is the literal first frame Kling will animate — "
                        "read it carefully and anchor every shot to what is visibly in it)."
                    )},
                    {"type": "image_url", "image_url": {"url": image_data_uri, "detail": "high"}},
                    {"type": "text", "text": user_msg},
                ],
            },
        ],
        max_completion_tokens=2200,
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    try:
        data = _extract_json_object(raw)
    except Exception as e:
        logger.warning("compose_kling_prompts JSON parse failed: %s | raw=%s", e, raw[:400])
        raise

    neg = (data.get("negative_prompt") or "").strip()
    if not neg:
        neg = "sliding feet, morphing fabric, extra fingers, changing outfit, plastic skin"

    meta = {
        "mode": m,
        "arc_tone": arc,
        "n_shots": n,
        "total_duration": total,
        "element_tags": tags,
        "start_frame_url": start_frame_url,
        "continuation": bool(previous_prompt and previous_prompt.strip()),
        "model": _GPT_MODEL,
    }

    if m == "custom_multi_shot":
        # Normalize & enforce our contracts over GPT's output.
        shots_out: list = []
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
        return {
            "mode": m,
            "shots": shots_out,
            "negative_prompt": neg,
            "meta": meta,
        }

    # multi_shot
    single_prompt = (data.get("prompt") or "").strip()
    if not single_prompt:
        # Fallback: if GPT returned a shots array by mistake, stitch them.
        gpt_shots = data.get("shots") or []
        single_prompt = " ".join((s.get("prompt") or "").strip() for s in gpt_shots).strip()
    return {
        "mode": m,
        "prompt": single_prompt,
        "negative_prompt": neg,
        "meta": meta,
    }
