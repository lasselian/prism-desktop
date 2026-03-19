"""
Location Manager for Prism Desktop.
Provides a unified get_location() API that dispatches to:
  - Windows: WinRT Geolocation API
  - Linux: GeoClue2 via D-Bus (dbus-next)
"""

import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows imports (guarded)
# ---------------------------------------------------------------------------
_WINRT_AVAILABLE = False
if sys.platform == 'win32':
    try:
        from winrt.windows.devices.geolocation import (
            Geolocator,
            GeolocationAccessStatus,
            PositionAccuracy,
        )
        _WINRT_AVAILABLE = True
    except ImportError:
        _WINRT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Linux imports (guarded)
# ---------------------------------------------------------------------------
_DBUS_NEXT_AVAILABLE = False
if sys.platform == 'linux':
    try:
        from dbus_next.aio import MessageBus
        from dbus_next import BusType, Variant
        _DBUS_NEXT_AVAILABLE = True
    except ImportError:
        _DBUS_NEXT_AVAILABLE = False


# ===================================================================
# Public API
# ===================================================================

async def get_location() -> Optional[dict]:
    """
    Unified location getter.

    Returns a dict suitable for the HA mobile app update_location webhook:
        {"gps": [latitude, longitude], "gps_accuracy": accuracy_metres}
    Returns None if location is unavailable or the platform is unsupported.
    """
    if sys.platform == 'win32':
        return await _get_windows_location()
    elif sys.platform == 'linux':
        return await _get_linux_location()
    else:
        logger.warning("[Location] Unsupported platform: %s", sys.platform)
        return None


async def is_geoclue2_available() -> bool:
    """Probe the system D-Bus for GeoClue2. Returns True if reachable."""
    if not _DBUS_NEXT_AVAILABLE:
        return False
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await bus.introspect(
            'org.freedesktop.GeoClue2',
            '/org/freedesktop/GeoClue2/Manager',
        )
        bus.disconnect()
        return True
    except Exception:
        return False


def ensure_desktop_file() -> None:
    """
    Create ~/.local/share/applications/prism-desktop.desktop if it does not
    exist.  GeoClue2 requires a matching .desktop file for the DesktopId
    property or it will refuse to provide location data.
    """
    desktop_dir = Path.home() / '.local' / 'share' / 'applications'
    desktop_file = desktop_dir / 'prism-desktop.desktop'

    if desktop_file.exists():
        return

    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Prism Desktop\n"
        "Comment=Home Assistant Tray Application\n"
        "Exec=prism-desktop\n"
        "Icon=prism-desktop\n"
        "Categories=Utility;\n"
        "Terminal=false\n"
    )
    logger.info("[Location] Created %s for GeoClue2 authorization", desktop_file)


def get_distro_info() -> dict:
    """Parse /etc/os-release and return {"id": "ubuntu", "name": "Ubuntu"}."""
    info = {"id": "linux", "name": "Linux"}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    info["id"] = line.strip().split("=", 1)[1].strip('"')
                elif line.startswith("NAME="):
                    info["name"] = line.strip().split("=", 1)[1].strip('"')
    except FileNotFoundError:
        pass
    return info


def get_geoclue2_install_hint(distro_id: str) -> str:
    """Return a distro-specific install command for GeoClue2."""
    hints = {
        "ubuntu": "sudo apt install geoclue-2.0",
        "debian": "sudo apt install geoclue-2.0",
        "fedora": "sudo dnf install geoclue2",
        "arch": "sudo pacman -S geoclue",
        "manjaro": "sudo pacman -S geoclue",
        "opensuse-leap": "sudo zypper install geoclue2",
        "opensuse-tumbleweed": "sudo zypper install geoclue2",
    }
    return hints.get(
        distro_id,
        "Install the 'geoclue2' package using your distribution's package manager",
    )


# ===================================================================
# Windows backend
# ===================================================================

