"""Telegram notification service — video tamamlandığında bildirim gönderir."""

import json
import logging
import httpx

from config import settings

logger = logging.getLogger(__name__)

_TG_BASE = "https://api.telegram.org/bot{token}/{method}"


async def notify_video_ready(
    result_url: str,
    job_id: str,
    mode: str = "",
    extra: str = "",
) -> None:
    """Video üretimi tamamlandığında Telegram'a bildirim gönderir."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return

    token = settings.TELEGRAM_BOT_TOKEN
    chat_ids = [cid.strip() for cid in settings.TELEGRAM_CHAT_ID.split(",") if cid.strip()]

    mode_label = f" · {mode}" if mode else ""
    extra_label = f"\n{extra}" if extra else ""
    caption = f"✅ Video hazır{mode_label}{extra_label}\n🎬 Job: `{job_id[:8]}`"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for chat_id in chat_ids:
                # Try sendVideo first (shows inline preview)
                if result_url.startswith("http"):
                    resp = await client.post(
                        _TG_BASE.format(token=token, method="sendVideo"),
                        data={
                            "chat_id": chat_id,
                            "video": result_url,
                            "caption": caption,
                            "parse_mode": "Markdown",
                            "supports_streaming": "true",
                        },
                    )
                    if resp.status_code == 200 and resp.json().get("ok"):
                        logger.info("[%s] Telegram video notification sent to %s", job_id, chat_id)
                        continue
                    logger.debug("[%s] sendVideo failed for %s (%s), falling back to sendMessage", job_id, chat_id, resp.text[:120])

                # Fallback: plain message with link
                await client.post(
                    _TG_BASE.format(token=token, method="sendMessage"),
                    data={
                        "chat_id": chat_id,
                        "text": f"{caption}\n[Videoyu aç]({result_url})",
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": "false",
                    },
                )
                logger.info("[%s] Telegram message notification sent to %s", job_id, chat_id)

    except Exception as exc:
        logger.warning("[%s] Telegram notification failed: %s", job_id, exc)


async def notify_new_whatsapp_user(phone: str) -> None:
    """New WA user pending onayı için admine inline butonlu mesaj yollar."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return

    token = settings.TELEGRAM_BOT_TOKEN
    chat_ids = [cid.strip() for cid in settings.TELEGRAM_CHAT_ID.split(",") if cid.strip()]

    text = (
        f"🔔 *Yeni WhatsApp kullanıcısı onay bekliyor*\n\n"
        f"📱 Numara: `{phone}`\n\n"
        f"Panel: {settings.BASE_URL}/admin-panel"
    )
    reply_markup = json.dumps({
        "inline_keyboard": [[
            {"text": "✅ Onayla", "callback_data": f"wa_approve:{phone}"},
            {"text": "🚫 Engelle", "callback_data": f"wa_block:{phone}"},
        ]]
    })

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for chat_id in chat_ids:
                await client.post(
                    _TG_BASE.format(token=token, method="sendMessage"),
                    data={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "reply_markup": reply_markup,
                    },
                )
        logger.info("Telegram new-WA-user notification sent for %s", phone)
    except Exception as exc:
        logger.warning("Telegram WA notify failed for %s: %s", phone, exc)


async def notify_element_ready(phone: str, code: str, success: bool = True, error: str = "") -> None:
    """WhatsApp element oluşturma tamamlanınca admine bildirim yollar."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return

    token = settings.TELEGRAM_BOT_TOKEN
    chat_ids = [cid.strip() for cid in settings.TELEGRAM_CHAT_ID.split(",") if cid.strip()]

    if success:
        text = f"✅ Element hazır: `{code}`\n📱 {phone}"
    else:
        text = f"❌ Element oluşturma başarısız: `{code}`\n📱 {phone}\n{error[:200]}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for chat_id in chat_ids:
                await client.post(
                    _TG_BASE.format(token=token, method="sendMessage"),
                    data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                )
    except Exception as exc:
        logger.warning("Telegram element notify failed: %s", exc)


async def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Telegram inline buton geri bildirimi (toast benzeri kısa mesaj)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                _TG_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method="answerCallbackQuery"),
                data={"callback_query_id": callback_query_id, "text": text[:200]},
            )
    except Exception as exc:
        logger.warning("Telegram answerCallbackQuery failed: %s", exc)


async def edit_message_text(chat_id: str | int, message_id: int, new_text: str) -> None:
    """Onay/engel işleminden sonra inline butonlu mesajı güncelle."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                _TG_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method="editMessageText"),
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": new_text,
                    "parse_mode": "Markdown",
                },
            )
    except Exception as exc:
        logger.warning("Telegram editMessageText failed: %s", exc)
