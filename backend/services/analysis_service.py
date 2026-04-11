"""Analysis Service – uses OpenAI GPT-4o Vision to analyse garment photos
and generate multi-scene prompts for the video pipeline."""

import base64
import json
import logging
import random
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

GARMENT FIDELITY (ABSOLUTE — apply to every single shot):
- Describe the garment exactly as it appears in the image — slits, hem length, openings, cuts are locked by the reference
- Do NOT override garment geometry with positive enforcements like "closed skirt", "sealed gown", "legs hidden", "no slit" — element references carry the true silhouette
- Choose framing and camera angles that naturally showcase the garment; hem/feet visibility depends on the garment, not a blanket rule

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




# ─── Defile shot arc templates ───────────────────────────────────────────────
# Bank of narrative arcs for fashion runway sequences. The user picks one
# from the UI (or "auto" for random). Each arc is a sequence of beats; GPT
# adapts the beat count to match the user-requested shot count (merge when
# fewer, expand when more).
_DEFILE_SHOT_ARCS: list[dict] = [
    {
        "id": "classic_approach",
        "name": "Classic Approach",
        "description": "Klasik defile: uzaktan yürüyüş → tracking → pivot → arkadan çıkış",
        "beats": [
            "WIDE APPROACH — model starts at the far end of the runway (under/beside the architectural feature), walks toward camera with a confident stride, full body visible",
            "MEDIUM TRACKING — camera glides alongside at front three-quarter angle as she walks closer, revealing the front construction and fabric behavior",
            "LOW ANGLE PIVOT — at the camera end of the runway, low upward angle as she executes a confident runway pivot/turn",
            "BACK VIEW EXIT — follow shot from behind as she walks away down the runway toward the far end (she never turns back to face camera)",
        ],
    },
    {
        "id": "overhead_descent",
        "name": "Overhead Descent",
        "description": "Kuşbakışı → ön medium → yan profil → detay → yüz reveal → arka çıkış",
        "beats": [
            "OVERHEAD WIDE — high angle top-down vantage as the model begins walking forward from the far end, establishing runway and garment silhouette in context",
            "FRONTAL MEDIUM — eye-level medium shot as she approaches camera with a confident stride",
            "SIDE PROFILE TRACKING — camera tracks laterally alongside her from the side of the runway, full body in profile",
            "CLOSE-UP FRONT DETAIL — tight framing on hip/torso showing fabric construction, appliqué, or structural highlight",
            "FACE REVEAL — soft push-in on her face and upper shoulders, composed expression, eyes forward",
            "BACK VIEW EXIT — she pivots at the camera end and walks away, back silhouette retreating toward the arch",
        ],
    },
    {
        "id": "editorial_detail",
        "name": "Editorial Detail",
        "description": "Detay → dolly out → ¾ ön → arc shot → arkadan çıkış",
        "beats": [
            "CLOSE-UP TEXTURE — tight shot on fabric drape, appliqué, or structural highlight as she begins to walk (upper torso framing, feet unseen)",
            "DOLLY OUT WIDE — camera slowly pulls back revealing the full silhouette mid-stride on the runway",
            "FRONT THREE-QUARTER — medium tracking along her front quarter angle, garment construction fully visible",
            "ARC SHOT — camera orbits around her as she reaches the camera end and slows",
            "BACK VIEW EXIT — slow retreat down the runway, hold focus on the back construction and hem",
        ],
    },
    {
        "id": "dramatic_low_angle",
        "name": "Dramatic Low Angle",
        "description": "Heroic alt açı → ön → yüz → hip detay → silüet çıkış",
        "beats": [
            "LOW WIDE — heroic low-angle composition as she emerges from the far end, ascending framing",
            "MEDIUM FRONT — eye-level as she continues toward camera with a clean stride",
            "CLOSE-UP FACE — brief intimate face in frame, composed expression, gaze forward",
            "HIP-LEVEL DETAIL — tight shot on hip/skirt transition showing fabric flow",
            "EXIT PIVOT BACK — she reaches camera, pivots, silhouette backlit by lighting as she walks away",
        ],
    },
    {
        "id": "side_study",
        "name": "Side Study",
        "description": "Ön yaklaşma → yan profil → ¾ ön → detay → arkadan çıkış",
        "beats": [
            "WIDE FRONTAL APPROACH — establishing wide from the runway end, model walking toward camera",
            "SIDE PROFILE PASS — camera locked at the side of the runway, full body profile as she strides past",
            "THREE-QUARTER FRONT — returns to front three-quarter revealing construction lines and neckline",
            "CLOSE-UP DETAIL — tight framing on a key structural element (appliqué, neckline, or waist detail)",
            "BACK VIEW EXIT — follow shot from behind as she walks away down the runway",
        ],
    },
    {
        "id": "couture_moment",
        "name": "Couture Moment",
        "description": "Wide kuruluş → tracking → hip close → yüz+yaka → arkadan çıkış",
        "beats": [
            "WIDE ESTABLISHING — far wide showing her small within the architectural frame, beginning to walk forward",
            "MEDIUM TRACKING — camera glides alongside her at front three-quarter angle",
            "HIP-LEVEL CLOSE — tight framing on fabric flow at hip/skirt transition",
            "FACE AND NECKLINE — soft close on face and upper neckline detail as she reaches the camera end",
            "BACK VIEW EXIT — follow shot retreating toward the arch, focus on the back structure and hem",
        ],
    },
]


