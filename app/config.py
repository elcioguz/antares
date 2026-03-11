from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Uygulama
    APP_NAME: str = "Antares ArGe RAG"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Veritabanı
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # OpenAI
    OPENAI_API_KEY: str

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # RAG
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_MODEL: str = "gpt-4o-mini"
    MAX_TOKENS: int = 600
    TOP_K_CHUNKS: int = 4
    GUVEN_ESIGI: float = 0.72

    # Cache
    CACHE_TTL_SECONDS: int = 86400  # 24 saat

    class Config:
        env_file = ".env"


settings = Settings()