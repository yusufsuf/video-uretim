"""Configuration loader – reads API keys from .env file."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    FAL_KEY: str = os.getenv("FAL_KEY", "")

    # Kling Direct API
    KLING_ACCESS_KEY: str = os.getenv("KLING_ACCESS_KEY", "")
    KLING_SECRET_KEY: str = os.getenv("KLING_SECRET_KEY", "")

    # Public base URL (set to your Coolify domain in production)
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

    # CORS: comma-separated list of allowed origins (e.g. "https://a.com,https://b.com")
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://localhost:5173",
    )

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")

    # Telegram notifications
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # WhatsApp / Evolution API integration
    WHATSAPP_API_KEY: str = os.getenv("WHATSAPP_API_KEY", "")        # n8n → backend auth
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "")      # backend → Evolution send
    EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "")
    EVOLUTION_INSTANCE: str = os.getenv("EVOLUTION_INSTANCE", "")
    DAILY_VIDEO_LIMIT: int = int(os.getenv("DAILY_VIDEO_LIMIT", "5"))

    # Directories
    UPLOAD_DIR: str = os.path.join(os.path.dirname(__file__), "uploads")
    OUTPUT_DIR: str = os.path.join(os.path.dirname(__file__), "outputs")
    DATA_DIR: str = os.path.join(os.path.dirname(__file__), "data")
    TEMP_DIR: str = os.path.join(os.path.dirname(__file__), "temp")


settings = Settings()

# Create directories if they don't exist
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
os.makedirs(settings.DATA_DIR, exist_ok=True)
os.makedirs(settings.TEMP_DIR, exist_ok=True)
