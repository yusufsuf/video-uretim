"""Image quality pre-check for Kling element creation.

Amaç: düşük çözünürlüklü veya bulanık görsellerle element yaratmayı engellemek —
bu Kling Omni'de kimlik rekonstrüksiyonunu zayıflatır ve tutarsızlık üretir.

Kontroller:
  1. Min. çözünürlük — her iki boyut ≥ 1024px (Kling önerisi: ≥300px, biz sertleştiriyoruz)
  2. Aspect ratio — çok garip (20:1 gibi) olmasın
  3. Dosya formatı — jpg/png/webp
  4. Bulanıklık — edge yoğunluğuyla kabaca tahmin (PIL-only, numpy gerektirmez)
"""

import io
import logging
from typing import Optional

import httpx
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

MIN_DIMENSION_PX = 1024     # her iki boyut en az 1024px olmalı
MIN_ASPECT_RATIO = 0.4      # en dar tolerans (2:5)
MAX_ASPECT_RATIO = 2.5      # en geniş tolerans (5:2)
MAX_FILE_BYTES = 10 * 1024 * 1024  # Kling max 10MB
BLUR_EDGE_THRESHOLD = 8.0   # average edge intensity — altı büyük ihtimalle bulanık


async def _download(url: str, timeout: int = 15) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def _check_blur(img: Image.Image) -> tuple[bool, float]:
    """Edge yoğunluğuyla bulanıklık tahmini. Döner (is_blurry, score)."""
    # Küçült → işlem hızlı
    small = img.copy()
    small.thumbnail((512, 512))
    gray = small.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    # Ortalama edge intensity — yüksek değer = keskin detay
    hist = edges.histogram()
    total_pixels = sum(hist)
    if total_pixels == 0:
        return False, 0.0
    weighted_sum = sum(i * h for i, h in enumerate(hist))
    avg = weighted_sum / total_pixels
    return avg < BLUR_EDGE_THRESHOLD, avg


async def validate_image_url(url: str) -> dict:
    """Tek görsel doğrulaması. Döner:
    {
        "ok": bool,                    # kritik hata yoksa True
        "error": str|None,             # bloke eden hata mesajı
        "warnings": list[str],         # bloke etmeyen uyarılar (bulanıklık vs.)
        "width": int, "height": int, "bytes": int, "blur_score": float
    }
    """
    result: dict = {
        "ok": False, "error": None, "warnings": [],
        "width": 0, "height": 0, "bytes": 0, "blur_score": 0.0,
    }
    try:
        data = await _download(url)
    except Exception as exc:
        result["error"] = f"Görsel indirilemedi: {exc}"
        return result

    result["bytes"] = len(data)
    if len(data) > MAX_FILE_BYTES:
        result["error"] = f"Dosya boyutu çok büyük ({len(data) / 1024 / 1024:.1f}MB, max 10MB)"
        return result

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        result["error"] = f"Geçerli bir görsel değil: {exc}"
        return result

    w, h = img.size
    result["width"], result["height"] = w, h

    if img.format not in ("JPEG", "PNG", "WEBP"):
        result["error"] = f"Desteklenmeyen format: {img.format} (sadece JPG/PNG/WebP)"
        return result

    if w < MIN_DIMENSION_PX or h < MIN_DIMENSION_PX:
        result["error"] = (
            f"Çözünürlük düşük ({w}×{h}px). Kimlik tutarlılığı için "
            f"her iki boyut da en az {MIN_DIMENSION_PX}px olmalı."
        )
        return result

    ratio = w / h if h > 0 else 0
    if ratio < MIN_ASPECT_RATIO or ratio > MAX_ASPECT_RATIO:
        result["error"] = (
            f"Görsel çok orantısız ({w}×{h}, oran {ratio:.2f}). "
            f"Normal portre/kare bir fotoğraf yükleyin."
        )
        return result

    try:
        is_blurry, score = _check_blur(img)
        result["blur_score"] = score
        if is_blurry:
            result["warnings"].append(
                f"Görsel bulanık olabilir (edge score {score:.1f} < {BLUR_EDGE_THRESHOLD}). "
                f"Keskin bir fotoğraf kullanın."
            )
    except Exception as exc:
        logger.debug("Blur check failed for %s: %s", url[:80], exc)

    result["ok"] = True
    return result


async def validate_element_images(image_urls: list[str]) -> dict:
    """Tüm element görsellerini kontrol et. Döner:
    {
        "ok": bool,                     # tüm kritik kontroller geçtiyse True
        "errors": list[str],            # bloke eden hatalar
        "warnings": list[str],          # uyarılar (bulanıklık vs.)
        "details": list[dict],          # her URL için detay
    }
    """
    details = []
    errors = []
    warnings = []
    for i, url in enumerate(image_urls):
        res = await validate_image_url(url)
        details.append(res)
        if res.get("error"):
            errors.append(f"Görsel {i + 1}: {res['error']}")
        for w in res.get("warnings", []):
            warnings.append(f"Görsel {i + 1}: {w}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "details": details,
    }
