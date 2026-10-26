"""ORM tables for PRD §8. Importing this package registers every table on `Base.metadata`."""

from app.models import core, payables

__all__ = ["core", "payables"]
