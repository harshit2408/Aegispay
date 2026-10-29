"""Independent process settings (PRD §7, R22); environment variables only."""

from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.paypal.client import _validated_base_url
from app.service_security import validate_service_environment


def _database_url(value: str) -> str:
    try:
        url = make_url(value)
        if url.drivername == "sqlite+aiosqlite":
            return value
        if url.drivername in ("postgres", "postgresql", "postgresql+psycopg"):
            url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)
    except Exception:
        raise ValueError("invalid database URL") from None


class _PayPalSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"

    @field_validator("paypal_base_url")
    @classmethod
    def paypal_origin(cls, value: str) -> str:
        try:
            return _validated_base_url(value)
        except ValueError:
            raise ValueError("invalid PayPal HTTPS origin") from None


class Settings(_PayPalSettings):
    database_url: str = Field(repr=False)
    demo_enabled: bool = False
    demo_replay_mode: bool = False
    treasury_webhook_id: str = ""
    vendor_webhook_id: str = ""
    vendor_client_id: str | None = Field(default=None, repr=False)
    vendor_client_secret: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def database(cls, value: str) -> str:
        return _database_url(value)

    @model_validator(mode="after")
    def demo_is_sandbox_only(self) -> "Settings":
        if self.demo_replay_mode and not self.demo_enabled:
            raise ValueError("DEMO_REPLAY_MODE requires DEMO_ENABLED")
        if self.demo_enabled and self.paypal_base_url not in (
            "https://api-m.sandbox.paypal.com",
            "https://api.sandbox.paypal.com",
        ):
            raise ValueError("demo requires sandbox PayPal")
        return self


class SettlementSettings(_PayPalSettings):
    settlement_database_url: str = Field(repr=False)
    treasury_client_id: str = Field(min_length=1, repr=False)
    treasury_client_secret: SecretStr = Field(min_length=1)
    treasury_account_email: str = Field(min_length=1, repr=False)
    vendor_client_id: str = Field(min_length=1, repr=False)
    vendor_client_secret: SecretStr = Field(min_length=1)
    vendor_account_email: str = Field(min_length=1, repr=False)
    audit_hmac_key: SecretStr = Field(min_length=32)
    audit_anchor_repo: str = Field(default="", repr=False)
    audit_anchor_token: SecretStr | None = None

    @field_validator("audit_anchor_repo")
    @classmethod
    def anchor_repo(cls, value: str) -> str:
        from app.audit.anchor import validate_remote

        return validate_remote(value) if value else ""

    @field_validator("audit_anchor_token", mode="before")
    @classmethod
    def anchor_token(cls, value):
        return value or None

    @model_validator(mode="after")
    def anchor_token_has_repo(self) -> "SettlementSettings":
        if self.audit_anchor_token and not self.audit_anchor_repo:
            raise ValueError("AUDIT_ANCHOR_TOKEN requires AUDIT_ANCHOR_REPO")
        return self

    @field_validator("settlement_database_url")
    @classmethod
    def postgres_database(cls, value: str) -> str:
        value = _database_url(value)
        if make_url(value).drivername != "postgresql+asyncpg":
            raise ValueError("settlement requires PostgreSQL with asyncpg")
        return value

    @field_validator("treasury_client_id", "vendor_client_id", mode="before")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("client ID cannot be blank")
        return value

    @field_validator("treasury_client_secret", "vendor_client_secret", mode="after")
    @classmethod
    def nonblank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("client secret cannot be blank")
        return value

    @field_validator("treasury_account_email", "vendor_account_email")
    @classmethod
    def account_email(cls, value: str) -> str:
        if "@" not in value or any(char.isspace() for char in value):
            raise ValueError("account email is invalid")
        return value


def _load_settings(settings_type):
    try:
        return settings_type(_env_file=None)
    except ValidationError as error:
        fields = sorted({str(part) for item in error.errors() for part in item["loc"]})
        names = ", ".join(fields or ["settings"])
        raise ValueError("Invalid service configuration fields: " + names) from None


@lru_cache
def get_settings() -> Settings:
    validate_service_environment("api")
    return _load_settings(Settings)


@lru_cache
def get_settlement_settings() -> SettlementSettings:
    validate_service_environment("settlement")
    return _load_settings(SettlementSettings)
