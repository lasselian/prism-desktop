"""Write the notes of a GitHub release, taken from CHANGELOG.md.

Usage: python .github/scripts/release_notes.py <version> [--changelog PATH]

Prints the notes for the "## <version>" section of the changelog (a leading "v"
on the version is ignored). The release workflow runs it, so every release gets
its notes without anyone pasting them in. Exits with an error when the
changelog has no such section, so a release cannot go out without notes.

The changelog's "### Fixes" and "### Security" sections keep their own headings.
Any other "###" heading (a feature name such as "Notifications") is shown as a
subsection of "New". Text before the first "###" is kept as an introduction.
"""

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Changelog heading -> heading in the release notes. Anything else goes under NEW.
OWN_HEADINGS = {"fixes": "🐛 Fixes", "security": "🔒 Security"}
NEW = "✨ New"
SECTION_ORDER = (NEW, *OWN_HEADINGS.values())

INSTALL = """---

## Install

| Platform | File |
|---|---|
| Windows | `PrismDesktopSetup.exe` |
| Linux | `PrismDesktop-x86_64.AppImage` |
| macOS (Apple Silicon) | `PrismDesktop-macOS-arm64.dmg` |
| macOS (Intel) | `PrismDesktop-macOS-x86_64.dmg` |

Checksums for every file are in `SHA256SUMS`.
"""


def version_section(changelog: str, version: str) -> str | None:
    """The text under "## <version>", up to the next "## " heading; None if there is none."""
    start = re.search(rf"^## v?{re.escape(version)}(?:\s.*)?$", changelog, re.MULTILINE)
    if not start:
        return None
    rest = changelog[start.end() :]
    end = re.search(r"^## (?!#)", rest, re.MULTILINE)
    return (rest[: end.start()] if end else rest).strip()


def split_subsections(section: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a version's text into its introduction and its (heading, body) subsections."""
    parts = re.split(r"^### +(.+?) *$", section, flags=re.MULTILINE)
    intro = parts[0].strip()
    return intro, [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def build_notes(changelog: str, version: str) -> str:
    section = version_section(changelog, version)
    if section is None:
        raise ValueError(f'CHANGELOG.md has no "## {version}" section')
    intro, subsections = split_subsections(section)
    if not intro and not subsections:
        raise ValueError(f'The "## {version}" section of CHANGELOG.md is empty')

    grouped: dict[str, list[str]] = {title: [] for title in SECTION_ORDER}
    for heading, body in subsections:
        own = OWN_HEADINGS.get(heading.lower())
        if own:
            grouped[own].append(body)
        else:
            grouped[NEW].append(f"### {heading}\n\n{body}")

    blocks = [intro] if intro else []
    blocks += [f"## {title}\n\n" + "\n\n".join(bodies) for title, bodies in grouped.items() if bodies]
    return "\n\n".join(blocks) + "\n\n" + INSTALL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("version", help="release version, for example 1.8.2")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    args = parser.parse_args()
    try:
        notes = build_notes(args.changelog.read_text(encoding="utf-8"), args.version.removeprefix("v"))
    except (OSError, ValueError) as e:
        print(f"::error::{e}" if "GITHUB_ACTIONS" in os.environ else f"error: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
