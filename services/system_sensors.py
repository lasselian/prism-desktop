"""
Extra computer sensors for the Prism Desktop device in Home Assistant.

Two toggleable groups:
  system   — battery, charging, disk usage, CPU temperature, last boot
  presence — idle time, screen locked, microphone in use, camera in use

Every collector returns None when the value can't be read on this machine
(no battery on a desktop, no lock-state API on this OS, helper tool missing).
Sensors that return None at startup are never registered, and None values are
never pushed, so HA doesn't fill up with permanently "unknown" entities.

Collectors block (subprocess / /proc scans) — call collect() from an executor.
"""

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

import psutil

logger = logging.getLogger(__name__)

SYSTEM_GROUP = "system"
PRESENCE_GROUP = "presence"

# Registered with HA via services.mobile_app.SENSORS. Keys must be accepted by
# the mobile_app register_sensor schema, so no custom fields in here.
SENSOR_DEFS = [
    {
        "type": "sensor",
        "unique_id": "battery_level",
        "name": "Battery Level",
        "device_class": "battery",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "state": None,
    },
    {
        "type": "binary_sensor",
        "unique_id": "battery_charging",
        "name": "Battery Charging",
        "device_class": "battery_charging",
        "entity_category": "diagnostic",
        "state": None,
    },
    {
        "type": "sensor",
        "unique_id": "disk_usage",
        "name": "Disk Usage",
        "icon": "mdi:harddisk",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "state": None,
    },
    {
        "type": "sensor",
        "unique_id": "cpu_temperature",
        "name": "CPU Temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "state": None,
    },
    {
        "type": "sensor",
        "unique_id": "last_boot",
        "name": "Last Boot",
        "device_class": "timestamp",
        "entity_category": "diagnostic",
        "state": None,
    },
    {
        "type": "sensor",
        "unique_id": "idle_time",
        "name": "Idle Time",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "measurement",
        "icon": "mdi:timer-sand",
        "state": None,
    },
    {
        "type": "binary_sensor",
        "unique_id": "screen_locked",
        "name": "Screen Locked",
        "icon": "mdi:monitor-lock",
        "state": None,
    },
    {
        "type": "binary_sensor",
        "unique_id": "mic_in_use",
        "name": "Microphone In Use",
        "icon": "mdi:microphone",
        "state": None,
    },
    {
        "type": "binary_sensor",
        "unique_id": "camera_in_use",
        "name": "Camera In Use",
        "icon": "mdi:webcam",
        "state": None,
    },
]

GROUPS = {
    SYSTEM_GROUP: ("battery_level", "battery_charging", "disk_usage", "cpu_temperature", "last_boot"),
    PRESENCE_GROUP: ("idle_time", "screen_locked", "mic_in_use", "camera_in_use"),
}

# Binary presence sensors are cheap and automations care about the transition,
# so the metrics loop polls these often and pushes only on change.
FAST_SENSORS = ("screen_locked", "mic_in_use", "camera_in_use")


