"""WhatsApp integration routes.

Three endpoint groups:
1. n8n endpoints (X-WhatsApp-Api-Key header auth) — /api/whatsapp/*
2. Admin endpoints (require_admin dependency) — /admin/whatsapp-users/*
3. Telegram callback webhook — /api/whatsapp/telegram-callback
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from dependencies import require_admin
from services import telegram_service, whatsapp_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])


# ─── Auth dependency for n8n endpoints ─────────────────────────────

def require_wa_api_key(x_whatsapp_api_key: Optional[str] = Header(None)) -> None:
    if not settings.WHATSAPP_API_KEY:
        raise HTTPException(status_code=503, detail="WhatsApp API not configured.")
    if x_whatsapp_api_key != settings.WHATSAPP_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key.")


# ─── Request models ────────────────────────────────────────────────

class CheckUserReq(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class RegisterUserReq(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    name: Optional[str] = Field(default=None, max_length=80)


class CreateElementReq(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=40)
    image_urls: list[str] = Field(min_length=2, max_length=4)
    requester_phone: Optional[str] = Field(default=None, max_length=32)


class TrackGenReq(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


# ─── n8n endpoints ─────────────────────────────────────────────────

@router.post("/api/whatsapp/check-user", dependencies=[Depends(require_wa_api_key)])
async def check_user_endpoint(body: CheckUserReq):
    return await whatsapp_service.check_user(body.phone)


@router.post("/api/whatsapp/register-user", dependencies=[Depends(require_wa_api_key)])
async def register_user_endpoint(body: RegisterUserReq):
    """Pending kullanıcı ekle + Telegram'a inline butonlu bildirim yolla.

    Idempotent: aynı numara tekrar gelirse mevcut kayıt güncellenir, Telegram
    bildirimi sadece ilk pending girişte atılır.
    """
    existing = await whatsapp_service.check_user(body.phone)
    result = await whatsapp_service.register_user(body.phone, body.name)
    # Sadece daha önce kaydolmamış kullanıcı için Telegram'a bildirim
    if existing.get("status") == "unknown":
        await telegram_service.notify_new_whatsapp_user(phone=result["phone"])
    return result


@router.get("/api/whatsapp/elements/{code}", dependencies=[Depends(require_wa_api_key)])
async def get_element_endpoint(code: str):
    elem = await whatsapp_service.get_element_by_code(code)
    if not elem:
        raise HTTPException(status_code=404, detail="Element kodu bulunamadı.")
    if not elem.get("element_id"):
        raise HTTPException(status_code=409, detail="Element hâlâ hazırlanıyor.")
    return elem


@router.post("/api/whatsapp/elements", dependencies=[Depends(require_wa_api_key)])
async def create_element_endpoint(body: CreateElementReq):
    """Element oluşturma task'ı başlatır, async çalışır.

    Admin user_id gereklidir (library_items.user_id için). ADMIN_EMAIL'den alınır.
    """
    from services.auth_service import _admin_client
    db = _admin_client()
    # Admin profilini bul (library ownership için)
    def _q():
        return db.table("profiles").select("id").eq("role", "admin").limit(1).execute()
    import asyncio as _aio
    res = await _aio.to_thread(_q)  # type: ignore[arg-type]
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=500, detail="Admin profili bulunamadı.")
    admin_user_id = rows[0]["id"]

    # Aynı kod var mı?
    existing = await whatsapp_service.get_element_by_code(body.code)
    if existing:
        raise HTTPException(status_code=409, detail="Bu element kodu zaten kullanımda.")

    try:
        job_id = await whatsapp_service.create_element_async(
            code=body.code, name=body.name,
            image_urls=body.image_urls,
            admin_user_id=admin_user_id,
            requester_phone=body.requester_phone or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"job_id": job_id, "code": body.code, "status": "processing"}


@router.get("/api/whatsapp/elements/status/{job_id}", dependencies=[Depends(require_wa_api_key)])
async def element_status_endpoint(job_id: str):
    st = whatsapp_service.get_element_job_status(job_id)
    return st


@router.post("/api/whatsapp/track-generation", dependencies=[Depends(require_wa_api_key)])
async def track_generation_endpoint(body: TrackGenReq):
    new_count = await whatsapp_service.increment_daily(body.phone)
    return {"daily_count": new_count, "daily_limit": settings.DAILY_VIDEO_LIMIT}


# ─── Admin endpoints ───────────────────────────────────────────────

@router.get("/admin/whatsapp-users")
async def list_wa_users(admin: dict = Depends(require_admin)):
    return await whatsapp_service.get_all_users()


@router.post("/admin/whatsapp-users/{phone}/approve")
async def approve_wa_user(phone: str, admin: dict = Depends(require_admin)):
    try:
        row = await whatsapp_service.set_user_status(phone, "approved")
    except ValueError as e:
        if str(e) == "user_not_found":
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
        raise HTTPException(status_code=400, detail="Geçersiz istek.")
    # Kullanıcıya bildir
    await whatsapp_service.send_whatsapp_text(
        phone,
        "✅ Erişiminiz onaylandı! Artık mesaj gönderip video üretebilirsiniz.",
    )
    return row


@router.post("/admin/whatsapp-users/{phone}/block")
async def block_wa_user(phone: str, admin: dict = Depends(require_admin)):
    try:
        row = await whatsapp_service.set_user_status(phone, "blocked")
    except ValueError as e:
        if str(e) == "user_not_found":
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
        raise HTTPException(status_code=400, detail="Geçersiz istek.")
    return row


# ─── Telegram callback (inline button handler) ─────────────────────

@router.post("/api/whatsapp/telegram-callback")
async def telegram_callback(request: Request):
    """Telegram inline button webhook.

    Güvenlik: Telegram webhook URL'i rahatlıkla tahmin edilemez olacak
    şekilde secret path ile set edilmelidir. Ek katman olarak bot_token
    header doğrulaması yapılabilir (setWebhook'ta secret_token kullanılır).
    """
    update = await request.json()
    cb = update.get("callback_query")
    if not cb:
        return {"ok": True}

    data = cb.get("data", "")
    cb_id = cb.get("id", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")

    if not data or ":" not in data:
        await telegram_service.answer_callback_query(cb_id, "Bilinmeyen komut")
        return {"ok": True}

    action, phone = data.split(":", 1)
    if action == "wa_approve":
        try:
            await whatsapp_service.set_user_status(phone, "approved")
            await whatsapp_service.send_whatsapp_text(
                phone, "✅ Erişiminiz onaylandı! Artık video üretebilirsiniz.",
            )
            await telegram_service.answer_callback_query(cb_id, f"✅ {phone} onaylandı")
            if chat_id and msg_id:
                await telegram_service.edit_message_text(
                    chat_id, msg_id, f"✅ *Onaylandı*\n📱 `{phone}`",
                )
        except Exception as exc:
            logger.exception("wa_approve failed")
            await telegram_service.answer_callback_query(cb_id, f"Hata: {exc}")

    elif action == "wa_block":
        try:
            await whatsapp_service.set_user_status(phone, "blocked")
            await telegram_service.answer_callback_query(cb_id, f"🚫 {phone} engellendi")
            if chat_id and msg_id:
                await telegram_service.edit_message_text(
                    chat_id, msg_id, f"🚫 *Engellendi*\n📱 `{phone}`",
                )
        except Exception as exc:
            logger.exception("wa_block failed")
            await telegram_service.answer_callback_query(cb_id, f"Hata: {exc}")
    else:
        await telegram_service.answer_callback_query(cb_id, "Bilinmeyen işlem")

    return {"ok": True}
