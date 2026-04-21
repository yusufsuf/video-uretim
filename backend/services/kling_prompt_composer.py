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

import json
import logging
import random
from typing import List, Optional

from openai import AsyncOpenAI

from config import settings

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
    "runway":    "final_back_walk",
    "editorial": "dolly_in_face",
    "street":    "over_shoulder_back",
    "cinematic": "back_detail_close",
    "lookbook":  "three_quarter_turn_orbit",
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
) -> str:
    tag_line = ", ".join(f"@{t}" for t in element_tags) if element_tags else "(no elements — write generic fashion scene)"
    arc_guidance = _ARC_TONE_GUIDANCE.get(arc_tone, _ARC_TONE_GUIDANCE["runway"])

    shot_lines = []
    for i, (tr, dur, cam) in enumerate(zip(time_ranges, durations, cameras), 1):
        shot_lines.append(f'  - shot_no={i}, time_range="{tr}", duration={dur}, camera_type="{cam}"')
    shot_block = "\n".join(shot_lines)

    parts = [
        f"ELEMENT TAGS (use verbatim, with @ prefix): {tag_line}",
        f"ARC TONE: {arc_tone}",
        f"ARC GUIDANCE: {arc_guidance}",
        f"TOTAL SHOTS: {len(durations)}",
        f"SHOT CONTRACTS (write one prompt per shot, honor camera_type exactly):",
        shot_block,
    ]
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
) -> str:
    tag_line = ", ".join(f"@{t}" for t in element_tags) if element_tags else "(no elements — write generic fashion scene)"
    arc_guidance = _ARC_TONE_GUIDANCE.get(arc_tone, _ARC_TONE_GUIDANCE["runway"])

    parts = [
        f"ELEMENT TAGS (use verbatim, with @ prefix): {tag_line}",
        f"ARC TONE: {arc_tone}",
        f"ARC GUIDANCE: {arc_guidance}",
        f"TARGET BEATS (implied cuts inside the single paragraph): {n_shots}",
        f"TOTAL DURATION: {total_duration}s",
        "Pace the beats so they fit the total duration naturally. Do NOT output time stamps or shot numbers.",
    ]
    if director_note and director_note.strip():
        parts.append(f"DIRECTOR NOTE (shape the overall narrative around this): {director_note.strip()}")

    parts.append("Return the JSON object now.")
    return "\n\n".join(parts)


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
    element_tags: List[str],
    n_shots: int,
    total_duration: int,
    arc_tone: str = "runway",
    director_note: Optional[str] = None,
    mode: str = "custom_multi_shot",
) -> dict:
    """Compose Kling 3.0 Omni prompts.

    mode="custom_multi_shot" → per-shot paragraphs with explicit camera contracts.
        Returns {"mode", "shots":[...], "negative_prompt", "meta"}
    mode="multi_shot" → single unified paragraph (Kling auto-cuts).
        Returns {"mode", "prompt", "negative_prompt", "meta"}
    """
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
        cameras = _assign_cameras(n, arc)
        user_msg = _build_user_message_custom(tags, durations, time_ranges, cameras, arc, director_note)
        system_msg = _SYSTEM_PROMPT_BASE + "\n" + _MODE_RULES_CUSTOM
    else:
        durations, time_ranges, cameras = [], [], []
        user_msg = _build_user_message_multi(tags, n, total, arc, director_note)
        system_msg = _SYSTEM_PROMPT_BASE + "\n" + _MODE_RULES_MULTI

    resp = await client.chat.completions.create(
        model=_GPT_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
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
