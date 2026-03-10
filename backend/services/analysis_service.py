"""Analysis Service – uses OpenAI GPT-4o Vision to analyse garment photos
and generate multi-scene prompts for the video pipeline."""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from config import settings
from models import DressAnalysisResult, MultiScenePrompt, GenerationRequest, PhotoType, SuggestShotsRequest, RefineShotRequest

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _encode_image(image_path: str) -> str:
    """Return a base64-encoded data-URI for a local image file."""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    suffix = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{data}"


def _image_content(path_or_url: str, detail: str = "high") -> dict:
    """Return an image_url content block for GPT-4o.
    Accepts either a local file path (encodes to base64) or an HTTP URL (passes directly).
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return {"url": path_or_url, "detail": detail}
    return {"url": _encode_image(path_or_url), "detail": detail}


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
        {"type": "image_url", "image_url": _image_content(front_path)},
    ]
    if back_path:
        image_contents.append(
            {"type": "image_url", "image_url": _image_content(back_path)}
        )

    label = "Bu elbise fotoğraflarını analiz et. İlk fotoğraf ön görünüm"
    if back_path:
        label += ", ikinci fotoğraf arka görünüm."
    else:
        label += "."

    response = await client.chat.completions.create(
        model="gpt-5.4",
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

LOCATION RULES (CRITICAL — MUST FOLLOW):
- You MUST use the EXACT location the user specified. Do NOT invent your own location.
- Location mapping:
  - "studio" → Professional fashion photography studio with clean backdrop, softboxes, seamless paper
  - "beach" → Beautiful beach with sand and ocean, golden hour sunlight
  - "city_street" → Elegant urban street with architecture, well-lit
  - "garden" → Lush green garden with flowers, natural daylight
  - "rooftop" → Modern rooftop terrace with city skyline view
  - "runway" → Fashion runway with professional stage lighting
  - "custom" → Use the user's custom description EXACTLY as described
- If the user provides a custom location description, follow it PRECISELY. Do not add extra locations or change the setting.

LIGHTING & TIME RULES (CRITICAL):
- DEFAULT to bright, well-lit conditions: golden hour, soft daylight, or professional studio lighting
- NEVER use nighttime/dark settings unless the user explicitly requests it
- Always specify: "bright", "well-lit", "golden hour sunlight", "soft natural daylight", or "professional studio lighting"
- Avoid: "dim", "dark", "nighttime", "moonlight", "shadows" (unless user requests moody/dark mood)
- The background_image_prompt MUST include specific lighting: "bathed in warm golden hour light" or "bright natural daylight streaming in"

BACKGROUND IMAGE PROMPT RULES:
- Describe ONLY the environment/setting — NO people, NO models, NO clothing
- Be VERY specific about lighting, colors, materials, atmosphere
- MUST match the user's chosen location EXACTLY
- MUST be bright and well-lit (golden hour or daylight default)
- Example for studio: "A spacious professional fashion photography studio with clean white seamless backdrop, large softbox lighting from left, natural light from floor-to-ceiling windows on the right, polished concrete floor, bright and well-lit atmosphere"
- Example for beach: "A pristine sandy beach at golden hour, gentle turquoise waves, warm sunlight casting long shadows, scattered seashells, clear sky with soft warm colors, bright and inviting atmosphere"

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

DURATION RULES:
- Distribute the total duration EVENLY across all shots
- For example: 10 seconds with 2 shots → each shot "5"
- For example: 15 seconds with 3 shots → each shot "5"
- Each shot duration must be between "3" and "10" (string format)
- First shot should establish the scene (wider angle)
- Last shot should be a closing/signature shot

PROMPT STRUCTURE (each shot, 40-80 words):
- Camera position and movement
- Model action and garment interaction (reference @Element1)
- Lighting and atmosphere (MUST mention bright/well-lit conditions)
- Each shot should use a DIFFERENT angle/movement for variety

SCENE CONTINUITY RULES (critical — videos are chained clip-to-clip, each clip starts from the last frame of the previous):
- Scene 1 MUST clearly establish: model full body, garment front view, lighting, environment — this sets the visual anchor for all subsequent clips
- Every scene after the first: begin the prompt with "Seamlessly continuing from previous shot,"
- Reference @Element1 in EVERY scene prompt without exception (garment consistency lock)
- Keep IDENTICAL lighting conditions across ALL scenes — do NOT shift bright→dim or warm→cool between scenes
- Camera movement at the END of scene N should logically transition into scene N+1
  Example: Scene 1 ends with a Dolly Back (wider frame) → Scene 2 begins with a Tracking Shot from that wider position
- Do NOT introduce new environments, new backgrounds, or new lighting sources mid-sequence
- The model's body position at the end of scene N should connect naturally with the start of scene N+1

FORBIDDEN:
- '8k', 'hyper-realistic', 'unreal engine', 'masterpiece'
- Do NOT zoom into face close-up in first shot
- NEVER use Turkish — all output in English
- Do NOT mention specific model appearance (skin color, hair etc.)
- Do NOT use nighttime/dark settings by default

Return JSON only:
{
  "background_image_prompt": "Detailed scene/environment description for Nano Banana (NO people, MUST match user's location, MUST be well-lit)",
  "total_duration": total_seconds,
  "scene_count": number,
  "garment_lock_description": "Technical garment description for consistency",
  "location_theme": "overall location theme",
  "scenes": [
    {
      "scene_number": 1,
      "scene_title": "short title",
      "duration": "5",
      "prompt": "Cinematic multishot prompt with @Element1 reference, camera work, bright lighting, and garment details",
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
    style_image_url: Optional[str] = None,
) -> MultiScenePrompt:
    """Create multi-scene prompts for the video generator."""
    import re

    location_str = request.custom_location if request.location == "custom" else request.location.value

    # Map user-friendly camera move names to cinematography terms
    _cam_move_map = {
        "orbit":     "360° Orbit / Arc Shot",
        "dolly_in":  "Dolly In (Push In)",
        "dolly_out": "Dolly Out (Pull Out)",
        "pan":       "Pan Left/Right",
        "tilt_up":   "Tilt Up",
        "tracking":  "Tracking Shot / Follow Shot",
        "crane":     "Crane Shot (rising)",
        "static":    "Handheld / Static",
    }

    user_text = (
        f"Garment analysis:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"Location: {location_str}\n"
        f"Total video duration: {total_duration} seconds\n"
        f"Number of shots: {scene_count}\n"
        f"Mood: {request.mood or analysis.mood}\n"
        f"The garment photos are referenced as @Element1 in prompts.\n"
    )

    _angle_map = {
        "eye_level":  "Eye Level",
        "low_angle":  "Low Angle (upward, powerful)",
        "high_angle": "High Angle (downward, editorial)",
        "profile":    "Profile Shot (90° side)",
        "rear":       "Rear Shot (from behind)",
        "dutch":      "Dutch Angle (tilted, dramatic)",
    }
    _size_map = {
        "wide":             "Wide Shot (full body + environment)",
        "medium_wide":      "Medium Wide Shot (knees to head)",
        "medium":           "Medium Shot (waist to head)",
        "close_up":         "Close-Up (face or garment detail)",
        "extreme_close_up": "Extreme Close-Up (fabric/texture/accessory)",
    }

    if request.shots:
        user_text += "\n[CRITICAL] Per-shot configuration — you MUST follow this EXACTLY:\n"
        for i, shot in enumerate(request.shots):
            cam_term   = _cam_move_map.get(shot.camera_move, shot.camera_move)
            angle_term = _angle_map.get(shot.camera_angle, shot.camera_angle)
            size_term  = _size_map.get(shot.shot_size, shot.shot_size)
            user_text += (
                f"  Shot {i + 1}: camera_movement=\"{cam_term}\", "
                f"camera_angle=\"{angle_term}\", "
                f"shot_size=\"{size_term}\", "
                f"duration={shot.duration}s"
            )
            if shot.description:
                user_text += f", additional_instruction=\"{shot.description}\""
            user_text += "\n"
        user_text += (
            "Each scene's 'camera_movement', 'camera_angle', and 'shot_size' fields MUST match the specifications above. "
            "Each scene's 'duration' field MUST match the specified seconds above exactly.\n"
        )

    if video_description:
        user_text += f"\nUser's additional description: {video_description}\n"

    if location_image_path:
        user_text += "\nThe user sent a location reference photo. Use this setting as inspiration for the background_image_prompt and scene descriptions."

    if style_image_url:
        user_text += "\nThe user also sent a style reference image. Use its visual mood, color palette, and atmosphere as inspiration for all scene prompts."

    # Build message content
    content_parts = [{"type": "text", "text": user_text}]

    if location_image_path:
        content_parts.append({
            "type": "image_url",
            "image_url": _image_content(location_image_path),
        })

    if style_image_url:
        content_parts.append({
            "type": "image_url",
            "image_url": _image_content(style_image_url),
        })

    response = await client.chat.completions.create(
        model="gpt-5.4",
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


# ─── Shot Description Suggestions ──────────────────────────────────
_CAM_MOVE_MAP = {
    "orbit":     "360° Orbit / Arc Shot",
    "dolly_in":  "Dolly In (Push In)",
    "dolly_out": "Dolly Out (Pull Out)",
    "pan":       "Pan Left/Right",
    "tilt_up":   "Tilt Up",
    "tracking":  "Tracking Shot / Follow Shot",
    "crane":     "Crane Shot (rising)",
    "static":    "Handheld / Static",
}

_CAMERA_ANGLE_MAP = {
    "eye_level":  "Eye Level",
    "low_angle":  "Low Angle (shooting upward — powerful, elongating)",
    "high_angle": "High Angle (shooting downward — editorial, elegant)",
    "profile":    "Profile Shot (90° side view — silhouette focus)",
    "rear":       "Rear Shot (from behind — back of garment, mystery)",
    "dutch":      "Dutch Angle (tilted frame — dramatic, fashion-forward)",
}

_SHOT_SIZE_MAP = {
    "wide":              "Wide Shot / Extreme Wide Shot (full body + environment)",
    "medium_wide":       "Medium Wide Shot (knees to head — full outfit visible)",
    "medium":            "Medium Shot (waist to head — upper garment focus)",
    "close_up":          "Close-Up (face or garment panel detail)",
    "extreme_close_up":  "Extreme Close-Up (fabric texture, embellishment, accessory detail)",
}

_LOCATION_MAP = {
    "studio":      "professional fashion photography studio with clean backdrop and softbox lighting",
    "beach":       "pristine beach at golden hour with turquoise waves",
    "city_street": "elegant urban street with architectural backdrop, well-lit",
    "garden":      "lush garden with flowers and warm natural daylight",
    "rooftop":     "modern rooftop terrace with city skyline, bright daytime",
    "runway":      "high-end fashion runway with dramatic stage lighting",
    "custom":      "custom location",
}

SUGGEST_SHOTS_SYSTEM = """You are a professional fashion film director. Given a list of camera shots and a location, write a short cinematic prompt for each shot.

