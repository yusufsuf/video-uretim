"""Analysis Service – uses OpenAI GPT-4o Vision to analyse garment photos
and generate multi-scene prompts for the video pipeline."""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from config import settings
from models import DressAnalysisResult, MultiScenePrompt, GenerationRequest, PhotoType

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _encode_image(image_path: str) -> str:
    """Return a base64-encoded data-URI for a local image file."""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    suffix = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{data}"


# ─── Dress Analysis ────────────────────────────────────────────────
ANALYSIS_SYSTEM = """You are an elite fashion garment analyst specializing in haute couture construction and evening wear. You receive TWO photos of the same garment: FRONT view and BACK view. Your job is to describe this garment with such precision that an AI image/video generator can recreate it perfectly from your description alone.

FRONT ANALYSIS RULES:
- Describe the garment from top to bottom as seen from the front
- Start with the neckline: exact shape, depth, width
- Then shoulders and sleeves: sleeve type, length, cuff style, how they attach to the bodice
- Then bodice: fit type, structure, boning, panels, seam lines
- Then waist area: belt type, buckle/embellishment description, peplum shape and size
- Then skirt: silhouette shape, volume, how it falls from waist, number of layers if visible
- Then hem: exactly where it ends, how it finishes at the bottom
- Note the fabric behavior: how it catches light, folds, drapes

BACK ANALYSIS RULES:
- Describe the garment from top to bottom as seen from the back
- Start with back neckline: shape, depth, how it differs from front
- Then upper back: fabric panels, seam lines, how fabric sits on shoulder blades
- Then closure: zipper/buttons/hooks - exact type, starting position, ending position, visibility
- Then mid-back to waist: how fabric follows the body contour, darting, panels
- Then skirt from behind: how it falls, panels, volume, shape compared to front
- Then back hem: how it ends at floor level from behind

CRITICAL RULES:
- NEVER use the word 'train' or 'trailing' - the hem ends at floor level
- NEVER exaggerate or add details not visible in the photos
- Be precise about colors - use exact color terms, not vague ones
- If something is not clearly visible, say 'not clearly visible'

Return JSON only:
{
  "photo_type": "mannequin | ghost | flatlay",
  "garment_type": "exact garment type",
  "color": "precise main color",
  "color_secondary": "secondary color or none",
  "pattern": "pattern type or solid",
  "fabric": "detailed fabric description - weight, sheen, structure, drape",
  "neckline": "exact neckline shape and depth",
  "sleeve_type": "sleeve style, length, cuff detail",
  "cut_style": "detailed fit from bodice through skirt",
  "length": "exact garment length",
  "details": "ALL unique features: belt, buckle, peplum, decorative elements",
  "front_silhouette": "complete front description from neckline to hem",
  "back_details": "complete back description: neckline, closure, panels, skirt, hem",
  "back_silhouette": "how the garment shapes the body from behind",
  "hem_description": "how the hem finishes from BOTH front and back views",
  "description_en": "comprehensive 3-4 sentence description covering front and back views. NEVER use words train or trailing.",
  "season": "suitable season",
  "mood": "overall mood / atmosphere"
}"""


async def analyse_dress(front_path: str, back_path: Optional[str] = None) -> DressAnalysisResult:
    """Analyse one or two garment images and return structured data."""

    image_contents = [
        {
            "type": "image_url",
            "image_url": {"url": _encode_image(front_path), "detail": "high"},
        }
    ]
    if back_path:
        image_contents.append(
            {
                "type": "image_url",
                "image_url": {"url": _encode_image(back_path), "detail": "high"},
            }
        )

    label = "Bu elbise fotoğraflarını analiz et. İlk fotoğraf ön görünüm"
    if back_path:
        label += ", ikinci fotoğraf arka görünüm."
    else:
        label += "."

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": label},
                    *image_contents,
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1200,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    data = json.loads(raw)
    return DressAnalysisResult(**data)


