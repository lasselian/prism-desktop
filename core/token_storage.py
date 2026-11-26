"""
Secure Token Storage

Storage: OS keyring (GNOME Keyring, macOS Keychain, Windows Credential Locker)
with an encrypted-file fallback: DPAPI on Windows, Fernet + machine-derived key
elsewhere (and on Windows if DPAPI fails). A legacy Windows file in the Fernet
format is migrated to DPAPI when it is read. A successful keyring store deletes
the fallback file, so on load the file — when present — is always the freshest
copy and is checked first.

The module probes the keyring once on first use and caches the result.
"""

import functools
import os
import sys
import logging
import platform
from pathlib import Path

# On macOS, we never use the OS Keychain (ad-hoc signed apps trigger
# SecurityAgent password prompts). Skip importing keyring entirely to
# avoid any accidental Keychain interaction.
if sys.platform != "darwin":
    import keyring
else:
    keyring = None  # type: ignore[assignment]
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

    # On macOS, ad-hoc signed applications trigger SecurityAgent password dialogs
    # after every update because the code signature changes between builds, so the
    # Keychain is unusable without a paid Developer ID certificate. We use the
    # encrypted-file fallback (Fernet, key derived from IOPlatformUUID) instead.
    # NOTE: this is weaker than the Keychain — the key material is readable by any
    # process running as the user — it protects the token at rest, not from local
    # malware. Documented in README (macOS section).
    if sys.platform == "darwin":
        _keyring_probed = True
        _keyring_available = False
        logger.info("[TokenStorage] macOS ad-hoc build: Keychain disabled, using encrypted file storage.")
        return False

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
      - macOS:   IOPlatformUUID
      - Fallback: hostname + username (stable but less unique)

    The result must never change for a given machine: it keys the token file.
    """
    reader = _MACHINE_ID_READERS.get(sys.platform)
    seed = reader() if reader else None
    return (seed or _host_and_user()).encode("utf-8")


def _linux_machine_id() -> str | None:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            seed = Path(path).read_text().strip()
        except Exception:
            continue
        if seed:
            return seed
    return None


def _windows_machine_guid() -> str | None:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        )
        seed, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return seed
    except Exception:
        return None


def _macos_platform_uuid() -> str | None:
    try:
        import subprocess

        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2]
    except Exception:
        pass
    return None


def _host_and_user() -> str:
    # os.getlogin() can raise OSError on macOS GUI apps launched without a
    # controlling terminal (e.g. from Finder / LaunchServices). Fall back
    # to os.getuid() which always works.
    try:
        login = os.getlogin()
    except OSError:
        login = str(os.getuid())
    return f"{platform.node()}:{login}"


_MACHINE_ID_READERS = {
    "linux": _linux_machine_id,
    "win32": _windows_machine_guid,
    "darwin": _macos_platform_uuid,
}


def _write_private(path: Path, data: bytes) -> None:
    """Atomically write `data` to `path`, readable by the current user only.

    A Fernet key is derived from the (world-readable) machine id plus the salt,
    so for those files permissions are what actually keep other local users out.
    """
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _tighten(path: Path) -> None:
    """Best effort: make a pre-existing token file private (older versions wrote 0644)."""
    try:
        if path.exists() and os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass


@functools.lru_cache(maxsize=4)
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


# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------
# On Windows the encrypted file is sealed with DPAPI (CryptProtectData) instead
# of a key derived from MachineGuid, which any local program can read. DPAPI
# keys come from the user's Windows login, so the file only opens for this
# user. It still opens for any program running as this user: like the keyring,
# it protects the token at rest, not from local malware.

# Marks a DPAPI file. Fernet tokens are base64 ("gAAAA..."), so they never
# start with this.
_DPAPI_MAGIC = b"PRISM-DPAPI1\n"
_DPAPI_ENTROPY = b"PrismDesktop/ha_token/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _dpapi_call(func_name: str, data: bytes) -> bytes:
    """Run CryptProtectData or CryptUnprotectData on `data`. Raises OSError."""
    # The platform check also tells type checkers on other systems to skip the
    # Windows-only ctypes names below.
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes):
        # The buffer is returned too: it must outlive the call that reads it.
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = getattr(crypt32, func_name)
    func.argtypes = [
        ctypes.POINTER(DataBlob),  # input
        ctypes.c_void_p,  # description
        ctypes.POINTER(DataBlob),  # entropy
        ctypes.c_void_p,  # reserved
        ctypes.c_void_p,  # prompt
        wintypes.DWORD,  # flags
        ctypes.POINTER(DataBlob),  # output
    ]
    func.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    data_in, _keep_in = blob(data)
    entropy, _keep_entropy = blob(_DPAPI_ENTROPY)
    data_out = DataBlob()
    if not func(
        ctypes.byref(data_in),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(data_out.pbData, ctypes.c_void_p))


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_call("CryptProtectData", data)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_call("CryptUnprotectData", data)


def _enc_store(token: str) -> None:
    """Encrypt and write the token to disk."""
    enc_dir = _get_enc_dir()
    salt_path = enc_dir / _SALT_FILENAME
    enc_path = enc_dir / _ENC_FILENAME

    if sys.platform == "win32":
        try:
            sealed = _DPAPI_MAGIC + _dpapi_protect(token.encode("utf-8"))
        except OSError as e:
            logger.warning(f"[TokenStorage] DPAPI unavailable ({e}), using machine-key encryption.")
        else:
            _write_private(enc_path, sealed)
            try:
                salt_path.unlink()  # only the Fernet format needs a salt
            except OSError:
                pass  # none there, or locked: a leftover salt is harmless
            logger.info("[TokenStorage] Token stored in DPAPI-encrypted file.")
            return

    # Load or create salt
    if salt_path.exists():
        salt = salt_path.read_bytes()
        _tighten(salt_path)
    else:
        salt = os.urandom(16)
        _write_private(salt_path, salt)

    key = _derive_key(salt)
    f = Fernet(key)
    _write_private(enc_path, f.encrypt(token.encode("utf-8")))
    logger.info("[TokenStorage] Token stored in encrypted file.")


def _enc_load() -> str:
    """Load and decrypt the token from disk. Returns empty string on failure."""
    enc_dir = _get_enc_dir()
    salt_path = enc_dir / _SALT_FILENAME
    enc_path = enc_dir / _ENC_FILENAME

    if not enc_path.exists():
        return ""

    try:
        _tighten(enc_path)
        data = enc_path.read_bytes()
        if data.startswith(_DPAPI_MAGIC):
            if sys.platform != "win32":
                logger.error(
                    "[TokenStorage] The token file is sealed with Windows DPAPI and cannot be decrypted "
                    "on this platform."
                )
                return ""
            return _dpapi_unprotect(data[len(_DPAPI_MAGIC) :]).decode("utf-8")

        if not salt_path.exists():
            return ""
        _tighten(salt_path)
        key = _derive_key(salt_path.read_bytes())
        token = Fernet(key).decrypt(data).decode("utf-8")
    except (InvalidToken, Exception):
        logger.exception("[TokenStorage] Failed to decrypt token file")
        return ""

    if sys.platform == "win32" and token:
        # Older versions sealed the file with the MachineGuid-derived key;
        # re-seal it with DPAPI now that we can read it.
        try:
            _enc_store(token)
        except OSError as e:
            logger.warning(f"[TokenStorage] Could not move token file to DPAPI: {e}")
    return token


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


def clear_token() -> None:
    """Remove the stored HA token from every backend (keyring and encrypted file).

    Only call this for an explicit user action: load_token() returns "" when a
    backend is merely unavailable, so clearing on an empty value would destroy
    a token that is still there.
    """
    _enc_delete()
    if _probe_keyring():
        try:
            keyring.delete_password(SERVICE_NAME, KEY_TOKEN)
        except Exception as e:
            # PasswordDeleteError just means there was no entry.
            logger.debug(f"[TokenStorage] Keyring delete skipped: {e}")


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