Rules:
- Each description is 15-25 words
- Focus on: camera movement, model pose/action, garment interaction, lighting
- All descriptions in English
- Reference the garment generically (e.g. "the garment", "the outfit")
- Descriptions should be natural continuations of each other (chained shots)
- First shot: full-body establishing shot
- Vary angles and energy across shots
- Do NOT add extra shots or skip any
- Respond ONLY with a JSON array of strings, one per shot

Example output for 2 shots:
["fashion model stands tall, full body reveal, slow dolly in, soft studio lighting", "seamlessly continuing, model turns gracefully, orbit shot captures garment silhouette, warm light"]"""


async def refine_shot_description(request: RefineShotRequest) -> str:
    """Convert a user's casual description into a cinematic English shot prompt."""
    cam_term    = _CAM_MOVE_MAP.get(request.camera_move, request.camera_move)
    angle_term  = _CAMERA_ANGLE_MAP.get(request.camera_angle, request.camera_angle)
    size_term   = _SHOT_SIZE_MAP.get(request.shot_size, request.shot_size)

    system = (
        "You are a professional fashion film director. "
        "The user describes what they want to happen in a shot in their own words (possibly in Turkish). "
        "Convert it into a precise, cinematic English prompt (20-35 words) suitable for an AI video generator. "
        "You MUST incorporate ALL THREE cinematography parameters: the exact camera movement, the exact camera angle, "
        "and the exact shot size specified. Also include the action described, garment reference as 'the outfit', and lighting. "
        "If a location image is provided, derive the setting from that image — do NOT default to studio. "
        "Return ONLY the prompt string, no quotes, no extra text."
    )

    if request.location_image_url:
        user_content = [
            {
                "type": "text",
                "text": (
                    f"Camera movement: {cam_term}\n"
                    f"Camera angle: {angle_term}\n"
                    f"Shot size: {size_term}\n"
                    f"Duration: {request.duration}s\n"
                    f"User description: {request.user_description}\n\n"
                    "The image below is the location/background reference. "
                    "Use the setting shown in the image as the environment for this shot.\n"
                    "Write the cinematic prompt:"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": request.location_image_url, "detail": "low"},
            },
        ]
        model = "gpt-5.4"
    else:
        location_str = (
            request.custom_location
            if request.location == "custom" and request.custom_location
            else _LOCATION_MAP.get(request.location, request.location)
        )
        user_content = (
            f"Location: {location_str}\n"
            f"Camera movement: {cam_term}\n"
            f"Camera angle: {angle_term}\n"
            f"Shot size: {size_term}\n"
            f"Duration: {request.duration}s\n"
            f"User description: {request.user_description}\n\n"
            "Write the cinematic prompt:"
        )
        model = "gpt-4o-mini"

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.7,
        max_tokens=120,
    )
    return (response.choices[0].message.content or "").strip().strip('"').strip("'")


