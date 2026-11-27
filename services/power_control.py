"""
PC Power Control for Prism Desktop.

Allows Home Assistant to shut down, restart, sleep, hibernate, or lock this PC
via mobile_app push commands (`command_shutdown`, `command_restart`,
`command_sleep`, `command_hibernate`, `command_lock`). Every command is off by
default and must be explicitly enabled in config['power_control'].

Also provides one-click creation (and update) of a Home Assistant Template
Switch helper that powers the PC on via Wake-on-LAN and powers it off via
remote shutdown.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import psutil

from services.mobile_app import _get_device_name, register_mobile_app, register_sensors

logger = logging.getLogger(__name__)

POWER_ACTIONS: tuple[str, ...] = ("shutdown", "restart", "sleep", "hibernate", "lock")

# Actions that take this PC offline/suspended so HA should see logged-out immediately.
OFFLINE_ACTIONS: frozenset[str] = frozenset({"shutdown", "restart", "sleep", "hibernate"})

COMMAND_MESSAGES: dict[str, str] = {f"command_{action}": action for action in POWER_ACTIONS}

_MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")
_VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "docker",
    "br-",
    "veth",
    "virbr",
    "vmnet",
    "vboxnet",
    "tailscale",
    "wg",
    "utun",
    "tun",
    "tap",
    "cni",
    "flannel",
    "podman",
    "zt",
)

_LINUX_COMMANDS: dict[str, tuple[list[str], ...]] = {
    "shutdown": (["systemctl", "poweroff"], ["loginctl", "poweroff"], ["poweroff"]),
    "restart": (["systemctl", "reboot"], ["loginctl", "reboot"], ["reboot"]),
    "sleep": (["systemctl", "suspend"], ["loginctl", "suspend"]),
    "hibernate": (["systemctl", "hibernate"], ["loginctl", "hibernate"]),
    "lock": (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]),
}

_MACOS_COMMANDS: dict[str, list[str]] = {
    "shutdown": ["osascript", "-e", 'tell application "System Events" to shut down'],
    "restart": ["osascript", "-e", 'tell application "System Events" to restart'],
    "sleep": ["pmset", "sleepnow"],
    "lock": ["osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}'],
}

_WINDOWS_SUBPROCESS_COMMANDS: dict[str, list[str]] = {
    "shutdown": ["shutdown", "/s", "/t", "0"],
    "restart": ["shutdown", "/r", "/t", "0"],
    "hibernate": ["shutdown", "/h"],
}


@dataclass
class PowerSwitchResult:
    """Outcome of creating or updating the Home Assistant PC power switch."""

    ok: bool
    created: bool = False
    updated: bool = False
    entry_id: str = ""
    switch_name: str = ""
    mac: str = ""
    wol_available: bool = True
    error: str = ""


# ── Command parsing & permissions ─────────────────────────────────────────────


def parse_power_command(message: object) -> Optional[str]:
    """Map a mobile_app command message (e.g. 'command_shutdown') to an action name."""
    if not isinstance(message, str):
        return None
    return COMMAND_MESSAGES.get(message.strip().lower())


def is_action_supported(action: str, platform_name: Optional[str] = None) -> bool:
    """True when `action` is a valid power action supported on this OS."""
    if action not in POWER_ACTIONS:
        return False
    plat = platform_name or sys.platform
    if plat == "darwin" and action == "hibernate":
        return False
    return True


def is_action_allowed(config: dict, action: str) -> bool:
    """True only when `action` is valid and explicitly enabled in config['power_control']."""
    if action not in POWER_ACTIONS:
        return False
    power_cfg = config.get("power_control")
    if not isinstance(power_cfg, dict):
        return False
    return bool(power_cfg.get(f"allow_{action}", False))


# ── OS execution ──────────────────────────────────────────────────────────────


def _clean_linux_env() -> dict[str, str]:
    """Strip PyInstaller/AppImage library overrides before invoking system tools."""
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


def _run_command(cmd: list[str], env: Optional[dict[str, str]] = None) -> tuple[bool, str]:
    """Run a system power command with a bounded timeout."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": 10,
        "check": False,
    }
    if env is not None:
        kwargs["env"] = env
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    try:
        res = subprocess.run(cmd, **kwargs)
    except OSError as exc:
        return False, str(exc)
    except subprocess.SubprocessError as exc:
        return False, str(exc)

    if res.returncode == 0:
        return True, ""
    detail = (res.stderr or res.stdout or f"exit code {res.returncode}").strip()
    return False, detail


