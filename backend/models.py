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
    """Tek bir video sahnesi için prompt."""
    model_config = {"protected_namespaces": ()}

    scene_number: int = Field(description="Sahne numarası")
    scene_title: str = Field(default="", description="Kısa sahne başlığı")
    camera_prompt: str = Field(description="Kamera açısı ve hareketi")
    model_action_prompt: str = Field(description="Mankenin hareketi / pozu")
    lighting_prompt: str = Field(description="Aydınlatma ayarı")
    pose_description: str = Field(default="", description="Detaylı poz açıklaması (Claid için)")
    background_description: str = Field(default="", description="Arka plan açıklaması (Claid için)")
    photo_prompt: str = Field(default="", description="Claid fotoğraf prompt'u (açı + poz + arka plan)")
    full_scene_prompt: str = Field(description="Kling video prompt'u (video hareketi)")
    duration_seconds: int = Field(description="Bu sahnenin süresi (saniye)")
    view_type: str = Field(default="front", description="front / back / transition")


class MultiScenePrompt(BaseModel):
    """Çoklu sahne için üretilen prompt seti."""
    model_config = {"protected_namespaces": ()}

    background_prompt: str = Field(description="Genel mekan tanımı")
    total_duration: int = Field(description="Toplam video süresi")
    scene_count: int = Field(description="Sahne sayısı")
    scenes: List[SingleScenePrompt] = Field(description="Sahne listesi")
    garment_lock_description: str = Field(default="", description="Tüm sahnelerde tutarlı kıyafet tanımı")
    location_theme: str = Field(default="", description="Genel mekan teması")


class GenerationRequest(BaseModel):
    """Frontend'den gelen video üretim talebi."""
    model_config = {"protected_namespaces": ()}

    location: LocationPreset = LocationPreset.STUDIO
    custom_location: Optional[str] = None
    camera_style: Optional[str] = None
    model_action: Optional[str] = None
    mood: Optional[str] = None


class JobStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    PREPROCESSING = "preprocessing"
    GENERATING_VTO = "generating_vto"
    GENERATING_PHOTO = "generating_photo"
    GENERATING_VIDEO = "generating_video"
    MERGING = "merging"
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
