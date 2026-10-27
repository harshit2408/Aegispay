"""Export the actual FastAPI app without starting services or reading settings."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def render_openapi() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed schema has drifted")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "openapi.json"
    generated = render_openapi()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != generated:
            sys.exit("OpenAPI drift: run python backend/scripts/export_openapi.py")
    else:
        target.write_text(generated, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
