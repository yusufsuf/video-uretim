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
ANALYSIS_SYSTEM = """You are an elite fashion garment analyst specializing in haute couture construction and evening wear. You receive TWO photos of the same garment: FRONT view and BACK view. Your job is to describe this garment with such precision that an AI image/video generator can recreate it perfectly from your description alone — especially when the model turns and shows the back.

FRONT ANALYSIS RULES:
- Describe the garment from top to bottom as seen from the front
- Start with the neckline: exact shape, depth, width
- Then shoulders and sleeves: sleeve type, length, cuff style, how they attach to the bodice
- STRUCTURAL ELEMENTS: if there are 3D appliqués, sculptural cord/rope work, foam structures, pleated panels, floral constructions — describe each one: location on body, shape, size, how many, how they are attached, direction they face
- Then bodice: fit type, structure, boning, panels, seam lines
- Then waist area: belt type, buckle/embellishment description, peplum shape and size
- Then skirt: silhouette shape, volume, how it falls from waist, number of layers if visible
- Then hem: exactly where it ends, how it finishes at the bottom
- Note the fabric behavior: how it catches light, folds, drapes

BACK ANALYSIS RULES (CRITICAL — this is used to maintain accuracy when the model turns):
- Describe EXACTLY what becomes visible when the model faces away from camera
- SHOULDER/CAPE STRUCTURE: if there is a cape, cord structure, or draping element over the shoulders — describe it precisely from the back: how many strands, how they fan/drape, starting from neck/collar and going over shoulders, whether the back is exposed between them
- BACK OPENING: describe the exact exposure level — fully open, partially open, keyhole, V-cut, etc. — and the depth it reaches (nape of neck / mid-back / lower back / waist / hip)
- CLOSURE: zipper/buttons/hooks — exact type, material (exposed metal / concealed / decorative), starting position (top of back / below shoulder cape), ending position (waist / hips / hem)
- MID-BACK TO WAIST: how fabric follows the body contour, darting, panels, any seam lines visible
- BACK SKIRT: how it falls from behind, whether there is a back slit (how deep), back hem shape
- Write this as a complete sentence description that an AI can use as a prompt: e.g. "Back view reveals a deeply open back with multi-strand rope cape draping over both shoulders from a high collar, center metal zipper running from mid-back to waist, fitted mermaid skirt with center back slit at lower hem"

CRITICAL RULES:
- NEVER use the word 'train' or 'trailing' — the hem ends at floor level
- NEVER exaggerate or add details not visible in the photos
- Be precise about colors — use exact color terms, not vague ones
- If something is not clearly visible, say 'not clearly visible'
- The back_details field must be detailed enough to use as a standalone AI video prompt for a back-view shot

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
  "details": "ALL unique features: 3D appliqués, sculptural elements, cord/rope work, belt, buckle, peplum — each described precisely",
  "front_silhouette": "complete front description from neckline to hem — include all structural/sculptural elements with positions",
  "back_details": "FULL back view description as a standalone AI prompt: shoulder/cape structure from behind, back exposure depth, closure type and position, skirt from behind, back slit if any — enough detail to generate an accurate back-view shot",
  "back_silhouette": "how the garment shapes the body silhouette from behind — include any asymmetry or unique structure",
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
        max_completion_tokens=1800,
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

PROMPT STRUCTURE (each shot, 30-50 words, HARD LIMIT: 480 characters):
- Camera position and movement
- Model action and garment interaction (reference @Element1)
- Lighting and atmosphere (MUST mention bright/well-lit conditions)
- Each shot should use a DIFFERENT angle/movement for variety
- MUST include the exact garment_lock_description in every prompt (color, silhouette, key details)
- CRITICAL: Keep each shot prompt under 480 characters. Be concise — cut filler words.

GARMENT CONSISTENCY RULES (CRITICAL):
- Extract the garment's key identifiers from the analysis: exact color, fabric, silhouette shape, and 1-2 defining structural details
- Embed these identifiers verbatim into EVERY scene prompt — e.g. "ivory silk bias-cut gown with deep V neckline"
- The garment must appear IDENTICAL in every shot — same color, same cut, same details
- Do NOT let the AI infer or simplify the garment — always state it explicitly

BACK VIEW SHOTS (when camera shows model from behind or turning away):
- You MUST embed the full back_details from the analysis into the prompt verbatim
- Include: back shoulder/cape structure, back opening depth, closure type, back skirt shape, back slit if any
- Example: "...turning away reveals multi-strand rope cape draping over both shoulders from high collar, deep open back, center metal zipper, fitted skirt with back slit..."
- Never omit back structural details from back-facing shots — this is the main cause of inconsistency

SCENE CONTINUITY RULES (critical — videos are chained clip-to-clip, each clip starts from the last frame of the previous):
- Scene 1 MUST clearly establish: model full body, garment front view, lighting, environment — this sets the visual anchor for all subsequent clips
- Every scene after the first: begin the prompt with "Seamlessly continuing from previous shot,"
- Reference @Element1 in EVERY scene prompt without exception (garment consistency lock)
- Keep IDENTICAL lighting conditions across ALL scenes — do NOT shift bright→dim or warm→cool between scenes
- Camera movement at the END of scene N should logically transition into scene N+1
  Example: Scene 1 ends with a Dolly Back (wider frame) → Scene 2 begins with a Tracking Shot from that wider position
- Do NOT introduce new environments, new backgrounds, or new lighting sources mid-sequence
- The model's body position at the end of scene N should connect naturally with the start of scene N+1

FRAMING & HEM RULES (ABSOLUTE — apply to every single shot):
- The dress hem ALWAYS touches the ground — never write shots where hem floats, lifts, or shows a gap above floor
- NEVER frame below the hem — feet, shoes, ankles, and toes must NEVER appear in any shot
- Framing floor: the bottom of every frame must cut at or just above the hem, NOT below it
- Reinforce this with language like "dress hem grazing the floor", "full-length gown pooling at ground level"

FORBIDDEN:
- '8k', 'hyper-realistic', 'unreal engine', 'masterpiece'
- Do NOT zoom into face close-up in first shot
- NEVER use Turkish — all output in English
- Do NOT mention specific model appearance (skin color, hair etc.)
- Do NOT use nighttime/dark settings by default
- NEVER mention feet, shoes, heels, boots, ankles, or toes

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
            cam_term   = _cam_move_map.get(shot.camera_move, shot.camera_move) if shot.camera_move else ""
            angle_term = _angle_map.get(shot.camera_angle, shot.camera_angle) if shot.camera_angle else ""
            size_term  = _size_map.get(shot.shot_size, shot.shot_size) if shot.shot_size else ""
            user_text += f"  Shot {i + 1}:"
            if cam_term:
                user_text += f" camera_movement=\"{cam_term}\""
            if angle_term:
                user_text += f", camera_angle=\"{angle_term}\""
            if size_term:
                user_text += f", shot_size=\"{size_term}\""
            user_text += f", duration={shot.duration}s"
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
        max_completion_tokens=3000,
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
        max_completion_tokens=120,
    )
    return (response.choices[0].message.content or "").strip().strip('"').strip("'")


_DEFILE_MULTISHOT_SYSTEM = """You are a luxury fashion film director. You receive a composed runway scene image showing a fashion model in a garment, and a list of shot durations.

