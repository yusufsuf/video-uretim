"""Analysis Service – uses OpenAI GPT-4o Vision to analyse garment photos
and generate multi-scene prompts for the video pipeline."""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from config import settings
from models import DressAnalysisResult, MultiScenePrompt, GenerationRequest, PhotoType

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _encode_image(image_path: str) -> str:
    """Return a base64-encoded data-URI for a local image file."""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    suffix = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{data}"


# ─── Dress Analysis ────────────────────────────────────────────────
ANALYSIS_SYSTEM = """Sen uzman bir moda analisti ve AI prompt mühendisisin.
Sana gönderilen elbise fotoğraflarını (ön ve/veya arka) analiz edeceksin.
Yanıtını SADECE aşağıdaki JSON formatında ver, ekstra metin ekleme:

{
  "photo_type": "mannequin | ghost | flatlay",
  "garment_type": "...",
  "color": "...",
  "pattern": "...",
  "fabric": "...",
  "cut_style": "...",
  "length": "...",
  "details": "...",
  "season": "...",
  "mood": "..."
}

Eğer birden fazla fotoğraf varsa, bunları aynı kıyafetin ön ve arka görüntüsü olarak değerlendir ve tek bir birleşik analiz üret."""


async def analyse_dress(front_path: str, back_path: Optional[str] = None) -> DressAnalysisResult:
    """Analyse one or two garment images and return structured data."""

    image_contents = [
        {
            "type": "image_url",
            "image_url": {"url": _encode_image(front_path), "detail": "high"},
        }
    ]
    if back_path:
        image_contents.append(
            {
                "type": "image_url",
                "image_url": {"url": _encode_image(back_path), "detail": "high"},
            }
        )

    label = "Bu elbise fotoğraflarını analiz et. İlk fotoğraf ön görünüm"
    if back_path:
        label += ", ikinci fotoğraf arka görünüm."
    else:
        label += "."

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": label},
                    *image_contents,
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=800,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    data = json.loads(raw)
    return DressAnalysisResult(**data)


# ─── Multi-Scene Prompt Generation ─────────────────────────────────
MULTI_SCENE_SYSTEM = """Sen profesyonel bir AI video yönetmenisin.
Sana bir kıyafet analizi, mekan bilgisi, toplam süre ve varsa bir mekan referans fotoğrafı verilecek.
Bunlara göre bir moda videosu için ÇOKLU SAHNE planı oluşturacaksın.

ÖNEMLİ KURALLAR:
- Her sahne farklı bir kamera açısı ve manken hareketi içermeli
- Sahneler profesyonel bir moda videosu akışı oluşturmalı (giriş, detay, tam boy, kapanış gibi)
- Her sahne minimum 5, maksimum 10 saniye olmalı
- Toplam süre, kullanıcının istediği süreye yaklaşmalı
- Eğer mekan referans fotoğrafı varsa, o mekanı sahne betimlemelerinde kullan
- full_scene_prompt İngilizce olmalı, sinematik ve detaylı olmalı

Yanıtını SADECE aşağıdaki JSON formatında ver:

{
  "background_prompt": "genel mekan tanımı",
  "total_duration": toplam_süre_saniye,
  "scene_count": sahne_sayısı,
  "scenes": [
    {
      "scene_number": 1,
      "camera_prompt": "kamera açısı ve hareketi",
      "model_action_prompt": "manken hareketi",
      "lighting_prompt": "aydınlatma",
      "full_scene_prompt": "Bu sahne için tam İngilizce video prompt",
      "duration_seconds": 5
    }
  ]
}"""


async def generate_multi_scene_prompt(
    analysis: DressAnalysisResult,
    request: GenerationRequest,
    total_duration: int = 10,
    location_image_path: Optional[str] = None,
) -> MultiScenePrompt:
    """Create multi-scene prompts for the video generator."""
    import re

    location_str = request.custom_location if request.location == "custom" else request.location.value

    user_text = (
        f"Kıyafet analizi:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"Mekan: {location_str}\n"
        f"Toplam video süresi: {total_duration} saniye\n"
        f"Kamera stili: {request.camera_style or 'farklı açılardan çeşitlendir'}\n"
        f"Manken hareketi: {request.model_action or 'otomatik seç, çeşitli hareketler'}\n"
        f"Mood: {request.mood or analysis.mood}\n"
    )

    if location_image_path:
        user_text += "\nKullanıcı bir mekan referans fotoğrafı gönderdi. Bu mekanı videonun arka planı olarak kullan, sahne betimlemelerinde bu mekanın özelliklerini yansıt."

    # Build message content
    content_parts = [{"type": "text", "text": user_text}]

    if location_image_path:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": _encode_image(location_image_path), "detail": "high"},
        })

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": MULTI_SCENE_SYSTEM},
            {"role": "user", "content": content_parts},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=3000,
    )

    raw = response.choices[0].message.content
    if not raw:
        raise ValueError("GPT-4o returned empty response for multi-scene prompt")

    raw = raw.strip()
    logger.info("Multi-scene raw response (first 300 chars): %s", raw[:300])

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # Try to extract JSON object if there's surrounding text
    if not raw.startswith("{"):
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raise ValueError(f"Could not find JSON in GPT response: {raw[:200]}")

    data = json.loads(raw)
    return MultiScenePrompt(**data)

