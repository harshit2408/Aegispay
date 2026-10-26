"""R2: `paypal.payouts` only from `settlement/`; `paypal.invoicing` only from `vendor_sim/`."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
# module name -> directory that may import it
BOUNDARIES = {
    "payouts": APP / "settlement",
    "invoicing": APP / "vendor_sim",
}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP.parent).with_suffix("").parts)


def imports_module(source: str, module: str, is_package: bool, target: str = "payouts") -> bool:
    """True if `source` (module `module`) imports `app.paypal.<target>` in any spelling."""
    forbidden = f"app.paypal.{target}"
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == forbidden or a.name.startswith(forbidden + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            if base == forbidden or base.startswith(forbidden + "."):
                return True
            if base == "app.paypal" and any(a.name == target for a in node.names):
                return True
    return False


@pytest.mark.parametrize(("target", "allowed_dir"), BOUNDARIES.items())
def test_boundary_holds(target, allowed_dir):
    own_file = APP / "paypal" / f"{target}.py"
    offenders = []
    for path in APP.rglob("*.py"):
        if allowed_dir in path.parents or path == own_file:
            continue
        source = path.read_text(encoding="utf-8")
        if imports_module(source, _module_name(path), path.name == "__init__.py", target):
            offenders.append(str(path.relative_to(APP)))
    assert not offenders, f"boundary violated, {target} imported from: {offenders}"


def test_detector_catches_every_import_spelling():
    mod = "app.decision.nodes.x"
    for target in BOUNDARIES:
        for src in (
            f"import app.paypal.{target}",
            f"from app.paypal.{target} import X",
            f"from app.paypal import {target}",
            f"from app.paypal import client, {target} as p",
            f"from ...paypal.{target} import X",
            f"from ...paypal import {target}",
        ):
            assert imports_module(src, mod, False, target), src
    assert not imports_module("from app.paypal.client import PayPalClient", mod, False)
    assert not imports_module("from app.paypal import invoicing", mod, False, "payouts")
