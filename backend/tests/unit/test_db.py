from sqlalchemy import text

from app.db import get_session


async def test_session_runs_select_1():
    async for session in get_session():
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
