"""Operator CLI: dry run by default; explicit flags for database and sandbox invoice writes."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from app.db import get_sessionmaker
from app.demo.seeder import DemoSeed, assert_demo, manifest_for, seed_demo
from app.paypal.client import PayPalClient
from app.vendor_sim.gateway import VendorGateway


async def run(args: argparse.Namespace) -> dict:
    settings = Settings(_env_file=None)
    assert_demo(settings)
    config = DemoSeed.model_validate_json(args.config.read_text(encoding="utf-8"))
    if not args.apply_db:
        if args.send_invoices:
            raise ValueError("--send-invoices also requires --apply-db")
        return manifest_for(config)
    gateway = None
    client = None
    if args.send_invoices:
        # The CLI runs in the trusted setup/settlement environment, never in the API process.
        from app.vendor_sim.gateway import Invoicing

        if not settings.vendor_client_id or not settings.vendor_client_secret:
            raise ValueError("Vendor write credentials are required to create sandbox invoices")
        client = PayPalClient(
            app="VENDOR",
            base_url=settings.paypal_base_url,
            client_id=settings.vendor_client_id,
            client_secret=settings.vendor_client_secret.get_secret_value(),
        )
        gateway = VendorGateway(Invoicing(client))
    try:
        return await seed_demo(
            get_sessionmaker(settings.database_url),
            config=config,
            settings=settings,
            manifest_dir=args.manifest_dir,
            gateway=gateway,
        )
    finally:
        if client is not None:
            await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="private JSON with verified identity and operator hashes",
    )
    parser.add_argument("--manifest-dir", type=Path, default=Path(".demo-runs"))
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--send-invoices", action="store_true")
    manifest = asyncio.run(run(parser.parse_args()))
    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
