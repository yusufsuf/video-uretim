"""Authentication service — Supabase Auth + profiles table."""

import asyncio
from functools import lru_cache

from fastapi import HTTPException
from supabase import create_client, Client

from config import settings


@lru_cache(maxsize=1)
def _client() -> Client:
    """Service-role client — bypasses RLS, used for all server-side ops."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# ─── Auth ──────────────────────────────────────────────────────────

async def register_user(email: str, password: str, full_name: str) -> dict:
    c = _client()
    try:
        res = await asyncio.to_thread(
            lambda: c.auth.sign_up({
                "email": email,
                "password": password,
                "options": {"data": {"full_name": full_name}},
            })
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Kayıt hatası: {e}")

    if res.user is None:
        raise HTTPException(status_code=400, detail="Kayıt başarısız.")

    # Admin e-postası → otomatik onayla
    if settings.ADMIN_EMAIL and email.lower() == settings.ADMIN_EMAIL.lower():
        uid = str(res.user.id)
        await asyncio.to_thread(
            lambda: c.table("profiles")
            .update({"approved": True, "role": "admin"})
            .eq("id", uid)
            .execute()
        )

    return {"message": "Kayıt başarılı. Lütfen admin onayını bekleyin."}


async def login_user(email: str, password: str) -> dict:
    c = _client()
    try:
        res = await asyncio.to_thread(
            lambda: c.auth.sign_in_with_password({"email": email, "password": password})
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Login hatası: {e}")

    uid = str(res.user.id)
    profile_res = await asyncio.to_thread(
        lambda: c.table("profiles").select("*").eq("id", uid).maybe_single().execute()
    )
    profile = profile_res.data
    if not profile:
        raise HTTPException(status_code=401, detail="Kullanıcı profili bulunamadı.")
    if not profile["approved"]:
        raise HTTPException(status_code=403, detail="Hesabınız henüz onaylanmadı.")

    return {
        "access_token": res.session.access_token,
        "user": {
            "id": uid,
            "email": profile["email"],
            "full_name": profile["full_name"],
            "role": profile["role"],
        },
    }


async def get_profile_by_token(token: str) -> dict:
    c = _client()
    try:
        res = await asyncio.to_thread(lambda: c.auth.get_user(token))
    except Exception:
        raise HTTPException(status_code=401, detail="Geçersiz veya süresi dolmuş token.")

    uid = str(res.user.id)
    profile_res = await asyncio.to_thread(
        lambda: c.table("profiles").select("*").eq("id", uid).maybe_single().execute()
    )
    profile = profile_res.data
    if not profile:
        raise HTTPException(status_code=401, detail="Kullanıcı profili bulunamadı.")
    if not profile["approved"]:
        raise HTTPException(status_code=403, detail="Hesabınız henüz onaylanmadı.")

    return profile


# ─── Admin ─────────────────────────────────────────────────────────

async def get_all_users() -> list:
    c = _client()
    res = await asyncio.to_thread(
        lambda: c.table("profiles").select("*").order("created_at", desc=True).execute()
    )
    return res.data or []


async def approve_user(user_id: str) -> dict:
    c = _client()
    await asyncio.to_thread(
        lambda: c.table("profiles").update({"approved": True}).eq("id", user_id).execute()
    )
    return {"message": "Kullanıcı onaylandı."}


async def reject_user(user_id: str) -> dict:
    c = _client()
    await asyncio.to_thread(
        lambda: c.auth.admin.delete_user(user_id)
    )
    return {"message": "Kullanıcı reddedildi ve silindi."}
