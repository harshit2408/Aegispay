"""Durable admission for one governed invoice or mandate authoring execution."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LlmBudget(Base):
    __tablename__ = "llm_budgets"
    __table_args__ = (
        CheckConstraint("requests BETWEEN 0 AND 4", name="ck_llm_requests"),
        CheckConstraint("charged_micros BETWEEN 0 AND 1000000", name="ck_llm_charge"),
    )
    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    charged_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    halted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
