"""Telegram notification service — video tamamlandığında bildirim gönderir."""

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
