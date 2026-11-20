"""Fail if core/ imports from ui/ or services/ (see docs/ARCHITECTURE.md)."""

import ast
import sys
from pathlib import Path

FORBIDDEN = {"core": ("ui", "services")}


def imported_packages(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module.split(".")[0]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    problems = []
    for layer, banned in FORBIDDEN.items():
        for path in sorted((root / layer).rglob("*.py")):
            for lineno, package in imported_packages(path):
                if package in banned:
                    problems.append(f"{path.relative_to(root)}:{lineno}: {layer}/ must not import {package}")
    print("\n".join(problems) or "Layering OK: core/ imports neither ui/ nor services/.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