def _run(cmd: list, timeout: float = 2.0) -> Optional[str]:
    """Run a helper command; None if it's missing, fails or times out."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


# ── system group ──────────────────────────────────────────────────────────────


def _battery():
    try:
        return psutil.sensors_battery()
    except Exception:
        return None


def get_battery_level() -> Optional[float]:
    b = _battery()
    return round(b.percent, 1) if b else None


def get_battery_charging() -> Optional[bool]:
    b = _battery()
    if not b or b.power_plugged is None:
        return None
    return bool(b.power_plugged)


def get_disk_usage() -> Optional[float]:
    try:
        return round(psutil.disk_usage(os.path.abspath(os.sep)).percent, 1)
    except Exception:
        return None


# Preferred hwmon chips, in order. Falls back to the first chip with a reading.
_CPU_TEMP_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")


def get_cpu_temperature() -> Optional[float]:
    if not hasattr(psutil, "sensors_temperatures"):  # Windows / macOS builds
        return None
    try:
        chips = psutil.sensors_temperatures()
    except Exception:
        return None
    for name in _CPU_TEMP_CHIPS:
        readings = chips.get(name)
        if readings:
            # Package/Tctl/first sensor is listed first on the chips above
            return round(readings[0].current, 1)
    return None


def get_last_boot() -> Optional[str]:
    try:
        return datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()
    except Exception:
        return None


# ── presence group ────────────────────────────────────────────────────────────


def get_idle_time() -> Optional[int]:
    """Seconds since the last keyboard/mouse input."""
    if sys.platform == "win32":
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return int(((ticks - info.dwTime) & 0xFFFFFFFF) / 1000)

    if sys.platform == "darwin":
        out = _run(["ioreg", "-c", "IOHIDSystem"])
        if out:
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    try:
                        return int(int(line.split("=")[-1].strip()) / 1_000_000_000)
                    except ValueError:
                        return None
        return None

    # Linux: GNOME/Mutter exposes idle time on the session bus (works on Wayland).
    out = _run(
        [
            "busctl",
            "--user",
            "call",
            "org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor",
            "GetIdletime",
        ]
    )
    if out and out.startswith("t "):
        try:
            return int(out.split()[1]) // 1000
        except ValueError:
            return None
    # X11 sessions on other desktops
    out = _run(["xprintidle"])
    if out:
        try:
            return int(out.strip()) // 1000
        except ValueError:
            return None
    return None


def get_screen_locked() -> Optional[bool]:
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        # The input desktop is the secure Winlogon desktop while locked; a normal
        # process can't open or switch to it.
        desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desk:
            return True
        try:
            return not bool(user32.SwitchDesktop(desk))
        finally:
            user32.CloseDesktop(desk)

    if sys.platform == "darwin":
        out = _run(["ioreg", "-n", "Root", "-d1"])
        if out is None:
            return None
        return '"CGSSessionScreenIsLocked"=Yes' in out.replace(" ", "")

    # Linux: set by GNOME, KDE and others through logind
    out = _run(["loginctl", "show-session", "auto", "-p", "LockedHint", "--value"])
    if out is None:
        return None
    return out.strip() == "yes"


def _windows_consent_in_use(capability: str) -> Optional[bool]:
    """Windows records mic/camera use per app; a start time with no stop time means active."""
    import winreg

    base = rf"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\{capability}"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return None

    def active(key) -> bool:
        try:
            start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
            stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
        except OSError:
            return False
        return start > 0 and stop == 0

    def scan(parent) -> bool:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(parent, i)
            except OSError:
                return False
            i += 1
            try:
                with winreg.OpenKey(parent, name) as sub:
                    if name == "NonPackaged":
                        if scan(sub):
                            return True
                    elif active(sub):
                        return True
            except OSError:
                continue

    with root:
        return scan(root)


def get_mic_in_use() -> Optional[bool]:
    if sys.platform == "win32":
        return _windows_consent_in_use("microphone")
    if sys.platform == "darwin":
        return None
    # PipeWire (pipewire-pulse) and PulseAudio both list active capture streams
    out = _run(["pactl", "list", "short", "source-outputs"])
    if out is None:
        return None
    return bool(out.strip())


def get_camera_in_use() -> Optional[bool]:
    if sys.platform == "win32":
        return _windows_consent_in_use("webcam")
    if sys.platform == "darwin":
        return None
    # Linux: any process of ours holding a /dev/video* node open. Only sees
    # processes owned by the current user, which is where cameras get opened.
    devices = [d for d in os.listdir("/dev") if d.startswith("video")] if os.path.isdir("/dev") else []
    if not devices:
        return None
    me = os.getpid()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit() and int(p) != me]
    except OSError:
        return None
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}").startswith("/dev/video"):
                        return True
                except OSError:
                    continue
        except OSError:
            continue  # process gone, or not ours
    return False


COLLECTORS: dict[str, Callable[[], object]] = {
    "battery_level": get_battery_level,
    "battery_charging": get_battery_charging,
    "disk_usage": get_disk_usage,
    "cpu_temperature": get_cpu_temperature,
    "last_boot": get_last_boot,
    "idle_time": get_idle_time,
    "screen_locked": get_screen_locked,
    "mic_in_use": get_mic_in_use,
    "camera_in_use": get_camera_in_use,
}


def collect(unique_ids: Iterable[str]) -> dict:
    """Read the given sensors, dropping any that are unavailable or raise."""
    states = {}
    for uid in unique_ids:
        fn = COLLECTORS.get(uid)
        if not fn:
            continue
        try:
            value = fn()
        except Exception as e:
            logger.debug(f"[SystemSensors] {uid} failed: {e}")
            continue
        if value is not None:
            states[uid] = value
    return states


def enabled_sensor_ids(mobile_cfg: dict) -> set:
    """Sensor ids switched on by the send_system / send_presence settings."""
    ids = set()
    if mobile_cfg.get("send_system", False):
        ids.update(GROUPS[SYSTEM_GROUP])
    if mobile_cfg.get("send_presence", False):
        ids.update(GROUPS[PRESENCE_GROUP])
    return ids
