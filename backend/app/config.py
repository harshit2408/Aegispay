"""Independent process settings (PRD §7, R22); environment variables only."""

from functools import lru_cache
from urllib.parse import urlsplit

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
    checkpoint_db_url: str | None = Field(default=None, repr=False)
    llm_api_key: SecretStr | None = None
    frontend_origin: str = "http://localhost:3000"
    storage_path: str = "/tmp/aegis-documents"
    demo_enabled: bool = False
    demo_replay_mode: bool = False
    treasury_webhook_id: str = ""
    vendor_webhook_id: str = ""
    vendor_client_id: str | None = Field(default=None, repr=False)
    vendor_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = Field(default=None, min_length=32)
    org_hard_max_single_cents: int = Field(default=100000, ge=0)
    org_hard_daily_cap_cents: int = Field(default=500000, ge=0)
    org_hard_vendor_daily_cap_cents: int = Field(default=150000, ge=0)
    org_hard_vendor_30d_cap_cents: int = Field(default=1000000, ge=0)
    org_hard_max_invoices_per_vendor_per_day: int = Field(default=5, ge=1)
    org_hard_max_price_variance_bps: int = Field(default=200, ge=0)
    org_hard_max_short_pay_delta_cents: int = Field(default=20000, ge=0)
    org_hard_max_dual_approval_threshold: int = Field(default=80, ge=0, le=100)
    org_allowed_timezones: str = "UTC,America/New_York,Asia/Kolkata"
    payee_cooling_hours: int = Field(default=72, ge=72)
    vendor_new_days: int = Field(default=7, ge=0)

    @field_validator("session_secret", mode="before")
    @classmethod
    def unset_session_secret(cls, value):
        # Compose passes SESSION_SECRET="" when unset; treat it as absent (sessions fail closed).
        return value or None

    @field_validator("frontend_origin")
    @classmethod
    def frontend(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in ("localhost", "127.0.0.1", "[::1]")
                )
            )
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("frontend origin must be HTTPS or local HTTP")
        return value.rstrip("/")

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
    audit_anchor_token_hosts: str = "github.com"  # comma-separated; the token goes nowhere else
    velocity_max_payouts_per_hour: int = Field(default=20, ge=1)
    velocity_max_cents_per_hour: int = Field(default=300000, ge=1)
    alert_webhook_url: str = Field(default="", repr=False)

    @field_validator("alert_webhook_url")
    @classmethod
    def alert_url(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("alert webhook must be HTTPS")
        return value

    @field_validator("audit_anchor_repo")
    @classmethod
    def anchor_repo(cls, value: str) -> str:
        from app.audit.anchor import validate_remote

        return validate_remote(value) if value else ""

    @property
    def anchor_token_hosts(self) -> frozenset[str]:
        return frozenset(
            h.strip().lower() for h in self.audit_anchor_token_hosts.split(",") if h.strip()
        )

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
