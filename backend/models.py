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
    pattern: str = Field(description="Desen bilgisi (düz, çizgili, çiçekli vb.)")
    fabric: str = Field(description="Kumaş tipi tahmini (ipek, pamuk, polyester vb.)")
    cut_style: str = Field(description="Kesim stili (A-line, bodycon, oversize vb.)")
    length: str = Field(description="Uzunluk (mini, midi, maxi vb.)")
    details: str = Field(description="Ekstra detaylar (dantel, düğme, kemer vb.)")
    season: str = Field(description="Uygun mevsim önerisi")
    mood: str = Field(description="Genel mood / atmosfer")


class SingleScenePrompt(BaseModel):
    """Tek bir video sahnesi için prompt."""
    model_config = {"protected_namespaces": ()}

    scene_number: int = Field(description="Sahne numarası")
    camera_prompt: str = Field(description="Kamera açısı ve hareketi")
    model_action_prompt: str = Field(description="Mankenin hareketi / pozu")
    lighting_prompt: str = Field(description="Aydınlatma ayarı")
    full_scene_prompt: str = Field(description="Bu sahne için tam prompt")
    duration_seconds: int = Field(description="Bu sahnenin süresi (saniye)")


class MultiScenePrompt(BaseModel):
    """Çoklu sahne için üretilen prompt seti."""
    model_config = {"protected_namespaces": ()}

    background_prompt: str = Field(description="Genel mekan tanımı")
    total_duration: int = Field(description="Toplam video süresi")
    scene_count: int = Field(description="Sahne sayısı")
    scenes: List[SingleScenePrompt] = Field(description="Sahne listesi")


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
