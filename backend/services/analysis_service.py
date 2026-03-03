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
MULTI_SCENE_SYSTEM = """You are a professional fashion film director specializing in natural, organic cinematography. Your goal is to create realistic, life-like fashion videos without artificial AI glints or synthetic 'plastic' looks.

NATURAL LOOK RULES (CRITICAL):
- NO artificial sparkles, NO synthetic glints, NO over-sharpened digital looks.
- LIGHTING: Use 'Soft diffused natural light', 'Indirect ambient lighting', 'Overcast day lighting', or 'Soft window light'. Avoid 'harsh spotlights' or 'specular highlights'.
- TEXTURE: Emphasize 'Matte fabric finish', 'Natural fabric grain', and 'Realistic skin textures'.
- ATMOSPHERE: Use 'Subtle film grain', 'Natural color grading', and 'Organic shadows'.

SCENE & CAMERA RULES:
- Use realistic camera movements: 'Gentle handheld sway', 'Slow natural tracking', 'Steady tripod shot'. Avoid 'extreme drone' or 'high-speed orbital' moves.
- Depth of field should be natural - not overly blurred, but enough to feel like a real lens.
- Each scene MUST use a DIFFERENT camera angle. NEVER repeat the same angle.
- First scene MUST NOT start with a face zoom. Prefer full-body or medium shot.

ABSOLUTE GARMENT RULES:
- Hem MUST end EXACTLY at floor level (for long garments). NO shoes visible. NO trailing fabric.
- The garment is SACRED. Do not add any shine or details not present in the analysis.
- Every scene prompt MUST describe the garment's color, fabric, and silhouette.

VIEW TYPE RULES:
- Each scene must have a view_type: "front", "back", or "transition"
- front: Scene mainly shows the garment from the front
- back: Scene mainly shows the garment from behind (walking away, rear view)
- transition: Scene shows the model turning/transitioning between front and back
- Mix view types for variety. Include at least one "back" scene.

VIDEO PROMPT STRUCTURE (80-120 words per prompt):
- Sentence 1: Camera movement and model action.
- Sentence 2: Complete garment journey with fabric texture in natural light.
- Sentence 3: Natural silhouette and organic movement of the fabric.
- Sentence 4: Environment with realistic, soft lighting.
- Sentence 5: 'The [garment] hem ends cleanly at floor level with no shoes visible. The garment remains perfectly visible and unchanged.'
- FINAL TAGS (ALWAYS): Every prompt MUST end with: "Cinematic realism, shot on 35mm film, natural lighting, soft shadows, high dynamic range, realistic skin texture, non-synthetic, organic look, professional fashion photography."

FORBIDDEN WORDS (NEVER USE):
- '8k', 'hyper-realistic', 'shiny', 'sparkling', 'glittering', 'specular', 'unreal engine', 'masterpiece'
- 'zooming in on face', 'close-up of face'
- NEVER show shoes, feet, or ankles (for long garments).
- NEVER use Turkish in output.

Return JSON only:
{
  "background_prompt": "overall setting description",
  "total_duration": total_seconds,
  "scene_count": number,
  "garment_lock_description": "Technical garment description used consistently across all scenes",
  "location_theme": "overall location theme",
  "scenes": [
    {
      "scene_number": 1,
      "scene_title": "short title",
      "camera_prompt": "camera angle and movement",
      "model_action_prompt": "model action",
      "lighting_prompt": "lighting setup",
      "pose_description": "detailed pose with garment details for photo generation",
      "background_description": "setting for this specific scene",
      "full_scene_prompt": "80-120 word cinematic video prompt with garment details and final tags",
      "duration_seconds": 5,
      "view_type": "front | back | transition"
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
        f"Kıyafet analizi:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"Mekan: {location_str}\n"
        f"Toplam video süresi: {total_duration} saniye\n"
        f"İstenen sahne sayısı: {scene_count}\n"
        f"Kamera stili: {request.camera_style or 'farklı açılardan çeşitlendir'}\n"
        f"Manken hareketi: {request.model_action or 'otomatik seç, çeşitli hareketler'}\n"
        f"Mood: {request.mood or analysis.mood}\n"
    )

    if video_description:
        user_text += f"\nKullanıcının ek açıklaması: {video_description}\n"

    if location_image_path:
        user_text += "\nKullanıcı bir mekan referans fotoğrafı gönderdi. Bu mekanı videonun arka planı olarak kullan, sahne betimlemelerinde bu mekanın özelliklerini yansıt."

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