def _get_shot_arc_by_id(arc_id: Optional[str]) -> Optional[dict]:
    """Find an arc by its ID. Returns None if not found or arc_id is empty/auto."""
    if not arc_id or arc_id == "auto":
        return None
    for arc in _DEFILE_SHOT_ARCS:
        if arc["id"] == arc_id:
            return arc
    return None


def list_defile_shot_arcs() -> list[dict]:
    """Return the public list of shot arcs for frontend consumption."""
    return [
        {
            "id": arc["id"],
            "name": arc["name"],
            "description": arc["description"],
            "beats": arc["beats"],
        }
        for arc in _DEFILE_SHOT_ARCS
    ]


def _pick_defile_shot_arc() -> dict:
    """Randomly select a shot arc template from the bank."""
    return random.choice(_DEFILE_SHOT_ARCS)


def _format_shot_arc(arc: dict, n_shots: int) -> str:
    """Format a shot arc for injection into the GPT user message."""
    beats = arc["beats"]
    lines = "\n".join(f"  Beat {i + 1}: {b}" for i, b in enumerate(beats))
    adaptation: str
    if n_shots == len(beats):
        adaptation = "Map each beat 1-to-1 to the requested shots."
    elif n_shots < len(beats):
        adaptation = (
            f"You have {n_shots} shots but {len(beats)} beats. Merge or select beats to fit, "
            "but ALWAYS keep an approach beat (first), at least one middle detail beat, and a back-view exit (last)."
        )
    else:
        adaptation = (
            f"You have {n_shots} shots but only {len(beats)} beats. Expand by adding intermediate "
            "camera variations (e.g. close-up fabric detail, arc shot, dolly, tilt) while preserving the arc order."
        )
    return (
        f"NARRATIVE ARC — '{arc['name']}':\n{lines}\n\n"
        f"ADAPTATION: {adaptation}"
    )


