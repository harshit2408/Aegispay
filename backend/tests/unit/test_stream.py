"""P2-21: real HTTP stream over committed Postgres notifications (no evidence payload)."""

import asyncio
import json
import socket
from datetime import UTC, datetime, timedelta
from time import perf_counter

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text

from app.agents.provider import postgres_dsn
from app.auth import COOKIE, token_hash
from app.db import get_session
from app.models.auth import OperatorSession
from app.routers.stream import router
from tests.seed import make_execution, seed_world


async def test_authenticated_http_stream_delivers_commit_under_one_second(
    pg_sessionmaker, pg_url, monkeypatch, record_property
):
    monkeypatch.setattr("app.routers.stream.HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setenv("SESSION_SECRET", "synthetic-sse-test-session-key-123456")
    token = "synthetic-sse-session"
    now = datetime.now(UTC)
    async with pg_sessionmaker.begin() as session:
        await seed_world(session)
        session.add(
            OperatorSession(
                token_hash=token_hash(token),
                operator_id="op-ctl",
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )

    app = FastAPI()
    app.include_router(router)
    app.state.runtime_dsn = postgres_dsn(pg_url)
    app.state.sessions = pg_sessionmaker

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            assert (await client.get("/api/stream")).status_code == 401
            client.cookies.set(COOKIE, token)
            async with client.stream("GET", "/api/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-store"
                lines = response.aiter_lines()
                assert await anext(lines) == ": connected"
                assert await anext(lines) == ""
                async with asyncio.timeout(1):
                    assert await anext(lines) == ": keepalive"
                    assert await anext(lines) == ""
                # Invalid payloads may come from another producer. The stream drops them.
                async with pg_sessionmaker.begin() as session:
                    await session.execute(
                        text("SELECT pg_notify('aegis_status', :payload)"),
                        {"payload": json.dumps({"type": "node", "evidence": "private"})},
                    )
                start = perf_counter()
                async with pg_sessionmaker.begin() as session:
                    session.add(make_execution("stream-case", status="RECEIVED"))
                async with asyncio.timeout(1):
                    line = await anext(lines)
                    while not line.startswith("data: "):
                        line = await anext(lines)
                elapsed_ms = (perf_counter() - start) * 1000
                event = json.loads(line.removeprefix("data: "))
                assert event["execution_id"] == "stream-case"
                assert event["status"] == "RECEIVED"
                assert set(event) == {"type", "execution_id", "name", "status", "ts"}
                assert elapsed_ms < 1000
                record_property("sse_commit_to_http_ms", round(elapsed_ms, 2))
                assert await anext(lines) == ""

                # A revoked session cannot observe the next committed notification.
                async with pg_sessionmaker.begin() as session:
                    login = await session.get(OperatorSession, token_hash(token))
                    await session.delete(login)
                    session.add(make_execution("stream-after-logout", status="RECEIVED"))
                async with asyncio.timeout(1):
                    try:
                        while True:
                            line = await anext(lines)
                            assert not line.startswith("data: ")
                    except StopAsyncIteration:
                        pass
                    else:
                        raise AssertionError("Revoked stream emitted another notification")
    finally:
        server.should_exit = True
        async with asyncio.timeout(5):
            await serving
        listener.close()