Your task: Write one cinematic English prompt per shot for Kling 3.0 Pro multishot video generation.

RUNWAY WALK STRUCTURE — MANDATORY:
The sequence must depict ONE complete runway journey in this exact order:
1. Model starts at the far end of the runway and walks TOWARD the camera (front view, approach)
2. Middle shots: cinematic detail shots as model walks closer — vary angles (low angle, close-up fabric, medium tracking)
3. SECOND-TO-LAST shot: model reaches the camera end of the runway, slows, executes a confident runway pivot/turn
4. LAST shot: model walks AWAY from camera down the runway (back view, retreating) — the walk is finished, model does NOT turn back around

CRITICAL RULES:
- The model NEVER turns back to face the camera after the final pivot — the sequence ends with the model walking away
- Each shot continues seamlessly from the previous (chained within one video generation)
- NEVER repeat the same camera angle or movement twice
- Reference the garment color and silhouette visible in the image
- For back-view shots: explicitly describe the back structure visible in the image — shoulder elements, back opening, closure, skirt from behind
- Style: luxury fashion film, editorial Vogue aesthetic, smooth cinematic movement
- Each prompt: 30-50 words, HARD LIMIT: 480 characters, in English only
- Keep lighting consistent across all shots

SHOT COUNT GUIDANCE:
- 1 shot: full runway walk from far end toward camera, model confident stride
- 2 shots: (1) approach walk toward camera, (2) end pivot + walk away
- 3 shots: (1) wide approach, (2) mid close-up detail, (3) pivot + walk away
- 4 shots: (1) wide establishing approach, (2) medium tracking, (3) end pivot, (4) back view walk away
- 5 shots: (1) wide approach, (2) low angle, (3) close-up fabric, (4) end pivot, (5) back view walk away