_DEFILE_MULTISHOT_SYSTEM = """You are a luxury fashion film director. You receive a composed runway scene image showing a fashion model in a garment, and a list of shot durations.

Your task: Write one cinematic English prompt per shot for Kling 3.0 Pro multishot video generation.

NARRATIVE ARC — MANDATORY SCAFFOLDING:
The user message will contain a NARRATIVE ARC with named beats (e.g. "WIDE APPROACH", "CLOSE-UP DETAIL", "BACK VIEW EXIT"). This arc is the skeleton of the sequence — follow it in order, one beat per shot unless the adaptation instruction says otherwise.

RUNWAY JOURNEY CONTRACT:
- The sequence must depict ONE continuous runway journey, chained across shots
- Regardless of the selected arc, the FINAL shot is ALWAYS a back view walking away — the model never turns back to face camera after the final pivot
- Shots chain seamlessly, lighting stays constant, location never changes

CRITICAL RULES:
- Follow the arc beat order exactly; each beat becomes one shot's framing/angle intent
- NEVER repeat the same camera angle or movement twice in a row
- Reference the garment color and silhouette visible in the image
- For back-view shots: explicitly describe the back structure visible in the image — shoulder elements, back opening, closure, skirt from behind
- Style: luxury fashion film, editorial Vogue aesthetic, smooth cinematic movement
- Each prompt: 30-50 words, HARD LIMIT: 480 characters, in English only
- Keep lighting consistent across all shots

CINEMATIC DEPTH (every shot):
- End each prompt with a short phrase establishing shallow depth of field (e.g. "shallow depth of field", "background dissolves into soft bokeh", "creamy bokeh behind"). This adds cinematic separation between model and environment.

SCENE ANCHORING (every shot):
- In every shot, include a brief 3-5 word reminder of the location/environment visible in the image (e.g. "within the stone courtyard", "beneath the draped arch", "along the illuminated runway"). This prevents Kling from drifting between shots — the environment must stay locked across the entire sequence.

Camera vocabulary (vary across shots):
Wide Shot, Medium Shot, Close-Up, Extreme Close-Up, Low Angle, High Angle, Tracking Shot, Dolly In, Dolly Out, Arc Shot, Tilt Up, Follow Shot, Steadicam, Slow Motion, Overhead

ABSOLUTE RULES (every shot, no exceptions):
- Describe the garment exactly as it appears in the image — do NOT invent, add, or remove any structural detail (slits, openings, cuts, hem length are all locked by the reference)
- Do NOT override the garment geometry with positive phrases like "closed skirt", "sealed gown", "no front slit", "legs hidden" — the element references carry the true garment shape
- NEVER add spectators, audience, crowd, seated guests, cameramen, photographers, or crew — the space is empty except for the model
- NEVER add trees, flowers, plants, decorative accessories, or any elements not already in the scene
- The background/location stays EXACTLY as it appears in the image — do not invent or add any environmental elements

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
    shot_arc_id: Optional[str] = None,
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
        # User override — creative direction wins, skip arc injection
        selected_arc = None
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
        # Use explicit arc if provided by user, otherwise random pick
        selected_arc = _get_shot_arc_by_id(shot_arc_id) or _pick_defile_shot_arc()
        arc_block = _format_shot_arc(selected_arc, n_shots)
        user_text = (
            f"Outfit: {outfit_name or 'fashion garment'}\n"
            f"Number of shots: {n_shots}\n"
            f"Shot durations:\n{shots_description}\n\n"
            f"{arc_block}\n\n"
            "Analyze the runway scene in the image and write a cinematic multishot script that "
            "follows the narrative arc above. Return exactly the JSON array with the durations specified above."
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

    arc_label = selected_arc["name"] if selected_arc else "user-direction"
    logger.info("Defile multishot prompts for '%s': %d shots (arc: %s)",
                outfit_name, len(result), arc_label)
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
- Do NOT write positive enforcements like "closed skirt", "sealed gown", "no slit", "legs hidden" — the element reference already locks garment geometry

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


_OZEL_MULTISHOT_SYSTEM = """You are a fashion film director creating shot prompts for Kling AI video generation.

You receive:
1. A START FRAME image — this is the SCENE/LOCATION where all shots take place
2. 1–3 garment reference images (front required, back/side optional) — these are for element reference only
3. An optional creative brief

STEP 1 — EXTRACT THE SCENE (from the FIRST image — the start frame):
Identify and memorize the exact location: floor material, architecture, lighting, atmosphere.
Write a 6-10 word scene description to use as anchor in every prompt.
Example: "grand marble hall with black-and-white checkered floor"

STEP 2 — STUDY THE GARMENT (from garment reference images):
Note silhouette, key design details, hem length. You do NOT describe these in prompts — @Element1 handles garment appearance.

STEP 3 — GENERATE SHOTS:
- EVERY prompt MUST follow this exact pattern:
  "@Element1 In the [scene anchor], [model action and camera movement]"
- @Element1 is a Kling reference token that renders the exact garment
- The scene anchor keeps every shot locked to the start frame location
- NEVER describe garment color, fabric, or construction — @Element1 handles it
- NEVER change the scene between shots — all shots happen in the same location
- Each shot: 30–50 words, HARD LIMIT 480 characters
- If creative brief provided, translate each movement/moment into a shot
- If no brief, generate a compelling editorial fashion sequence

