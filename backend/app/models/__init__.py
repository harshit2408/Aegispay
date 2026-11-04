"""ORM tables for PRD §8. Importing this package registers every table on `Base.metadata`."""

from app.models import auth, core, llm, payables

__all__ = ["auth", "core", "llm", "payables"]
