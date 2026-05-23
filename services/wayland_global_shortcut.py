"""
Wayland global shortcut backend using the XDG desktop portal.

Wayland compositors intentionally block arbitrary applications from
capturing global keyboard input. The supported path is the
org.freedesktop.portal.GlobalShortcuts interface, which is currently
implemented by KDE's portal backend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus


PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
DBUS_IFACE = "org.freedesktop.DBus"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"
SHORTCUT_ID = "toggle-dashboard"
APP_ID = "prism-desktop"

logger = logging.getLogger(__name__)


class WaylandGlobalShortcut:
    """Registers one global keyboard shortcut through the desktop portal."""

    def __init__(self, shortcut_str: str, on_trigger: Callable[[], None]):
        self.shortcut_str = shortcut_str
        self.on_trigger = on_trigger

        self._bus: MessageBus | None = None
        self._task: asyncio.Task | None = None
        self._session_handle: str | None = None
        self._response_waiters: dict[str, asyncio.Future] = {}
        self._match_rules: set[str] = set()
        self._handler_installed = False

    def start(self):
        """Start portal registration in the running asyncio loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._on_task_done)

    def stop(self):
        """Stop portal registration and close the session."""
        if self._task and not self._task.done():
            self._task.cancel()

    def is_alive(self) -> bool:
        """Return whether the backend task is still running."""
        return bool(self._task and not self._task.done())

    async def _run(self):
        try:
            preferred_trigger = _pynput_to_portal_shortcut(self.shortcut_str)
            _ensure_desktop_file()
            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
            await self._register_app_id()
            self._bus.add_message_handler(self._handle_message)
            self._handler_installed = True

            await self._add_match(
                "type='signal',"
                f"sender='{PORTAL_BUS_NAME}',"
                f"interface='{GLOBAL_SHORTCUTS_IFACE}',"
                "member='Activated',"
                f"path='{PORTAL_OBJECT_PATH}'"
            )

            self._session_handle = await self._create_session()
            logger.info("Wayland shortcut session created: %s", self._session_handle)
            await self._add_match(
                "type='signal',"
                f"sender='{PORTAL_BUS_NAME}',"
                f"interface='{SESSION_IFACE}',"
                "member='Closed',"
                f"path='{self._session_handle}'"
            )
            await self._bind_shortcut(self._session_handle, preferred_trigger)
            logger.info(
                "Wayland portal shortcut bound: shortcut=%s preferred_trigger=%s",
                SHORTCUT_ID,
                preferred_trigger,
            )

            # Keep the session alive until the backend is stopped.
            await asyncio.Future()
        except asyncio.CancelledError:
            raise
        finally:
            await self._cleanup()

    def _on_task_done(self, task: asyncio.Task):
        """Log async failures so portal registration does not fail silently."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.exception("Wayland portal shortcut backend failed", exc_info=exc)

    def _handle_message(self, message: Message):
        if message.message_type != MessageType.SIGNAL:
            return

        if (
            message.interface == REQUEST_IFACE
            and message.member == "Response"
            and message.path in self._response_waiters
        ):
            future = self._response_waiters.pop(message.path)
            if not future.done():
                future.set_result(message.body)
            return

        if (
            message.interface == GLOBAL_SHORTCUTS_IFACE
            and message.member == "Activated"
            and len(message.body) >= 2
        ):
            session_handle, shortcut_id = message.body[:2]
            if session_handle == self._session_handle and shortcut_id == SHORTCUT_ID:
                self.on_trigger()
            return

        if (
            message.interface == SESSION_IFACE
            and message.member == "Closed"
            and message.path == self._session_handle
        ):
            if self._task and not self._task.done():
                self._task.cancel()

    async def _create_session(self) -> str:
        request_token = _random_token("prism_req")
        session_token = _random_token("prism_session")
        response_future = await self._prepare_request(request_token)

        reply = await self._call_portal(
            member="CreateSession",
            signature="a{sv}",
            body=[{
                "handle_token": Variant("s", request_token),
                "session_handle_token": Variant("s", session_token),
            }],
        )

        expected_request_path = _request_path(self._bus.unique_name, request_token)
        returned_request_path = reply.body[0]
        if returned_request_path != expected_request_path:
            await self._add_match(
                "type='signal',"
                f"sender='{PORTAL_BUS_NAME}',"
                f"interface='{REQUEST_IFACE}',"
                "member='Response',"
                f"path='{returned_request_path}'"
            )
            self._response_waiters[returned_request_path] = self._response_waiters.pop(expected_request_path)

        response_code, results = await asyncio.wait_for(response_future, timeout=15)
        if response_code != 0:
            raise RuntimeError(f"Global shortcut session was not granted (response={response_code})")

        session_handle = results["session_handle"]
        if isinstance(session_handle, Variant):
            session_handle = session_handle.value
        return session_handle

    async def _bind_shortcut(self, session_handle: str, preferred_trigger: str):
        request_token = _random_token("prism_bind")
        response_future = await self._prepare_request(request_token)

        reply = await self._call_portal(
            member="BindShortcuts",
            signature="oa(sa{sv})sa{sv}",
            body=[
                session_handle,
                [[
                    SHORTCUT_ID,
                    {
                        "description": Variant("s", "Toggle Prism Desktop"),
                        "preferred_trigger": Variant("s", preferred_trigger),
                    },
                ]],
                "",
                {"handle_token": Variant("s", request_token)},
            ],
        )

        expected_request_path = _request_path(self._bus.unique_name, request_token)
        returned_request_path = reply.body[0]
        if returned_request_path != expected_request_path:
            await self._add_match(
                "type='signal',"
                f"sender='{PORTAL_BUS_NAME}',"
                f"interface='{REQUEST_IFACE}',"
                "member='Response',"
                f"path='{returned_request_path}'"
            )
            self._response_waiters[returned_request_path] = self._response_waiters.pop(expected_request_path)

        response_code, _results = await asyncio.wait_for(response_future, timeout=60)
        if response_code != 0:
            raise RuntimeError(f"Global shortcut binding failed (response={response_code})")

    async def _register_app_id(self):
        """Associate this host process with a stable portal app id."""
        reply = await self._bus.call(Message(
            destination=PORTAL_BUS_NAME,
            path=PORTAL_OBJECT_PATH,
            interface=REGISTRY_IFACE,
            member="Register",
            signature="sa{sv}",
            body=[APP_ID, {}],
        ))
        if reply.message_type == MessageType.ERROR:
            # The registry helper is optional and may fail when the host app's
            # desktop file has not been indexed yet. Treat those cases as a
            # warning so Prism can keep using the portal path where supported.
            text = reply.body[0] if reply.body else reply.error_name
            if "App info not found" not in text:
                raise RuntimeError(f"Registry.Register failed: {text}")
            logger.warning("Wayland portal app id registration skipped: %s", text)
        else:
            logger.info("Wayland portal app id registered: %s", APP_ID)

    async def _prepare_request(self, token: str) -> asyncio.Future:
        request_path = _request_path(self._bus.unique_name, token)
        await self._add_match(
            "type='signal',"
            f"sender='{PORTAL_BUS_NAME}',"
            f"interface='{REQUEST_IFACE}',"
            "member='Response',"
            f"path='{request_path}'"
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._response_waiters[request_path] = future
        return future

    async def _call_portal(self, member: str, signature: str, body: list):
        reply = await self._bus.call(Message(
            destination=PORTAL_BUS_NAME,
            path=PORTAL_OBJECT_PATH,
            interface=GLOBAL_SHORTCUTS_IFACE,
            member=member,
            signature=signature,
            body=body,
        ))
        if reply.message_type == MessageType.ERROR:
            text = reply.body[0] if reply.body else reply.error_name
            raise RuntimeError(f"{member} failed: {text}")
        return reply

    async def _add_match(self, rule: str):
        if not self._bus or rule in self._match_rules:
            return
        reply = await self._bus.call(Message(
            destination=DBUS_IFACE,
            path=DBUS_OBJECT_PATH,
            interface=DBUS_IFACE,
            member="AddMatch",
            signature="s",
            body=[rule],
        ))
        if reply.message_type == MessageType.ERROR:
            text = reply.body[0] if reply.body else reply.error_name
            raise RuntimeError(f"AddMatch failed: {text}")
        self._match_rules.add(rule)

    async def _remove_match(self, rule: str):
        if not self._bus or rule not in self._match_rules:
            return
        reply = await self._bus.call(Message(
            destination=DBUS_IFACE,
            path=DBUS_OBJECT_PATH,
            interface=DBUS_IFACE,
            member="RemoveMatch",
            signature="s",
            body=[rule],
        ))
        if reply.message_type != MessageType.ERROR:
            self._match_rules.discard(rule)

    async def _close_session(self):
        if not self._bus or not self._session_handle:
            return
        await self._bus.call(Message(
            destination=PORTAL_BUS_NAME,
            path=self._session_handle,
            interface=SESSION_IFACE,
            member="Close",
        ))

    async def _cleanup(self):
        for future in self._response_waiters.values():
            if not future.done():
                future.cancel()
        self._response_waiters.clear()

        try:
            await self._close_session()
        except Exception:
            pass

        if self._bus and self._handler_installed:
            self._bus.remove_message_handler(self._handle_message)
            self._handler_installed = False

        if self._bus:
            for rule in list(self._match_rules):
                try:
                    await self._remove_match(rule)
                except Exception:
                    pass
            self._bus.disconnect()
            self._bus = None

        self._session_handle = None
        self._match_rules.clear()


def _request_path(unique_name: str, token: str) -> str:
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"


def _random_token(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _pynput_to_portal_shortcut(shortcut_str: str) -> str:
    """
    Convert Prism's stored pynput notation to the XDG shortcuts format.

    Example:
      <ctrl>+<alt>+h -> CTRL+ALT+h
    """
    modifier_map = {
        "<ctrl>": "CTRL",
        "<alt>": "ALT",
        "<shift>": "SHIFT",
        "<cmd>": "LOGO",
    }
    key_map = {
        "<esc>": "Escape",
        "<enter>": "Return",
        "<return>": "Return",
        "<space>": "space",
        "<tab>": "Tab",
        "<backspace>": "BackSpace",
        "<delete>": "Delete",
        "<home>": "Home",
        "<end>": "End",
        "<page_up>": "Page_Up",
        "<page_down>": "Page_Down",
        "<up>": "Up",
        "<down>": "Down",
        "<left>": "Left",
        "<right>": "Right",
    }

    parts = []
    for part in shortcut_str.split("+"):
        normalized = part.strip().lower()
        if not normalized:
            continue
        if normalized in modifier_map:
            parts.append(modifier_map[normalized])
            continue
        if normalized in key_map:
            parts.append(key_map[normalized])
            continue
        if normalized.startswith("<f") and normalized.endswith(">"):
            parts.append(normalized[1:-1].upper())
            continue
        parts.append(normalized.strip("<>"))

    if not parts:
        raise ValueError("Shortcut is empty")
    return "+".join(parts)


def is_wayland_session() -> bool:
    """Return whether the current desktop session is Wayland."""
    if os.name != "posix":
        return False
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def is_kde_wayland_session() -> bool:
    """Return whether the current session is KDE/Plasma on Wayland."""
    if not is_wayland_session():
        return False

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    session_desktop = os.environ.get("XDG_SESSION_DESKTOP", "").upper()
    return (
        "KDE" in desktop
        or "PLASMA" in desktop
        or "KDE" in session_desktop
        or "PLASMA" in session_desktop
    )


def supports_wayland_global_shortcuts() -> bool:
    """Return whether Prism currently supports global shortcuts on this Wayland desktop."""
    return is_kde_wayland_session()


def _install_appimage_icon(appdir: str) -> str:
    """Copy the bundled AppImage icon to a persistent location and return its path."""
    src = Path(appdir) / "usr/share/icons/hicolor/256x256/apps/prism-desktop.png"
    if not src.exists():
        return "prism-desktop"
    dest_dir = Path.home() / ".local/share/icons/hicolor/256x256/apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "prism-desktop.png"
    shutil.copy2(src, dest)
    return str(dest)


def _ensure_desktop_file():
    """Ensure the portal app id matches an installed desktop entry."""
    # Skip writing when an external AppImage manager is handling desktop integration.
    # DESKTOPINTEGRATION is set by AppImageLauncher to its own binary path — any
    # non-empty value means it is active and will write its own .desktop entry.
    # APPIMAGELAUNCHER_DISABLE is the opt-out flag used by tools like Gear Lever;
    # we accept any non-empty/non-"0" value, not just "1", for broader compatibility.
    disable_val = os.environ.get("APPIMAGELAUNCHER_DISABLE", "")
    if disable_val and disable_val != "0":
        return
    if os.environ.get("DESKTOPINTEGRATION"):
        return

    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_file = desktop_dir / f"{APP_ID}.desktop"
    if desktop_file.exists():
        return

    appimage = os.environ.get("APPIMAGE")
    appdir = os.environ.get("APPDIR")
    exec_path = appimage if appimage else "prism-desktop"
    icon_path = _install_appimage_icon(appdir) if appdir else "prism-desktop"

    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Prism Desktop\n"
        "Comment=Home Assistant Tray Application\n"
        f"Exec={exec_path}\n"
        f"Icon={icon_path}\n"
        "Categories=Utility;\n"
        "Terminal=false\n"
    )
    logger.info("Created desktop entry for portal app id: %s", desktop_file)