def _execute_windows(action: str) -> tuple[bool, str]:
    if action in _WINDOWS_SUBPROCESS_COMMANDS:
        return _run_command(_WINDOWS_SUBPROCESS_COMMANDS[action])
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False, "Windows API unavailable"
    if action == "lock":
        ok = bool(windll.user32.LockWorkStation())
        return (True, "") if ok else (False, "LockWorkStation failed")
    if action == "sleep":
        # bHibernate=False, bForce=False, bWakeupEventsDisabled=False
        ok = bool(windll.powrprof.SetSuspendState(0, 0, 0))
        return (True, "") if ok else (False, "SetSuspendState failed")
    return False, f"Unsupported action: {action}"


def _execute_linux(action: str) -> tuple[bool, str]:
    candidates = _LINUX_COMMANDS.get(action, ())
    env = _clean_linux_env()
    search_path = env.get("PATH")
    for cmd in candidates:
        exe = shutil.which(cmd[0], path=search_path)
        if exe:
            return _run_command([exe, *cmd[1:]], env=env)
    return False, f"No supported system command found for '{action}'"


def _execute_macos(action: str) -> tuple[bool, str]:
    if action == "hibernate":
        return False, "Hibernate is not supported on macOS"
    cmd = _MACOS_COMMANDS.get(action)
    if not cmd:
        return False, f"Unsupported action: {action}"
    return _run_command(cmd)


def execute_power_action(action: str, platform_name: Optional[str] = None) -> tuple[bool, str]:
    """Execute a local power action synchronously. Call from an executor thread."""
    if action not in POWER_ACTIONS:
        return False, f"Unknown power action: {action}"
    plat = platform_name or sys.platform
    logger.info(f"[PowerControl] Executing power action '{action}' on {plat}")
    if plat == "win32":
        return _execute_windows(action)
    if plat == "darwin":
        return _execute_macos(action)
    return _execute_linux(action)


# ── MAC address detection & validation ────────────────────────────────────────


def normalize_mac(raw: object) -> Optional[str]:
    """Validate and normalize a MAC address to uppercase colon-separated form."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not _MAC_RE.fullmatch(cleaned):
        return None
    normalized = cleaned.replace("-", ":").upper()
    if normalized in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        return None
    return normalized


def _route_local_ip(ha_url: str) -> Optional[str]:
    """Find the local IPv4 address used to route to Home Assistant (sends no traffic)."""
    if not ha_url:
        return None
    try:
        parts = urlsplit(ha_url)
        host = parts.hostname
        if not host:
            return None
        port = parts.port or (443 if parts.scheme == "https" else 80)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, port))
            return sock.getsockname()[0]
    except Exception:
        return None


def _is_virtual_iface(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(prefix) for prefix in _VIRTUAL_IFACE_PREFIXES)


def _iface_mac(addrs: list) -> Optional[str]:
    af_link = getattr(psutil, "AF_LINK", None)
    if af_link is None:
        return None
    for addr in addrs:
        if addr.family == af_link:
            mac = normalize_mac(addr.address)
            if mac:
                return mac
    return None


def _iface_ipv4s(addrs: list) -> list[str]:
    return [addr.address for addr in addrs if addr.family == socket.AF_INET and addr.address]


def detect_mac_address(ha_url: str = "") -> Optional[str]:
    """Detect the MAC address of the active NIC used to reach Home Assistant."""
    try:
        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()
    except Exception as exc:
        logger.warning(f"[PowerControl] Failed to enumerate network interfaces: {exc}")
        return None

    route_ip = _route_local_ip(ha_url)
    if route_ip:
        for addrs in if_addrs.values():
            if route_ip in _iface_ipv4s(addrs):
                mac = _iface_mac(addrs)
                if mac:
                    return mac

    fallback: Optional[str] = None
    for name, addrs in if_addrs.items():
        stat = if_stats.get(name)
        if stat is not None and not stat.isup:
            continue
        mac = _iface_mac(addrs)
        if not mac:
            continue
        ipv4s = [ip for ip in _iface_ipv4s(addrs) if not ip.startswith(("127.", "169.254."))]
        if ipv4s and not _is_virtual_iface(name):
            return mac
        if fallback is None and not _is_virtual_iface(name):
            fallback = mac
    return fallback


# ── Home Assistant switch creation / update ───────────────────────────────────


def slugify_ha(name: str) -> str:
    """Convert a device or entity name to a Home Assistant object_id slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "prism_desktop"


