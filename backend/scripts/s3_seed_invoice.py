"""Create + send the sandbox PayPal invoice for the S3 PO (run after app.demo --apply-db).

PRD §15 S3 has no PayPal invoice, but POST /api/documents requires one that references the PO,
so a real-API S3 run needs this reference invoice. The attack document is the PDF; the verdict
still comes from the system, never from this script. Journals the invoice id in the manifest.

  python /scripts/s3_seed_invoice.py --config <seed.json> --manifest <demo-runs/RUN.json>
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from app.demo.seeder import SCENARIOS, DemoSeed, assert_demo
from app.paypal.client import PayPalClient
from app.vendor_sim.gateway import InvoiceSpec, Invoicing, VendorGateway


async def run(config_path: Path, manifest_path: Path) -> str:
    settings = Settings(_env_file=None)
    assert_demo(settings)
    config = DemoSeed.model_validate_json(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = manifest["scenarios"]["S3"]
    name, number, item, qty, price, _ = next(s for s in SCENARIOS if s[0] == "S3")
    if not settings.vendor_client_id or not settings.vendor_client_secret:
        raise ValueError("Vendor write credentials are required to create sandbox invoices")
    client = PayPalClient(
        app="VENDOR",
        base_url=settings.paypal_base_url,
        client_id=settings.vendor_client_id,
        client_secret=settings.vendor_client_secret.get_secret_value(),
    )
    try:
        gateway = VendorGateway(Invoicing(client))
        if row["paypal_invoice_id"] is None:
            row["paypal_invoice_id"] = await gateway.create_draft(
                InvoiceSpec(
                    invoice_number=row["invoice_number"],
                    reference=row["po_id"],
                    note=f"AegisPay sandbox {name}",
                    invoicer_email=config.vendor_email,
                    recipient_email=config.treasury_email,
                    item_name=item,
                    quantity=qty,
                    unit_cents=price,
                ),
                request_id=f"aegis-demo-{config.run_id}-{number}-new",
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        invoice = await gateway.get_invoice(row["paypal_invoice_id"])
        if invoice.status == "DRAFT":
            await gateway.send(
                row["paypal_invoice_id"], request_id=f"aegis-demo-{config.run_id}-{number}-send"
            )
        return row["paypal_invoice_id"]
    finally:
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.write(asyncio.run(run(args.config, args.manifest)) + "\n")
