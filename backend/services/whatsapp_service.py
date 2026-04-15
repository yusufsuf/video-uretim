"""WhatsApp service — kullanıcı kaydı, element yönetimi, Evolution API.

Tüm Supabase ops service_role key ile yapılır (RLS bypass).
Element oluşturma Kling API'ye delegate edilir (kling_service.create_element).
"""

import asyncio
import logging
import uuid
from datetime import date
from functools import lru_cache
from typing import Optional

import httpx
from supabase import create_client, Client

from config import settings
from services import kling_service

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _db() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# In-memory element creation jobs (async polling için)
# { job_id: {done: bool, element_id: int|None, error: str|None, code: str} }
element_jobs: dict[str, dict] = {}


# ─── Phone normalization ───────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """Sadece rakamlar + başa '+' — çok hafif normalize."""
    if not phone:
        return ""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        return "+" + "".join(c for c in phone[1:] if c.isdigit())
    return "".join(c for c in phone if c.isdigit())


# ─── User registry ─────────────────────────────────────────────────

async def check_user(phone: str) -> dict:
    """Kullanıcı durumunu döner. Günlük sayaç gerekirse resetlenir.

    Döner: {status: "unknown"|"pending"|"approved"|"blocked", name, daily_count, daily_limit}
    """
    phone = _normalize_phone(phone)
    if not phone:
        return {"status": "unknown", "daily_limit": settings.DAILY_VIDEO_LIMIT}

    def _q():
        return _db().table("whatsapp_users").select("*").eq("phone", phone).limit(1).execute()

    res = await asyncio.to_thread(_q)  # type: ignore[arg-type]
    rows = res.data or []
    if not rows:
        return {"status": "unknown", "daily_limit": settings.DAILY_VIDEO_LIMIT}

    row = rows[0]
    # Günlük reset
    today = date.today().isoformat()
    if row.get("daily_reset_date") != today and row.get("daily_video_count", 0) > 0:
        def _reset():
            _db().table("whatsapp_users").update({
                "daily_video_count": 0,
                "daily_reset_date": today,
            }).eq("phone", phone).execute()
        await asyncio.to_thread(_reset)  # type: ignore[arg-type]
        row["daily_video_count"] = 0

    return {
        "status": row["status"],
        "name": row.get("name"),
        "phone": phone,
        "daily_count": row.get("daily_video_count", 0),
        "daily_limit": settings.DAILY_VIDEO_LIMIT,
    }


async def register_user(phone: str, name: Optional[str] = None) -> dict:
    """Yeni kullanıcıyı pending olarak ekler (idempotent — varsa no-op)."""
    phone = _normalize_phone(phone)
    if not phone:
        raise ValueError("invalid_phone")

    def _upsert():
        return _db().table("whatsapp_users").upsert({
            "phone": phone,
            "name": name,
            "status": "pending",
        }, on_conflict="phone").execute()

    await asyncio.to_thread(_upsert)  # type: ignore[arg-type]
    logger.info("WhatsApp user registered: %s", phone)
    return {"phone": phone, "status": "pending"}


async def set_user_status(phone: str, status: str) -> dict:
    """Admin onay/engel işlemi. status ∈ {approved, blocked, pending}."""
    phone = _normalize_phone(phone)
    if status not in ("approved", "blocked", "pending"):
        raise ValueError("invalid_status")

    update = {"status": status}
    if status == "approved":
        from datetime import datetime, timezone
        update["approved_at"] = datetime.now(timezone.utc).isoformat()

    def _upd():
        return _db().table("whatsapp_users").update(update).eq("phone", phone).execute()

    res = await asyncio.to_thread(_upd)  # type: ignore[arg-type]
    rows = res.data or []
    if not rows:
        raise ValueError("user_not_found")
    return rows[0]


async def get_all_users() -> list:
    """Admin panel için tüm WA kullanıcılarını döner."""
    def _q():
        return _db().table("whatsapp_users").select("*").order("created_at", desc=True).execute()
    res = await asyncio.to_thread(_q)  # type: ignore[arg-type]
    return res.data or []


async def increment_daily(phone: str) -> int:
    """Günlük video sayacını +1 yapar, yeni sayıyı döner."""
    phone = _normalize_phone(phone)
    today = date.today().isoformat()

    def _q():
        return _db().table("whatsapp_users").select("daily_video_count, daily_reset_date") \
            .eq("phone", phone).limit(1).execute()
    res = await asyncio.to_thread(_q)  # type: ignore[arg-type]
    rows = res.data or []
    if not rows:
        return 0
    current = 0 if rows[0].get("daily_reset_date") != today else (rows[0].get("daily_video_count") or 0)
    new_count = current + 1

    def _upd():
        return _db().table("whatsapp_users").update({
            "daily_video_count": new_count,
            "daily_reset_date": today,
        }).eq("phone", phone).execute()
    await asyncio.to_thread(_upd)  # type: ignore[arg-type]
    return new_count


# ─── Element management ────────────────────────────────────────────