def resolve_notify_service(device_name: str, services: list[dict]) -> str:
    """Resolve this PC's `notify.mobile_app_*` service name from HA's service list."""
    expected = f"mobile_app_{slugify_ha(device_name)}"
    notify_services: dict[str, Any] = {}
    for entry in services:
        if isinstance(entry, dict) and entry.get("domain") == "notify":
            svc_map = entry.get("services")
            if isinstance(svc_map, dict):
                notify_services = svc_map
            break

    if expected in notify_services:
        return f"notify.{expected}"
    for name in notify_services:
        if name.startswith("mobile_app_") and slugify_ha(name) == expected:
            return f"notify.{name}"
    return f"notify.{expected}"


def resolve_logged_in_entity(device_name: str, entities: list[dict]) -> str:
    """Resolve this PC's `sensor.*_logged_in` entity id from HA's states."""
    expected = f"sensor.{slugify_ha(device_name)}_logged_in"
    for entry in entities:
        eid = entry.get("entity_id", "") if isinstance(entry, dict) else ""
        if isinstance(eid, str) and eid == expected:
            return eid
    return expected


def has_wake_on_lan_service(services: list[dict]) -> bool:
    """True if Home Assistant has the `wake_on_lan.send_magic_packet` service loaded."""
    for entry in services:
        if isinstance(entry, dict) and entry.get("domain") == "wake_on_lan":
            svc_map = entry.get("services")
            return isinstance(svc_map, dict) and "send_magic_packet" in svc_map
    return False


def _schema_field_names(data_schema: object) -> set[str]:
    if not isinstance(data_schema, list):
        return set()
    return {item["name"] for item in data_schema if isinstance(item, dict) and isinstance(item.get("name"), str)}


def build_switch_form_data(
    data_schema: object,
    *,
    name: Optional[str],
    state_template: str,
    turn_on: list[dict[str, Any]],
    turn_off: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the Template Switch config/options form submission matching HA's schema."""
    fields = _schema_field_names(data_schema)

    state_key = "state" if ("state" in fields and "value_template" not in fields) else "value_template"
    turn_on_key = "on_action" if ("on_action" in fields and "turn_on" not in fields) else "turn_on"
    turn_off_key = "off_action" if ("off_action" in fields and "turn_off" not in fields) else "turn_off"

    payload: dict[str, Any] = {
        state_key: state_template,
        turn_on_key: turn_on,
        turn_off_key: turn_off,
    }
    if name is not None and (not fields or "name" in fields):
        payload["name"] = name
    return payload


def _format_ha_error(status: int, data: object, fallback: str) -> str:
    if status in (401, 403):
        return "Home Assistant rejected the request (admin token required)"
    if status == 404:
        return "Home Assistant Template helper API is unavailable"
    if status == 0:
        return str(data) if data else fallback
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, dict) and errors:
            return ", ".join(f"{k}: {v}" for k, v in errors.items())
        msg = data.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return f"{fallback} (HTTP {status})"