ABSOLUTE RULES (every shot, no exceptions):
- EVERY prompt starts with "@Element1 In the [scene anchor]," — no exceptions
- NEVER add stage lights, beauty dishes, tripods, or studio equipment
- Describe the garment exactly as the reference images show — slits, hem length, trains, openings are locked by the element reference, do NOT override them with positive phrases like "closed skirt" or "no slit"
- Vary camera angles for cinematic flow (wide, medium, close-up, etc.)
- Each shot continues seamlessly from the previous

Return JSON: {"scene_anchor": "...", "shots": [{"duration": 5, "prompt": "@Element1 In the ..., ..."}]}"""


async def generate_ozel_multishot_prompt(
    image_url: str,
    back_image_url: Optional[str] = None,
    side_image_url: Optional[str] = None,
    video_description: Optional[str] = None,
    scene_count: Optional[int] = None,
    total_duration: Optional[int] = None,
    start_frame_url: Optional[str] = None,
) -> list[dict]:
    """Generate @Element1-based multishot prompts for Özel mode.

    start_frame_url: the scene image shown first to GPT so it can extract the scene anchor.
    image_url: front garment reference (for @Element1).
    """

    if not video_description:
        video_description = "An elegant editorial fashion film. Model moves gracefully — slow walk, gentle turn, standing with presence. Cinematic lighting, atmospheric mood."

    constraint_note = ""
    if scene_count and total_duration:
        avg = total_duration // scene_count
        constraint_note = (
            f"\n\nCONSTRAINT: Generate EXACTLY {scene_count} shots. "
            f"Each shot approximately {avg}s (min 3s, max 10s). Total = {total_duration}s."
        )
    elif scene_count:
        constraint_note = f"\n\nCONSTRAINT: Generate EXACTLY {scene_count} shots."
    elif total_duration:
        constraint_note = (
            f"\n\nCONSTRAINT: Total duration must be EXACTLY {total_duration}s. "
            "Split into logical shots of 3–6s each."
        )

    views = ["front"]
    if side_image_url:
        views.append("side")
    if back_image_url:
        views.append("back")
    images_note = f"Garment reference images provided: {', '.join(views)} view(s)."
    scene_note = "The FIRST image is the start frame (the scene/location). " if start_frame_url else ""

    user_text = (
        f"{scene_note}{images_note}\n\n"
        f"Creative brief:\n{video_description}"
        f"{constraint_note}\n\n"
        "Generate shots where every prompt follows: '@Element1 In the [scene anchor], ...'"
    )

    content: list = []
    # Start frame first (scene anchor source) — then garment references
    if start_frame_url:
        content.append({"type": "image_url", "image_url": {"url": start_frame_url, "detail": "high"}})
    content.append({"type": "image_url", "image_url": {"url": image_url, "detail": "high"}})
    if side_image_url:
        content.append({"type": "image_url", "image_url": {"url": side_image_url, "detail": "high"}})
    if back_image_url:
        content.append({"type": "image_url", "image_url": {"url": back_image_url, "detail": "high"}})
    content.append({"type": "text", "text": user_text})

    response = await client.chat.completions.create(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": _OZEL_MULTISHOT_SYSTEM},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_completion_tokens=1200,
    )

    import re as _re

    raw = (response.choices[0].message.content or "").strip()

    scene_anchor: str = ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            _raw_anchor = parsed.get("scene_anchor", "")
            scene_anchor = _raw_anchor if isinstance(_raw_anchor, str) else ""
            if "shots" in parsed:
                shots = parsed["shots"]
            else:
                shots = next(v for v in parsed.values() if isinstance(v, list))
        elif isinstance(parsed, list):
            shots = parsed
        else:
            shots = next(v for v in parsed.values() if isinstance(v, list))
    except Exception:
        match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if match:
            shots = json.loads(match.group(0))
        else:
            raise ValueError(f"Could not parse ozel multishot prompts: {raw[:200]}")

    logger.info("Ozel scene anchor: '%s'", scene_anchor)

    result = []
    for s in shots:
        prompt = str(s.get("prompt", ""))
        dur = str(max(3, min(10, int(s.get("duration", 5)))))
        # Guarantee @Element1 prefix (GPT system prompt mandates it, this is just a safety net)
        if not prompt.startswith("@Element1"):
            prompt = "@Element1 " + prompt
        result.append({"duration": dur, "prompt": prompt[:480]})

    logger.info("Ozel multishot prompts: %d shots, total %ds", len(result), sum(int(s["duration"]) for s in result))
    return result


async def extract_scene_anchor(start_frame_url: str) -> str:
    """Start frame görselinden kısa bir sahne tanımı çıkar (Stüdyo modu için)."""
    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": _image_content(start_frame_url, detail="low")},
                    {"type": "text", "text": 'Describe the location/setting of this scene in 5-8 words only. No people, no garments. Return JSON only: {"scene_anchor": "..."}'},
                ],
            }],
            response_format={"type": "json_object"},
            max_tokens=80,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        anchor = data.get("scene_anchor", "")
        return str(anchor) if anchor else "elegant fashion studio with soft ambient lighting"
    except Exception:
        return "elegant fashion studio with soft ambient lighting"


async def generate_studio_ai_shots(
    element_image_url: str,
    start_frame_url: Optional[str] = None,
    shot_count: int = 2,
    user_hint: Optional[str] = None,
    element_names: Optional[list] = None,      # ["@2305", "@ayakkabi", ...]
    element_image_urls: Optional[list] = None, # all element image URLs
) -> list[dict]:
    """Stüdyo modu için AI çekim açıklamaları üretir."""
    shot_count = max(1, min(5, shot_count))

    # Build element token info for the prompt
    all_image_urls = element_image_urls or [element_image_url]
    all_names = element_names or ["@Element1"]

    def _infer_type(token: str) -> str:
        """Element isminden tipini çıkar."""
        name = token.lstrip("@").upper()
        if name.startswith("BBC-"):
            return "dress/garment"
        if name.startswith("AYAKKABI") or name.startswith("AYAKKABІ"):
            return "shoes/footwear"
        return "fashion element"

    token_info = ", ".join(
        f"{name} ({_infer_type(name)})" for name in all_names
    )

    user_content: list = []
    if start_frame_url:
        user_content.append({"type": "image_url", "image_url": _image_content(start_frame_url, detail="low")})
    # Add all element images (max 4)
    from itertools import islice as _islice
    for img_url in _islice(iter(all_image_urls), 4):
        user_content.append({"type": "image_url", "image_url": _image_content(img_url, detail="high")})

    # Separate hero vs supporting elements
    hero_tokens = [n for n in all_names if _infer_type(n) == "dress/garment"]
    support_tokens = [n for n in all_names if _infer_type(n) == "shoes/footwear"]
    other_tokens = [n for n in all_names if n not in hero_tokens and n not in support_tokens]

    hero_str = ", ".join(hero_tokens) if hero_tokens else ", ".join(all_names)
    support_str = ", ".join(support_tokens) if support_tokens else ""

    hint_text = f"\n\nUser notes: {user_hint}" if user_hint else ""
    support_rule = (
        f"- SUPPORTING elements ({support_str}): NEVER the main focus. "
        f"Only appear naturally and briefly — e.g. the shoe tip subtly visible at the hem as the model walks. "
        f"Do NOT write close-up shoe shots, heel detail shots, or any shot where footwear is the subject.\n"
    ) if support_str else ""

    user_content.append({
        "type": "text",
        "text": (
            f"Elements in this video: {token_info}.\n"
            f"Generate {shot_count} cinematic shot descriptions for this fashion video.\n"
            f"HIERARCHY RULES (strictly follow):\n"
            f"- HERO elements ({hero_str}): always the main subject. "
            f"Frame shots around the garment — silhouette, fabric movement, full-body, 3/4 angle, slow walk.\n"
            f"{support_rule}"
            f"- Always reference the hero @token in every shot description.\n"
            f"- Each description: 1-2 sentences, specific camera movement, model action.\n"
            f"- Duration: 5 seconds each. Avoid generic phrases.{hint_text}\n\n"
            f"Return JSON only: {{\"shots\": [{{\"description\": \"...\", \"duration\": 5}}, ...]}}"
        ),
    })

    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a fashion film director creating cinematic shot descriptions for Kling AI. "
                        "Hero elements (dress/garment) are always the primary subject of every shot. "
                        "Supporting elements (shoes/footwear) must NEVER be the focus — they appear only incidentally "
                        "when naturally visible during walking, like a shoe tip peeking under the hem. "
                        "Always reference elements by their @token names. "
                        "Never use the word 'train' or 'trailing'."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            max_tokens=700,
            temperature=0.8,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        shots = data.get("shots", [])
        return [
            {"description": str(s.get("description", "")), "duration": int(s.get("duration", 5))}
            for s in shots
        ]
    except Exception as e:
        logger.error("Studio AI shots error: %s", e)
        return [
            {"description": "Model walks confidently towards camera showcasing the garment details", "duration": 5}
            for _ in range(shot_count)
        ]




async def translate_garment_description(user_description: str) -> str:
    """Translate any-language garment description into concise English.

    Returns up to ~25 words of English text suitable for embedding in a
    [FABRIC LOCK: ...] Kling prompt anchor. Falls back to input on error.
    """
    if not user_description or not user_description.strip():
        return user_description
    # Fast path: ASCII-only = likely already English
    if all(ord(c) < 128 for c in user_description):
        return user_description

    system = (
        "You translate fashion garment descriptions into concise English for AI "
        "video prompts. Output maximum 25 words describing fabric, cut, color, "
        "silhouette and key visual details only. Plain text, no quotes, no prefixes."
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_description.strip()},
            ],
            max_tokens=80,
            temperature=0.2,
        )
        translated = (resp.choices[0].message.content or "").strip()
        # Strip surrounding quotes if any
        if translated and translated[0] in ('"', "'") and translated[-1] in ('"', "'"):
            translated = translated[1:-1].strip()
        logger.info("Garment desc translated: %s → %s",
                    user_description[:60], translated[:80])
        return translated if translated else user_description
    except Exception as e:
        logger.warning("translate_garment_description failed (%s) — using raw input", e)
        return user_description


async def translate_studio_shot_description(
    user_description: str,
    scene_anchor: str,
) -> str:
    """Translate/refine the user's free-form shot description (any language) into a
    clean, Kling-optimised English video prompt.

    Returns only the motion/camera description — the @ElementN prefix and scene
    anchor wrapper are added by the pipeline caller.
    Max ~120 words, no Turkish, no @ElementN tokens.
    """
    system = (
        "You are a Kling AI video prompt specialist for fashion films. "
        "Convert the user's description into a precise English video generation prompt. "
        "Focus on: model movement, walking direction, camera angle, shot framing, body language, pace. "
        "The setting/location is already handled separately — do NOT include location details. "
        "Do NOT include @Element tokens. Do NOT include garment constraint text. "
        "Output only the motion/camera description, maximum 120 words, plain text."
    )
    user_msg = (
        f"Scene context: {scene_anchor}\n"
        f"User request: {user_description}\n\n"
        "Write the Kling video prompt for this shot."
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=160,
            temperature=0.3,
        )
        translated = (resp.choices[0].message.content or "").strip()
        logger.info("Studio shot translated: %s → %s", user_description[:60], translated[:80])  # type: ignore[index]
        return translated if translated else user_description
    except Exception as e:
        logger.warning("translate_studio_shot_description failed (%s) — using raw description", e)
        return user_description


async def parse_studio_scenario_text(
    text: str,
    shot_count: int = 4,
    total_duration: int = 15,
) -> list[dict]:
    """Parse a free-form scenario/script text into individual studio shot configs.

    Accepts any language and any format (time-coded, bullet points, paragraph).
    Returns a list of {description: str, duration: int} dicts ready to fill
    the studio shot cards.

    Args:
        text: Raw scenario text from the user (e.g. "0–3 sec: Close shot...")
        shot_count: Desired number of output shots (1–5)
        total_duration: Total video duration in seconds (3–15)

    Returns:
        List of exactly shot_count dicts: [{description: str, duration: int}]
    """
    system = f"""You are a cinematic video shot designer for luxury fashion films.
