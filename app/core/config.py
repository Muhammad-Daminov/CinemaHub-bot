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
    # Injected by Render on every build; absent locally. Reported by
    # /health so "what is actually running?" is answerable without
    # fingerprinting the OpenAPI schema — which is how a failed deploy
    # went unnoticed for a day, serving a build two phases old.
    RENDER_GIT_COMMIT: str | None = Field(default=None)

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
    # Declared once. There were previously two GEMINI_MODEL fields here and
    # the later one silently won, so editing the first had no effect. This
    # keeps the value that was actually in force.
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    AI_CATALOG_CONTEXT_LIMIT: int = Field(default=150)

    # --- Business rules ---
    AI_DAILY_LIMIT_FREE: int = Field(default=3)

    # --- REST API rate limits (requests per minute, per caller) ---
    # Generous by design: the Mini App fires a dozen catalog requests on
    # first paint, and a limit that a normal session can reach is a bug
    # report, not a protection. 0 disables limiting.
    API_RATE_LIMIT_PER_MINUTE: int = Field(default=120)
    # Routes that cost megabytes or a third-party call — receipt upload and
    # video delivery. A human does these a handful of times a minute at most.
    API_RATE_LIMIT_EXPENSIVE_PER_MINUTE: int = Field(default=10)

    # --- Payments ---
    # Seeds administrators on first migration only. Authority lives in
    # chp_users.role + chp_admin_permissions from then on, so editing this
    # afterwards grants nothing — appoint through the admin panel instead.
    ADMIN_IDS: str = Field(default="", description="Legacy admin seed; superseded by role/permissions")
    # The one account that may appoint administrators. Authoritative:
    # whoever is named here is promoted on startup and any previous holder
    # is demoted, which is how Super Admin ownership transfers.
    SUPER_ADMIN_TELEGRAM_ID: int | None = Field(default=6427415448)
    # DEPRECATED — plans live in chp_subscription_plans as of Phase 4.
    # Retained solely so migration e58a3c7b91d4 can seed the initial
    # premium plan from whatever was in force at upgrade time; editing
    # them now changes nothing. Edit the plan in the admin panel instead.
    PREMIUM_PRICE: float = Field(default=50000)
    PREMIUM_SUBSCRIPTION_DAYS: int = Field(default=30)
    TOPUP_PRESET_AMOUNTS: str = Field(default="10000,25000,50000,100000")

    # --- Referral ---
    # Paid to referrer and referee alike when the referee's first top-up is
    # approved — the rule recorded in IDEAS.md I-2, chosen over rewarding
    # signup because signups are free to manufacture and an approved
    # payment is not. The amount was never specified anywhere in the
    # project, so it is configuration with a documented default rather
    # than a number invented in code; 0 disables payouts entirely and is
    # the safe setting if the figure is still being decided.
    REFERRAL_BONUS_AMOUNT: float = Field(default=5000)

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
