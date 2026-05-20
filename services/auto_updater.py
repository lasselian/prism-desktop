"""
Auto-update: source installs use git pull; packaged/frozen builds download
the latest GitHub release asset and apply it in-place.
"""

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.build_info import REPO_ROOT, APP_VERSION

_REPO = "HomeRiz/prism-desktop"
_API_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _platform_asset_name() -> str | None:
    """Return the expected GitHub release asset filename for this platform/arch."""
    if sys.platform == "win32":
        return "PrismDesktopSetup.exe"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
        return f"PrismDesktop-{arch}.AppImage"
    return None


class AutoUpdateThread(QThread):
    """
    Source installs  → git pull + pip install.
    Frozen/packaged  → download the matching GitHub release asset and apply it.
    """

    progress = pyqtSignal(str)
    success = pyqtSignal(str)   # "updated" | "already_up_to_date"
    error = pyqtSignal(str)

    _GIT_TIMEOUT = 60
    _PIP_TIMEOUT = 120
    _NET_TIMEOUT = (10, 120)   # (connect, read) seconds for HTTP requests

    def run(self):
        if getattr(sys, "frozen", False):
            self._frozen_update()
        else:
            self._source_update()

    # ── Source install (git pull) ─────────────────────────────────────────────

    def _run_cmd(self, cmd: list, timeout: int) -> subprocess.CompletedProcess:
        kwargs = {
            "cwd": str(REPO_ROOT),
            "capture_output": True,
            "text": True,
            "timeout": timeout,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    def _source_update(self):
        try:
            self.progress.emit("Fetching latest changes…")
            result = self._run_cmd(["git", "pull", "--ff-only"], self._GIT_TIMEOUT)

            if result.returncode != 0:
                stderr = (result.stderr or result.stdout).strip()
                self.error.emit(stderr or "git pull failed.")
                return

            if "Already up to date." in result.stdout:
                self.success.emit("already_up_to_date")
                return

            req = REPO_ROOT / "requirements.txt"
            if req.exists():
                self.progress.emit("Updating dependencies…")
                self._run_cmd(
                    [sys.executable, "-m", "pip", "install", "-r", str(req), "--quiet"],
                    self._PIP_TIMEOUT,
                )

            self.success.emit("updated")

        except subprocess.TimeoutExpired:
            self.error.emit("Update timed out — check your network connection.")
        except FileNotFoundError:
            self.error.emit("git is not available on this system.")
        except Exception as exc:
            self.error.emit(str(exc))

    # ── Frozen/packaged install (GitHub release asset) ────────────────────────

    def _frozen_update(self):
        asset_name = _platform_asset_name()
        if not asset_name:
            self.error.emit(f"Auto-update is not supported on {sys.platform}.")
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="prism_update_"))
        tmp_path = tmp_dir / asset_name
        applied = False
        try:
            # 1. Resolve download URL from GitHub release metadata
            self.progress.emit("Fetching release information…")
            resp = requests.get(_API_LATEST, headers=_GH_HEADERS, timeout=self._NET_TIMEOUT)
            resp.raise_for_status()
            assets = resp.json().get("assets", [])
            asset = next((a for a in assets if a["name"] == asset_name), None)
            if not asset:
                self.error.emit(f"No release asset found for this platform ({asset_name}).")
                return

            # 2. Stream-download with progress
            self.progress.emit("Downloading update…")
            total = asset.get("size", 0)
            done = 0
            with requests.get(
                asset["browser_download_url"], stream=True, timeout=self._NET_TIMEOUT
            ) as dl:
                dl.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                self.progress.emit(
                                    f"Downloading update… {done * 100 // total}%"
                                )

            # 3. Apply the downloaded asset
            self.progress.emit("Applying update…")
            self._apply_frozen(tmp_path)
            applied = True
            self.success.emit("updated")

        except requests.RequestException as exc:
            self.error.emit(f"Download failed: {exc}")
        except OSError as exc:
            self.error.emit(f"Could not apply update: {exc}")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            # On Windows, the installer file must stay alive while the installer
            # process runs; the OS will clean %TEMP% on next session.
            # On Linux (and on error), clean up immediately.
            if not (applied and sys.platform == "win32"):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _apply_frozen(self, asset_path: Path) -> None:
        if sys.platform == "win32":
            self._apply_windows(asset_path)
        elif sys.platform.startswith("linux"):
            self._apply_linux(asset_path)
        else:
            raise OSError(f"Unsupported platform for in-place update: {sys.platform}")

    def _apply_windows(self, installer_path: Path) -> None:
        """Launch the Inno Setup installer silently; it replaces the installation."""
        subprocess.Popen(
            [str(installer_path), "/VERYSILENT", "/NORESTART", "/NOCANCEL"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _apply_linux(self, appimage_path: Path) -> None:
        """
        Replace the running AppImage at sys.executable.
        Linux keeps the old inode open for the running process; only the
        directory entry is updated, so the replacement is safe mid-run.
        """
        current = Path(sys.executable).resolve()
        appimage_path.chmod(0o755)
        shutil.move(str(appimage_path), str(current))


# ── Post-update flag ──────────────────────────────────────────────────────────

def _flag_path() -> Path:
    return Path(tempfile.gettempdir()) / "prism_desktop_just_updated"


def write_update_flag() -> None:
    """Record the pre-update version so the next startup can run a sanity check."""
    try:
        _flag_path().write_text(APP_VERSION, encoding="utf-8")
    except OSError:
        pass


def consume_update_flag() -> str | None:
    """
    Read and delete the update flag written before the restart.
    Returns the version string that was running before the update, or None.
    """
    path = _flag_path()
    if not path.exists():
        return None
    try:
        prev = path.read_text(encoding="utf-8").strip()
        path.unlink(missing_ok=True)
        return prev or "unknown"
    except OSError:
        return None


# ── App restart ───────────────────────────────────────────────────────────────

def restart_app() -> None:
    """Restart the application after a successful update."""
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("linux"):
            # New AppImage is already in place at sys.executable; re-exec it.
            subprocess.Popen([sys.executable])
        # On Windows the Inno Setup installer spawns the new version itself;
        # just quit so the installer can replace our files cleanly.
    else:
        write_update_flag()
        subprocess.Popen([sys.executable] + sys.argv)
    QApplication.quit()
