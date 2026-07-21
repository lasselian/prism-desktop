"""
Secure Token Storage

Storage: OS keyring (GNOME Keyring, macOS Keychain, Windows Credential Locker)
with an encrypted-file fallback (Fernet + machine-derived key). A successful
keyring store deletes the fallback file, so on load the file — when present —
is always the freshest copy and is checked first.

The module probes the keyring once on first use and caches the result.
"""

import os
import sys
import logging
import platform
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

from core.utils import get_config_path

logger = logging.getLogger(__name__)

SERVICE_NAME = "PrismDesktop"
KEY_TOKEN = "ha_token"

# File names for encrypted fallback (stored next to config.json)
_ENC_FILENAME = ".ha_token.enc"
_SALT_FILENAME = ".ha_token.salt"

# Cached keyring probe result
_keyring_probed = False
_keyring_available = False


# ---------------------------------------------------------------------------
# Keyring probe
# ---------------------------------------------------------------------------

def _probe_keyring() -> bool:
    """Test whether the OS keyring actually works by doing a write/read/delete cycle."""
    global _keyring_probed, _keyring_available
    if _keyring_probed:
        return _keyring_available

    _keyring_probed = True
    probe_key = "__prism_probe__"
    probe_val = "probe_test"

    try:
        backend = keyring.get_keyring()
        logger.info(f"[TokenStorage] Keyring backend: {type(backend).__name__}")

        keyring.set_password(SERVICE_NAME, probe_key, probe_val)
        result = keyring.get_password(SERVICE_NAME, probe_key)

        if result == probe_val:
            # Clean up probe value
            try:
                keyring.delete_password(SERVICE_NAME, probe_key)
            except Exception:
                pass  # delete not supported on all backends
            _keyring_available = True
            logger.info("[TokenStorage] Keyring probe successful — using OS keyring.")
        else:
            _keyring_available = False
            logger.warning("[TokenStorage] Keyring probe failed (read-back mismatch) — using encrypted file.")
    except Exception as e:
        _keyring_available = False
        logger.warning(f"[TokenStorage] Keyring probe failed ({e}) — using encrypted file.")

    return _keyring_available


# ---------------------------------------------------------------------------
# Encrypted file helpers
# ---------------------------------------------------------------------------

def _get_enc_dir() -> Path:
    """Get the directory where encrypted token files are stored (same as config.json)."""
    return get_config_path().parent


def _get_machine_seed() -> bytes:
    """
    Derive a machine-specific seed for key generation.
    
    Uses (in order of preference):
      - Linux:   /etc/machine-id
      - Windows: MachineGuid from the registry
      - Fallback: hostname + username (stable but less unique)
    """
    seed = None

    if sys.platform == "linux":
        try:
            seed = Path("/etc/machine-id").read_text().strip()
        except Exception:
            pass
        if not seed:
            try:
                seed = Path("/var/lib/dbus/machine-id").read_text().strip()
            except Exception:
                pass

    elif sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            seed, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
        except Exception:
            pass

    elif sys.platform == "darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    seed = line.split('"')[-2]
                    break
        except Exception:
            pass

    if not seed:
        seed = f"{platform.node()}:{os.getlogin()}"

    return seed.encode("utf-8")


def _derive_key(salt: bytes) -> bytes:
    """Derive a Fernet key from the machine seed + salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    raw = kdf.derive(_get_machine_seed())
    return base64.urlsafe_b64encode(raw)


def _enc_store(token: str) -> None:
    """Encrypt and write the token to disk."""
    enc_dir = _get_enc_dir()
    salt_path = enc_dir / _SALT_FILENAME
    enc_path = enc_dir / _ENC_FILENAME

    # Load or create salt
    if salt_path.exists():
        salt = salt_path.read_bytes()
    else:
        salt = os.urandom(16)
        salt_path.write_bytes(salt)

    key = _derive_key(salt)
    f = Fernet(key)
    enc_path.write_bytes(f.encrypt(token.encode("utf-8")))
    logger.info("[TokenStorage] Token stored in encrypted file.")


def _enc_load() -> str:
    """Load and decrypt the token from disk. Returns empty string on failure."""
    enc_dir = _get_enc_dir()
    salt_path = enc_dir / _SALT_FILENAME
    enc_path = enc_dir / _ENC_FILENAME

    if not enc_path.exists() or not salt_path.exists():
        return ""

    try:
        salt = salt_path.read_bytes()
        key = _derive_key(salt)
        f = Fernet(key)
        return f.decrypt(enc_path.read_bytes()).decode("utf-8")
    except (InvalidToken, Exception) as e:
        logger.error(f"[TokenStorage] Failed to decrypt token file: {e}")
        return ""


def _enc_delete() -> None:
    """Remove encrypted token files from disk."""
    enc_dir = _get_enc_dir()
    for name in (_ENC_FILENAME, _SALT_FILENAME):
        path = enc_dir / name
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_token(token: str) -> None:
    """Store the HA token securely. Tries keyring first, then encrypted file."""
    if not token:
        return

    if _probe_keyring():
        try:
            keyring.set_password(SERVICE_NAME, KEY_TOKEN, token)
            # Clean up any leftover encrypted file from a previous fallback
            _enc_delete()
            return
        except Exception as e:
            logger.warning(f"[TokenStorage] Keyring write failed ({e}), falling back to encrypted file.")
            # Best effort: clear the old keyring entry so it can never shadow
            # the fresher token we're about to write to the encrypted file.
            try:
                keyring.delete_password(SERVICE_NAME, KEY_TOKEN)
            except Exception:
                pass

    _enc_store(token)


def load_token() -> str:
    """
    Load the HA token.

    The encrypted file is checked first: a successful keyring store always
    deletes it, so if it exists the most recent store fell back to the file
    and any keyring entry is stale (e.g. written in an earlier session when
    the keyring still worked).
    """
    token = _enc_load()
    if token:
        return token

    if _probe_keyring():
        try:
            token = keyring.get_password(SERVICE_NAME, KEY_TOKEN)
            if token:
                return token
        except Exception as e:
            logger.warning(f"[TokenStorage] Keyring read failed: {e}")

    return ""
