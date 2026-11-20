"""Import every module of the app, to catch platform-specific import errors.

Runs in CI on Windows, macOS and Linux with QT_QPA_PLATFORM=offscreen. A
QApplication exists first, since some modules build Qt objects at import time.
Modules that import Linux-only dependencies at the top level are skipped on
other systems; the app only loads them on Linux.
"""

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("core", "services", "ui")

# Modules that need dbus-next, which requirements.txt installs on Linux only.
LINUX_ONLY = frozenset({"services.dbus_notifier", "services.wayland_global_shortcut"})


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
    on_linux = sys.platform.startswith("linux")
    failures = []
    skipped = []
    count = 0
    for name in module_names():
        if name in LINUX_ONLY and not on_linux:
            skipped.append(name)
            continue
        count += 1
        try:
            importlib.import_module(name)
        except Exception:
            failures.append(name)
            print(f"FAILED {name}\n{traceback.format_exc()}")
    print(f"Imported {count - len(failures)} of {count} modules on {sys.platform}.")
    if skipped:
        print(f"Skipped (Linux only): {', '.join(skipped)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