The user provides a scenario script (any language, any format). Your job is to convert it into individual shot descriptions for Kling 3.0 Pro multishot generation.

RULES:
- Output exactly the requested number of shots
- Each shot: a rich, complete English cinematic description — include ALL relevant details from the script:
  * Camera angle and movement (e.g. "slow push-in", "low angle", "arc shot")
  * Model movement and body language (e.g. "slowly turns her head", "subtle breathing", "shifts arm on railing")
  * Lighting and atmosphere from the script (e.g. "golden sunlight behind her", "soft lens flare", "warm dusk glow")
  * Location/environment cues that set the scene (e.g. "on the stone balcony", "beneath Mediterranean arches")
  * Mood and pacing (e.g. "slow cinematic fashion film", "composed elegance", "natural minimal movement")
- Preserve the creative intent of every section of the script faithfully — do NOT omit details
- Max 480 characters per shot description
- The TOTAL duration across all shots MUST equal exactly {total_duration} seconds
- Distribute the total duration across shots intelligently — vary shot lengths based on content (action shots shorter, detail/atmosphere shots longer) but the sum MUST be {total_duration}s
- If the text contains time markers (e.g. "0–3 sec", "3–7 sec"), use those to calculate per-shot durations
- Duration per shot: minimum 3s, maximum 10s
- Do NOT include garment geometry enforcement phrases (no "closed skirt", "no slit", etc.)
- Do NOT add spectators, crowd, or crew