_DEFILE_MULTISHOT_SYSTEM = """You are a luxury fashion film director. You receive a composed runway scene image showing a fashion model in a garment, and a list of shot durations.

Your task: Write one cinematic English prompt per shot for Kling 3.0 Pro multishot video generation.

RULES:
- Shot 1: MUST be wide/establishing — full body visible, set the scene, model entering or standing at runway start
- Each subsequent shot continues seamlessly from the previous (chained within one video generation)
- NEVER repeat the same camera angle or movement twice
- Each prompt references what you see in the image: garment color, silhouette, runway setting
- Style: luxury fashion film, editorial Vogue aesthetic, smooth cinematic movement, shallow depth of field
- Each prompt: 35-55 words, in English only
- Model actions: walking toward camera, pivoting, pausing, turning, fabric swinging with movement
- Keep lighting consistent across all shots (match what you see in the image)

Camera vocabulary to use (vary across shots):
Wide Shot, Medium Shot, Close-Up, Extreme Close-Up, Low Angle, High Angle, Bird's Eye, Tracking Shot, Dolly In, Dolly Out, Arc Shot, Tilt Up, Tilt Down, Follow Shot, Steadicam, Slow Motion

Return ONLY a JSON array with exactly N objects:
[
  {"duration": "5", "prompt": "..."},
  {"duration": "4", "prompt": "..."}
]"""


