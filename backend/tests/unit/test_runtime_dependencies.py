"""Every third-party module imported by `app/` is a runtime dependency, not a dev-only one.

A dev-only dependency passes pytest but crashes the deployed API on import (e.g. `cryptography`
for R10 webhook verification)."""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_dependencies() -> set[str]:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {
        _normalize(re.split(r"[\s\[<>=!~;]", dep, maxsplit=1)[0]) for dep in project["dependencies"]
    }


def _imported_top_level_modules() -> set[str]:
    found: set[str] = set()
    for path in (BACKEND / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found - set(sys.stdlib_module_names) - {"app"}


def test_app_imports_only_runtime_dependencies():
    runtime = _runtime_dependencies()
    distributions = packages_distributions()
    missing = {
        module: distributions.get(module)
        for module in _imported_top_level_modules()
        if not any(_normalize(d) in runtime for d in distributions.get(module, []))
    }
    assert not missing, f"imported by app/ but not in [project].dependencies: {missing}"
