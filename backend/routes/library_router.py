"""Library routes — CRUD for per-user visual reference items."""

import asyncio
import os
import tempfile
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from dependencies import get_current_user
from services.library_service import add_item, delete_item, get_items
from services.nano_banana_service import generate_venue_variants
from services.video_service import upload_to_fal

router = APIRouter(tags=["library"])

ALLOWED_CATEGORIES = {
    # Legacy categories (existing data)
    "character", "background", "style",
    # New element sub-categories
    "element", "costume", "scene", "effect", "item", "other",
}
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/items")
async def list_items(category: str = None, user: dict = Depends(get_current_user)):
    return await get_items(user["id"], category=category or None)


@router.post("/items")
async def upload_item(
    name: str = Form(...),
    category: str = Form(...),
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
    file3: Optional[UploadFile] = File(None),
    file4: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    if category not in ALLOWED_CATEGORIES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Geçersiz kategori.")

    ext = os.path.splitext(file.filename or "img.jpg")[1].lower()
    if ext not in ALLOWED_EXTS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Sadece JPG, PNG veya WebP yükleyebilirsiniz.")

    primary_bytes = await file.read()

    # Collect extra files (side/back for character; additional views for background)
    extra_files = []
    for uf in [file2, file3, file4]:
        if uf and uf.filename:
            extra_ext = os.path.splitext(uf.filename)[1].lower()
            if extra_ext not in ALLOWED_EXTS:
                continue
            extra_files.append((await uf.read(), uf.content_type or "image/jpeg", extra_ext))

    return await add_item(
        user_id=user["id"],
        name=name,
        category=category,
        primary_bytes=primary_bytes,
        primary_content_type=file.content_type or "image/jpeg",
        primary_ext=ext,
        extra_files=extra_files,
    )


@router.delete("/items/{item_id}")
async def remove_item(item_id: str, user: dict = Depends(get_current_user)):
    await delete_item(user["id"], item_id)
    return {"message": "Öğe silindi."}


@router.post("/generate-venue-variants")
async def generate_venue_variants_endpoint(
    name: str = Form(...),
    count: int = Form(...),
    aspect_ratio: str = Form("9:16"),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Generate N angle variants of a venue photo via NB2 and save to library."""
    count = max(1, min(4, count))
    ext = os.path.splitext(file.filename or "venue.jpg")[1].lower() or ".jpg"
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="Sadece JPG, PNG veya WebP yükleyebilirsiniz.")

    file_bytes = await file.read()

    # Write to temp file, upload to fal.ai CDN, generate variants in parallel
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        fal_url = await upload_to_fal(tmp_path)
        variant_urls = await generate_venue_variants(fal_url, count, aspect_ratio)
    finally:
        os.unlink(tmp_path)

    # Download all variants and save to Supabase library
    async with httpx.AsyncClient(timeout=60) as client:
        responses = await asyncio.gather(*[client.get(u) for u in variant_urls])

    primary_bytes = responses[0].content
    extra_files = [(r.content, "image/png", ".png") for r in responses[1:]]

    return await add_item(
        user_id=user["id"],
        name=name,
        category="background",
        primary_bytes=primary_bytes,
        primary_content_type="image/png",
        primary_ext=".png",
        extra_files=extra_files,
    )
