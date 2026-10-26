"""R2: `app.paypal.payouts` (Treasury writes) may be imported only from `app/settlement/`."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = "app.paypal.payouts"
ALLOWED_DIR = APP / "settlement"
PAYOUTS_FILE = APP / "paypal" / "payouts.py"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP.parent).with_suffix("").parts)


def imports_payouts(source: str, module: str, is_package: bool) -> bool:
    """True if `source` (module `module`) imports the forbidden module in any spelling."""
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == FORBIDDEN or a.name.startswith(FORBIDDEN + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            if base == FORBIDDEN or base.startswith(FORBIDDEN + "."):
                return True
            if base == "app.paypal" and any(a.name == "payouts" for a in node.names):
                return True
    return False


def test_only_settlement_imports_payouts():
    offenders = []
    for path in APP.rglob("*.py"):
        if ALLOWED_DIR in path.parents or path == PAYOUTS_FILE:
            continue
        source = path.read_text(encoding="utf-8")
        if imports_payouts(source, _module_name(path), path.name == "__init__.py"):
            offenders.append(str(path.relative_to(APP)))
    assert not offenders, f"R2 violated, payouts imported from: {offenders}"


def test_detector_catches_every_import_spelling():
    mod = "app.decision.nodes.x"
    for src in (
        "import app.paypal.payouts",
        "from app.paypal.payouts import Payouts",
        "from app.paypal import payouts",
        "from app.paypal import client, payouts as p",
        "from ...paypal.payouts import Payouts",
        "from ...paypal import payouts",
    ):
        assert imports_payouts(src, mod, False), src
    assert not imports_payouts("from app.paypal.client import PayPalClient", mod, False)
    assert not imports_payouts("from app.paypal import invoicing", mod, False)
