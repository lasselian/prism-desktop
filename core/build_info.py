"""
Build/version metadata helpers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

APP_VERSION = "1.5.5"
REPO_ROOT = Path(__file__).resolve().parent.parent
SHORT_COMMIT_LENGTH = 12

try:
    from core._generated_build_info import BUILD_COMMIT, BUILD_DIRTY
except ImportError:
    BUILD_COMMIT = ""
    BUILD_DIRTY = False


def _run_git_command(*args: str) -> str:
    """Return git command output, or an empty string when unavailable."""
    import sys
    if getattr(sys, "frozen", False):
        return ""
    try:
        kwargs = {"cwd": REPO_ROOT, "stderr": subprocess.DEVNULL, "text": True}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.check_output(["git", *args], **kwargs).strip()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return ""


def get_build_commit() -> str:
    """Return the embedded commit when present, else the current checkout commit."""
    if BUILD_COMMIT:
        return BUILD_COMMIT
    return _run_git_command("rev-parse", f"--short={SHORT_COMMIT_LENGTH}", "HEAD")


def is_dirty_build() -> bool:
    """Report whether the source tree had local modifications for this build."""
    if BUILD_COMMIT:
        return BUILD_DIRTY
    return bool(_run_git_command("status", "--porcelain"))


def get_display_version() -> str:
    """Return a user-facing version string including commit metadata when available."""
    commit = get_build_commit()
    if not commit:
        return APP_VERSION

    suffix = f" ({commit})"
    if is_dirty_build():
        suffix = f" ({commit}, dirty)"
    return f"{APP_VERSION}{suffix}"
