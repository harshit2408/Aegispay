"""Settings from env / .env (PRD §7). Later checklist items add the remaining §7 fields."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    demo_enabled: bool = False
    demo_replay_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