async def _get_windows_location() -> Optional[dict]:
    """Fetch location via the Windows Runtime Geolocation API."""
    if not _WINRT_AVAILABLE:
        logger.warning(
            "[Location] winrt-Windows.Devices.Geolocation not installed "
            "— location unavailable"
        )
        return None

    try:
        access_status = await Geolocator.request_access_async()
        if access_status != GeolocationAccessStatus.ALLOWED:
            logger.warning(
                "[Location] Access denied (status=%s). "
                "Enable location in Windows Settings → Privacy → Location.",
                access_status,
            )
            return None

        geolocator = Geolocator()
        geolocator.desired_accuracy = PositionAccuracy.HIGH

        position = await geolocator.get_geoposition_async()
        coord = position.coordinate

        lat = coord.latitude
        lon = coord.longitude
        accuracy = coord.accuracy  # metres

        logger.info("[Location] Fix acquired: (%.5f, %.5f) ±%.0fm", lat, lon, accuracy)
        return {"gps": [lat, lon], "gps_accuracy": accuracy}

    except Exception as e:
        logger.warning("[Location] Failed to get position: %s", e)
        return None


# ===================================================================
# Linux backend (GeoClue2 via D-Bus)
# ===================================================================

async def _get_linux_location() -> Optional[dict]:
    """Fetch location via GeoClue2 over the system D-Bus."""
    if not _DBUS_NEXT_AVAILABLE:
        logger.warning(
            "[Location] dbus-next not installed — location unavailable. "
            "Install with: pip install dbus-next"
        )
        return None

    try:
        return await _geoclue2_get_position()
    except Exception as e:
        logger.warning("[Location] GeoClue2 failed: %s", e)
        return None


async def _geoclue2_get_position() -> Optional[dict]:
    """
    Core GeoClue2 D-Bus flow:
      1. Connect to system bus
      2. Get a Client from the Manager
      3. Set DesktopId + accuracy level
      4. Start the client, wait for LocationUpdated signal
      5. Read lat/lon/accuracy from the Location object
      6. Stop and disconnect
    """
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        # --- Manager proxy ---
        manager_introspection = await bus.introspect(
            'org.freedesktop.GeoClue2',
            '/org/freedesktop/GeoClue2/Manager',
        )
        manager_proxy = bus.get_proxy_object(
            'org.freedesktop.GeoClue2',
            '/org/freedesktop/GeoClue2/Manager',
            manager_introspection,
        )
        manager = manager_proxy.get_interface('org.freedesktop.GeoClue2.Manager')

        # --- Create client ---
        client_path = await manager.call_get_client()

        client_introspection = await bus.introspect(
            'org.freedesktop.GeoClue2', client_path,
        )
        client_proxy = bus.get_proxy_object(
            'org.freedesktop.GeoClue2', client_path, client_introspection,
        )
        client = client_proxy.get_interface('org.freedesktop.GeoClue2.Client')
        client_props = client_proxy.get_interface('org.freedesktop.DBus.Properties')

        # --- Configure client ---
        # DesktopId must match the .desktop filename (without .desktop extension)
        await client_props.call_set(
            'org.freedesktop.GeoClue2.Client',
            'DesktopId',
            Variant('s', 'prism-desktop'),
        )
        # RequestedAccuracyLevel: 8 = EXACT (GPS-level / best available)
        await client_props.call_set(
            'org.freedesktop.GeoClue2.Client',
            'RequestedAccuracyLevel',
            Variant('u', 8),
        )

        # --- Listen for location update ---
        location_future = asyncio.get_event_loop().create_future()

        def on_location_updated(old_path: str, new_path: str):
            if not location_future.done():
                location_future.set_result(new_path)

        client.on_location_updated(on_location_updated)

        # --- Start and wait ---
        await client.call_start()
        new_location_path = await asyncio.wait_for(location_future, timeout=30)

        # --- Read location properties ---
        loc_introspection = await bus.introspect(
            'org.freedesktop.GeoClue2', new_location_path,
        )
        loc_proxy = bus.get_proxy_object(
            'org.freedesktop.GeoClue2', new_location_path, loc_introspection,
        )
        loc = loc_proxy.get_interface('org.freedesktop.GeoClue2.Location')

        lat = await loc.get_latitude()
        lon = await loc.get_longitude()
        accuracy = await loc.get_accuracy()

        # --- Stop client ---
        await client.call_stop()

        logger.info("[Location] Fix acquired: (%.5f, %.5f) ±%.0fm", lat, lon, accuracy)
        return {"gps": [lat, lon], "gps_accuracy": accuracy}

    finally:
        bus.disconnect()