async def generate_defile_multishot_prompt(
    scene_frame_url: str,
    shot_configs: list,
    outfit_name: str = "",
) -> list[dict]:
    """Analyze a NB2-composed runway scene frame and generate multishot prompts for Kling.

    Args:
        scene_frame_url: fal.ai CDN URL of the composed scene frame (NB2 output).
        shot_configs: List of DefileShotConfig objects with .duration attributes.
        outfit_name: Optional outfit name for logging.

    Returns:
        List of {"duration": str, "prompt": str} dicts, one per shot.
    """
    import re as _re

    n_shots = len(shot_configs)
    durations = [str(s.duration) for s in shot_configs]
    shots_description = "\n".join(
        f"  Shot {i + 1}: {d} seconds" for i, d in enumerate(durations)
    )

    user_text = (
        f"Outfit: {outfit_name or 'fashion garment'}\n"
        f"Number of shots: {n_shots}\n"
        f"Shot durations:\n{shots_description}\n\n"
        "Analyze the runway scene in the image and write a cinematic multishot script. "
        "Return exactly the JSON array with the durations specified above."
    )

    response = await client.chat.completions.create(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": _DEFILE_MULTISHOT_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": scene_frame_url, "detail": "high"}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.75,
        max_tokens=1200,
    )

    raw = (response.choices[0].message.content or "").strip()

    # Unwrap if GPT returned {"shots": [...]} or similar wrapper
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            shots = parsed
        else:
            # Find first list value in the dict
            shots = next(v for v in parsed.values() if isinstance(v, list))
    except Exception:
        match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if match:
            shots = json.loads(match.group(0))
        else:
            raise ValueError(f"Could not parse defile multishot prompts: {raw[:200]}")

    # Enforce correct durations (GPT sometimes changes them) and count
    result = []
    for i, cfg in enumerate(shot_configs):
        prompt_text = shots[i]["prompt"] if i < len(shots) else f"Fashion model walks runway, cinematic shot {i + 1}, elegant movement, luxury editorial"
        result.append({"duration": str(cfg.duration), "prompt": prompt_text})

    logger.info("Defile multishot prompts for '%s': %d shots", outfit_name, len(result))
    return result


async def suggest_shot_descriptions(request: SuggestShotsRequest) -> list[str]:
    """Generate cinematic shot descriptions for the multishot designer."""
    location_str = (
        request.custom_location
        if request.location == "custom" and request.custom_location
        else _LOCATION_MAP.get(request.location, request.location)
    )

    shots_text = "\n".join(
        f"  Shot {i + 1}: {_CAM_MOVE_MAP.get(s.camera_move, s.camera_move)}, {s.duration}s"
        for i, s in enumerate(request.shots)
    )

    user_msg = f"Location: {location_str}\n\nShots:\n{shots_text}\n\nWrite a cinematic description for each shot."

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SUGGEST_SHOTS_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.8,
        max_tokens=600,
    )

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    import re
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)

    descriptions: list[str] = json.loads(raw)
    # Ensure we return exactly one description per shot
    while len(descriptions) < len(request.shots):
        descriptions.append("")
    return descriptions[: len(request.shots)]

