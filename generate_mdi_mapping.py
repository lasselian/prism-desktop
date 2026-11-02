"""
Generate mdi_mapping.json from the MDI CSS file.

Run this script once before building to ensure the mapping is bundled
in the release. Both build_exe.py and build_linux.py call this automatically.
"""

import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

MDI_CSS_URL = (
    "https://raw.githubusercontent.com/Templarian/"
    "MaterialDesign-Webfont/master/css/materialdesignicons.css"
)
OUTPUT = Path(__file__).parent / "mdi_mapping.json"


def generate(force: bool = False) -> bool:
    if OUTPUT.exists() and not force:
        print(f"mdi_mapping.json already exists ({OUTPUT}), skipping.")
        return True
    try:
        print("Fetching MDI icon mapping from GitHub...")
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(MDI_CSS_URL, context=context, timeout=30) as resp:
            css = resp.read().decode("utf-8")

        pattern = re.compile(
            r'\.mdi-([a-z0-9-]+)::?before\s*\{\s*content:\s*"\\([0-9a-f]+)"',
            re.IGNORECASE,
        )
        mapping = {m.group(1): chr(int(m.group(2), 16)) for m in pattern.finditer(css)}

        OUTPUT.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {len(mapping)} icons to {OUTPUT}")
        return True
    except Exception as exc:
        print(f"ERROR: Failed to generate MDI mapping: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    force = "--force" in sys.argv
    sys.exit(0 if generate(force=force) else 1)
