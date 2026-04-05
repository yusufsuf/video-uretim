"""Pydantic models for request / response validation."""

from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class PhotoType(str, Enum):
    MANNEQUIN = "mannequin"       # Manken üzerinde elbise
    GHOST = "ghost"               # Ghost mannequin / hayalet manken
    FLATLAY = "flatlay"           # Düz yatırılmış ürün fotoğrafı


class LocationPreset(str, Enum):
    STUDIO = "studio"
    BEACH = "beach"
    CITY_STREET = "city_street"
    GARDEN = "garden"
    ROOFTOP = "rooftop"
    RUNWAY = "runway"
    CUSTOM = "custom"


class DressAnalysisResult(BaseModel):
    """GPT-4o Vision tarafından üretilen elbise analiz raporu."""
    photo_type: PhotoType = Field(description="Fotoğraf tipi (mannequin / ghost / flatlay)")
    garment_type: str = Field(description="Kıyafet türü (elbise, ceket, pantolon vb.)")
    color: str = Field(description="Ana renk / renkler")
    color_secondary: str = Field(default="none", description="İkincil renk")
    pattern: str = Field(description="Desen bilgisi (düz, çizgili, çiçekli vb.)")
    fabric: str = Field(description="Kumaş tipi tahmini (ipek, pamuk, polyester vb.)")
    neckline: str = Field(default="", description="Yaka şekli ve derinliği")
    sleeve_type: str = Field(default="", description="Kol tipi, uzunluk, manşet")
    cut_style: str = Field(description="Kesim stili (A-line, bodycon, oversize vb.)")
    length: str = Field(description="Uzunluk (mini, midi, maxi vb.)")
    details: str = Field(description="Ekstra detaylar (dantel, düğme, kemer vb.)")
    front_silhouette: str = Field(default="", description="Önden tam siluet açıklaması")
    back_details: str = Field(default="", description="Arkadan detaylı açıklama (kapama, dikişler)")
    back_silhouette: str = Field(default="", description="Arkadan siluet açıklaması")
    hem_description: str = Field(default="", description="Etek ucu tanımı (ön ve arkadan)")
    description_en: str = Field(default="", description="3-4 cümlelik İngilizce tam kıyafet açıklaması")
    season: str = Field(description="Uygun mevsim önerisi")
    mood: str = Field(description="Genel mood / atmosfer")


class SingleScenePrompt(BaseModel):
    """Tek bir video sahnesi (multishot shot) için prompt."""
    model_config = {"protected_namespaces": ()}

    scene_number: int = Field(description="Sahne numarası")
    scene_title: str = Field(default="", description="Kısa sahne başlığı")
    duration: str = Field(description="Bu shot'un süresi (string, ör: '3')")
    prompt: str = Field(description="Sinematik sahne prompt'u (kamera + hareket + detay)")
    camera_angle: str = Field(default="", description="Kullanılan kamera açısı")
    camera_movement: str = Field(default="", description="Kullanılan kamera hareketi")
    shot_size: str = Field(default="", description="Çekim boyu (Wide, Medium, Close-Up vb.)")


class MultiScenePrompt(BaseModel):
    """Çoklu sahne için üretilen prompt seti."""
    model_config = {"protected_namespaces": ()}

    background_image_prompt: str = Field(description="Nano Banana arka plan prompt'u (sadece mekan, insan yok)")
    total_duration: int = Field(description="Toplam video süresi")
    scene_count: int = Field(description="Sahne sayısı")
    scenes: List[SingleScenePrompt] = Field(description="Sahne listesi")
    garment_lock_description: str = Field(default="", description="Tüm sahnelerde tutarlı kıyafet tanımı")
    location_theme: str = Field(default="", description="Genel mekan teması")


class ShotConfig(BaseModel):
    """Per-shot configuration from user for multishot video."""
    camera_move: str = Field(default="dolly_in", description="Camera movement type (orbit, dolly_in, dolly_out, pan, tilt_up, tracking, crane, static)")
    duration: int = Field(default=5, description="Shot duration in seconds", ge=3, le=10)
    description: Optional[str] = Field(default=None, description="Additional description for this shot")
    camera_angle: str = Field(default="", description="Camera angle (eye_level, low_angle, high_angle, profile, rear, dutch) — empty = auto")
    shot_size: str = Field(default="", description="Shot size (wide, medium_wide, medium, close_up, extreme_close_up) — empty = auto")


class GenerationRequest(BaseModel):
    """Frontend'den gelen video üretim talebi."""
    model_config = {"protected_namespaces": ()}

    location: LocationPreset = LocationPreset.STUDIO
    custom_location: Optional[str] = None
    mood: Optional[str] = None
    generate_audio: bool = True
    shots: Optional[List["ShotConfig"]] = None


class LibraryItem(BaseModel):
    """Kullanıcının görsel kütüphanesindeki tek bir öğe."""
    id: str
    user_id: str
    name: str
    category: str  # character | background | style
    image_url: str
    storage_path: str
    kling_element_id: Optional[int] = None  # cached Kling element ID
    created_at: Optional[str] = None


class SuggestShotItem(BaseModel):
    camera_move: str
    duration: int


class SuggestShotsRequest(BaseModel):
    location: str = "studio"
    custom_location: Optional[str] = None
    shots: List["SuggestShotItem"]


class RefineShotRequest(BaseModel):
    camera_move: str
    duration: int
    user_description: str
    location: str = "studio"
    custom_location: Optional[str] = None
    location_image_url: Optional[str] = None  # library bg URL or base64 data URI
    camera_angle: str = "eye_level"
    shot_size: str = "wide"


class DefileOutfit(BaseModel):
    """Defile koleksiyonundaki tek bir kıyafet."""
    front_url: str
    side_url: Optional[str] = None
    back_url: Optional[str] = None
    extra_urls: Optional[List[str]] = None  # all extra angle URLs from element library
    name: Optional[str] = None
    category: Optional[str] = None  # e.g. "costume", "scene", "character", etc.


class DefileShotConfig(BaseModel):
    """Global defile multishot konfigürasyonu — tek bir sahne."""
    duration: int = Field(default=5, ge=3, le=10, description="Sahne süresi (saniye)")


class DefileCollectionRequest(BaseModel):
    """Defile koleksiyon video üretim talebi."""
    outfits: List[DefileOutfit]                        # 2–12 kıyafet
    shot_configs: List[DefileShotConfig] = Field(      # Global multishot konfigürasyonu
        default_factory=lambda: [DefileShotConfig(duration=5)],
        description="Her kıyafet için uygulanacak sahne listesi (global)",
    )
    runway_background_url: Optional[str] = None
    runway_background_extra_urls: Optional[List[str]] = None
    start_frame_url: Optional[str] = None  # If provided, skip NB2 — use this as scene frame
    aspect_ratio: str = "9:16"
    generate_audio: bool = True
    provider: str = "fal"  # "fal" = fal.ai | "kling" = Kling Direct


class JobStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    GENERATING_PROMPTS = "generating_prompts"
    GENERATING_BACKGROUND = "generating_background"
    GENERATING_VIDEO = "generating_video"
    COMPLETED = "completed"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = ""
    progress: int = 0            # 0-100
    result_url: Optional[str] = None
    analysis: Optional[DressAnalysisResult] = None
    scene_prompt: Optional[MultiScenePrompt] = None
    debug_payload: Optional[dict] = None  # actual request body sent to video API