async def _config_entry_exists(ha_client: Any, entry_id: str) -> bool:
    if not entry_id:
        return False
    status, data = await ha_client.get_config_entries()
    if status != 200 or not isinstance(data, list):
        return False
    return any(isinstance(item, dict) and item.get("entry_id") == entry_id for item in data)


async def _create_switch_via_config_flow(
    ha_client: Any,
    *,
    name: str,
    state_template: str,
    turn_on: list[dict[str, Any]],
    turn_off: list[dict[str, Any]],
) -> tuple[bool, str, str]:
    """Run HA's `template` → `switch` config flow. Returns (ok, entry_id, error)."""
    status, start = await ha_client.start_config_flow("template")
    if status != 200 or not isinstance(start, dict) or not start.get("flow_id"):
        return False, "", _format_ha_error(status, start, "Could not start Template helper flow")

    flow_id = str(start["flow_id"])
    try:
        step = start
        if step.get("type") == "menu":
            menu_options = step.get("menu_options")
            if isinstance(menu_options, (list, dict)) and "switch" not in menu_options:
                await ha_client.abort_config_flow(flow_id)
                return False, "", "Home Assistant does not offer a Template Switch helper"
            status, step = await ha_client.continue_config_flow(flow_id, {"next_step_id": "switch"})
            if status != 200 or not isinstance(step, dict):
                await ha_client.abort_config_flow(flow_id)
                return False, "", _format_ha_error(status, step, "Could not select Template Switch step")

        form_data = build_switch_form_data(
            step.get("data_schema"),
            name=name,
            state_template=state_template,
            turn_on=turn_on,
            turn_off=turn_off,
        )
        status, final = await ha_client.continue_config_flow(flow_id, form_data)
        if status == 200 and isinstance(final, dict) and final.get("type") == "create_entry":
            result_obj = final.get("result")
            entry_id = str(result_obj.get("entry_id", "")) if isinstance(result_obj, dict) else ""
            return True, entry_id, ""

        await ha_client.abort_config_flow(flow_id)
        return False, "", _format_ha_error(status, final, "Could not create Home Assistant switch")
    except Exception as exc:
        await ha_client.abort_config_flow(flow_id)
        return False, "", str(exc) or type(exc).__name__


