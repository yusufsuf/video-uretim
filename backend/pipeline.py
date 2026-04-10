"""Pipeline – orchestrates the full fashion video generation workflow.

New Flow (v2):
1. Analyse the garment (GPT-4o Vision)
2. Generate multi-scene prompts (GPT-4o – cinematography rules)
3. Generate background image (Nano Banana Pro via fal.ai)
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
    SingleScenePrompt,
)
from services.analysis_service import analyse_dress, generate_multi_scene_prompt, generate_defile_multishot_prompt, generate_custom_multishot_prompt, generate_ozel_multishot_prompt, extract_scene_anchor, analyse_garment_slits, translate_studio_shot_description
from services.nano_banana_service import generate_background, generate_scene_frame
from services.video_service import (
    download_file,
    generate_multishot_video,
    generate_motion_control_video,
    extract_last_frame,
    upload_to_fal,
    concatenate_clips,
)
import io
import shutil

from PIL import Image

logger = logging.getLogger(__name__)


def _tr_error(exc: Exception) -> str:
    """Convert a raw exception into a user-friendly Turkish error message."""
    msg = str(exc).lower()

    # ── OpenAI / GPT ──────────────────────────────────────────────────────────
    if "insufficient_quota" in msg or "you exceeded your current quota" in msg:
        return "OpenAI krediniz tükendi. Lütfen platform.openai.com adresinden hesabınıza kredi yükleyin."
    if "rate_limit_exceeded" in msg or "rate limit" in msg:
        return "OpenAI istek limiti aşıldı. Lütfen birkaç dakika bekleyip tekrar deneyin."
    if "invalid_api_key" in msg or "incorrect api key" in msg:
        return "OpenAI API anahtarı geçersiz. Lütfen ayarlarınızı kontrol edin."
    if "context_length_exceeded" in msg or "max_tokens" in msg:
        return "Prompt çok uzun, OpenAI işleyemedi. Lütfen açıklama uzunluğunu azaltın."
    if "openai" in msg and ("timeout" in msg or "timed out" in msg):
        return "OpenAI yanıt vermedi (zaman aşımı). Lütfen tekrar deneyin."

    # ── fal.ai / Kling ────────────────────────────────────────────────────────
    if "fal" in msg and ("quota" in msg or "limit" in msg or "credit" in msg):
        return "fal.ai krediniz tükendi. Lütfen fal.ai hesabınıza kredi yükleyin."
    if "fal" in msg and ("timeout" in msg or "timed out" in msg):
        return "Video üretimi zaman aşımına uğradı. Lütfen tekrar deneyin."
    if "kling" in msg and "error" in msg:
        return f"Kling video üretimi başarısız oldu. Lütfen tekrar deneyin."
    if "422" in msg and "fal" in msg:
        return "fal.ai isteği reddetti. Görsel formatı veya parametreler hatalı olabilir."
    if "image" in msg and ("too large" in msg or "size" in msg or "10mb" in msg or "10 mb" in msg):
        return "Görsel dosya boyutu çok büyük. Lütfen 10 MB'dan küçük bir görsel yükleyin."

    # ── Nano Banana / Arka plan ───────────────────────────────────────────────
    if "nano" in msg or "background" in msg and "timeout" in msg:
        return "Arka plan üretimi zaman aşımına uğradı. Lütfen tekrar deneyin."

    # ── Ağ / Bağlantı ─────────────────────────────────────────────────────────
    if "connection" in msg and ("refused" in msg or "reset" in msg or "aborted" in msg):
        return "Sunucuya bağlanılamadı. İnternet bağlantınızı kontrol edin ve tekrar deneyin."
    if "timeout" in msg or "timed out" in msg:
        return "İşlem zaman aşımına uğradı. Lütfen tekrar deneyin."
    if "name or service not known" in msg or "dns" in msg or "nodename" in msg:
        return "DNS hatası: sunucuya ulaşılamadı. İnternet bağlantınızı kontrol edin."
    if "ssl" in msg or "certificate" in msg:
        return "SSL/Sertifika hatası oluştu. Lütfen tekrar deneyin."

    # ── Görsel / Dosya ────────────────────────────────────────────────────────
    if "ssrf" in msg:
        return "Güvenlik hatası: geçersiz görsel URL'si. Lütfen farklı bir görsel deneyin."
    if "cannot identify image file" in msg or "not an image" in msg:
        return "Yüklenen dosya geçerli bir görsel değil. Lütfen JPG, PNG veya WEBP dosyası yükleyin."
    if "permission denied" in msg or "access denied" in msg:
        return "Dosya erişim hatası. Lütfen tekrar deneyin."
    if "no space left" in msg or "disk full" in msg:
        return "Sunucu diski dolu. Lütfen daha sonra tekrar deneyin."
    if "özel modda video promptu zorunludur" in msg:
        return "Özel mod için video açıklaması boş bırakılamaz. Lütfen bir açıklama girin."

    # ── Supabase / Depolama ───────────────────────────────────────────────────
    if "supabase" in msg or "storage" in msg and "error" in msg:
        return "Video kaydedilirken hata oluştu. Lütfen tekrar deneyin."

    # ── Genel fallback ────────────────────────────────────────────────────────
    return f"Beklenmedik bir hata oluştu. Lütfen tekrar deneyin. (Detay: {exc})"


_ELEMENTS_MAX_BYTES = 9 * 1024 * 1024  # 9 MB — safe margin under Kling's 10 MB limit
_ELEMENTS_MAX_PX = 1536  # max dimension for element images

# ── Layered Negative Prompt System ────────────────────────────────────────────
# Layer 1: STRUCTURAL — garment integrity
_NEG_STRUCTURAL = (
    "changed outfit, different dress, altered silhouette, different fabric, "
    "costume change, wardrobe change, morphing clothes, distortion, extra fabric, "
    "tail formation, dress change, redesign, altered proportions, "
    "floating hem, lifted skirt, hem above ground, gap between dress and floor, "
    "short dress, mini dress, midi dress, knee-length dress, calf-length dress, "
    "cropped skirt, raised hemline, above-ankle hem, shortened dress, "
    "front slit, side slit, back slit visible, high slit, thigh slit, deep slit, "
    "visible knee, visible thigh, visible shin, visible calf, exposed leg, leg gap, "
    "opened hem, split skirt, parted skirt, walking slit, step gap, slit while walking, "
    "skirt opening during movement, fabric parting while walking"
)
# Layer 2: ANATOMICAL — human figure integrity
_NEG_ANATOMICAL = (
    "deformed hands, deformed face, extra limbs, missing fingers, elongated neck, "
    "fused face, asymmetric eyes, bad anatomy, distorted face, disfigured, "
    "extra fingers, mutated hands, poorly drawn face"
)
# Layer 3: TECHNICAL — image/video quality
_NEG_TECHNICAL = (
    "blur, distort, low quality, low resolution, pixelated, grainy, "
    "noise, compression artifacts, jpeg artifacts, glitches, flicker, "
    "frame drops, shaky, watermark, text, logo, oversaturated, "
    "cartoon, anime, illustration, painting, artificial, synthetic, plastic"
)
# Layer 4: FOOT/SHOE visibility (fashion-specific)
_NEG_FEET = (
    "feet, bare feet, shoes, heels, boots, footwear, visible ankles, visible toes"
)

# Composite negatives
_BASE_NEGATIVE = f"{_NEG_STRUCTURAL}, {_NEG_ANATOMICAL}, {_NEG_TECHNICAL}, {_NEG_FEET}"

_TRAIN_NEGATIVE = (
    ", train, trailing fabric, floor-length train, dragging hem, sweeping train, "
    "extended hem, pooling fabric, cathedral train, chapel train, court train, "
    "brush train, fabric trail, hem trail, skirt extension, elongated skirt back"
)

# Defile adds environmental layer
_NEG_ENVIRONMENTAL = (
    "spectators, audience, crowd, seated guests, cameraman, photographer, crew, "
    "people in background, bystanders, onlookers, "
    "trees, flowers, plants, flower arrangements, decorative props, added accessories, "
    "extra furniture, added decor, altered background, modified scenery, "
    "cluttered background, distracting elements, messy, chaotic"
)
_DEFILE_NEGATIVE = f"{_BASE_NEGATIVE}, {_NEG_ENVIRONMENTAL}"

# NOTE: HEM_LOCK / HEM_LOCK_SHORT enforcement was removed — Kling elements now
# preserve garment silhouette and slit geometry reliably from the reference
# images alone, so force-injecting "no slit / sealed gown / legs hidden" into
# every shot prompt caused over-correction (legs hidden on garments that
# actually showed them, slit elimination on designs that had slits, etc.).

_TRAIN_WORDS = {"train", "trailing", "sweep", "court", "chapel", "cathedral", "sweeping hem", "kuyruk", "uzun kuyruk"}


def _has_train(analysis) -> bool:
    """Return True if the garment analysis indicates a train/trailing hem."""
    combined = " ".join([
        analysis.hem_description or "",
        analysis.back_details or "",
        analysis.back_silhouette or "",
        analysis.length or "",
        analysis.front_silhouette or "",
        analysis.description_en or "",
    ]).lower()
    return any(w in combined for w in _TRAIN_WORDS)


# ── Prompt Engineering System ─────────────────────────────────────────────────
# Source: sistem-detayları.txt — 7-layer prompt engineering for NB Pro + Kling

# 1. FABRIC PHYSICS — auto-detected from DressAnalysisResult.fabric
_FABRIC_PHYSICS: dict[str, str] = {
    # Lightweight / flowing
    "silk":    "silk floats with a gentle delay, fluid drape, liquid sheen, specular highlights",
    "satin":   "satin falls with a gentle delay, liquid sheen, smooth specular highlights on folds",
    "chiffon": "translucent chiffon, light refraction, delicate float, gossamer layers",
    "organza": "sheer organza with light refraction, translucency, delicate hexagonal weave texture",
    "tulle":   "soft tulle layers with airy volume, translucent mesh, gentle float",
    "crepe":   "matte crepe with subtle drape, soft flowing movement, minimal sheen",
    "jersey":  "stretchy jersey clings to form, smooth drape, body-hugging movement",
    # Heavy / structured
    "velvet":  "light-trapping velvet, shadow pooling in folds, directional nap with subtle sheen",
    "denim":   "denim keeps its weight, holds its structure, stiff fabric movement",
    "wool":    "wool keeps its weight, structured drape, warm heavy fabric movement",
    "tweed":   "textured tweed holds its structure, woven surface detail, structured movement",
    "leather": "leather holds rigid form, smooth surface sheen, minimal fabric movement",
    "lace":    "intricate lace overlay with transparency, delicate pattern visible, fine needlework detail",
    "sequin":  "sequined surface catching light, shimmering reflections shift with movement",
    "brocade": "heavy brocade with raised woven pattern, structured drape, rich texture detail",
    # Defaults
    "cotton":  "natural cotton drape, soft matte texture, gentle movement",
    "linen":   "linen with natural creases, matte texture, structured casual drape",
    "polyester": "smooth synthetic drape, consistent sheen, fluid movement",
}

def _get_fabric_physics(analysis) -> str:
    """Extract fabric physics prompt from garment analysis."""
    if not analysis:
        return ""
    fabric = (analysis.fabric or "").lower().strip()
    # Direct match
    for key, prompt in _FABRIC_PHYSICS.items():
        if key in fabric:
            return prompt
    # Fuzzy match on description
    desc = (analysis.description_en or "").lower()
    for key, prompt in _FABRIC_PHYSICS.items():
        if key in desc:
            return prompt
    return "natural fabric drape, consistent texture throughout movement"


# 2. LIGHT ANCHORING — constant across all NB Pro and Kling prompts
_LIGHT_ANCHOR = (
    "Two-point lighting: soft key light at 45 degrees from left, "
    "cool rim light at 120 degrees from right for silhouette separation. "
    "Catchlights visible in eyes. Consistent shadow direction throughout."
)

_LIGHT_ANCHOR_SHORT = "Soft key light 45° left, cool rim light 120° right, consistent shadows."


# 3. CAMERA VOCABULARY — Kling-optimized sinema dili
_CAMERA_VOCABULARY: dict[str, str] = {
    "dolly_in":   "slow dolly-in push toward subject, 35mm cinematic feel, smooth steady movement",
    "dolly_out":  "slow dolly-out pulling away from subject, revealing full silhouette and environment",
    "orbit":      "quarter-circle orbit at hip height, revealing garment from multiple angles, smooth arc",
    "pan":        "locked-off tripod, slow horizontal pan following the model's walk, hip height camera",
    "tilt_up":    "locked-off tripod, slow vertical tilt from hem detail up to full silhouette reveal",
    "tilt_down":  "locked-off tripod, slow vertical tilt from face down to hem and fabric detail",
    "tracking":   "lateral tracking shot at hip height, camera moves parallel to model's walk, steady pace",
    "crane":      "elevated crane descending slowly, bird's-eye transitioning to eye-level reveal",
    "static":     "locked-off static tripod at eye level, premium stability, model moves within frame",
    "low_angle":  "low angle static shot looking upward, model dominates frame, power and elegance",
    "high_angle": "elevated angle looking down, geometric composition, full garment silhouette visible",
    "close_up":   "tight close-up, 85mm portrait lens feel, fabric texture and construction detail",
    "medium":     "medium shot at waist height, 50mm prime feel, balanced figure and garment detail",
    "wide":       "wide establishing shot, 24mm cinematic, full environment and silhouette context",
}

def _get_camera_prompt(camera_move: str, shot_size: str = "", camera_angle: str = "") -> str:
    """Get Kling-optimized camera prompt from camera_move + optional size/angle."""
    cam = _CAMERA_VOCABULARY.get(camera_move, _CAMERA_VOCABULARY["static"])
    # Override with specific shot_size if given
    if shot_size and shot_size in _CAMERA_VOCABULARY:
        cam = _CAMERA_VOCABULARY[shot_size]
    return cam


# 4. IMMUTABLE GARMENT ANCHOR — built from DressAnalysisResult
def _build_garment_anchor(analysis) -> str:
    """Build an immutable anchor block from garment analysis for prompt consistency."""
    if not analysis:
        return ""
    parts = []
    if analysis.color:
        parts.append(analysis.color)
    if analysis.fabric:
        parts.append(analysis.fabric)
    if analysis.garment_type:
        parts.append(analysis.garment_type)
    if analysis.cut_style:
        parts.append(f"{analysis.cut_style} cut")
    if analysis.length:
        parts.append(f"{analysis.length} length")
    anchor = " ".join(parts) if parts else "garment"
    return (
        f"[GARMENT ANCHOR: {anchor}. "
        f"Exact cut preserved, no redesign, unchanged silhouette, no added fabric, "
        f"structure remains identical throughout all movement.]"
    )


# 5. MICRO-ACTIONS — subtle realism details
_MICRO_ACTIONS = (
    "Subtle natural breathing visible. "
    "Fabric settles naturally with gravity after each movement. "
    "Hair responds subtly to movement direction."
)


# 5b. STYLE BIBLE — consistent style sentence appended to EVERY shot prompt.
# Research: repeating a short, identical "style bible" across all shots of a
# multi-shot render measurably reduces color/lighting drift between cuts.
# Two versions: full (~128 chars) used when budget allows, short (~62 chars)
# for tight budgets like Defile where HEM_LOCK + GPT text already eat space.
_STYLE_BIBLE = (
    "Style: high-end fashion editorial, cinematic color grading, "
    "shallow depth of field, photorealistic skin, consistent lighting."
)
_STYLE_BIBLE_SHORT = "Style: cinematic editorial, photoreal skin, consistent light."

# 5c. SHORTER MICRO-ACTIONS — for modes where budget is tight (Defile/Studio).
_MICRO_ACTIONS_SHORT = "Subtle breathing, fabric settles naturally with gravity."


# 5d. FABRIC-SPECIFIC NEGATIVE PROMPTS — injected when fabric is known
# to prevent Kling from drifting toward the wrong texture/finish.
_FABRIC_NEGATIVES: dict[str, str] = {
    "silk":    "satin sheen, plastic-looking fabric, rubber texture, vinyl finish",
    "satin":   "matte cotton finish, dull fabric, chalky texture",
    "chiffon": "stiff fabric, heavy drape, opaque thick fabric, rigid cloth",
    "organza": "soft flowy drape, matte fabric, thick weave",
    "tulle":   "heavy fabric, opaque cloth, rigid structure",
    "velvet":  "shiny synthetic fabric, plastic sheen, smooth satin finish",
    "denim":   "soft silk-like drape, shiny fabric, fluid flow, satin sheen",
    "leather": "soft fabric drape, cotton texture, matte cloth finish",
    "cotton":  "synthetic plastic sheen, satin shine, vinyl finish",
    "linen":   "shiny synthetic fabric, silk-like drape, plastic finish",
    "wool":    "shiny synthetic fabric, plastic sheen, satin finish",
    "tweed":   "smooth fabric, silk drape, shiny finish",
    "lace":    "solid opaque fabric, thick weave, heavy cloth",
    "crepe":   "shiny glossy surface, plastic sheen, satin finish",
    "jersey":  "rigid stiff fabric, structured drape, heavy cloth",
    "sequin":  "matte dull surface, flat fabric, no reflections",
    "brocade": "plain flat fabric, smooth surface, no texture",
    "polyester": "heavy matte drape, rigid cloth",
}

def _get_fabric_negative(fabric: Optional[str]) -> str:
    """Return fabric-specific negative prompt additions."""
    if not fabric:
        return ""
    f = fabric.lower().strip()
    for key, neg in _FABRIC_NEGATIVES.items():
        if key in f:
            return neg
    return ""


# 5e. UNIFIED GARMENT META RESOLVER ────────────────────────────────────────
# Consolidates fabric/description/color info from either DressAnalysisResult
# or a library_items row dict. Library metadata (user-authored) takes
# precedence over analysis (AI-inferred) when both are available.

def _make_garment_meta(
    analysis=None,
    lib_row: Optional[dict] = None,
) -> dict:
    """Unified garment info for prompt enhancement.

    Returns: {"fabric": str, "description": str, "color": str, "name": str}
    Library row (user-authored) overrides analysis (AI-guessed).
    """
    meta: dict = {"fabric": "", "description": "", "color": "", "name": ""}
    if analysis is not None:
        meta["fabric"] = (getattr(analysis, "fabric", "") or "").strip()
        meta["color"] = (getattr(analysis, "color", "") or "").strip()
        meta["description"] = (getattr(analysis, "description_en", "") or "").strip()
        meta["name"] = (getattr(analysis, "garment_type", "") or "").strip()
    if lib_row:
        if lib_row.get("fabric"):
            meta["fabric"] = str(lib_row["fabric"]).strip()
        if lib_row.get("description"):
            meta["description"] = str(lib_row["description"]).strip()
        if lib_row.get("name") and not meta["name"]:
            meta["name"] = str(lib_row["name"]).strip()
    return meta


async def _resolve_garment_meta_from_url(
    front_url: Optional[str],
    analysis=None,
) -> dict:
    """Fetch library metadata for a garment URL + merge with analysis.

    Also translates non-English (e.g. Turkish) descriptions into concise
    English before they flow into prompt anchors, so Kling never sees raw
    Turkish text which it cannot parse visually.
    """
    lib_row = None
    if front_url:
        try:
            from services.library_service import get_item_by_url
            lib_row = await get_item_by_url(front_url)
        except Exception as _exc:
            logger.debug("Library lookup failed for %s: %s", str(front_url)[:60], _exc)
    meta = _make_garment_meta(analysis=analysis, lib_row=lib_row)
    desc = meta.get("description") or ""
    if desc:
        try:
            translated = await _ensure_english_description(desc)
            if translated:
                meta["description"] = translated
        except Exception as _exc:
            logger.debug("Description translation failed: %s", _exc)
    return meta


def _build_meta_anchor(meta: Optional[dict], max_len: int = 100) -> str:
    """Build a terse fabric/description lock anchor (≤ max_len chars).

    Priority: fabric is the key signal; description is the accent.
    Element name (user-chosen label like "Elbise1" or "test-outfit") is
    deliberately EXCLUDED — it's noise that leaks into prompts and confuses
    Kling without adding any visual information.
    """
    if not meta:
        return ""
    fabric = (meta.get("fabric") or "").strip()
    desc = (meta.get("description") or "").strip()
    color = (meta.get("color") or "").strip()

    if not fabric and not desc and not color:
        return ""

    # Start with fabric (most important signal for rendering)
    if fabric:
        head = fabric
        if color:
            head = f"{color} {head}"
        prefix = f"[FABRIC LOCK: {head}"
        # Room left after "]" closing bracket
        desc_budget = max_len - len(prefix) - 4  # "— ]"
        if desc and desc_budget > 10:
            desc_short = _smart_truncate(desc, desc_budget).rstrip().rstrip(",.;:")
            return f"{prefix} — {desc_short}]"
        return f"{prefix}]"

    # No fabric — use description (or color) as outfit tag
    if desc:
        body = f"{color} — {desc}" if color else desc
        desc_budget = max_len - len("[OUTFIT: ]")
        body_short = body[:desc_budget].rstrip().rstrip(",.;:")
        return f"[OUTFIT: {body_short}]"

    if color:
        return f"[OUTFIT: {color[:max_len - 10]}]"
    return ""


def _smart_truncate(text: str, max_len: int) -> str:
    """Truncate at the last sentence/word boundary within max_len — never mid-word.

    Tries sentence boundaries first (. ! ?), then commas, then any whitespace.
    Falls back to a hard cut only if no boundary exists in the latter 70% of
    the budget (preventing too-aggressive loss of content).
    """
    if len(text) <= max_len:
        return text
    head = text[:max_len]
    soft_floor = int(max_len * 0.7)
    # Sentence-ending separators: keep the punctuation
    for sep in (". ", "! ", "? "):
        idx = head.rfind(sep)
        if idx >= soft_floor:
            return head[: idx + 1].rstrip()
    # Non-terminal separators: drop the separator + any trailing punctuation
    for sep in (", ", "; ", " "):
        idx = head.rfind(sep)
        if idx >= soft_floor:
            return head[:idx].rstrip().rstrip(",;:")
    return head  # fallback: hard cut (rare)


def _get_fabric_physics_str(fabric: Optional[str]) -> str:
    """Map a fabric string (not an analysis object) to physics description."""
    if not fabric:
        return ""
    f = fabric.lower().strip()
    for key, prompt in _FABRIC_PHYSICS.items():
        if key in f:
            return prompt
    return ""


def _apply_quality_layers(
    core_prompt: str,
    meta: Optional[dict] = None,
    max_len: int = 512,
) -> str:
    """Layer quality anchors onto an existing shot prompt without truncating
    the core mid-sentence.

    The core_prompt (HEM_LOCK + GPT shot description, @Element tokens, etc.)
    is the most important signal for the model. It is ALWAYS preserved in full
    if it fits, otherwise trimmed at a sentence/word boundary — never mid-word.

    Strategy (priority order — highest first):
      1. Core prompt — sacred, never cut mid-word
      2. [FABRIC LOCK: ...] prefix — carries user-authored fabric/desc
      3. Style Bible short — cross-shot consistency
      4. Fabric physics — material behaviour
      5. Micro actions — realism details

    If space runs out, layers are dropped starting from the bottom of the
    priority list. The full Style Bible is preferred over the short one
    when there's room.
    """
    core = core_prompt.strip()
    anchor = _build_meta_anchor(meta, max_len=100) if meta else ""
    fabric_val = (meta or {}).get("fabric", "")
    physics = _get_fabric_physics_str(fabric_val) if fabric_val else ""

    # Pick the longest style bible that will plausibly fit.
    # If core alone is already tight, prefer the short version.
    core_budget_pressure = len(core) + len(anchor) + 2
    preferred_style = _STYLE_BIBLE if core_budget_pressure < 350 else _STYLE_BIBLE_SHORT

    # Build the layer stack in priority order (drop from tail when over budget)
    layers: list[tuple[str, str]] = [
        ("core", core),
    ]
    if anchor:
        layers.insert(0, ("anchor", anchor))  # prepend
    if preferred_style:
        layers.append(("style_bible", preferred_style))
    if physics:
        layers.append(("physics", physics))
    layers.append(("micro", _MICRO_ACTIONS_SHORT))

    # Keep dropping from the tail (lowest priority) until we fit
    def _assemble(ls: list[tuple[str, str]]) -> str:
        return " ".join(text for _, text in ls)

    while len(_assemble(layers)) > max_len and len(layers) > 2:
        layers.pop()  # drop last (lowest priority) layer

    # If dropping optional layers still isn't enough, swap full Style Bible
    # for the short version (if present)
    if len(_assemble(layers)) > max_len:
        for i, (key, _txt) in enumerate(layers):
            if key == "style_bible":
                layers[i] = ("style_bible", _STYLE_BIBLE_SHORT)
                break

    # Last resort: drop style bible entirely
    if len(_assemble(layers)) > max_len:
        layers = [(k, t) for k, t in layers if k != "style_bible"]

    # If we STILL don't fit, it's because anchor + core is too long.
    # Trim the core at a word boundary (anchor stays intact as it carries fabric lock).
    if len(_assemble(layers)) > max_len:
        over = len(_assemble(layers)) - max_len
        for i, (key, text) in enumerate(layers):
            if key == "core":
                trimmed = _smart_truncate(text, len(text) - over - 2)
                layers[i] = ("core", trimmed)
                break

    result = _assemble(layers)
    # Absolute safety net — if somehow still over, smart-truncate the whole thing
    if len(result) > max_len:
        result = _smart_truncate(result, max_len)
    return result


# ─── Translation cache for garment descriptions ──────────────────────────────
# In-process cache so we don't re-translate the same user description on every
# Defile outfit or Studio shot. Key: raw description, Value: English version.
_DESC_TRANSLATION_CACHE: dict[str, str] = {}


async def _ensure_english_description(desc: Optional[str]) -> Optional[str]:
    """Translate a garment description to English if not already ASCII.

    Caches results in-process to avoid repeat API calls during a single
    Defile run with multiple outfits sharing the same description.
    Falls back to the original text on any error.
    """
    if not desc:
        return desc
    stripped = desc.strip()
    if not stripped:
        return desc
    # Fast path: already ASCII → assume English, skip translation
    if all(ord(c) < 128 for c in stripped):
        return stripped
    if stripped in _DESC_TRANSLATION_CACHE:
        return _DESC_TRANSLATION_CACHE[stripped]
    try:
        from services.analysis_service import translate_garment_description
        translated = await translate_garment_description(stripped)
        _DESC_TRANSLATION_CACHE[stripped] = translated
        return translated
    except Exception as _exc:
        logger.debug("Description translation skipped: %s", _exc)
        return stripped


# 6. COMPOSITE PROMPT BUILDER — combines all layers for Kling shot prompts
def _build_enhanced_prompt(
    base_prompt: str,
    analysis=None,
    camera_move: str = "",
    include_light: bool = True,
    include_micro: bool = True,
    max_len: int = 512,
) -> str:
    """Build a 7-layer enhanced prompt for Kling video generation.

    Layers: garment_anchor + fabric_physics + light + camera + base + micro
    Priority: garment_anchor and fabric_physics are NEVER truncated.
    """
    parts: list[str] = []

    # Layer 1: Garment anchor (highest priority)
    anchor = _build_garment_anchor(analysis)
    if anchor:
        parts.append(anchor)

    # Layer 2: Fabric physics
    physics = _get_fabric_physics(analysis)
    if physics:
        parts.append(physics)

    # Layer 3: Light anchoring
    if include_light:
        parts.append(_LIGHT_ANCHOR_SHORT)

    # Layer 4: Camera
    if camera_move:
        parts.append(_get_camera_prompt(camera_move))

    # Layer 5: Base prompt (the actual shot description)
    parts.append(base_prompt)

    # Layer 6: Micro-actions
    if include_micro:
        parts.append(_MICRO_ACTIONS)

    # Layer 7: Style Bible — identical tail on every shot to lock cross-shot look.
    parts.append(_STYLE_BIBLE)

    combined = " ".join(parts)
    return combined[:max_len]


# 7. ENHANCED NB PRO PROMPT BUILDER — for scene frame composition
def _build_nb_pro_compose_prompt(
    analysis=None,
    aspect_ratio: str = "9:16",
) -> str:
    """Build an enhanced NB Pro Edit prompt for scene frame composition."""
    garment_hint = f"{analysis.color} {analysis.garment_type}" if analysis else "garment"
    fabric_hint = _get_fabric_physics(analysis) if analysis else ""

    return (
        f"Fashion editorial photo: the first image is the location/scene — "
        f"preserve it EXACTLY as-is: architecture, lighting, floor, walls, all structural elements unchanged. "
        f"Do NOT add spectators, audience, crowd, cameramen, photographers, trees, flowers, plants, or any props not already in the scene. "
        f"Place a tall fashion model at the FAR END of the scene — at the entrance point of the runway or space, "
        f"positioned deep in the background so the full depth of the scene is visible in front of her. "
        f"The model faces the camera directly, standing at the very beginning of the runway/walkway, "
        f"as if she has just stepped onto the catwalk and is about to walk forward toward the camera. "
        f"Full body visible from head to floor, medium-wide shot, confident upright stance. "
        f"The model wears the {garment_hint} from the reference images (images 2 onward). "
        f"Preserve all garment details: exact colors, fabric, cut, silhouette, length. "
        f"{fabric_hint + '. ' if fabric_hint else ''}"
        f"{_LIGHT_ANCHOR} "
        f"CRITICAL: the garment hem must touch and rest exactly on the floor — "
        f"the bottom of the garment grazes the floor surface. "
        f"Shoes and feet must NOT be visible — the hem completely covers the feet. "
        f"Professional fashion photography, sharp focus, editorial quality."
    )


async def _to_fal_url_compressed(url: str) -> str:
    """Like _to_fal_url but also resizes/compresses the image to stay under Kling's
    10 MB elements limit. Returns a fal.ai CDN URL pointing to the compressed image.
    """
    clean_url: str = url.split("?")[0]
    if not _is_ssrf_safe(clean_url):
        raise ValueError(f"SSRF blocked: {clean_url}")
    try:
        local = await download_file(clean_url, settings.TEMP_DIR, extension=".jpg")
        # Compress with Pillow
        with Image.open(local) as img:
            img = img.convert("RGB")
            w, h = img.size
            # Resize if either dimension exceeds max
            if w > _ELEMENTS_MAX_PX or h > _ELEMENTS_MAX_PX:
                img.thumbnail((_ELEMENTS_MAX_PX, _ELEMENTS_MAX_PX), Image.LANCZOS)
            # Save with progressive quality reduction until under limit
            quality = 88
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            while buf.tell() > _ELEMENTS_MAX_BYTES and quality > 50:
                quality -= 10
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
            with open(local, "wb") as f:
                f.write(buf.getvalue())
        fal_url = await upload_to_fal(local)
        try:
            os.remove(local)
        except Exception:
            pass
        logger.info("Compressed + re-uploaded for elements: %s → %s (q=%d)", clean_url[:60], fal_url[:60], quality)
        return fal_url
    except Exception as e:
        logger.warning("Could not compress %s (%s) — falling back to _to_fal_url", clean_url[:60], e)
        return await _to_fal_url(url)

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

    NB Pro Edit (and some other fal.ai models) cannot access Supabase storage or
    local-server URLs. This helper downloads the file and re-uploads it so
    fal.ai can fetch it reliably.
    """
    # Already on fal.ai CDN — skip (but NOT fal.media scene frames which Kling may time out on)
    if any(s in url for s in ("fal.run", "storage.googleapis.com/isolate")):
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


