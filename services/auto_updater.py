"""
Auto-update: source installs use git pull; packaged/frozen builds download
the latest GitHub release asset and apply it in-place.
"""

import hashlib
import logging
import os
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

logger = logging.getLogger(__name__)

_REPO = "lasselian/prism-desktop"
_API_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

_update_in_progress = False


def _platform_asset_name() -> str | None:
    """Return the expected GitHub release asset filename for this platform/arch."""
    if sys.platform == "win32":
        return "PrismDesktopSetup.exe"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
        return f"PrismDesktop-{arch}.AppImage"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"PrismDesktop-macOS-{arch}.zip"
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
        global _update_in_progress
        if _update_in_progress:
            self.error.emit("An update is already in progress.")
            return
        _update_in_progress = True
        try:
            if getattr(sys, "frozen", False):
                self._frozen_update()
            else:
                self._source_update()
        finally:
            _update_in_progress = False

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

            # 3. Verify checksum if a SHA256SUMS asset is present in the release
            if not self._verify_checksum(tmp_path, asset_name, assets):
                self.error.emit("Checksum mismatch — the downloaded file may be corrupt. Aborting.")
                return

            # 4. Apply the downloaded asset
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

    def _verify_checksum(self, file_path: Path, asset_name: str, assets: list) -> bool:
        """
        Verify SHA-256 checksum against a SHA256SUMS release asset if one exists.
        Returns True (pass) when no checksum asset is published — verification is
        opportunistic, not mandatory, until checksums are part of every release.
        """
        sums_asset = next(
            (a for a in assets if a["name"] in ("SHA256SUMS", "sha256sums.txt")), None
        )
        if not sums_asset:
            return True

        resp = requests.get(
            sums_asset["browser_download_url"], headers=_GH_HEADERS, timeout=self._NET_TIMEOUT
        )
        resp.raise_for_status()

        expected = None
        for line in resp.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == asset_name:
                expected = parts[0].lower()
                break

        if not expected:
            return True

        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest() == expected

    def _apply_frozen(self, asset_path: Path) -> None:
        if sys.platform == "win32":
            self._apply_windows(asset_path)
        elif sys.platform.startswith("linux"):
            self._apply_linux(asset_path)
        elif sys.platform == "darwin":
            self._apply_macos(asset_path)
        else:
            raise OSError(f"Unsupported platform for in-place update: {sys.platform}")

    def _apply_windows(self, installer_path: Path) -> None:
        """Launch the Inno Setup installer silently; its [Run] section (without
        skipifsilent) relaunches the app once files are replaced — see setup.iss."""
        args = [str(installer_path), "/VERYSILENT", "/NORESTART", "/NOCANCEL"]
        proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
        logger.info(f"Installer launched (pid={proc.pid}): {' '.join(args)}")

    def _apply_linux(self, appimage_path: Path) -> None:
        """
        Replace the running AppImage in-place.
        Uses $APPIMAGE (set by the AppImage runtime) to get the real file path;
        sys.executable resolves to the mount point inside /tmp and is wrong here.
        Linux keeps the old inode open for the running process; only the
        directory entry is updated, so the replacement is safe mid-run.
        """
        appimage_env = os.environ.get("APPIMAGE")
        current = Path(appimage_env).resolve() if appimage_env else Path(sys.executable).resolve()
        appimage_path.chmod(0o755)
        try:
            # Unlink before replacing: opening an in-use executable for writing
            # raises ETXTBSY on Linux. Removing the directory entry lets the
            # kernel keep the old inode alive for the running process while we
            # place the new file at that path.
            if current.exists():
                current.unlink()
            shutil.move(str(appimage_path), str(current))
        except PermissionError:
            raise OSError(
                f"Cannot replace {current} — check that you own the file or have write permission."
            )

    def _apply_macos(self, zip_path: Path) -> None:
        """
        Extract the updated .app bundle from the downloaded ZIP and replace
        the running application bundle in /Applications (or current bundle dir).
        """
        import zipfile
        current_app = None
        for p in Path(sys.executable).parents:
            if p.suffix == ".app":
                current_app = p
                break

        if not current_app or not current_app.exists():
            raise OSError(f"Could not locate the .app bundle for {sys.executable}")

        extract_dir = zip_path.parent / "extracted"
        extract_dir.mkdir(exist_ok=True)

        try:
            subprocess.run(["ditto", "-xk", str(zip_path), str(extract_dir)], check=True)
        except Exception:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

        new_app = extract_dir / "PrismDesktop.app"
        if not new_app.exists():
            apps = list(extract_dir.glob("*.app"))
            if apps:
                new_app = apps[0]
            else:
                raise OSError("Downloaded archive did not contain PrismDesktop.app")

        # Strip quarantine flag and ad-hoc sign new .app
        try:
            subprocess.run(["xattr", "-cr", str(new_app)], check=False)
            subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(new_app)], check=False)
        except Exception:
            pass

        # Move/replace current .app bundle
        backup_app = current_app.with_suffix(".app.old")
        if backup_app.exists():
            shutil.rmtree(backup_app, ignore_errors=True)

        # Two moves, and the window between them is the dangerous part: if the
        # second fails the bundle has already been moved aside, so without a
        # rollback a failed update leaves the user with no application at all.
        # Restore the backup before reporting the failure.
        try:
            shutil.move(str(current_app), str(backup_app))
        except OSError as exc:
            raise OSError(
                f"Cannot replace {current_app} — check that you own the bundle "
                f"or have write permission ({exc})."
            ) from exc

        try:
            shutil.move(str(new_app), str(current_app))
        except OSError as exc:
            try:
                shutil.move(str(backup_app), str(current_app))
            except OSError:
                # Both the install and the rollback failed. Say exactly where
                # the old bundle is: it is still on disk and the user can move
                # it back by hand, which is much better than "update failed"
                # next to an empty /Applications entry.
                raise OSError(
                    f"Update failed and the previous version could not be "
                    f"restored automatically. Your app is still on disk at "
                    f"{backup_app} — rename it back to {current_app.name} to "
                    f"recover ({exc})."
                ) from exc
            raise OSError(
                f"Update failed; the previous version has been restored ({exc})."
            ) from exc

        shutil.rmtree(backup_app, ignore_errors=True)


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
    write_update_flag()
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("linux"):
            # New AppImage is in place at $APPIMAGE; re-exec from there.
            # sys.executable points into the /tmp squashfs mount, not the file.
            appimage = os.environ.get("APPIMAGE") or sys.executable
            subprocess.Popen([appimage])
        elif sys.platform == "darwin":
            current_app = None
            for p in Path(sys.executable).parents:
                if p.suffix == ".app":
                    current_app = p
                    break
            if current_app:
                subprocess.Popen(["open", "-n", str(current_app)])
            else:
                subprocess.Popen([sys.executable])
        else:
            # Windows: the installer (spawned in _apply_windows) relaunches the
            # app itself once it's done; don't race it by launching here too.
            logger.info("Quitting so the Windows installer can relaunch the app")
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    QApplication.quit()
