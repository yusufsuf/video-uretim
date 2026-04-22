"""Photo montage — concatenate up to 4 images side-by-side at full resolution.

Upload sırasına göre yan yana dizer. Boyut düşürmez — en uzun görselin
yüksekliği hedef alınır, daha kısa olanlar bu yüksekliğe orantılı olarak
yukarı ölçeklenir (Lanczos). Arka plan beyaz.
"""

import io
import logging
import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from PIL import Image

from config import settings
from dependencies import get_current_user
from limiter import limiter

router = APIRouter(prefix="/api/photo-montage", tags=["photo-montage"])
logger = logging.getLogger(__name__)

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
