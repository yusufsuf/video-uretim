"""Order service — save and lookup 6-digit order codes in Supabase."""

import asyncio
from functools import lru_cache
from typing import Optional

from supabase import create_client, Client

from config import settings


@lru_cache(maxsize=1)
def _db() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def save_order(code: str, shot_configs: list) -> dict:
    """Insert a short-code → shot_configs mapping into the orders table.

    Raises ValueError if the code is already used.
    """
    existing = await lookup_order(code)
    if existing:
        raise ValueError("duplicate_code")

    def _insert():
        return _db().table("orders").insert({
            "code": code.upper(),
            "shot_configs": shot_configs,
        }).execute()

    res = await asyncio.to_thread(_insert)  # type: ignore[arg-type]
    return (res.data or [{}])[0]


async def lookup_order(code: str) -> Optional[dict]:
    """Return the order row for the given 6-char code, or None if not found."""
    def _query():
        return _db().table("orders").select("*").eq("code", code.upper()).execute()

    res = await asyncio.to_thread(_query)  # type: ignore[arg-type]
    rows = res.data or []
    return rows[0] if rows else None
