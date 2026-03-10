"""Authentication service — Supabase Auth + profiles table."""

import asyncio
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

from fastapi import HTTPException
from supabase import create_client, Client

from config import settings


@lru_cache(maxsize=1)
def _admin_client() -> Client:
    """Service-role client for DB queries — never used for user sign_in/sign_up."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _fresh_auth_client() -> Client:
    """Fresh anon client for user auth ops — prevents session contamination."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


# ─── Auth ──────────────────────────────────────────────────────────

async def register_user(email: str, password: str, full_name: str) -> dict:
    db = _admin_client()
    logger.info("[register] attempt: %s", email)
    try:
        res = await asyncio.to_thread(
            lambda: db.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name},
            })
        )
        logger.info("[register] create_user success for %s", email)
    except Exception as e:
        logger.warning("[register] create_user exception: %s", e)
        err = str(e).lower()
        if "already registered" in err or "already exists" in err or "duplicate" in err:
            raise HTTPException(status_code=400, detail="Bu e-posta adresi zaten kayıtlı.")
        raise HTTPException(status_code=400, detail=f"Kayıt hatası: {e}")

    if res.user is None:
        logger.warning("[register] res.user is None for %s", email)
        raise HTTPException(status_code=400, detail="Kayıt başarısız.")

    uid = str(res.user.id)
    is_admin = bool(settings.ADMIN_EMAIL and email.lower() == settings.ADMIN_EMAIL.lower())

    # profiles tablosuna manuel insert (admin.create_user trigger'ı tetiklemeyebilir)
    profile_data = {
        "id": uid,
        "email": email,
        "full_name": full_name,
        "approved": is_admin,
        "role": "admin" if is_admin else "user",
    }

    def _upsert_profile(*_: object, **__: object) -> None:
        db.table("profiles").upsert(profile_data).execute()

    def _delete_user(*_: object, **__: object) -> None:
        db.auth.admin.delete_user(uid)

    try:
        await asyncio.to_thread(_upsert_profile)
    except Exception as e:
        # Auth kullanıcısı oluştu ama profil yazılamadı — geri al
        try:
            await asyncio.to_thread(_delete_user)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Profil oluşturulamadı: {e}")

    return {"message": "Kayıt başarılı. Lütfen admin onayını bekleyin."}


async def login_user(email: str, password: str) -> dict:
    auth = _fresh_auth_client()
    try:
        res = await asyncio.to_thread(
            lambda: auth.auth.sign_in_with_password({"email": email, "password": password})
        )
        if not res.user or not res.session:
            raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")

    uid = str(res.user.id)
    db = _admin_client()
    try:
        profile_res = await asyncio.to_thread(
            lambda: db.table("profiles").select("*").eq("id", uid).execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profil sorgu hatası: {e}")

    profiles = profile_res.data if profile_res else []
    if not profiles:
        raise HTTPException(status_code=401, detail="Kullanıcı profili bulunamadı.")
    profile = profiles[0]
    if not profile["approved"]:
        raise HTTPException(status_code=403, detail="Hesabınız henüz onaylanmadı.")

    return {
        "access_token": res.session.access_token,
        "refresh_token": res.session.refresh_token,
        "expires_at": res.session.expires_at,
        "user": {
            "id": uid,
            "email": profile["email"],
            "full_name": profile["full_name"],
            "role": profile["role"],
        },
    }


async def refresh_session(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token."""
    auth = _fresh_auth_client()
    try:
        res = await asyncio.to_thread(
            lambda: auth.auth.refresh_session(refresh_token)
        )
        if not res.session:
            raise HTTPException(status_code=401, detail="Token yenilenemedi.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Token yenilenemedi.")

    return {
        "access_token": res.session.access_token,
        "refresh_token": res.session.refresh_token,
        "expires_at": res.session.expires_at,
    }


async def get_profile_by_token(token: str) -> dict:
    db = _admin_client()
    try:
        res = await asyncio.to_thread(lambda: db.auth.get_user(token))
    except Exception:
        raise HTTPException(status_code=401, detail="Geçersiz veya süresi dolmuş token.")

    uid = str(res.user.id)
    profile_res = await asyncio.to_thread(
        lambda: db.table("profiles").select("*").eq("id", uid).execute()
    )
    profiles = profile_res.data if profile_res else []
    if not profiles:
        raise HTTPException(status_code=401, detail="Kullanıcı profili bulunamadı.")
    profile = profiles[0]
    if not profile["approved"]:
        raise HTTPException(status_code=403, detail="Hesabınız henüz onaylanmadı.")

    return profile


# ─── Admin ─────────────────────────────────────────────────────────

async def get_all_users() -> list:
    db = _admin_client()
    res = await asyncio.to_thread(
        lambda: db.table("profiles").select("*").order("created_at", desc=True).execute()
    )
    return res.data or []


async def approve_user(user_id: str) -> dict:
    db = _admin_client()
    await asyncio.to_thread(
        lambda: db.table("profiles").update({"approved": True}).eq("id", user_id).execute()
    )
    return {"message": "Kullanıcı onaylandı."}


async def reject_user(user_id: str) -> dict:
    db = _admin_client()
    await asyncio.to_thread(
        lambda: db.auth.admin.delete_user(user_id)
    )
    return {"message": "Kullanıcı reddedildi ve silindi."}