Return a JSON object:
{{"shots": [{{"description": "...", "duration": 4}}, {{"description": "...", "duration": 3}}]}}"""

    user_msg = (
        f"Scenario text:\n{text}\n\n"
        f"Required shots: {shot_count}\n"
        f"Total video duration: {total_duration}s (distribute across the {shot_count} shots)\n\n"
        "Parse this into the shot array."
    )

    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        shots = parsed.get("shots") or parsed.get("Shots") or []

        # Enforce shot count, clamp duration, ensure required keys
        fallback_dur = max(3, min(10, total_duration // shot_count))
        result = []
        for i in range(shot_count):
            if i < len(shots):
                s = shots[i]
                dur = max(3, min(10, int(s.get("duration") or fallback_dur)))
                desc = str(s.get("description") or "").strip()
            else:
                dur = fallback_dur
                desc = f"Cinematic fashion shot {i + 1}, model elegant movement, warm editorial light."
            result.append({"description": desc, "duration": dur})

        # Ensure total matches requested total_duration (adjust last shot)
        current_total = sum(r["duration"] for r in result)
        if current_total != total_duration and result:
            diff = total_duration - current_total
            result[-1]["duration"] = max(3, min(10, result[-1]["duration"] + diff))

        logger.info("parse_studio_scenario_text: %d shots, %ds total from %d chars of input",
                     len(result), sum(r["duration"] for r in result), len(text))
        return result
    except Exception as exc:
        logger.warning("parse_studio_scenario_text failed (%s) — returning generic shots", exc)
        fallback_dur = max(3, min(10, total_duration // shot_count))
        return [
            {"description": f"Cinematic fashion shot {i + 1}, model poised movement, luxury editorial.", "duration": fallback_dur}
            for i in range(shot_count)
        ]


async def analyse_garment_slits(
    frontal_url: str,
    reference_urls: Optional[list[str]] = None,
) -> str:
    """Analyse element images with GPT-4o Vision and return a precise slit/train constraint
    string ready to be injected into Kling shot prompts.

    Example return value:
        "Garment has a small back-center slit only. NO front slit. NO side slit. NO train."
    """
    image_blocks: list[dict] = []

    # frontal image — label it
    image_blocks.append({"type": "text", "text": "FRONT VIEW:"})
    image_blocks.append({"type": "image_url", "image_url": _image_content(frontal_url, detail="high")})

    _refs: list[str] = list(reference_urls or [])
    for label, ref_url in zip(("BACK VIEW", "SIDE VIEW", "EXTRA VIEW"), _refs):
        image_blocks.append({"type": "text", "text": f"{label}:"})
        image_blocks.append({"type": "image_url", "image_url": _image_content(ref_url, detail="high")})

    image_blocks.append({"type": "text", "text": (
        "Examine these garment reference images carefully. "
        "Answer ONLY about: (1) front slit, (2) side slit, (3) back slit, (4) train/trailing fabric. "
        "For each: state whether it EXISTS or DOES NOT EXIST, and if it exists describe its size/location precisely. "
        "Then write a single compact constraint sentence (max 40 words) starting with 'Garment:' "
        "that an AI video generator must follow exactly when animating this garment from any angle. "
        'Return JSON only: {"constraint": "..."}'
    )})

    try:
        resp = await client.chat.completions.create(
            model="gpt-5.4",
            messages=[{"role": "user", "content": image_blocks}],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        constraint = str(data.get("constraint", "")).strip()
        logger.info("Garment slit analysis: %s", constraint)
        return constraint if constraint else ""
    except Exception as e:
        logger.warning("analyse_garment_slits failed: %s", e)
        return ""

