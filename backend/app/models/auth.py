"""Opaque operator sessions and bounded login buckets (R25)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    upload_window_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"

    bucket_key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)


class OpsAlert(Base):
    __tablename__ = "ops_alerts"

    alert_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text)
    execution_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
