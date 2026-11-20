"""Import every module of the app, to catch platform-specific import errors.

Runs in CI on Windows, macOS and Linux with QT_QPA_PLATFORM=offscreen. A
QApplication exists first, since some modules build Qt objects at import time.
"""

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("core", "services", "ui")


def module_names():
    yield "main"
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            parts = path.relative_to(ROOT).with_suffix("").parts
            yield ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])  # noqa: F841 - kept alive while importing
    failures = []
    count = 0
    for name in module_names():
        count += 1
        try:
            importlib.import_module(name)
        except Exception:
            failures.append(name)
            print(f"FAILED {name}\n{traceback.format_exc()}")
    print(f"Imported {count - len(failures)} of {count} modules on {sys.platform}.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