async def get_element_by_code(code: str) -> Optional[dict]:
    """library_items üzerinden koda göre element bulur.

    Döner: {element_id, name, front_url, extra_urls, library_id} veya None
    """
    if not code:
        return None
    code = code.strip().lower()

    def _q():
        return _db().table("library_items").select("*") \
            .eq("whatsapp_code", code).limit(1).execute()
    res = await asyncio.to_thread(_q)  # type: ignore[arg-type]
    rows = res.data or []
    if not rows:
        return None

    r = rows[0]
    return {
        "library_id": r["id"],
        "element_id": r.get("kling_element_id"),
        "name": r.get("name"),
        "front_url": r.get("image_url"),
        "extra_urls": r.get("extra_urls") or [],
    }


async def _save_element_record(
    code: str,
    name: str,
    front_url: str,
    extra_urls: list[str],
    element_id: int,
    admin_user_id: str,
) -> None:
    """Başarılı element oluşturma sonrası library_items'e kaydet."""
    row = {
        "user_id": admin_user_id,       # admin'in user_id'si (library ownership için)
        "name": name,
        "category": "element",
        "image_url": front_url,
        "storage_path": "",             # zaten yüklü URL — storage path yok
        "extra_urls": extra_urls,
        "extra_storage_paths": [],
        "kling_element_id": element_id,
        "whatsapp_code": code.strip().lower(),
    }

    def _insert():
        return _db().table("library_items").insert(row).execute()
    await asyncio.to_thread(_insert)  # type: ignore[arg-type]


async def _run_element_creation(
    job_id: str,
    code: str,
    name: str,
    image_urls: list[str],
    admin_user_id: str,
) -> None:
    """Arka planda Kling element oluştur + DB'ye kaydet."""
    from services import telegram_service
    try:
        # Mevcut kod kontrolü
        existing = await get_element_by_code(code)
        if existing:
            raise RuntimeError(f"Element kodu zaten kullanımda: {code}")

        frontal = image_urls[0]
        refs = image_urls[1:4]
        element_id = await kling_service.create_element(
            frontal_image_url=frontal,
            reference_image_urls=refs,
            name=name[:20] or code[:20],
            description=f"WhatsApp element {code}"[:100],
        )

        await _save_element_record(
            code=code, name=name, front_url=frontal,
            extra_urls=refs, element_id=element_id,
            admin_user_id=admin_user_id,
        )

        element_jobs[job_id] = {
            "done": True, "element_id": element_id,
            "error": None, "code": code,
        }
        logger.info("WA element created: %s → id=%d", code, element_id)
        await telegram_service.notify_element_ready(
            phone=element_jobs[job_id].get("phone", ""),
            code=code, success=True,
        )

    except Exception as exc:
        err = str(exc)[:300]
        element_jobs[job_id] = {
            "done": True, "element_id": None,
            "error": err, "code": code,
        }
        logger.exception("WA element creation failed: %s", exc)
        await telegram_service.notify_element_ready(
            phone=element_jobs.get(job_id, {}).get("phone", ""),
            code=code, success=False, error=err,
        )


async def create_element_async(
    code: str,
    name: str,
    image_urls: list[str],
    admin_user_id: str,
    requester_phone: str = "",
) -> str:
    """Element oluşturma task'ını başlatır, job_id döner."""
    if not code or not name or len(image_urls) < 2:
        raise ValueError("code, name ve en az 2 görsel gereklidir (1 frontal + 1 refer)")
    if len(image_urls) > 4:
        image_urls = image_urls[:4]

    job_id = uuid.uuid4().hex
    element_jobs[job_id] = {
        "done": False, "element_id": None,
        "error": None, "code": code, "phone": requester_phone,
    }
    asyncio.create_task(
        _run_element_creation(job_id, code, name, image_urls, admin_user_id)
    )
    return job_id


def get_element_job_status(job_id: str) -> dict:
    """Element job durumu. done=False ise hâlâ çalışıyor."""
    return element_jobs.get(job_id, {"done": False, "error": "unknown_job"})


# ─── Evolution API (WhatsApp mesaj gönderimi) ──────────────────────

async def send_whatsapp_text(phone: str, text: str) -> bool:
    """Evolution API üzerinden WhatsApp'a düz metin mesaj gönderir."""
    if not settings.EVOLUTION_API_URL or not settings.EVOLUTION_API_KEY or not settings.EVOLUTION_INSTANCE:
        logger.debug("Evolution API not configured, skipping send to %s", phone)
        return False

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{settings.EVOLUTION_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    body = {"number": _normalize_phone(phone).lstrip("+"), "text": text}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("Evolution sendText HTTP %s: %s", resp.status_code, resp.text[:200])
                return False
        return True
    except Exception as exc:
        logger.warning("Evolution sendText failed: %s", exc)
        return False


async def send_whatsapp_video(phone: str, video_url: str, caption: str = "") -> bool:
    """Evolution API üzerinden WA'ya video gönderir."""
    if not settings.EVOLUTION_API_URL or not settings.EVOLUTION_API_KEY or not settings.EVOLUTION_INSTANCE:
        return False

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendMedia/{settings.EVOLUTION_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    body = {
        "number": _normalize_phone(phone).lstrip("+"),
        "mediatype": "video",
        "media": video_url,
        "caption": caption,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("Evolution sendMedia HTTP %s: %s", resp.status_code, resp.text[:200])
                return False
        return True
    except Exception as exc:
        logger.warning("Evolution sendMedia failed: %s", exc)
        return False
