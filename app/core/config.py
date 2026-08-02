"""
Centralized application configuration.
Single source of truth for all environment-driven settings — nothing
in the codebase should call os.environ / os.getenv directly.
"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    APP_NAME: str = "CinemaHub Pro"
    ENVIRONMENT: str = Field(default="development")  # development | production
    DEBUG: bool = Field(default=False)
    PORT: int = Field(default=8000)  # Render injects this at runtime

    # --- Database (NeonDB / Postgres, async) ---
    DATABASE_URL: str = Field(
        ...,
        description="postgresql+asyncpg://user:pass@host/db?ssl=require",
    )
    DB_POOL_SIZE: int = Field(default=5)
    DB_MAX_OVERFLOW: int = Field(default=5)
    DB_POOL_TIMEOUT: int = Field(default=30)
    DB_ECHO: bool = Field(default=False)

    # --- Redis (caching, rate limits, FSM storage) ---
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # --- Telegram ---
    BOT_TOKEN: str = Field(...)
    WEBHOOK_BASE_URL: str | None = Field(default=None)
    WEBHOOK_SECRET: str | None = Field(default=None)

    # --- Third-party APIs ---
    TMDB_API_KEY: str = Field(...)
    GEMINI_API_KEY: str = Field(...)
    GEMINI_MODEL: str = Field(default="gemini-2.0-flash")
    AI_CATALOG_CONTEXT_LIMIT: int = Field(default=150)
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")

    # --- Business rules ---
    AI_DAILY_LIMIT_FREE: int = Field(default=3)
    AUTO_DELETE_SECONDS: int = Field(default=900)  # 15 min — copyright safety window

    # --- Payments ---
    ADMIN_IDS: str = Field(default="", description="Comma-separated Telegram user IDs of admins")
    PREMIUM_PRICE: float = Field(default=50000)  # in your local currency's smallest display unit
    PREMIUM_SUBSCRIPTION_DAYS: int = Field(default=30)
    TOPUP_PRESET_AMOUNTS: str = Field(default="10000,25000,50000,100000")

    @property
    def admin_ids_list(self) -> list[int]:
        return [int(x) for x in self.ADMIN_IDS.split(",") if x.strip()]

    @property
    def topup_preset_amounts_list(self) -> list[int]:
        return [int(x) for x in self.TOPUP_PRESET_AMOUNTS.split(",") if x.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — import and call this, never instantiate Settings directly."""
    return Settings()


settings = get_settings()