# ─── Multi-Scene Prompt Generation ─────────────────────────────────
MULTI_SCENE_SYSTEM = """You are a professional fashion film director and cinematographer. You create multi-shot fashion video scripts with professional camera work.

PURPOSE:
You will receive a garment analysis + location/mood info. You must output:
1. A background_image_prompt: a text prompt for an AI image generator (Nano Banana 2) to create ONLY the background/setting image. NO people, NO models, NO mannequins in this prompt — just the scene/environment.
2. A list of multishot scenes, each with a cinematic prompt and duration.

BACKGROUND IMAGE PROMPT RULES:
- Describe ONLY the environment/setting — NO people, NO models, NO clothing
- Be specific about lighting, colors, materials, atmosphere
- Match the mood of the garment
- Example: "An elegant Parisian balcony overlooking the Eiffel Tower at golden hour, warm ambient lighting, marble railing with ornate iron details, soft clouds in pastel sky, shallow depth of field background"

MULTISHOT PROMPT RULES:
- Each shot prompt describes what happens in that segment of the video
- Reference garments using @Element1 (the uploaded garment photos)
- Use professional cinematography terms from this reference:

CAMERA ANGLES: Eye Level, High Angle, Low Angle, Bird's Eye View, Worm's Eye View, Over-the-Shoulder, Profile Shot, Rear Shot, Front Facing Shot, Dutch Angle
CAMERA MOVEMENTS: Zoom In, Zoom Out, Crash Zoom, Slow Zoom, Push In, Pull Out, Dolly In, Dolly Out, Truck Left/Right, Tracking Shot, Follow Shot, Arc Shot, 360° Orbit, Crane Shot, Jib Shot, Tilt Up, Tilt Down, Pan Left/Right, Whip Pan, Handheld, Steadicam Shot
SHOT SIZES: Extreme Wide Shot (EWS), Wide Shot (WS), Medium Wide Shot (MWS), Medium Shot (MS), Medium Close-Up (MCU), Close-Up (CU), Extreme Close-Up (ECU)
TRANSITIONS: Cut, Match Cut, Jump Cut, Fade In/Out, Cross Fade/Dissolve, Whip Transition, Flash Cut, Motion Blur Transition, Seamless Transition

STYLE COMBINATIONS:
- Couture: Slow Push In, Low Angle + Slow Motion, 45° Side Tracking, Arc Shot, Symmetrical Wide Shot, Soft Focus Background, Shallow DOF
- Runway: Front Tracking Shot, Eye Level, Steadicam Follow, Hard Cut Transitions
- Royal/Dramatic: Low Angle + Slow Push In, Crane Down Reveal, Arc Shot + Slow Motion, Fade to Black

PROMPT STRUCTURE (each shot, 40-80 words):
- Camera position and movement
- Model action and garment interaction (reference @Element1)
- Lighting and atmosphere
- Each shot should use a DIFFERENT angle/movement for variety

FORBIDDEN:
- '8k', 'hyper-realistic', 'unreal engine', 'masterpiece'
- Do NOT zoom into face close-up in first shot
- NEVER use Turkish — all output in English
- Do NOT mention specific model appearance (skin color, hair etc.)

Return JSON only:
{
  "background_image_prompt": "Detailed scene/environment description for Nano Banana (NO people)",
  "total_duration": total_seconds,
  "scene_count": number,
  "garment_lock_description": "Technical garment description for consistency",
  "location_theme": "overall location theme",
  "scenes": [
    {
      "scene_number": 1,
      "scene_title": "short title",
      "duration": "3",
      "prompt": "Cinematic multishot prompt with @Element1 reference, camera work, and garment details",
      "camera_angle": "Eye Level",
      "camera_movement": "Slow Push In",
      "shot_size": "Wide Shot"
    }
  ]
}"""


async def generate_multi_scene_prompt(
    analysis: DressAnalysisResult,
    request: GenerationRequest,
    total_duration: int = 10,
    scene_count: int = 2,
    video_description: Optional[str] = None,
    location_image_path: Optional[str] = None,
) -> MultiScenePrompt:
    """Create multi-scene prompts for the video generator."""
    import re

    location_str = request.custom_location if request.location == "custom" else request.location.value

    user_text = (
        f"Garment analysis:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"Location: {location_str}\n"
        f"Total video duration: {total_duration} seconds\n"
        f"Number of shots: {scene_count}\n"
        f"Mood: {request.mood or analysis.mood}\n"
        f"The garment photos are referenced as @Element1 in prompts.\n"
    )

    if video_description:
        user_text += f"\nUser's additional description: {video_description}\n"

    if location_image_path:
        user_text += "\nThe user sent a location reference photo. Use this setting as inspiration for the background_image_prompt and scene descriptions."

    # Build message content
    content_parts = [{"type": "text", "text": user_text}]

    if location_image_path:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": _encode_image(location_image_path), "detail": "high"},
        })

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": MULTI_SCENE_SYSTEM},
            {"role": "user", "content": content_parts},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=3000,
    )

    raw = response.choices[0].message.content
    if not raw:
        raise ValueError("GPT-4o returned empty response for multi-scene prompt")

    raw = raw.strip()
    logger.info("Multi-scene raw response (first 300 chars): %s", raw[:300])

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # Try to extract JSON object if there's surrounding text
    if not raw.startswith("{"):
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raise ValueError(f"Could not find JSON in GPT response: {raw[:200]}")

    data = json.loads(raw)
    return MultiScenePrompt(**data)

