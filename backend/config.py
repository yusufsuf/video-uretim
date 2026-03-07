"""Configuration loader – reads API keys from .env file."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    FAL_KEY: str = os.getenv("FAL_KEY", "")

    # Public base URL (set to your Coolify domain in production)
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

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
