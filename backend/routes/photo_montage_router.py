"""Photo montage — concatenate up to 4 images side-by-side at full resolution.

Upload sırasına göre yan yana dizer. Boyut düşürmez — en uzun görselin
yüksekliği hedef alınır, daha kısa olanlar bu yüksekliğe orantılı olarak
yukarı ölçeklenir (Lanczos). Arka plan beyaz.

Ayrıca: birleşmiş montage URL'inden GPT-5.5 vision ile detaylı İngilizce
garment description üretir (Kling element kaydederken kullanılmak üzere).
"""

import base64
import io
import logging
import os
import uuid
from typing import List

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, Field

from config import settings
from dependencies import get_current_user
from limiter import limiter

router = APIRouter(prefix="/api/photo-montage", tags=["photo-montage"])
logger = logging.getLogger(__name__)

_oai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None
_GPT_MODEL = "gpt-5.5"

_MAX_IMAGES = 4
_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_MAX_BYTES_PER = 50 * 1024 * 1024  # 50 MB per file


@router.post("")
@limiter.limit("60/hour")
async def combine_photos(
    request: Request,
    files: List[UploadFile] = File(...),
    _user: dict = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="En az 1 fotoğraf gerekli.")
    if len(files) > _MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"En fazla {_MAX_IMAGES} fotoğraf yüklenebilir.")

    images: List[Image.Image] = []
    for f in files:
        ext = os.path.splitext(f.filename or "img.jpg")[1].lower()
        if ext not in _ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Desteklenmeyen dosya türü: {ext}")
        content = await f.read()
        if len(content) > _MAX_BYTES_PER:
            raise HTTPException(status_code=413, detail=f"{f.filename}: 50 MB sınırını aşıyor.")
        if not content:
            raise HTTPException(status_code=400, detail=f"{f.filename}: Boş dosya.")
        try:
            img = Image.open(io.BytesIO(content))
            img.load()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{f.filename}: Görsel açılamadı.") from e
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        images.append(img)

    target_h = max(img.height for img in images)

    resized: List[Image.Image] = []
    for img in images:
        if img.height == target_h:
            resized.append(img)
            continue
        ratio = target_h / img.height
        new_w = max(1, round(img.width * ratio))
        resized.append(img.resize((new_w, target_h), Image.LANCZOS))

    total_w = sum(img.width for img in resized)
    canvas = Image.new("RGB", (total_w, target_h), (255, 255, 255))
    x = 0
    for img in resized:
        if img.mode == "RGBA":
            canvas.paste(img, (x, 0), img)
        else:
            canvas.paste(img.convert("RGB"), (x, 0))
        x += img.width

    filename = f"montage_{uuid.uuid4().hex}.jpg"
    path = os.path.join(settings.UPLOAD_DIR, filename)
    canvas.save(path, "JPEG", quality=92, optimize=True)

    base = settings.BASE_URL.rstrip("/")
    url = f"{base}/uploads/{filename}"
    logger.info("Photo montage created: %dx%d (%d files)", total_w, target_h, len(images))
    return {
        "url": url,
        "filename": filename,
        "width": total_w,
        "height": target_h,
        "count": len(images),
    }


# ─── Garment Description (Kling element açıklaması üretir) ────────────────


class AnalyzeGarmentRequest(BaseModel):
    image_url: str = Field(min_length=5)
    n_views: int = Field(default=0, ge=0, le=8)  # opsiyonel — kaç açı birleştirildi