Camera vocabulary (vary across shots):
Wide Shot, Medium Shot, Close-Up, Extreme Close-Up, Low Angle, High Angle, Tracking Shot, Dolly In, Dolly Out, Arc Shot, Tilt Up, Follow Shot, Steadicam, Slow Motion

ABSOLUTE RULES (every shot, no exceptions):
- Dress hem ALWAYS touches the ground — reinforce with "hem grazing the floor" or "gown pooling at ground"
- NEVER frame below the hem — feet, shoes, ankles must NOT appear in any shot
- NEVER mention feet, shoes, heels, boots, ankles, or toes

Return a JSON object with a single key "shots" containing exactly N objects:
{"shots": [
  {"duration": "5", "prompt": "..."},
  {"duration": "4", "prompt": "..."}
]}"""


async def generate_defile_multishot_prompt(
    scene_frame_url: str,
    shot_configs: list,
    outfit_name: str = "",
    video_description: Optional[str] = None,
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

    if video_description:
        user_text = (
            f"Outfit: {outfit_name or 'fashion garment'}\n"
            f"Number of shots: {n_shots}\n"
            f"Shot durations:\n{shots_description}\n\n"
            f"CRITICAL — The user has provided specific creative direction for this video. "
            f"You MUST follow these instructions exactly. Override any default structure with the user's intent:\n\n"
            f"{video_description}\n\n"
            f"Analyze the scene image and write a cinematic multishot script that fulfills the above direction. "
            f"Return exactly the JSON array with the durations specified above."
        )
    else:
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
        max_completion_tokens=1200,
    )

    raw = (response.choices[0].message.content or "").strip()

    # Parse {"shots": [...]} wrapper format
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "shots" in parsed:
            shots = parsed["shots"]
        elif isinstance(parsed, list):
            shots = parsed
        else:
            # Fallback: find first list value in the dict
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


_CUSTOM_MULTISHOT_SYSTEM = """You are a cinematic video director and garment analyst. You receive a creative brief and up to three reference images of the outfit (front view, and optionally side and back views).

STEP 1 — GARMENT ANALYSIS (examine ALL provided images before writing a single prompt):
Extract and lock these details — they are NON-NEGOTIABLE across all shots:
- Exact color name (e.g. "royal cobalt blue", not just "blue")
- Fabric type and behavior (matte, structured, draping, etc.)
- ALL structural/sculptural elements on the front: 3D appliqués, rope/cord work, cutouts, neckline shape — describe each precisely with its location
- Side silhouette (if side image provided): how the garment reads from the side, waist definition, skirt flare
- Back structure (if back image provided): shoulder/cape construction from behind, back opening depth, closure type and position, back skirt shape, back slit depth
- Hem behavior: does it touch the floor flat? Is there a slit? NO train unless explicitly visible

STEP 2 — BRIEF INTERPRETATION:
The creative brief is a MOVEMENT AND SCENE DIRECTOR — it tells you what the model does and the mood.
- Translate each described movement/moment into a separate shot
- The brief does NOT define garment details — you locked those in Step 1, inject them into every shot
- If the brief specifies shot count and/or total duration → respect those numbers exactly
- If the brief has no timing → split movements into logical shots of 3-6 seconds each
- Each shot duration: minimum 3 seconds, maximum 10 seconds

STEP 3 — GENERATE SHOTS:
GARMENT CONSISTENCY (NON-NEGOTIABLE):
- Every single shot prompt MUST contain: exact color + key structural detail (e.g. "royal blue gown, 3D swirl rope appliqué bodice, multi-strand halter cape")
- For shots where model turns or shows side: embed side silhouette details from Step 1
- For shots where model shows back: embed FULL back structure verbatim — cape from behind, open back depth, closure, back slit
- NEVER invent, add, simplify, or omit garment details — only what is visible in the images

