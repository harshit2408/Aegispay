"""Build the PRD §15 clean S1 invoice PDF: 100 x LiDAR bracket @ $4.80 = $480.00, no hidden text.

  python scripts/s1_make_pdf.py --run s1a --payee-email supplier@sandbox.example --out S1.pdf

The invoice number and PO follow the seeder's naming (`AP-1042-<run>`, `PO-1042-<run>`). The payee
email is only text on the page: the payee that gets paid always comes from vendor master (R1).
"""

import argparse
import os
import re
import sys
from pathlib import Path

from reportlab.pdfgen import canvas

QTY, UNIT_CENTS, ITEM = 100, 480, "LiDAR bracket"


def money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def build(path: Path, *, run: str, payee_email: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]{1,12}", run):
        raise ValueError("run must be 1-12 lowercase letters, digits or hyphens")
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    # Bold 16pt: the OCR-diff detector compares every text-layer word with tesseract's reading,
    # and thin small fonts make it read "s1a" as "sla", flagging this clean invoice as hidden text.
    pdf.setFont("Helvetica-Bold", 16)
    lines = [
        "INVOICE",
        f"Invoice number: AP-1042-{run}",
        f"Purchase order: PO-1042-{run}",
        f"Pay to: {payee_email}",
        f"{ITEM}   qty {QTY}   unit {money(UNIT_CENTS)}",
        f"Total due: {money(QTY * UNIT_CENTS)} USD",
    ]
    for row, line in enumerate(lines):
        pdf.drawString(60, 780 - 40 * row, line)
    pdf.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--payee-email", default=os.environ.get("VENDOR_ACCOUNT_EMAIL"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.payee_email:
        parser.error("--payee-email or VENDOR_ACCOUNT_EMAIL is required")
    build(args.out, run=args.run, payee_email=args.payee_email)
    sys.stdout.write(f"wrote {args.out}\n")
