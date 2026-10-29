"""Settlement-only verified JSON/CSV export; python -m app.audit.export EXECUTION_ID."""

import argparse
import asyncio
import csv
import io
import json
import sys
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit.anchor import AnchorUnavailable, GitAnchors
from app.audit.consumer import verified_rows
from app.audit.events import _timestamp
from app.config import get_settlement_settings
from app.models.payables import InvoiceExecution
from app.service_security import validate_database_role


async def export_events(
    sessions,
    anchors: GitAnchors,
    *,
    key: bytes,
    execution_id: str,
    format: str = "json",
    include_replay: bool = False,
) -> str:
    if format not in ("json", "csv"):
        raise ValueError("audit export format must be json or csv")
    snapshot = await anchors.read()
    checkpoint = snapshot.checkpoint
    if checkpoint is None:
        raise AnchorUnavailable("audit export requires an external checkpoint")
    async with sessions() as session:
        rows = await verified_rows(session, key=key, snapshot=snapshot)
        replayed = set(
            await session.scalars(
                select(InvoiceExecution.execution_id).where(InvoiceExecution.replayed.is_(True))
            )
        )
    selected = [
        row
        for row in rows
        if row.execution_id == execution_id
        and row.event_seq <= checkpoint.event_seq
        and (include_replay or row.execution_id not in replayed)
    ]
    events = [
        {
            "event_seq": row.event_seq,
            "execution_id": row.execution_id,
            "ts": _timestamp(row.ts),
            "actor": row.actor,
            "event_type": row.event_type,
            "payload": row.payload,
            "prev_hash": row.prev_hash,
            "hash": row.hash,
        }
        for row in selected
    ]
    anchor = asdict(checkpoint)
    anchor["created_at"] = _timestamp(checkpoint.created_at)
    pending = sum(
        row.execution_id == execution_id
        and row.event_seq > checkpoint.event_seq
        and (include_replay or row.execution_id not in replayed)
        for row in rows
    )
    if format == "json":
        return json.dumps(
            {"anchor": anchor, "events": events, "pending_unanchored_events": pending},
            ensure_ascii=False,
            sort_keys=True,
        )
    output = io.StringIO(newline="")
    fields = [
        "event_seq",
        "execution_id",
        "ts",
        "actor",
        "event_type",
        "payload",
        "prev_hash",
        "hash",
        "anchor_commit",
        "anchor_event_seq",
        "anchor_head_hash",
        "pending_unanchored_events",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    if not events:
        writer.writerow(
            {
                "anchor_commit": checkpoint.external_ref,
                "anchor_event_seq": checkpoint.event_seq,
                "anchor_head_hash": checkpoint.head_hash,
                "pending_unanchored_events": pending,
            }
        )
    for event in events:
        event = {
            **event,
            "payload": json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
            "anchor_commit": checkpoint.external_ref,
            "anchor_event_seq": checkpoint.event_seq,
            "anchor_head_hash": checkpoint.head_hash,
            "pending_unanchored_events": pending,
        }
        # CSV opened in a spreadsheet must not execute actor/document-derived formulas.
        writer.writerow(
            {
                name: "'" + value
                if isinstance(value, str)
                and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                else value
                for name, value in event.items()
            }
        )
    return output.getvalue()


async def _run(args) -> str:
    settings = get_settlement_settings()
    if not settings.audit_anchor_repo:
        raise AnchorUnavailable("audit export requires configured AUDIT_ANCHOR_REPO")
    engine = create_async_engine(settings.settlement_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await validate_database_role(session, "settlement")
        return await export_events(
            sessions,
            GitAnchors(
                settings.audit_anchor_repo,
                settings.audit_anchor_token.get_secret_value()
                if settings.audit_anchor_token
                else None,
            ),
            key=settings.audit_hmac_key.get_secret_value().encode(),
            execution_id=args.execution_id,
            format=args.format,
            include_replay=args.include_replay,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_id")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--include-replay", action="store_true")
    args = parser.parse_args()
    try:
        output = asyncio.run(_run(args))
    except Exception:  # No driver/transport traceback may expose service configuration.
        parser.exit(
            1,
            "Audit export refused: configuration, integrity or external checkpoint unavailable.\n",
        )
    sys.stdout.write(output + ("" if output.endswith("\n") else "\n"))


if __name__ == "__main__":
    main()
