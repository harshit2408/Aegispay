"""Async SQLAlchemy 2.0 engine, session factory and FastAPI session dependency."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    """One engine per URL; built lazily so importing this module needs no env."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker(get_settings().database_url)() as session:
        yield session