async def get_or_create_kling_element(
    front_url: str,
    frontal_image_url: str,
    reference_image_urls: list,
    name: str = "garment",
    description: str = "fashion garment",
) -> Optional[int]:
    """Return cached kling_element_id from Supabase, or create a new one and cache it.

    Returns None if the element cannot be created because only the frontal
    image is available (Kling requires ≥1 refer_image that differs from front).
    Callers should skip element_list in that case.

    front_url: the original library image_url (for cache lookup)
    frontal_image_url / reference_image_urls: compressed URLs for Kling API
    """
    from services.kling_service import create_element  # type: ignore[import]
    from services.library_service import get_item_by_url, set_kling_element_id

    # Check cache
    item = await get_item_by_url(front_url)
    if item and item.get("kling_element_id"):
        logger.info("Kling element cache HIT: item=%s, element_id=%d",
                     item["id"], item["kling_element_id"])
        return int(item["kling_element_id"])

    # Create new element — may fail if only the frontal image was provided
    try:
        element_id = await create_element(
            frontal_image_url=frontal_image_url,
            reference_image_urls=reference_image_urls,
            name=name,
            description=description,
        )
    except RuntimeError as exc:
        if "refer" in str(exc).lower() or "frontal" in str(exc).lower():
            logger.warning(
                "Kling element skipped (insufficient images): %s — falling back to start_image only",
                exc,
            )
            return None
        raise

    # Cache it
    if item:
        await set_kling_element_id(item["id"], element_id)
    else:
        logger.info("Kling element created (no library item to cache): element_id=%d", element_id)

    return element_id


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
    custom_scene_count: Optional[int] = None,
    custom_total_duration: Optional[int] = None,
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    library_style_url: Optional[str] = None,
    background_extra_urls: Optional[list] = None,
    watermark_path: Optional[str] = None,
    generation_mode: str = "classic",
    reference_video_url: Optional[str] = None,
    start_frame_url: Optional[str] = None,
    elements_json: Optional[str] = None,  # JSON array of {front_url, extra_urls, name}
    provider: str = "fal",  # "fal" = fal.ai proxy | "kling" = Kling Direct API
):
    """Execute the full pipeline asynchronously."""

    import re as _re_elem

    async def _gen_video(**kwargs) -> str:
        """Route to Kling Direct or fal.ai based on provider param."""
        if provider == "kling":
            from services.kling_service import (  # type: ignore[import]
                generate_multishot_video as _kling_gen,
            )

            # Pop fal.ai-style elements list and create real Kling elements (with cache)
            fal_elements = kwargs.pop("elements", []) or []
            element_list = []
            if fal_elements:
                logger.info("[%s] Creating %d Kling element(s)...", job_id, len(fal_elements))
                for i, e in enumerate(fal_elements):
                    if i >= 3:
                        break
                    eid = await get_or_create_kling_element(
                        front_url=e.get("original_front_url", e["frontal_image_url"]),
                        frontal_image_url=e["frontal_image_url"],
                        reference_image_urls=e.get("reference_image_urls", []),
                        name=f"garment{i + 1}",
                        description=f"fashion garment {i + 1}",
                    )
                    if eid is not None:
                        element_list.append({"element_id": int(eid)})
                logger.info("[%s] Kling elements ready: %s", job_id, element_list)

            # Replace @ElementN → <<<element_N>>> (Kling native token format)
            if "multi_prompt" in kwargs:
                kwargs["multi_prompt"] = [
                    {**s, "prompt": _re_elem.sub(
                        r"@Element(\d+)",
                        lambda m: f"<<<element_{m.group(1)}>>>",
                        s["prompt"],
                    ).strip()}
                    for s in kwargs["multi_prompt"]
                ]

            kwargs["element_list"] = element_list if element_list else None
            return await _kling_gen(**kwargs)
        return await generate_multishot_video(**kwargs)

    try:
        # Clamp values
        duration = max(3, min(15, duration))
        scene_count = max(1, min(8, scene_count))

        if generation_mode == "custom":
            # ── CUSTOM MODE: skip analysis / NB Pro / background ─────────────
            logger.info("[%s] Custom mode: bypassing analysis, NB Pro, background", job_id)

            _update_job(job_id, progress=20, message="Görsel hazırlanıyor...")
            fal_front_url = await _to_fal_url(front_url)

            # Detect train from description text (no analysis in custom mode).
            # Train suppression now lives entirely in the negative prompt; no
            # positive "no train" note is injected into the shot text because
            # elements preserve garment silhouette directly.
            _desc_lower = (video_description or "").lower()
            _custom_has_train = any(w in _desc_lower for w in _TRAIN_WORDS)
            _custom_negative = _BASE_NEGATIVE if _custom_has_train else _BASE_NEGATIVE + _TRAIN_NEGATIVE

            # Build elements for garment consistency
            _update_job(job_id, progress=55, message="Görseller hazırlanıyor (elements)...")
            elem_front_c = await _to_fal_url_compressed(front_url)
            custom_element: dict = {"frontal_image_url": elem_front_c, "reference_image_urls": []}
            if side_url:
                custom_element["reference_image_urls"].append(await _to_fal_url_compressed(side_url))
            if back_url:
                custom_element["reference_image_urls"].append(await _to_fal_url_compressed(back_url))
            custom_elements = [custom_element]
            logger.info("[%s] Custom elements ready: frontal + %d refs", job_id, len(custom_element["reference_image_urls"]))

            if reference_video_url:
                # ── MOTION CONTROL PATH ────────────────────────────────────
                logger.info("[%s] Motion control path active", job_id)
                _update_job(job_id, progress=65, message="Referans video yükleniyor...")
                fal_ref_video = await _to_fal_url(reference_video_url)

                _update_job(job_id, progress=70, message="Video üretiliyor (hareket kontrolü)...")
                _motion_prompt = str(video_description or "")
                clip_url_custom = await generate_motion_control_video(
                    image_url=fal_front_url,
                    video_url=fal_ref_video,
                    prompt=_motion_prompt,
                    elements=custom_elements,
                    aspect_ratio=aspect_ratio,
                    generate_audio=generate_audio,
                    negative_prompt=_custom_negative,
                    character_orientation="video",
                )
            else:
                # ── STANDARD CUSTOM PATH ───────────────────────────────────
                # Check if user provided per-shot prompts → skip GPT
                _custom_user_shots = request.shots and all((s.description or "").strip() for s in request.shots)
                if _custom_user_shots:
                    _update_job(job_id, progress=45, message="Kullanıcı senaryosu kullanılıyor...")
                    multi_prompt_custom = [
                        {"duration": s.duration, "prompt": s.description.strip()}
                        for s in request.shots
                    ]
                    logger.info("[%s] Custom: using %d user-provided prompts (GPT skipped)", job_id, len(multi_prompt_custom))
                else:
                    _update_job(job_id, progress=45, message="Senaryo üretiliyor...")
                    fal_back_url = await _to_fal_url(back_url) if back_url else None
                    fal_side_url_prompt = await _to_fal_url(side_url) if side_url else None
                    multi_prompt_custom = await generate_custom_multishot_prompt(
                        video_description=video_description,
                        image_url=fal_front_url,
                        back_image_url=fal_back_url,
                        side_image_url=fal_side_url_prompt,
                        scene_count=custom_scene_count,
                        total_duration=custom_total_duration,
                    )
                    logger.info("[%s] Custom multishot: %d prompt(s)", job_id, len(multi_prompt_custom))

                # Resolve garment meta (library lookup on front_url) + fabric-specific negatives
                _custom_meta = await _resolve_garment_meta_from_url(front_url, analysis=None)
                _custom_fabric_neg = _get_fabric_negative(_custom_meta.get("fabric"))
                if _custom_fabric_neg:
                    _custom_negative = _custom_negative + ", " + _custom_fabric_neg
                logger.info("[%s] Custom garment meta: fabric=%r desc=%s",
                            job_id, _custom_meta.get("fabric"),
                            (_custom_meta.get("description") or "")[:60])

                # Wrap each shot with 7-layer quality anchors
                multi_prompt_custom = [
                    {
                        "duration": p["duration"],
                        "prompt": _apply_quality_layers(
                            core_prompt=str(p["prompt"]),  # type: ignore[index]
                            meta=_custom_meta,
                            max_len=512,
                        ),
                    }
                    for p in multi_prompt_custom
                ]

                _update_job(job_id, progress=65, message="Video üretiliyor (özel mod)...")
                fal_start_url = await _to_fal_url(fal_front_url)

                total_custom_dur = sum(int(p["duration"]) for p in multi_prompt_custom)
                clip_url_custom = await _gen_video(
                    start_image_url=fal_start_url,
                    multi_prompt=multi_prompt_custom,
                    duration=str(total_custom_dur),
                    aspect_ratio=aspect_ratio,
                    generate_audio=generate_audio,
                    elements=custom_elements,
                    negative_prompt=_custom_negative,
                )

            _update_job(job_id, progress=85, message="Video indiriliyor...")
            final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
            clip_path_c = await download_file(clip_url_custom, settings.TEMP_DIR, extension=".mp4")
            shutil.move(clip_path_c, final_path)
            logger.info("[%s] Custom video ready: %s", job_id, final_path)

        elif generation_mode == "ozel":
            # ── ÖZEL MODE: elements with @Element1, separate start frame ──
            logger.info("[%s] Ozel mode: elements-based @Element1 prompts", job_id)

            _update_job(job_id, progress=15, message="Görsel hazırlanıyor...")

            _desc_lower = (video_description or "").lower()
            _ozel_has_train = any(w in _desc_lower for w in _TRAIN_WORDS)
            _ozel_negative = _BASE_NEGATIVE if _ozel_has_train else _BASE_NEGATIVE + _TRAIN_NEGATIVE

            # Build elements: front = frontal, back+side = reference_image_urls
            fal_ozel_front = await _to_fal_url_compressed(front_url)
            ozel_back_url: str | None = None
            ozel_side_url: str | None = None
            if back_url:
                ozel_back_url = await _to_fal_url_compressed(back_url)
            if side_url:
                ozel_side_url = await _to_fal_url_compressed(side_url)

            ozel_ref_urls = [u for u in [ozel_back_url, ozel_side_url] if u]
            ozel_element = {
                "frontal_image_url": fal_ozel_front,
                "reference_image_urls": ozel_ref_urls if ozel_ref_urls else [fal_ozel_front],
            }

            # Start frame: use dedicated upload or fall back to front image
            fal_ozel_start = await _to_fal_url(start_frame_url if start_frame_url else front_url)

            # Check if user provided per-shot prompts → skip GPT
            _ozel_user_shots = request.shots and all((s.description or "").strip() for s in request.shots)
            if _ozel_user_shots:
                _update_job(job_id, progress=30, message="Kullanıcı senaryosu kullanılıyor...")
                ozel_shots = [
                    {"duration": s.duration, "prompt": f"@Element1 {s.description.strip()}"}
                    for s in request.shots
                ]
                logger.info("[%s] Ozel: using %d user-provided prompts (GPT skipped)", job_id, len(ozel_shots))
            else:
                _update_job(job_id, progress=30, message="Sahneler planlanıyor...")
                ozel_shots = await generate_ozel_multishot_prompt(
                    image_url=fal_ozel_front,
                    back_image_url=ozel_back_url,
                    side_image_url=ozel_side_url,
                    video_description=video_description,
                    scene_count=custom_scene_count,
                    total_duration=custom_total_duration,
                    start_frame_url=fal_ozel_start,
                )

            # Resolve garment meta (library lookup on front_url) + fabric negatives
            _ozel_meta = await _resolve_garment_meta_from_url(front_url, analysis=None)
            _ozel_fabric_neg = _get_fabric_negative(_ozel_meta.get("fabric"))
            if _ozel_fabric_neg:
                _ozel_negative = _ozel_negative + ", " + _ozel_fabric_neg
            logger.info("[%s] Ozel garment meta: fabric=%r desc=%s",
                        job_id, _ozel_meta.get("fabric"),
                        (_ozel_meta.get("description") or "")[:60])

            # Wrap each shot with 7-layer quality anchors
            ozel_shots = [
                {
                    "duration": s["duration"],
                    "prompt": _apply_quality_layers(
                        core_prompt=str(s["prompt"]),  # type: ignore[index]
                        meta=_ozel_meta,
                        max_len=512,
                    ),
                }
                for s in ozel_shots
            ]

            total_ozel_dur = sum(int(p["duration"]) for p in ozel_shots)

            _update_job(job_id, progress=55, message="Video üretiliyor (özel mod)...")
            clip_url_ozel = await _gen_video(
                start_image_url=fal_ozel_start,
                multi_prompt=ozel_shots,
                duration=str(total_ozel_dur),
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
                elements=[ozel_element],
                negative_prompt=_ozel_negative,
            )

            _update_job(job_id, progress=85, message="Video indiriliyor...")
            final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
            clip_path_o = await download_file(clip_url_ozel, settings.TEMP_DIR, extension=".mp4")
            shutil.move(clip_path_o, final_path)
            logger.info("[%s] Ozel video ready: %s", job_id, final_path)

        elif generation_mode == "studio":
            # ── STUDIO MODE: kullanıcı tanımlı çekimler + elements, NB Pro/GPT yok ──
            logger.info("[%s] Studio mode: user shots + @Element1 elements", job_id)

            _update_job(job_id, progress=15, message="Görsel hazırlanıyor...")
            _studio_negative = _BASE_NEGATIVE + _TRAIN_NEGATIVE

            # Elements: multi-element support via elements_json, fallback to single
            if elements_json:
                import json as _json
                _elem_defs = _json.loads(elements_json)
                kling_elements = []
                for _ed in _elem_defs[:4]:  # max 4 elements
                    _fal_front = await _to_fal_url_compressed(_ed["front_url"])
                    _fal_extras = []
                    for _eu in (_ed.get("extra_urls") or [])[:3]:  # type: ignore[index]
                        _fal_extras.append(await _to_fal_url_compressed(_eu))
                    kling_elements.append({
                        "frontal_image_url": _fal_front,
                        "reference_image_urls": _fal_extras if _fal_extras else [_fal_front],
                        "original_front_url": _ed["front_url"],
                    })
            else:
                fal_studio_front = await _to_fal_url_compressed(front_url)
                studio_back_url: str | None = None
                studio_side_url: str | None = None
                if back_url:
                    studio_back_url = await _to_fal_url_compressed(back_url)
                if side_url:
                    studio_side_url = await _to_fal_url_compressed(side_url)
                studio_ref_urls = [u for u in [studio_back_url, studio_side_url] if u]
                kling_elements = [{
                    "frontal_image_url": fal_studio_front,
                    "reference_image_urls": studio_ref_urls if studio_ref_urls else [fal_studio_front],
                    "original_front_url": front_url,
                }]
            fal_studio_front = kling_elements[0]["frontal_image_url"]
            # Token prefix for all active elements: "@Element1", "@Element1 @Element2", etc.
            _element_prefix = " ".join(f"@Element{i+1}" for i in range(len(kling_elements)))

            # Başlangıç karesi
            fal_studio_start = await _to_fal_url(start_frame_url if start_frame_url else front_url)

            # GPT ile sahne anchor'ı çıkar
            _update_job(job_id, progress=25, message="Sahne analiz ediliyor...")
            scene_anchor = await extract_scene_anchor(fal_studio_start)
            logger.info("[%s] Studio scene anchor: %s", job_id, scene_anchor)

            # GPT-4o ile elbise yırtmaç/kuyruk analizi — prompt kısıtı üretir
            _update_job(job_id, progress=35, message="Elbise detayları analiz ediliyor...")
            _studio_ref_urls = [
                e for e in kling_elements[0].get("reference_image_urls", [])
                if e != kling_elements[0].get("frontal_image_url")
            ]
            garment_constraint = await analyse_garment_slits(
                frontal_url=kling_elements[0]["frontal_image_url"],
                reference_urls=_studio_ref_urls if _studio_ref_urls else None,
            )
            logger.info("[%s] Garment constraint: %s", job_id, garment_constraint)

            # Kullanıcı çekimlerini @Element tokenlarına dönüştür (1..N elementi için)
            # GPT-4o-mini ile kullanıcının açıklamasını Kling-optimized İngilizce prompt'a çevir
            if request.shots:
                import re as _re
                # Check if ALL shots have user-provided descriptions → skip GPT translate
                _all_have_desc = all((s.description or "").strip() for s in request.shots)
                studio_shots = []
                for shot in request.shots:
                    desc = (shot.description or "").strip()
                    if desc:
                        if _all_have_desc:
                            # User provided prompts — use directly without GPT translation
                            desc_stripped = _re.sub(r'^(@[Ee]lement\d+\s*)+', '', desc).strip()
                            prompt = f"{_element_prefix} {desc_stripped}"
                            logger.info("[%s] Studio shot: using user prompt directly (GPT skipped)", job_id)
                        else:
                            # Mixed — translate via GPT
                            desc_stripped = _re.sub(r'^(@[Ee]lement\d+\s*)+', '', desc).strip()
                            translated = await translate_studio_shot_description(
                                user_description=desc_stripped,
                                scene_anchor=scene_anchor,
                            )
                            prompt = f"{_element_prefix} {translated}"
                    else:
                        prompt = f"{_element_prefix} In the {scene_anchor}, model walks elegantly with tiny concealed steps, sealed skirt moves as one piece, showcasing the garment"
                    studio_shots.append({"duration": shot.duration, "prompt": prompt[:480]})
            else:
                studio_shots = [
                    {"duration": 5, "prompt": f"{_element_prefix} In the {scene_anchor}, model walks slowly towards camera with tiny concealed steps, sealed skirt moves as one closed column, showcasing the garment details"},
                    {"duration": 5, "prompt": f"{_element_prefix} In the {scene_anchor}, model turns gracefully showing the full garment silhouette from a 3/4 angle, skirt fabric remains sealed and closed throughout"},
                ]

            # Resolve garment meta from library for element[0] (primary garment).
            # This pulls user-authored fabric + description so the quality layers
            # (anchor, physics, fabric-negative) can lock the correct material.
            _studio_primary_url = kling_elements[0].get("original_front_url") or front_url
            _studio_meta = await _resolve_garment_meta_from_url(_studio_primary_url, analysis=None)
            logger.info("[%s] Studio garment meta: fabric=%r name=%r desc=%s",
                        job_id, _studio_meta.get("fabric"), _studio_meta.get("name"),
                        (_studio_meta.get("description") or "")[:60])

            # Fabric-specific negative additions (e.g. silk → block "satin sheen")
            _studio_fabric_neg = _get_fabric_negative(_studio_meta.get("fabric"))
            if _studio_fabric_neg:
                _studio_negative = _studio_negative + ", " + _studio_fabric_neg

            # Wrap each shot with quality anchors (garment lock + physics + style bible).
            # Garment shape / slit geometry is carried by the Kling elements themselves,
            # so no positive-text hem/slit enforcement is injected here. An optional
            # user-provided garment_constraint is the only shape hint kept.
            _gc_short = (str(garment_constraint)[:80]).strip() if garment_constraint else ""
            _locked: list = []
            for _s in studio_shots:
                desc = str(_s["prompt"])
                # desc = "@Element1 [@Element2 ...] <shot description>"
                _after_elem = desc[len(_element_prefix):].strip()  # type: ignore[index]
                # Core: element tokens + (optional garment constraint) + shot description
                if _gc_short:
                    _core_raw = f"{_element_prefix} {_gc_short} {_after_elem}".strip()
                else:
                    _core_raw = f"{_element_prefix} {_after_elem}".strip()
                _enhanced = _apply_quality_layers(
                    core_prompt=_core_raw,
                    meta=_studio_meta,
                    max_len=512,
                )
                _locked.append({"duration": _s["duration"], "prompt": _enhanced})  # type: ignore[index]
            studio_shots = _locked

            total_studio_dur = sum(int(p["duration"]) for p in studio_shots)

            # Save shot prompts to job so frontend can display them
            _studio_scene_prompt = MultiScenePrompt(
                background_image_prompt="",
                total_duration=total_studio_dur,
                scene_count=len(studio_shots),
                scenes=[
                    SingleScenePrompt(
                        scene_number=i + 1,
                        duration=str(s["duration"]),
                        prompt=s["prompt"],
                    )
                    for i, s in enumerate(studio_shots)
                ],
            )
            # Build debug payload mirroring the actual API call body
            _neg_str: str = _studio_negative  # type: ignore[assignment]
            _neg_preview = (_neg_str[:300] + "…") if len(_neg_str) > 300 else _neg_str
            _debug_payload = {
                "start_image_url": fal_studio_start,
                "multi_prompt": [
                    {"prompt": s["prompt"], "duration": s["duration"]}
                    for s in studio_shots
                ],
                "shot_type": "customize",
                "duration": str(total_studio_dur),
                "aspect_ratio": aspect_ratio,
                "generate_audio": generate_audio,
                "negative_prompt": _neg_preview,
                "elements": kling_elements,
                "provider": provider,
            }
            _update_job(job_id, scene_prompt=_studio_scene_prompt,
                        debug_payload=_debug_payload,
                        progress=55, message="Video üretiliyor (stüdyo modu)...")
            logger.info("[%s] Studio shots to send:\n%s", job_id,
                        "\n".join(f"  [{i+1}] ({s['duration']}s) {s['prompt']}" for i, s in enumerate(studio_shots)))

            clip_url_studio = await _gen_video(
                start_image_url=fal_studio_start,
                multi_prompt=studio_shots,
                duration=str(total_studio_dur),
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
                elements=kling_elements,
                negative_prompt=_studio_negative,
            )

            _update_job(job_id, progress=85, message="Video indiriliyor...")
            final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
            clip_path_st = await download_file(clip_url_studio, settings.TEMP_DIR, extension=".mp4")
            shutil.move(clip_path_st, final_path)
            logger.info("[%s] Studio video ready: %s", job_id, final_path)

        else:
            # Check if user provided per-shot prompts for ALL shots → skip analysis + GPT
            _classic_user_prompts = request.shots and all((s.description or "").strip() for s in request.shots)

            if _classic_user_prompts:
                # ── USER PROMPTS: skip GPT analysis + scene generation ────
                logger.info("[%s] User provided all shot prompts — skipping GPT analysis & scene generation", job_id)
                _update_job(job_id, progress=20, message="Kullanıcı senaryosu kullanılıyor...")

                # Minimal defaults (no GPT analysis)
                analysis = None
                has_train = False
                kling_negative = _BASE_NEGATIVE + _TRAIN_NEGATIVE

                # Library lookup: user may have tagged fabric on the library item
                # even though GPT analysis is skipped. Apply fabric-specific negatives.
                _classic_lib_row = None
                try:
                    from services.library_service import get_item_by_url as _get_item_by_url
                    _classic_lib_row = await _get_item_by_url(front_url)
                except Exception:
                    pass
                if _classic_lib_row and _classic_lib_row.get("fabric"):
                    _fab_neg = _get_fabric_negative(str(_classic_lib_row["fabric"]))
                    if _fab_neg:
                        kling_negative = kling_negative + ", " + _fab_neg
                        logger.info("[%s] Fabric negative (user-prompts path): %s", job_id, _fab_neg[:60])

                # Build a synthetic scene_prompt from user shots
                from models import DefileShotConfig as _DSC  # noqa: F811
                scene_prompt = MultiScenePrompt(
                    background_image_prompt="fashion runway, dramatic lighting",
                    total_duration=sum(s.duration for s in request.shots),
                    scene_count=len(request.shots),
                    scenes=[
                        SingleScenePrompt(
                            scene_number=i + 1,
                            duration=str(s.duration),
                            prompt=s.description.strip(),
                        )
                        for i, s in enumerate(request.shots)
                    ],
                )
                _update_job(job_id, scene_prompt=scene_prompt, progress=30,
                            message=f"{len(request.shots)} sahne (kullanıcı senaryosu).")
            else:
                # ── Steps 1-3: analysis + prompts + background ─────────────────
                _update_job(job_id, status=JobStatus.ANALYZING, progress=5, message="Elbise analiz ediliyor...")
                logger.info("[%s] Step 1 – Analysing garment", job_id)

                analysis = await analyse_dress(front_path, back_path)
                _update_job(job_id, analysis=analysis, progress=15, message="Elbise analizi tamamlandı.")
                logger.info("[%s] Analysis result: %s", job_id, analysis.garment_type)

                has_train = _has_train(analysis)
                kling_negative = _BASE_NEGATIVE if has_train else _BASE_NEGATIVE + _TRAIN_NEGATIVE
                logger.info("[%s] Train detected: %s", job_id, has_train)

                # Library override: if the user uploaded this garment to library
                # and tagged fabric/description, those user-authored values beat the
                # GPT analysis (which can mis-read AI-generated garments).
                _classic_lib_row = None
                try:
                    from services.library_service import get_item_by_url as _get_item_by_url
                    _classic_lib_row = await _get_item_by_url(front_url)
                except Exception:
                    pass
                if _classic_lib_row:
                    if _classic_lib_row.get("fabric"):
                        analysis.fabric = str(_classic_lib_row["fabric"])
                        logger.info("[%s] Library fabric override: %r", job_id, analysis.fabric)
                    if _classic_lib_row.get("description"):
                        # Append user description to analysis.description_en so it
                        # flows through all downstream prompts (NB Pro, GPT, _build_enhanced_prompt).
                        _user_desc = str(_classic_lib_row["description"])
                        analysis.description_en = (analysis.description_en or "") + " " + _user_desc
                        logger.info("[%s] Library description added: %s", job_id, _user_desc[:80])

                # Fabric-specific negative prompt (blocks wrong-material drift)
                _classic_fab_neg = _get_fabric_negative(getattr(analysis, "fabric", ""))
                if _classic_fab_neg:
                    kling_negative = kling_negative + ", " + _classic_fab_neg
                    logger.info("[%s] Fabric negative applied: %s", job_id, _classic_fab_neg[:60])

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

            if reference_image_url:
                background_url = reference_image_url
                logger.info("[%s] Step 3 – Using uploaded reference image as background: %s", job_id, background_url[:100])
                _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=50, message="Yüklenen arka plan kullanılıyor...")
            else:
                _update_job(job_id, status=JobStatus.GENERATING_BACKGROUND, progress=35, message="Arka plan üretiliyor...")
                logger.info("[%s] Step 3 – Generating background image via Nano Banana Pro", job_id)
                bg_prompt = scene_prompt.background_image_prompt
                logger.info("[%s] Background prompt: %s", job_id, bg_prompt[:120])
                background_url = await generate_background(prompt=bg_prompt, aspect_ratio=aspect_ratio)
                logger.info("[%s] Background generated: %s", job_id, background_url[:100])
                _update_job(job_id, progress=50, message="Arka plan hazır. Video üretiliyor...")

            logger.info("[%s] Step 4 – Generating multishot video", job_id)

            if request.shots and len(request.shots) == len(scene_prompt.scenes):
                for scene, shot in zip(scene_prompt.scenes, request.shots):
                    scene.duration = str(shot.duration)

            bg_pool = [background_url] + (background_extra_urls or [])
            multi_bg = len(bg_pool) > 1
            logger.info("[%s] Background pool: %d image(s), mode=%s",
                        job_id, len(bg_pool), "cycle" if multi_bg else "chain")

            garment_ref_urls = [front_url] + ([side_url] if side_url else []) + ([back_url] if back_url else [])
            _update_job(job_id, progress=52, message="Görseller hazırlanıyor...")
            fal_garment_refs: list = []
            for gurl in garment_ref_urls:
                fal_garment_refs.append(await _to_fal_url(gurl))
            logger.info("[%s] Garment refs on fal.ai CDN: %d", job_id, len(fal_garment_refs))

            # Build Kling elements for garment consistency
            elem_front_c = await _to_fal_url_compressed(front_url)
            kling_element: dict = {"frontal_image_url": elem_front_c, "reference_image_urls": []}
            if side_url:
                kling_element["reference_image_urls"].append(await _to_fal_url_compressed(side_url))
            if back_url:
                kling_element["reference_image_urls"].append(await _to_fal_url_compressed(back_url))
            kling_elements = [kling_element]
            logger.info("[%s] Kling elements ready: frontal + %d refs", job_id, len(kling_element["reference_image_urls"]))

            fal_bg_pool: list = []
            for bg_url in bg_pool:
                fal_bg_pool.append(await _to_fal_url(bg_url))
            logger.info("[%s] Background pool on fal.ai CDN: %d", job_id, len(fal_bg_pool))

            if generation_mode == "multishot":
                # ── MULTISHOT MODE: single NB Pro compose → GPT prompts → single Kling call ──
                logger.info("[%s] Multishot mode: single NB Pro + GPT multishot prompts + Kling", job_id)

                _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                            progress=55, message="Sahne kompoze ediliyor (multishot)...")

                nb_pro_prompt = _build_nb_pro_compose_prompt(analysis=analysis)

                scene_frame_url = await generate_scene_frame(
                    image_urls=[fal_bg_pool[0]] + fal_garment_refs,
                    prompt=nb_pro_prompt,
                    aspect_ratio=aspect_ratio,
                )
                logger.info("[%s] Multishot scene frame: %s", job_id, scene_frame_url[:80])

                # Check if user provided per-shot prompts → skip GPT
                _ms_user_prompts = request.shots and all((s.description or "").strip() for s in request.shots)

                if _ms_user_prompts:
                    _update_job(job_id, progress=65, message="Kullanıcı senaryosu kullanılıyor (multishot)...")
                    multi_prompt = [
                        {"duration": s.duration, "prompt": s.description.strip()}
                        for s in request.shots
                    ]
                    logger.info("[%s] Multishot: using %d user-provided prompts (GPT skipped)", job_id, len(multi_prompt))
                else:
                    _update_job(job_id, progress=65, message="Senaryo üretiliyor (multishot)...")
                    if request.shots:
                        shot_configs_ms = request.shots
                    else:
                        from models import DefileShotConfig
                        shot_configs_ms = [DefileShotConfig(duration=int(s.duration)) for s in scene_prompt.scenes]

                    multi_prompt = await generate_defile_multishot_prompt(
                        scene_frame_url=scene_frame_url,
                        shot_configs=shot_configs_ms,
                        outfit_name="",
                        video_description=video_description,
                    )
                    logger.info("[%s] Multishot: %d prompt(s) generated", job_id, len(multi_prompt))

                _update_job(job_id, progress=72, message="Video üretiliyor (multishot)...")

                scene_frame_url = await _to_fal_url(scene_frame_url)

                # Enhance each shot prompt with 7-layer system
                multi_prompt = [
                    {
                        "duration": p["duration"],
                        "prompt": _build_enhanced_prompt(
                            base_prompt=str(p["prompt"]),
                            analysis=analysis,
                            max_len=512,
                        ),
                    }
                    for p in multi_prompt
                ]
                total_ms_duration = sum(int(p["duration"]) for p in multi_prompt)
                clip_url = await _gen_video(
                    start_image_url=scene_frame_url,
                    multi_prompt=multi_prompt,
                    duration=str(total_ms_duration),
                    aspect_ratio=aspect_ratio,
                    generate_audio=generate_audio,
                    elements=kling_elements,
                    negative_prompt=kling_negative,
                )

                _update_job(job_id, progress=85, message="Video indiriliyor...")
                final_path = os.path.join(settings.OUTPUT_DIR, f"{uuid.uuid4().hex}.mp4")
                clip_path_ms = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
                shutil.move(clip_path_ms, final_path)
                logger.info("[%s] Multishot video ready: %s", job_id, final_path)

            else:
                # ── CLASSIC MODE: per-shot NB Pro compose + Kling animate ──────
                all_scenes = scene_prompt.scenes
                n_shots = len(all_scenes)
                logger.info("[%s] %d scene(s) — NB Pro compose + Kling per shot (classic)", job_id, n_shots)

                clip_paths = []
                current_start_image = fal_bg_pool[0]

                for shot_idx, scene in enumerate(all_scenes):
                    base_progress = 55 + int((shot_idx / n_shots) * 28)

                    start_image = fal_bg_pool[shot_idx % len(fal_bg_pool)] if multi_bg else current_start_image
                    shot_duration = int(scene.duration)

                    logger.info("[%s] Shot %d/%d: %ds", job_id, shot_idx + 1, n_shots, shot_duration)

                    _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                                progress=base_progress,
                                message=f"Sahne {shot_idx + 1}/{n_shots} kompoze ediliyor...")

                    angle = (scene.camera_angle or "eye_level").replace("_", " ")
                    size = (scene.shot_size or "full_body").replace("_", " ")
                    nb_pro_classic = _build_nb_pro_compose_prompt(analysis=analysis)
                    nb_pro_classic += f" Camera angle: {angle}. Shot framing: {size}. Context: {scene.prompt}."

                    scene_frame_url = await generate_scene_frame(
                        image_urls=[start_image] + fal_garment_refs,
                        prompt=nb_pro_classic,
                        aspect_ratio=aspect_ratio,
                    )
                    logger.info("[%s] Shot %d scene frame: %s", job_id, shot_idx + 1, scene_frame_url[:80])

                    _update_job(job_id, progress=base_progress + int(14 / n_shots),
                                message=f"Sahne {shot_idx + 1}/{n_shots} animate ediliyor...")

                    scene_frame_url = await _to_fal_url(scene_frame_url)

                    locked_prompt = _build_enhanced_prompt(
                        base_prompt=str(scene.prompt),
                        analysis=analysis,
                        camera_move=scene.camera_movement or "",
                        max_len=512,
                    )
                    clip_url = await _gen_video(
                        start_image_url=scene_frame_url,
                        multi_prompt=[{"duration": scene.duration, "prompt": locked_prompt}],
                        duration=str(shot_duration),
                        aspect_ratio=aspect_ratio,
                        generate_audio=generate_audio,
                        elements=kling_elements,
                        negative_prompt=kling_negative,
                    )

                    clip_path = await download_file(clip_url, settings.TEMP_DIR, extension=".mp4")
                    clip_paths.append(clip_path)
                    logger.info("[%s] Shot %d downloaded: %s", job_id, shot_idx + 1, clip_path)

                    if not multi_bg and shot_idx < n_shots - 1:
                        logger.info("[%s] Extracting last frame for chaining...", job_id)
                        last_frame_path = extract_last_frame(clip_path, settings.TEMP_DIR)
                        current_start_image = await upload_to_fal(last_frame_path)
                        try:
                            os.remove(last_frame_path)
                        except Exception:
                            pass
                        logger.info("[%s] Chain: next shot starts from %s", job_id, current_start_image[:80])

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

        from services.telegram_service import notify_video_ready
        await notify_video_ready(result_url or "", job_id, mode=generation_mode)

    except Exception as exc:
        logger.exception("[%s] Pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=_tr_error(exc),
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
      1. NB Pro compose: background + outfit images → establishing scene frame
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

        # ── Step 2b: Separate scene elements from outfit elements ─────────
        # Scene/mekan elements are shared across all outfits as Kling elements
        scene_elements: list = []  # DefileOutfit items with category="scene"
        outfit_only: list = []     # Non-scene outfits
        for _o in request.outfits:
            if _o.category == "scene":
                scene_elements.append(_o)
            else:
                outfit_only.append(_o)

        # Build Kling element dicts for scene elements
        kling_scene_elements: list = []
        for _se in scene_elements:
            _se_front_c = await _to_fal_url_compressed(_se.front_url)
            _se_refs: list = []
            if _se.side_url:
                _se_refs.append(await _to_fal_url_compressed(_se.side_url))
            if _se.back_url:
                _se_refs.append(await _to_fal_url_compressed(_se.back_url))
            for _seu in (_se.extra_urls or []):
                if _seu and len(_se_refs) < 2:
                    _se_refs.append(await _to_fal_url_compressed(_seu))
            if not _se_refs:
                _se_refs = [_se_front_c]
            kling_scene_elements.append({"frontal_image_url": _se_front_c, "reference_image_urls": _se_refs})
        if kling_scene_elements:
            logger.info("[%s] Defile: %d scene elements prepared for Kling", job_id, len(kling_scene_elements))

        # If all items are scene elements, nothing to generate
        if not outfit_only:
            outfit_only = [scene_elements[0]]  # fallback: treat first scene as outfit
            scene_elements = scene_elements[1:]
            kling_scene_elements = kling_scene_elements[1:] if kling_scene_elements else []

        n_outfits = len(outfit_only)

        # Re-upload outfit images for the filtered list
        fal_outfits_filtered: list = []
        for _of in outfit_only:
            fal_front = await _to_fal_url(_of.front_url)
            fal_side = await _to_fal_url(_of.side_url) if _of.side_url else None
            fal_back = await _to_fal_url(_of.back_url) if _of.back_url else None
            fal_outfits_filtered.append((fal_front, fal_side, fal_back))

        # ── Step 3: Per-outfit: NB Pro compose → GPT prompts → Kling ────────
        clip_paths: list = []

        for outfit_idx, outfit in enumerate(outfit_only):
            outfit_name = outfit.name or f"Kıyafet {outfit_idx + 1}"
            fal_front, fal_side, fal_back = fal_outfits_filtered[outfit_idx]
            base_progress = 20 + int((outfit_idx / n_outfits) * 65)

            # Background for this outfit (cycle pool)
            bg_for_outfit = fal_bg_pool[outfit_idx % len(fal_bg_pool)]

            # Garment refs: ALL angles for NB Pro (front + side + back + extras)
            garment_refs = [fal_front]
            if fal_side: garment_refs.append(fal_side)
            if fal_back: garment_refs.append(fal_back)
            # Also include extra_urls from element library items
            for _extra_u in (outfit.extra_urls or []):
                if _extra_u and _extra_u not in garment_refs:
                    garment_refs.append(await _to_fal_url(_extra_u))

            # ── 3a: Scene frame — use provided start frame or NB Pro compose ──
            if request.start_frame_url:
                # User provided a start frame — skip NB Pro composition
                scene_frame_url = request.start_frame_url
                logger.info("[%s] Outfit %d/%d: using provided start frame (NB Pro skipped): %s",
                            job_id, outfit_idx + 1, n_outfits, scene_frame_url[:80])
                _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                            progress=base_progress,
                            message=f"{outfit_name} — başlangıç karesi kullanılıyor ({outfit_idx + 1}/{n_outfits})...")
            else:
                _update_job(job_id, status=JobStatus.GENERATING_VIDEO,
                            progress=base_progress,
                            message=f"{outfit_name} — sahne kompoze ediliyor ({outfit_idx + 1}/{n_outfits})...")

                nb_pro_prompt = _build_nb_pro_compose_prompt(analysis=None)

                scene_frame_url = await generate_scene_frame(
                    image_urls=[bg_for_outfit] + garment_refs,
                    prompt=nb_pro_prompt,
                    aspect_ratio=request.aspect_ratio,
                )
                logger.info("[%s] Outfit %d/%d scene frame: %s",
                            job_id, outfit_idx + 1, n_outfits, scene_frame_url[:80])

            # ── 3b: Prompts — use user-provided or generate via GPT-4o Vision
            _user_has_prompts = all(sc.prompt for sc in shot_configs)

            if _user_has_prompts:
                # User provided prompts for all shots — skip GPT entirely
                _update_job(job_id, progress=base_progress + int(20 / n_outfits),
                            message=f"{outfit_name} — kullanıcı senaryosu kullanılıyor ({outfit_idx + 1}/{n_outfits})...")
                multi_prompt = [
                    {"duration": sc.duration, "prompt": sc.prompt}
                    for sc in shot_configs
                ]
                logger.info("[%s] Outfit %d/%d: using %d user-provided prompts (GPT skipped)",
                            job_id, outfit_idx + 1, n_outfits, len(multi_prompt))
            else:
                _update_job(job_id, progress=base_progress + int(20 / n_outfits),
                            message=f"{outfit_name} — senaryo üretiliyor ({outfit_idx + 1}/{n_outfits})...")
                multi_prompt = await generate_defile_multishot_prompt(
                    scene_frame_url=scene_frame_url,
                    shot_configs=shot_configs,
                    outfit_name=outfit_name,
                    shot_arc_id=getattr(request, "shot_arc", None),
                )
                logger.info("[%s] Outfit %d/%d prompts: %d shots, %ds total",
                            job_id, outfit_idx + 1, n_outfits, len(multi_prompt), total_duration)

            # Resolve outfit garment meta from library (fabric + user description)
            _defile_meta = await _resolve_garment_meta_from_url(outfit.front_url, analysis=None)
            logger.info("[%s] Defile outfit %d meta: fabric=%r desc=%s",
                        job_id, outfit_idx + 1,
                        _defile_meta.get("fabric"),
                        (_defile_meta.get("description") or "")[:60])

            # Per-outfit dynamic negative prompt — base + fabric-specific additions
            _defile_outfit_neg = _DEFILE_NEGATIVE
            _defile_fabric_neg = _get_fabric_negative(_defile_meta.get("fabric"))
            if _defile_fabric_neg:
                _defile_outfit_neg = _defile_outfit_neg + ", " + _defile_fabric_neg

            # Wrap each GPT shot with quality layers (garment lock anchor +
            # fabric physics + style bible). Garment silhouette / slit geometry
            # is preserved by the Kling element references, so no positive-text
            # "sealed gown" enforcement is injected into the shot prompt.
            multi_prompt = [
                {
                    "duration": p["duration"],
                    "prompt": _apply_quality_layers(
                        core_prompt=str(p["prompt"]),  # type: ignore[index]
                        meta=_defile_meta,
                        max_len=512,
                    ),
                }
                for p in multi_prompt
            ]

            # ── 3b-extra: Build compressed Kling elements for garment consistency ──
            # CRITICAL: elements carry the dress geometry (silhouette, slits, hem)
            elem_front_c = await _to_fal_url_compressed(outfit.front_url)
            _elem_refs: list = []
            if outfit.side_url:
                _elem_refs.append(await _to_fal_url_compressed(outfit.side_url))
            if outfit.back_url:
                _elem_refs.append(await _to_fal_url_compressed(outfit.back_url))
            # Include extra_urls from element library (max 2 refs for Kling — fewer refs = less identity drift)
            for _eu in (outfit.extra_urls or []):
                if _eu and len(_elem_refs) < 2:
                    _elem_refs.append(await _to_fal_url_compressed(_eu))
            if not _elem_refs:
                _elem_refs = [elem_front_c]
            outfit_element: dict = {"frontal_image_url": elem_front_c, "reference_image_urls": _elem_refs}
            kling_outfit_elements = [outfit_element] + kling_scene_elements
            logger.info("[%s] Defile outfit %d elements: 1 outfit + %d scene = %d total",
                        job_id, outfit_idx + 1, len(kling_scene_elements), len(kling_outfit_elements))

            # ── 3c: Video generation — single multishot call per outfit ─────

            # Re-upload scene frame so video API can download it reliably
            scene_frame_url = await _to_fal_url(scene_frame_url)

            _defile_provider = getattr(request, "provider", "fal")

            # Build debug_payload for this outfit
            _defile_debug = {
                "outfit": outfit_name,
                "start_image_url": scene_frame_url,
                "multi_prompt": [{"prompt": p["prompt"], "duration": p["duration"]} for p in multi_prompt],
                "duration": str(total_duration),
                "aspect_ratio": request.aspect_ratio,
                "generate_audio": request.generate_audio,
                "elements": kling_outfit_elements,
                "provider": _defile_provider,
            }

            _update_job(job_id, progress=base_progress + int(35 / n_outfits),
                        debug_payload=_defile_debug,
                        message=f"{outfit_name} — video üretiliyor ({outfit_idx + 1}/{n_outfits})...")

            if _defile_provider == "kling":
                from services.kling_service import (  # type: ignore[import]
                    generate_multishot_video as _kling_gen,
                )
                # Get cached or create new Kling element for outfit
                logger.info("[%s] Defile outfit %d: getting/creating Kling element...", job_id, outfit_idx + 1)
                _kling_eid = await get_or_create_kling_element(
                    front_url=outfit.front_url,
                    frontal_image_url=outfit_element["frontal_image_url"],
                    reference_image_urls=outfit_element["reference_image_urls"],
                    name=f"defile{outfit_idx + 1}",
                    description=f"defile outfit {outfit_idx + 1}",
                )
                _kling_elem_list: list = []
                if _kling_eid is not None:
                    _kling_elem_list.append({"element_id": int(_kling_eid)})
                    logger.info("[%s] Defile outfit %d: Kling element_id=%d", job_id, outfit_idx + 1, _kling_eid)
                else:
                    logger.info("[%s] Defile outfit %d: no Kling element (single image) — using start frame only",
                                job_id, outfit_idx + 1)

                # Also create Kling elements for scene elements
                for _si, _se in enumerate(scene_elements):
                    _scene_eid = await get_or_create_kling_element(
                        front_url=_se.front_url,
                        frontal_image_url=kling_scene_elements[_si]["frontal_image_url"],
                        reference_image_urls=kling_scene_elements[_si]["reference_image_urls"],
                        name=f"scene{_si + 1}",
                        description=f"defile scene element {_si + 1}",
                    )
                    if _scene_eid is not None:
                        _kling_elem_list.append({"element_id": int(_scene_eid)})
                        logger.info("[%s] Defile scene %d: Kling element_id=%d", job_id, _si + 1, _scene_eid)
                    else:
                        logger.info("[%s] Defile scene %d: skipped (single image)", job_id, _si + 1)

                # Build token references only for elements that exist
                if _kling_elem_list:
                    _elem_tokens = " ".join(f"<<<element_{i + 1}>>>" for i in range(len(_kling_elem_list)))
                    _kling_prompts = [
                        {"duration": p["duration"], "prompt": f"{_elem_tokens} {p['prompt']}"}
                        for p in multi_prompt
                    ]
                else:
                    _kling_prompts = [
                        {"duration": p["duration"], "prompt": p["prompt"]}
                        for p in multi_prompt
                    ]

                clip_url = await _kling_gen(
                    start_image_url=scene_frame_url,
                    multi_prompt=_kling_prompts,
                    duration=str(total_duration),
                    aspect_ratio=request.aspect_ratio,
                    generate_audio=request.generate_audio,
                    element_list=_kling_elem_list,
                    negative_prompt=_defile_outfit_neg,
                )
            else:
                clip_url = await generate_multishot_video(
                    start_image_url=scene_frame_url,
                    multi_prompt=multi_prompt,
                    duration=str(total_duration),
                    aspect_ratio=request.aspect_ratio,
                    generate_audio=request.generate_audio,
                    elements=kling_outfit_elements,
                    negative_prompt=_defile_outfit_neg,
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

        from services.telegram_service import notify_video_ready  # type: ignore[import]
        await notify_video_ready(result_url or "", job_id, mode="defile", extra=f"{n_outfits} kıyafet")

    except Exception as exc:
        logger.exception("[%s] Defile pipeline failed", job_id)
        _update_job(
            job_id,
            status=JobStatus.FAILED,
            message=_tr_error(exc),
        )
