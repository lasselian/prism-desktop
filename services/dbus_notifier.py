"""
Linux desktop notifications with action buttons via org.freedesktop.Notifications.

notify-send cannot round-trip button clicks, so notifications that carry HA
actions are posted directly on the session bus and ActionInvoked signals are
routed back to a per-notification callback.

This module imports dbus_next at the top level and must only be imported on
Linux (dbus-next carries a sys_platform == 'linux' marker in requirements.txt).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus

logger = logging.getLogger(__name__)

NOTIFY_BUS_NAME = "org.freedesktop.Notifications"
NOTIFY_OBJECT_PATH = "/org/freedesktop/Notifications"
NOTIFY_IFACE = "org.freedesktop.Notifications"

DBUS_IFACE = "org.freedesktop.DBus"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"


class DBusNotifier:
    """Posts notifications on the session bus and dispatches action clicks.

    All methods run on the qasync event loop. A failed connect is sticky
    (`_unavailable`) so callers can fall back to notify-send without retrying
    the bus on every notification; a failure of an individual Notify call only
    resets the bus so the next notification reattempts the connection.
    """

    MAX_PENDING = 50

    def __init__(self):
        self._bus: Optional[MessageBus] = None
        self._connect_lock = asyncio.Lock()
        self._unavailable = False
        self._actions_supported = False
        # notification id -> callback(action_key); insertion-ordered for eviction
        self._pending: dict[int, Callable[[str], None]] = {}

    @property
    def actions_supported(self) -> bool:
        return self._actions_supported

    async def connect(self) -> bool:
        """Connect to the session bus and subscribe to action signals.

        Lazy and idempotent. Returns False (and remembers the failure) if the
        session bus or notification daemon is unavailable.
        """
        async with self._connect_lock:
            if self._bus is not None:
                return True
            if self._unavailable:
                return False
            try:
                bus = await MessageBus(bus_type=BusType.SESSION).connect()
                reply = await bus.call(Message(
                    destination=NOTIFY_BUS_NAME,
                    path=NOTIFY_OBJECT_PATH,
                    interface=NOTIFY_IFACE,
                    member="GetCapabilities",
                    signature="",
                    body=[],
                ))
                if reply.message_type == MessageType.ERROR:
                    text = reply.body[0] if reply.body else reply.error_name
                    raise RuntimeError(f"GetCapabilities failed: {text}")
                caps = reply.body[0] if reply.body else []
                self._actions_supported = "actions" in caps

                bus.add_message_handler(self._handle_message)
                for member in ("ActionInvoked", "NotificationClosed"):
                    await self._add_match(bus, (
                        "type='signal',"
                        f"sender='{NOTIFY_BUS_NAME}',"
                        f"interface='{NOTIFY_IFACE}',"
                        f"member='{member}',"
                        f"path='{NOTIFY_OBJECT_PATH}'"
                    ))

                self._bus = bus
                logger.info(f"[DBusNotifier] Connected (actions supported: {self._actions_supported})")
                return True
            except Exception as e:
                logger.warning(f"[DBusNotifier] Session-bus notifications unavailable: {e}")
                self._unavailable = True
                self._bus = None
                return False

    async def _add_match(self, bus: MessageBus, rule: str):
        reply = await bus.call(Message(
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

    async def notify(
        self,
        app_name: str,
        summary: str,
        body: str,
        image_path: Optional[str] = None,
        actions: Optional[list[tuple[str, str]]] = None,
        on_action: Optional[Callable[[str], None]] = None,
    ) -> Optional[int]:
        """Post a notification. Returns the notification id, or None on failure.

        actions: list of (key, label) button pairs; on_action receives the key
        of the clicked button. Callbacks fire at most once per notification.
        """
        if self._bus is None:
            return None

        action_list: list[str] = []
        for key, label in (actions or []):
            action_list += [key, label]
        hints: dict = {}
        app_icon = ""
        if image_path:
            hints["image-path"] = Variant("s", image_path)
            app_icon = image_path

        try:
            reply = await self._bus.call(Message(
                destination=NOTIFY_BUS_NAME,
                path=NOTIFY_OBJECT_PATH,
                interface=NOTIFY_IFACE,
                member="Notify",
                signature="susssasa{sv}i",
                body=[app_name, 0, app_icon, summary, body, action_list, hints, -1],
            ))
            if reply.message_type == MessageType.ERROR:
                text = reply.body[0] if reply.body else reply.error_name
                logger.warning(f"[DBusNotifier] Notify failed: {text}")
                return None
            nid = reply.body[0]
        except Exception as e:
            logger.warning(f"[DBusNotifier] Notify call error: {e}")
            # Bus likely died (daemon restart, session teardown) — drop it so
            # the next notification reattempts the connection.
            self._bus = None
            return None

        if action_list and on_action:
            while len(self._pending) >= self.MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[nid] = on_action
        return nid

    def _handle_message(self, message: Message):
        if message.message_type != MessageType.SIGNAL or message.interface != NOTIFY_IFACE:
            return
        if message.member == "ActionInvoked" and len(message.body) >= 2:
            self._on_action_invoked(message.body[0], message.body[1])
        elif message.member == "NotificationClosed" and len(message.body) >= 1:
            self._on_closed(message.body[0])

    def _on_action_invoked(self, nid: int, key):
        # pop-first: dedupes duplicate signals; ids from other apps'
        # notifications miss the map and are ignored
        callback = self._pending.pop(nid, None)
        if callback is None:
            return
        try:
            callback(str(key))
        except Exception:
            logger.exception("[DBusNotifier] Action callback failed")

    def _on_closed(self, nid: int):
        self._pending.pop(nid, None)

    async def close(self):
        """Best-effort teardown (not required at process exit)."""
        if self._bus is not None:
            try:
                self._bus.remove_message_handler(self._handle_message)
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
        self._pending.clear()
