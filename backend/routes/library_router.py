"""Library routes — CRUD for per-user visual reference items."""

import os

from fastapi import APIRouter, Depends, File, Form, UploadFile

from dependencies import get_current_user
from services.library_service import add_item, delete_item, get_items

router = APIRouter(tags=["library"])

ALLOWED_CATEGORIES = {"character", "background", "style"}
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/items")
async def list_items(category: str = None, user: dict = Depends(get_current_user)):
    return await get_items(user["id"], category=category or None)


@router.post("/items")
async def upload_item(
    name: str = Form(...),
    category: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if category not in ALLOWED_CATEGORIES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Geçersiz kategori.")

    ext = os.path.splitext(file.filename or "img.jpg")[1].lower()
    if ext not in ALLOWED_EXTS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Sadece JPG, PNG veya WebP yükleyebilirsiniz.")

    file_bytes = await file.read()
    return await add_item(
        user_id=user["id"],
        name=name,
        category=category,
        file_bytes=file_bytes,
        content_type=file.content_type or "image/jpeg",
        ext=ext,
    )


@router.delete("/items/{item_id}")
async def remove_item(item_id: str, user: dict = Depends(get_current_user)):
    await delete_item(user["id"], item_id)
    return {"message": "Öğe silindi."}
