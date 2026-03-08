"""Library service — per-user visual reference library stored in Supabase."""

import asyncio
import uuid
from functools import lru_cache
from typing import Optional

from fastapi import HTTPException
from supabase import create_client, Client

from config import settings

BUCKET = "library"


@lru_cache(maxsize=1)
def _db() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def get_items(user_id: str, category: Optional[str] = None) -> list:
    """Return all library items for a user, optionally filtered by category."""
    def _query():
        q = _db().table("library_items").select("*").eq("user_id", user_id).order("created_at", desc=True)
        if category:
            q = q.eq("category", category)
        return q.execute()

    res = await asyncio.to_thread(_query)
    return res.data or []


async def add_item(
    user_id: str,
    name: str,
    category: str,
    file_bytes: bytes,
    content_type: str,
    ext: str,
) -> dict:
    """Upload image to Supabase Storage and insert a record into library_items."""
    storage_path = f"{user_id}/{uuid.uuid4().hex}{ext}"

    def _upload():
        _db().storage.from_(BUCKET).upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": content_type},
        )

    def _public_url() -> str:
        return _db().storage.from_(BUCKET).get_public_url(storage_path)

    def _insert(image_url: str) -> dict:
        res = _db().table("library_items").insert({
            "user_id": user_id,
            "name": name,
            "category": category,
            "image_url": image_url,
            "storage_path": storage_path,
        }).execute()
        return res.data[0] if res.data else {}

    await asyncio.to_thread(_upload)
    image_url = await asyncio.to_thread(_public_url)
    item = await asyncio.to_thread(_insert, image_url)
    return item


async def delete_item(user_id: str, item_id: str) -> None:
    """Delete a library item (verifies ownership first)."""
    def _fetch():
        return _db().table("library_items").select("*").eq("id", item_id).eq("user_id", user_id).execute()

    res = await asyncio.to_thread(_fetch)
    if not res.data:
        raise HTTPException(status_code=404, detail="Öğe bulunamadı.")

    storage_path = res.data[0]["storage_path"]

    def _remove_storage():
        _db().storage.from_(BUCKET).remove([storage_path])

    def _delete_row():
        _db().table("library_items").delete().eq("id", item_id).execute()

    await asyncio.to_thread(_remove_storage)
    await asyncio.to_thread(_delete_row)
