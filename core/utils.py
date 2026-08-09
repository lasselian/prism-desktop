import sys
import os
import platform
from pathlib import Path

import certifi

_LINUX_CA_BUNDLES = [
    "/etc/ssl/certs/ca-certificates.crt",  # Debian / Ubuntu / Arch
    "/etc/pki/tls/certs/ca-bundle.crt",    # RHEL / CentOS / Fedora
    "/etc/ssl/ca-bundle.pem",              # openSUSE
    "/etc/pki/tls/cert.pem",              # Amazon Linux
]

def configure_ssl() -> None:
    """Pin the CA bundle used for TLS verification (aiohttp/requests).

    Linux gets the live system bundle. Windows has no equivalent static
    bundle to prefer - Python's ssl module instead sources trust from the
    Windows CryptoAPI store, which is populated lazily by Windows' automatic
    root-cert update and can be stale or incomplete on a given machine, so
    fall back to the certifi bundle there (and on macOS).
    """
    system_bundle = os.environ.get("SSL_CERT_FILE")
    if not system_bundle and platform.system() == "Linux":
        for path in _LINUX_CA_BUNDLES:
            if Path(path).exists():
                system_bundle = path
                break
    if not system_bundle:
        system_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", system_bundle)      # ssl module (aiohttp/websockets)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", system_bundle)  # requests / urllib3


def get_system_font() -> str:
    """Get the appropriate system UI font for the current platform."""
    system = platform.system()
    if system == 'Windows':
        return 'Segoe UI'
    elif system == 'Darwin':
        return 'SF Pro Display'
    else:  # Linux
        return 'Ubuntu'

SYSTEM_FONT = get_system_font()

def get_resource_path(relative_path: str) -> Path:
    """Get absolute path to a resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller extracts to a temp folder and sets _MEIPASS
        base_path = sys._MEIPASS
        return Path(base_path) / relative_path
    except Exception:
        return Path(__file__).parent.parent / relative_path


def get_platform_config_dir() -> Path:
    """Get the appropriate config directory for each platform."""
    system = platform.system()

    if system == 'Windows':
        return Path(os.getenv('APPDATA', Path.home())) / "PrismDesktop"
    elif system == 'Darwin':
        return Path.home() / "Library" / "Application Support" / "PrismDesktop"
    else:
        # XDG compliant
        xdg_config = os.getenv('XDG_CONFIG_HOME', str(Path.home() / '.config'))
        return Path(xdg_config) / "PrismDesktop"


def get_config_path(filename: str = "config.json") -> Path:
    """Get absolute path to the config file: dev source dir, portable dir next
    to the exe, or the platform-specific config dir, in that priority."""
    if getattr(sys, 'frozen', False):
        exe_path = Path(sys.executable).parent
        portable_config = exe_path / filename

        # Portable Mode: config exists next to the exe
        if portable_config.exists():
            return portable_config

        # Installed Mode: fall back to the platform config directory
        app_data = get_platform_config_dir()
        app_data.mkdir(parents=True, exist_ok=True)
        return app_data / filename
    else:
        return Path(__file__).parent.parent / filename
