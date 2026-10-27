"""Settings from env / .env (PRD §7). Later checklist items add the remaining §7 fields."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    demo_enabled: bool = False
    demo_replay_mode: bool = False
    # Registered PayPal webhook IDs (not secrets): verification fails closed while empty (R10)
    treasury_webhook_id: str = ""
    vendor_webhook_id: str = ""

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        # Render hands out plain postgres:// URLs; SQLAlchemy async needs the asyncpg driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
