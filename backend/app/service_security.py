"""R22: service environment boundaries and PostgreSQL capability checks at startup."""

import os
from collections.abc import Mapping
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Service = Literal["api", "settlement"]

SHARED_VARIABLES = frozenset(
    {
        "PAYPAL_BASE_URL",
        "TREASURY_WEBHOOK_ID",
        "VENDOR_CLIENT_ID",
        "VENDOR_CLIENT_SECRET",
        "VENDOR_WEBHOOK_ID",
        "VENDOR_ACCOUNT_EMAIL",
        "ORG_HARD_MAX_SINGLE_CENTS",
        "ORG_HARD_DAILY_CAP_CENTS",
        "ORG_HARD_VENDOR_DAILY_CAP_CENTS",
        "ORG_HARD_VENDOR_30D_CAP_CENTS",
        "ORG_HARD_MAX_INVOICES_PER_VENDOR_PER_DAY",
        "ORG_HARD_MAX_PRICE_VARIANCE_BPS",
        "ORG_HARD_MAX_SHORT_PAY_DELTA_CENTS",
        "ORG_HARD_MAX_DUAL_APPROVAL_THRESHOLD",
        "ORG_ALLOWED_TIMEZONES",
        "PAYEE_COOLING_HOURS",
        "VENDOR_NEW_DAYS",
    }
)
API_VARIABLES = frozenset(
    {
        "DATABASE_URL",
        "CHECKPOINT_DB_URL",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL_EXTRACT",
        "LLM_MODEL_REASON",
        "SESSION_SECRET",
        "OPERATOR_SEED_FILE",
        "DEMO_ENABLED",
        "FORENSICS_DETECTOR_ENABLED",
        "DEMO_REPLAY_MODE",
    }
)
SETTLEMENT_VARIABLES = frozenset(
    {
        "SETTLEMENT_DATABASE_URL",
        "TREASURY_CLIENT_ID",
        "TREASURY_CLIENT_SECRET",
        "TREASURY_ACCOUNT_EMAIL",
        "AUDIT_HMAC_KEY",
        "AUDIT_ANCHOR_REPO",
        "VELOCITY_MAX_PAYOUTS_PER_HOUR",
        "VELOCITY_MAX_CENTS_PER_HOUR",
        "ALERT_WEBHOOK_URL",
    }
)
FRONTEND_VARIABLES = frozenset({"NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_AG_LICENSE_KEY"})
PROJECT_VARIABLES = SHARED_VARIABLES | API_VARIABLES | SETTLEMENT_VARIABLES | FRONTEND_VARIABLES


def validate_service_environment(service: Service, env: Mapping[str, str] | None = None) -> None:
    """Reject wrong-service variables even when empty; keep OS variables usable."""
    if service not in ("api", "settlement"):
        raise ValueError("unknown service")
    allowed = SHARED_VARIABLES | (API_VARIABLES if service == "api" else SETTLEMENT_VARIABLES)
    forbidden = (set(os.environ if env is None else env) & PROJECT_VARIABLES) - allowed
    if forbidden:
        fields = ", ".join(sorted(forbidden))
        raise ValueError(f"{service} environment contains forbidden variables: {fields}")


async def validate_database_role(session: AsyncSession, service: Service) -> None:
    """Require inherited own privileges and reject opposite membership, including SET ROLE."""
    if service not in ("api", "settlement"):
        raise ValueError("unknown service")
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError(f"{service} requires PostgreSQL")
    row = (
        (
            await session.execute(
                text("""
        SELECT r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
               r.rolreplication, r.rolbypassrls,
               current_user = session_user AS original_login,
               pg_has_role(current_user, to_regrole('aegis_api_role'),
                   CASE WHEN :service='api' THEN 'USAGE' ELSE 'MEMBER' END) AS api,
               pg_has_role(current_user, to_regrole('aegis_settlement_role'),
                   CASE WHEN :service='settlement' THEN 'USAGE' ELSE 'MEMBER' END) AS settlement
        FROM pg_roles r WHERE r.rolname = current_user
    """),
                {"service": service},
            )
        )
        .mappings()
        .one()
    )
    other = "settlement" if service == "api" else "api"
    if (
        not row["rolcanlogin"]
        or not row["original_login"]
        or any(
            row[name]
            for name in (
                "rolsuper",
                "rolcreatedb",
                "rolcreaterole",
                "rolreplication",
                "rolbypassrls",
            )
        )
        or not row[service]
        or row[other]
    ):
        raise ValueError(f"{service} database role lacks isolated service capabilities")