_GARMENT_ANALYZE_SYSTEM = """\
You are a fashion garment specialist writing PRODUCTION-READY English
descriptions of clothing for an AI video generator's element binding system
(Kling AI). The user gives you a side-by-side montage of the SAME garment
photographed from multiple angles (typical: front, side, back, optional
detail/closeup). Your job is to look at every angle and produce ONE rich,
unified description that captures EVERY visible detail.

CRITICAL RULES

1. SINGLE GARMENT, MULTIPLE ANGLES
   The montage shows ONE outfit from different sides. Do NOT describe each
   panel as a separate piece — synthesize a unified description that names
   each side-specific feature with its anatomical anchor (e.g., "back slit",
   "front V-neckline", "left-side hidden zipper", "right hip drape").

2. FRONT / BACK / SIDE DISCIPLINE
   - If a feature is visible only in the BACK panel, label it explicitly:
     "back slit reaching mid-thigh", "back keyhole", "back lace-up corseting",
     "low-cut back to lumbar", "covered back with high collar".
   - If only in the FRONT panel: "front plunging V-neck", "front hidden
     button placket", "front waist tie", "front side-slit on the right hip".
   - If only in a SIDE/PROFILE panel: "side-tracking dart from bust to waist",
     "deep underarm armhole visible in profile".
   - If in 3/4 or 360° views: note continuous features (full circle skirt,
     all-over embellishment).

3. DETAIL CHECKLIST — MENTION EVERY ITEM IF VISIBLE
   - Color (specific hue, NOT "dark" — use "deep navy", "champagne ivory",
     "matte black", "burgundy wine"). If multi-tone, name each tone and where.
   - Fabric/material (silk satin, chiffon, organza, crepe, velvet, mikado,
     tulle, lace, mesh, jersey, taffeta, leather, brocade, embroidered tulle).
   - Surface finish (matte, semi-gloss, high-shine, beaded, sequined,
     embroidered, jacquard, plissé/pleated, smocked, ruched).
   - Silhouette (A-line, mermaid, trumpet, sheath/column, ball gown, fit-and-
     flare, empire waist, drop-waist, peplum, bodycon, oversize).
   - Neckline (off-shoulder, halter, sweetheart, bateau/boat, V-neck,
     square, cowl, plunging, illusion, high-neck, asymmetric one-shoulder).
   - Sleeves (sleeveless, cap, short, three-quarter, long, bishop, balloon,
     puff, bell, fitted, dropped-shoulder, leg-of-mutton, slit sleeve).
   - Bodice (fitted, corseted, draped, ruched, structured, bustier).
   - Waist (natural, empire, drop, cinched belt, sash, peplum, basque).
   - Skirt (mini, midi, knee-length, tea-length, floor-length, cathedral
     train; full/circle, A-line, pencil, slim, mermaid flare; pleated,
     gathered, draped, layered, tiered).
   - Hemline detail (straight, asymmetric, high-low, scalloped, fringed,
     handkerchief, ruffled).
   - SLITS — be precise about location, depth, height: "back slit rising to
     mid-thigh", "front leg slit to upper thigh", "side slit at right hip
     extending to hipbone".
   - TRAIN/TAIL — if any: "sweep train (~30 cm)", "chapel train (~1 m)",
     "cathedral train (~2 m)", "watteau back panel falling from shoulders to
     floor". Specify which side it falls from.
   - Details/embellishments: appliqué, beading, sequins, crystals, embroidery,
     lace overlay, ruffle trim, feather trim, bow, button rows, draping, knots,
     cutouts (with location), illusion mesh panels, contrast piping.
   - Closures (visible zipper line, corset lacing, button row, hook-and-eye).
   - Lining/transparency (sheer overlay, opaque lining, mesh inset).

4. ANATOMICAL ANCHORING
   Every detail must be anchored to a body landmark — bust, waist, hip,
   thigh, knee, ankle, shoulder, neckline, scapula, lumbar, sacrum, hem.
   This is what gives Kling enough info to maintain consistency across
   rotating shots.

5. WRITE STYLE
   - ONE flowing English paragraph (4–8 sentences).
   - Lead with: subject + headline garment type + dominant color + silhouette.
     Example opener: "A floor-length black silk-satin mermaid gown with a
     fitted bodice and sweep train."
   - Then expand outward to neckline → bodice → waist → skirt → hem →
     embellishments → angle-specific reveals (slits, train, back details).
   - English only. No bullet points, no markdown, no headings.
   - Do NOT describe the model (skin, hair, pose, makeup) — only the garment.
   - Do NOT describe the background, the lighting setup, or the photo style.
   - Do NOT speculate about what is hidden — only describe what you see.

6. CONSISTENCY ANCHORS
   End with one terse sentence reinforcing the most identity-critical
   features that must remain stable across video shots — typically:
   color + silhouette + slit location(s) + train length + the single most
   distinctive embellishment. Format: "Identity anchors: <comma-separated
   list>." This sentence is what protects the element across rotating shots.

OUTPUT
Return ONLY the description (the paragraph + the identity anchors sentence).
No JSON, no preamble, no markdown.
"""


async def _fetch_image_data_uri(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        r = await http.get(url)
        r.raise_for_status()
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(r.content).decode('ascii')}"


@router.post("/analyze-garment")
@limiter.limit("60/hour")
async def analyze_garment(
    request: Request,
    body: AnalyzeGarmentRequest,
    _user: dict = Depends(get_current_user),
):
    """GPT-5.5 vision ile montage'dan elbisenin tüm detaylarını İngilizce
    olarak çıkarır. Sonuç metni Kling element kaydederken description alanına
    yapıştırılmak üzere üretilir — bu sayede element rotating shot'larda
    tutarlılığını korur (yırtmaç pozisyonu, kuyruk, embellishment vs.)."""
    if _oai is None:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY tanımlı değil.")

    try:
        image_uri = await _fetch_image_data_uri(body.image_url)
    except Exception as e:
        logger.warning("analyze-garment fetch failed (%s): %s", body.image_url, e)
        raise HTTPException(status_code=400, detail=f"Görsel indirilemedi: {e}") from e

    n_hint = (
        f"This montage contains {body.n_views} angles of the same garment side-by-side."
        if body.n_views and body.n_views > 1
        else "This montage shows the same garment from multiple angles, side-by-side."
    )

    try:
        resp = await _oai.chat.completions.create(
            model=_GPT_MODEL,
            messages=[
                {"role": "system", "content": _GARMENT_ANALYZE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            n_hint + " Read EVERY panel carefully and synthesize one "
                            "unified description following the rules. Anchor each "
                            "side-specific feature (slits, train, back cutouts, etc.) "
                            "to its anatomical location. Return only the description."
                        )},
                        {"type": "image_url", "image_url": {"url": image_uri, "detail": "high"}},
                    ],
                },
            ],
            max_completion_tokens=1500,
        )
    except Exception as e:
        logger.exception("analyze-garment GPT call failed")
        raise HTTPException(status_code=500, detail=f"Analiz başarısız: {e}") from e

    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="GPT boş yanıt döndürdü, tekrar deneyin.")

    return {
        "description": text,
        "model": _GPT_MODEL,
        "char_count": len(text),
    }