async def _update_switch_via_options_flow(
    ha_client: Any,
    entry_id: str,
    *,
    state_template: str,
    turn_on: list[dict[str, Any]],
    turn_off: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Update an existing Template Switch config entry via its options flow."""
    status, step = await ha_client.start_options_flow(entry_id)
    if status != 200 or not isinstance(step, dict) or not step.get("flow_id"):
        return False, _format_ha_error(status, step, "Could not open switch options flow")

    flow_id = str(step["flow_id"])
    try:
        form_data = build_switch_form_data(
            step.get("data_schema"),
            name=None,
            state_template=state_template,
            turn_on=turn_on,
            turn_off=turn_off,
        )
        status, final = await ha_client.continue_options_flow(flow_id, form_data)
        if status == 200 and isinstance(final, dict) and final.get("type") == "create_entry":
            return True, ""
        await ha_client.abort_options_flow(flow_id)
        return False, _format_ha_error(status, final, "Could not update Home Assistant switch")
    except Exception as exc:
        await ha_client.abort_options_flow(flow_id)
        return False, str(exc) or type(exc).__name__


def _resolve_target_mac(mac_address: Optional[str], config: dict, ha_url: str) -> tuple[Optional[str], str]:
    raw_input = (mac_address or "").strip()
    if raw_input:
        mac = normalize_mac(raw_input)
        if not mac:
            return None, "Invalid MAC address (expected XX:XX:XX:XX:XX:XX)"
        return mac, ""
    saved_mac = normalize_mac(config.get("power_control", {}).get("wol_mac", ""))
    if saved_mac:
        return saved_mac, ""
    detected = detect_mac_address(ha_url)
    if detected:
        return detected, ""
    return None, "Could not detect MAC address — enter it manually"


async def _ensure_mobile_app_registered(ha_url: str, ha_token: str, config: dict, save_config_fn: Callable[[], None]):
    webhook_id = config.get("mobile_app", {}).get("webhook_id", "")
    if webhook_id:
        return webhook_id
    webhook_id = await register_mobile_app(ha_url, ha_token, config, save_config_fn)
    if webhook_id:
        await register_sensors(ha_url, webhook_id)
    return webhook_id


async def create_ha_power_switch(
    ha_client: Any,
    config: dict,
    save_config_fn: Callable[[], None],
    *,
    mac_address: Optional[str] = None,
    switch_name: Optional[str] = None,
) -> PowerSwitchResult:
    """Create (or update) an on/off PC switch in Home Assistant and enable remote shutdown.

    On  -> wake_on_lan.send_magic_packet with this PC's MAC address
    Off -> notify.mobile_app_<device> with message 'command_shutdown'
    State -> {{ is_state('sensor.<device>_logged_in', 'logged-in') }}
    """
    ha_url = getattr(ha_client, "url", "") or config.get("home_assistant", {}).get("url", "")
    ha_token = getattr(ha_client, "token", "") or config.get("home_assistant", {}).get("token", "")
    if not ha_url or not ha_token:
        return PowerSwitchResult(ok=False, error="Configure and save Home Assistant URL and token first")

    # MAC detection follows the route to the primary (usually LAN) URL, not a fallback over VPN or internet.
    lan_url = config.get("home_assistant", {}).get("url", "") or ha_url
    mac, mac_error = _resolve_target_mac(mac_address, config, lan_url)
    if not mac:
        return PowerSwitchResult(ok=False, error=mac_error)

    webhook_id = await _ensure_mobile_app_registered(ha_url, ha_token, config, save_config_fn)
    if not webhook_id:
        return PowerSwitchResult(ok=False, error="Could not register this PC with Home Assistant")

    device_name = config.get("mobile_app", {}).get("device_name") or _get_device_name()
    target_name = (switch_name or device_name).strip() or "Prism Desktop"

    services = await ha_client.get_services()
    entities = await ha_client.get_entities()
    notify_service = resolve_notify_service(device_name, services)
    logged_in_entity = resolve_logged_in_entity(device_name, entities)
    wol_available = has_wake_on_lan_service(services)

    state_template = f"{{{{ is_state('{logged_in_entity}', 'logged-in') }}}}"
    turn_on = [{"action": "wake_on_lan.send_magic_packet", "data": {"mac": mac}}]
    turn_off = [{"action": notify_service, "data": {"message": "command_shutdown"}}]

    existing_entry_id = str(config.get("power_control", {}).get("switch_entry_id", "") or "")
    created = False
    updated = False
    entry_id = existing_entry_id

    if existing_entry_id and await _config_entry_exists(ha_client, existing_entry_id):
        ok, err = await _update_switch_via_options_flow(
            ha_client,
            existing_entry_id,
            state_template=state_template,
            turn_on=turn_on,
            turn_off=turn_off,
        )
        if not ok:
            return PowerSwitchResult(ok=False, mac=mac, wol_available=wol_available, error=err)
        updated = True
    else:
        ok, entry_id, err = await _create_switch_via_config_flow(
            ha_client,
            name=target_name,
            state_template=state_template,
            turn_on=turn_on,
            turn_off=turn_off,
        )
        if not ok:
            return PowerSwitchResult(ok=False, mac=mac, wol_available=wol_available, error=err)
        created = True

    power_cfg = config.setdefault("power_control", {})
    power_cfg["allow_shutdown"] = True
    power_cfg["wol_mac"] = mac
    if entry_id:
        power_cfg["switch_entry_id"] = entry_id
    save_config_fn()

    # No device name or MAC address here: prism.log gets shared in bug reports.
    logger.info(
        "[PowerControl] Home Assistant switch %s; remote shutdown enabled",
        "updated" if updated else "created",
    )
    return PowerSwitchResult(
        ok=True,
        created=created,
        updated=updated,
        entry_id=entry_id,
        switch_name=target_name,
        mac=mac,
        wol_available=wol_available,
    )