TRAIN / HEM RULE (ABSOLUTE):
- If a train IS visible → describe its exact length and behavior
- NEVER add a train that is not in the images
- NEVER write "no train" or "flat hem" — the pipeline injects this automatically

PROMPT RULES:
- Each shot prompt: 30-50 words, HARD LIMIT: 480 characters, in English only
- Each shot continues seamlessly from the previous
- Include: garment lock description, model action from brief, camera movement/framing, atmosphere
- Vary camera angles across shots for cinematic flow
- CRITICAL: If prompt exceeds 480 characters, cut filler — garment details are priority

ABSOLUTE RULES (every shot, no exceptions):
- Dress hem ALWAYS touches or grazes the floor
- NEVER frame below the hem — feet, shoes, ankles must NOT appear
- NEVER mention feet, shoes, heels, boots, ankles, or toes

Return JSON:
{"shots": [{"duration": "3", "prompt": "..."}, {"duration": "4", "prompt": "..."}, ...]}"""


async def generate_custom_multishot_prompt(
    video_description: str,
    image_url: str,
    back_image_url: Optional[str] = None,
    side_image_url: Optional[str] = None,
    scene_count: Optional[int] = None,
    total_duration: Optional[int] = None,
) -> list[dict]:
    """Generate Kling multishot prompts using the brief + outfit images — GPT analyzes garment and enriches prompts."""
    import re as _re

    views = ["Front view"]
    if side_image_url:
        views.append("side view")
    if back_image_url:
        views.append("back view")
    images_note = ", ".join(views) + " image(s) provided."

    if not video_description:
        video_description = (
            "No creative brief provided. Analyze the garment from the images and invent a cinematic fashion video scenario "
            "that best showcases it — highlight key structural details, silhouette, and most dramatic design elements. "
            "Choose camera angles and model movements that reveal the garment's unique features from multiple perspectives."
        )

    constraint_note = ""
    if scene_count and total_duration:
        per_shot = max(3, min(10, round(total_duration / scene_count)))
        constraint_note = (
            f"\n\nCONSTRAINT (MANDATORY): Generate EXACTLY {scene_count} shots. "
            f"Total duration must be EXACTLY {total_duration} seconds. "
            f"Distribute evenly: each shot approximately {per_shot} seconds. "
            f"You MUST return exactly {scene_count} shot objects in the JSON."
        )
    elif scene_count:
        constraint_note = (
            f"\n\nCONSTRAINT (MANDATORY): Generate EXACTLY {scene_count} shots. "
            f"You MUST return exactly {scene_count} shot objects in the JSON."
        )
    elif total_duration:
        constraint_note = (
            f"\n\nCONSTRAINT (MANDATORY): Total video duration must be EXACTLY {total_duration} seconds. "
            f"Split into logical shots of 3–10 seconds each to reach exactly {total_duration} seconds total."
        )

    user_text = (
        f"{images_note}\n\n"
        f"Creative brief:\n{video_description}"
        f"{constraint_note}\n\n"
        "Analyze the garment from the images, enrich any missing details, then return the shots JSON."
    )

    content: list = []
    content.append({"type": "image_url", "image_url": {"url": image_url, "detail": "high"}})
    if side_image_url:
        content.append({"type": "image_url", "image_url": {"url": side_image_url, "detail": "high"}})
    if back_image_url:
        content.append({"type": "image_url", "image_url": {"url": back_image_url, "detail": "high"}})
    content.append({"type": "text", "text": user_text})

    response = await client.chat.completions.create(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": _CUSTOM_MULTISHOT_SYSTEM},
            {
                "role": "user",
                "content": content,
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_completion_tokens=1500,
    )

    raw = (response.choices[0].message.content or "").strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "shots" in parsed:
            shots = parsed["shots"]
        elif isinstance(parsed, list):
            shots = parsed
        else:
            shots = next(v for v in parsed.values() if isinstance(v, list))
    except Exception:
        match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if match:
            shots = json.loads(match.group(0))
        else:
            raise ValueError(f"Could not parse custom multishot prompts: {raw[:200]}")

    result = [
        {"duration": str(max(3, min(10, int(s.get("duration", 4))))), "prompt": s["prompt"]}
        for s in shots
    ]

    logger.info("Custom multishot prompts: %d shots, total %ds", len(result), sum(int(s["duration"]) for s in result))
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
        max_completion_tokens=600,
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

