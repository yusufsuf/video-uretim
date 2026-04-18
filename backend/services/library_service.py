"""Library service — per-user visual reference library stored in Supabase."""

import asyncio
import logging
import uuid
from functools import lru_cache
from typing import Optional

from fastapi import HTTPException
from supabase import create_client, Client

logger = logging.getLogger(__name__)

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

    res = await asyncio.to_thread(_query)  # type: ignore[arg-type]
    return res.data or []


async def add_item(
    user_id: str,
    name: str,
    category: str,
    primary_bytes: bytes,
    primary_content_type: str,
    primary_ext: str,
    extra_files: list = [],  # [(bytes, content_type, ext), ...]
    fabric: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Upload images to Supabase Storage and insert a record into library_items.

    extra_files: list of (bytes, content_type, ext) tuples for side/back/additional images.
    For character: extra_files[0]=side, extra_files[1]=back.
    For background: extra_files[0..2] = additional background images.

    fabric: optional fabric type (silk, satin, chiffon, denim, ...). Used to
      drive fabric physics layer in prompt builder and fabric-specific negatives.
    description: optional free-text garment description (e.g. "thin straps, V-neck,
      ivory tone, matte finish"). Injected into garment anchor layer at render time.
    """
    primary_path = f"{user_id}/{uuid.uuid4().hex}{primary_ext}"

    def _upload_primary():
        _db().storage.from_(BUCKET).upload(
            path=primary_path, file=primary_bytes,
            file_options={"content-type": primary_content_type},
        )

    def _get_url(path: str) -> str:
        return _db().storage.from_(BUCKET).get_public_url(path)  # type: ignore[return-value]

    await asyncio.to_thread(_upload_primary)  # type: ignore[arg-type]
    image_url: str = _get_url(primary_path)

    # Upload extra files
    extra_urls: list[str] = []
    extra_storage_paths: list[str] = []

    for fb, ct, ex in extra_files:
        epath = f"{user_id}/{uuid.uuid4().hex}{ex}"

        def _upload_extra(_path=epath, _bytes=fb, _ct=ct):  # capture loop vars
            _db().storage.from_(BUCKET).upload(
                path=_path, file=_bytes, file_options={"content-type": _ct},
            )

        await asyncio.to_thread(_upload_extra)  # type: ignore[arg-type]
        extra_urls.append(_get_url(epath))
        extra_storage_paths.append(epath)

    _row: dict = {
        "user_id": user_id,
        "name": name,
        "category": category,
        "image_url": image_url,
        "storage_path": primary_path,
        "extra_urls": extra_urls,
        "extra_storage_paths": extra_storage_paths,
    }
    # Only include fabric/description if provided — keeps existing schema compatible
    # if the columns haven't been migrated yet.
    if fabric:
        _row["fabric"] = fabric.strip()[:64]
    if description:
        _row["description"] = description.strip()[:500]

    def _insert():
        return _db().table("library_items").insert(_row).execute()

    try:
        res = await asyncio.to_thread(_insert)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("library_service add_item INSERT failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"DB insert failed: {exc}")
    if not res.data:
        logger.error("library_service add_item INSERT returned no data: %s", res)
        raise HTTPException(status_code=500, detail="DB insert returned no data")
    return res.data[0]


async def get_item_by_url(image_url: str) -> Optional[dict]:
    """Find a library item by its image_url. Returns the row dict or None."""
    def _query():
        return _db().table("library_items").select("*").eq("image_url", image_url).limit(1).execute()

    try:
        res = await asyncio.to_thread(_query)  # type: ignore[arg-type]
        return res.data[0] if res.data else None
    except Exception:
        return None


async def set_kling_element_id(item_id: str, element_id: int) -> None:
    """Cache a Kling element_id on a library item. Timestamps for TTL check."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    def _update():
        _db().table("library_items").update(
            {
                "kling_element_id": element_id,
                "kling_element_created_at": now_iso,
            }
        ).eq("id", item_id).execute()

    try:
        await asyncio.to_thread(_update)  # type: ignore[arg-type]
        logger.info("Cached kling_element_id=%d for item %s", element_id, item_id)
    except Exception as exc:
        # kling_element_created_at kolonu henüz migre edilmemişse fallback: sadece id yaz
        logger.warning("Failed to cache kling_element with timestamp (%s); retrying without timestamp", exc)
        def _update_legacy():
            _db().table("library_items").update(
                {"kling_element_id": element_id}
            ).eq("id", item_id).execute()
        try:
            await asyncio.to_thread(_update_legacy)  # type: ignore[arg-type]
            logger.info("Cached kling_element_id=%d for item %s (legacy, no timestamp)", element_id, item_id)
        except Exception as exc2:
            logger.warning("Failed to cache kling_element_id for %s: %s", item_id, exc2)


async def delete_item(user_id: str, item_id: str) -> None:
    """Delete a library item and all its storage files (verifies ownership first)."""
    def _fetch():
        return _db().table("library_items").select("*").eq("id", item_id).eq("user_id", user_id).execute()

    res = await asyncio.to_thread(_fetch)  # type: ignore[arg-type]
    if not res.data:
        raise HTTPException(status_code=404, detail="Öğe bulunamadı.")

    row = res.data[0]
    paths_to_remove = [row["storage_path"]]
    for p in (row.get("extra_storage_paths") or []):
        if p:
            paths_to_remove.append(p)

    def _remove_storage():
        _db().storage.from_(BUCKET).remove(paths_to_remove)

    def _delete_row():
        _db().table("library_items").delete().eq("id", item_id).execute()

    await asyncio.to_thread(_remove_storage)  # type: ignore[arg-type]
    await asyncio.to_thread(_delete_row)  # type: ignore[arg-type]
