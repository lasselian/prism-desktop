"""
Notifications Manager for Prism Desktop
Native system notifications from Home Assistant.
Supports rich notifications with images (camera feeds etc) and up to
MAX_ACTIONS action buttons that fire mobile_app_notification_action back to HA.
"""

import asyncio
import functools
import platform
import subprocess
import logging
import os
import sys
import tempfile
import threading
from typing import Optional
from urllib.parse import urlsplit
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtGui import QDesktopServices

from services.mobile_app import (
    build_notification_action_event_data,
    fire_notification_action_event,
)

logger = logging.getLogger(__name__)

# HA companion apps render at most 3 notification actions; match that contract.
MAX_ACTIONS = 3
# Token prefix used in Windows toast button arguments to identify our buttons
# (body clicks / foreign arguments parse to None and are ignored).
WIN_TOKEN_PREFIX = "prism_action:"


def sanitize_actions(raw) -> list:
    """Canonicalize a notification's actions payload.

    Returns a list of {'action', 'title', 'uri'?} dicts with stripped,
    non-empty strings, truncated to MAX_ACTIONS. Anything malformed is
    dropped with a single summary warning per payload.
    """
    if not isinstance(raw, list):
        if raw is not None:
            logger.warning("[Notify] Ignoring malformed notification actions (not a list)")
        return []

    valid = []
    dropped = 0
    for entry in raw:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        action = entry.get('action')
        title = entry.get('title')
        if not isinstance(action, str) or not action.strip() \
                or not isinstance(title, str) or not title.strip():
            dropped += 1
            continue
        clean = {'action': action.strip(), 'title': title.strip()}
        uri = entry.get('uri')
        if isinstance(uri, str) and uri.strip():
            clean['uri'] = uri.strip()
        valid.append(clean)

    truncated = max(0, len(valid) - MAX_ACTIONS)
    if dropped or truncated:
        logger.warning(
            f"[Notify] Notification actions sanitized: {dropped} invalid dropped, "
            f"{truncated} beyond the {MAX_ACTIONS}-button limit"
        )
    return valid[:MAX_ACTIONS]


def is_safe_http_uri(uri) -> bool:
    """True only for well-formed http/https URIs with a host."""
    if not isinstance(uri, str) or not uri:
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return parts.scheme.lower() in ('http', 'https') and bool(parts.netloc)


def parse_action_token(token, count) -> Optional[int]:
    """Map a click token back to an action index, or None if it isn't ours.

    Accepts 'prism_action:<i>' (Windows button arguments) and '<i>' (Linux
    D-Bus action keys). Body clicks and unknown arguments return None.
    """
    if not isinstance(token, str):
        return None
    if token.startswith(WIN_TOKEN_PREFIX):
        token = token[len(WIN_TOKEN_PREFIX):]
    if not token.isdigit():
        return None
    idx = int(token)
    return idx if idx < count else None


class NotificationManager(QObject):
    """Native system notifications with image and action-button support."""

    APP_NAME = "Prism Desktop"
    APP_ID = "com.prismdesktop.app"

    def __init__(self, tray_icon: QSystemTrayIcon = None, ha_client=None,
                 config: Optional[dict] = None):
        super().__init__()
        self.tray_icon = tray_icon
        self.ha_client = ha_client
        # Live shared config dict (main.py's self.config) — read at click time
        # so a webhook_id registered after startup is picked up.
        self.config = config if config is not None else {}
        self._windows_registered = False
        self._win_register_lock = threading.Lock()
        self._dbus_notifier = None
        # Captured on the main thread; toast worker threads marshal back here.
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None

    def set_ha_client(self, client):
        """Update the HA client reference."""
        self.ha_client = client

    # ---- Platform dispatch ----

    def _show_notification(self, title: str, message: str, image_path: Optional[str] = None,
                           actions: Optional[list] = None, action_data: Optional[dict] = None):
        """Show a native notification on the current platform."""
        system = platform.system()

        if system == 'Windows':
            self._show_windows(title, message, image_path, actions, action_data)
        elif system == 'Linux':
            if actions:
                asyncio.ensure_future(
                    self._show_linux_actions(title, message, image_path, actions, action_data)
                )
            else:
                self._show_linux(title, message, image_path)
        else:
            self._show_fallback(title, message)

    def _get_icon_path(self) -> Optional[str]:
        candidates = [
            os.path.join(getattr(sys, '_MEIPASS', ''), 'icon.png'),
            os.path.join(os.path.dirname(sys.executable), 'icon.png'),
            os.path.join(os.path.dirname(__file__), '..', 'icon.png'),
        ]
        for p in candidates:
            if p and os.path.isfile(p):
                return os.path.abspath(p)
        return None

    def _register_windows_app(self) -> None:
        """Register app with Windows so it appears in notification settings."""
        try:
            from win11toast import register
            icon_path = self._get_icon_path()
            kwargs = {}
            if icon_path:
                kwargs['icon'] = icon_path
            register(self.APP_ID, self.APP_NAME, **kwargs)
            self._windows_registered = True
            logger.info("[Notify] Registered Prism Desktop with Windows notification system")
        except Exception as e:
            logger.warning(f"[Notify] Windows app registration failed: {e}")

    def _show_windows(self, title: str, message: str, image_path: Optional[str] = None,
                      actions: Optional[list] = None, action_data: Optional[dict] = None):
        """Windows: native toast via win11toast, on a worker thread.

        win11toast's toast() blocks until the toast is dismissed or activated,
        so every toast gets its own daemon thread to keep the UI loop free.
        """
        kwargs = {
            'app_id': self.APP_ID,
        }
        if image_path and os.path.isfile(image_path):
            kwargs['image'] = {
                'src': os.path.abspath(image_path),
                'placement': 'hero',
            }
        threading.Thread(
            target=self._windows_toast_worker,
            args=(title, message, kwargs, actions or [], action_data),
            daemon=True,
        ).start()

    def _windows_toast_worker(self, title: str, message: str, kwargs: dict,
                              actions: list, action_data: Optional[dict]):
        """Runs on a per-toast daemon thread. Must not touch Qt."""
        try:
            from win11toast import toast
            with self._win_register_lock:
                if not self._windows_registered:
                    self._register_windows_app()
            if actions:
                # 'foreground' activation requires a registered COM toast
                # activator (full packaged-app identity), which this app
                # doesn't have — Windows silently drops such buttons for
                # unpackaged apps. 'protocol' is what win11toast's own
                # examples use and needs no registration.
                kwargs['buttons'] = [
                    {
                        'activationType': 'protocol',
                        'arguments': f'{WIN_TOKEN_PREFIX}{i}',
                        'content': a['title'],
                    }
                    for i, a in enumerate(actions)
                ]
                kwargs['on_click'] = functools.partial(
                    self._on_windows_click, actions, action_data
                )
            toast(title, message, **kwargs)  # blocks until dismissed/activated
        except ImportError:
            logger.warning("[Notify] win11toast not installed, using fallback")
            self._marshal_fallback(title, message)
        except Exception as e:
            logger.warning(f"[Notify] win11toast error: {e}")
            self._marshal_fallback(title, message)

    def _marshal_fallback(self, title: str, message: str):
        """Schedule the Qt-balloon fallback on the main thread from a worker."""
        if not self._loop:
            return
        try:
            self._loop.call_soon_threadsafe(self._show_fallback, title, message)
        except RuntimeError:
            pass  # loop already closed (shutdown)

    def _on_windows_click(self, actions: list, action_data: Optional[dict], args):
        """win11toast activation callback — runs on a WinRT worker thread."""
        token = args.get('arguments', '') if isinstance(args, dict) else ''
        idx = parse_action_token(token, len(actions))
        logger.info(f"[Notify] Toast activated (args={args!r}) -> action index {idx}")
        if idx is None:
            return  # body click or foreign arguments — not an action button
        if not self._loop:
            logger.warning("[Notify] No event loop to dispatch notification action")
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._handle_action_task(actions[idx], action_data), self._loop
            )
        except RuntimeError as e:
            logger.warning(f"[Notify] Could not dispatch notification action: {e}")

    async def _handle_action_task(self, action: dict, action_data: Optional[dict]):
        """Marshal target so worker threads can reach _handle_action on the loop."""
        self._handle_action(action, action_data)

    def _show_linux(self, title: str, message: str, image_path: Optional[str] = None):
        """Linux: use notify-send."""
        try:
            cmd = ['notify-send', '-a', self.APP_NAME]
            if image_path and os.path.isfile(image_path):
                cmd += ['-i', image_path]
            cmd += [title, message]
            subprocess.run(cmd, check=False, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._show_fallback(title, message)

    async def _show_linux_actions(self, title: str, message: str, image_path: Optional[str],
                                  actions: list, action_data: Optional[dict]):
        """Linux notification with buttons via org.freedesktop.Notifications.

        Degrades to the plain notify-send path (no buttons) when the session
        bus is unavailable or the daemon lacks the "actions" capability.
        """
        notifier = None
        try:
            from services.dbus_notifier import DBusNotifier
            if self._dbus_notifier is None:
                self._dbus_notifier = DBusNotifier()
            notifier = self._dbus_notifier
        except ImportError as e:
            logger.warning(f"[Notify] dbus-next unavailable, actions unsupported: {e}")

        if notifier is not None:
            connected = await notifier.connect()
            if connected and notifier.actions_supported:
                keys = [(str(i), a['title']) for i, a in enumerate(actions)]
                on_action = functools.partial(self._on_linux_action, actions, action_data)
                nid = await notifier.notify(
                    self.APP_NAME, title, message,
                    image_path=image_path or self._get_icon_path(),
                    actions=keys,
                    on_action=on_action,
                )
                if nid is not None:
                    return
                logger.warning("[Notify] D-Bus Notify failed, falling back to notify-send")

        self._show_linux(title, message, image_path)

    def _on_linux_action(self, actions: list, action_data: Optional[dict], key: str):
        """DBusNotifier action callback — runs on the event-loop thread."""
        idx = parse_action_token(key, len(actions))
        if idx is not None:
            self._handle_action(actions[idx], action_data)

    def _show_fallback(self, title: str, message: str):
        """Fallback: Qt system tray balloon (no image support)."""
        if self.tray_icon and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                title, message,
                QSystemTrayIcon.MessageIcon.Information, 5000
            )

    # ---- Action dispatch ----

    def _handle_action(self, action: dict, action_data: Optional[dict]):
        """Single funnel for clicked actions (always on the event-loop thread)."""
        action_id = action.get('action', '')
        uri = action.get('uri')
        logger.info(f"[Notify] Notification action clicked: {action_id}")

        if uri:
            if is_safe_http_uri(uri):
                self._open_uri(uri)
            else:
                logger.warning("[Notify] Ignoring non-http(s) notification action URI")

        if action_id == 'URI':
            return  # companion-app contract: URI-only action fires no event
        asyncio.ensure_future(self._fire_action_event(action_id, action_data))

    def _open_uri(self, uri: str):
        """Open a URI in the default browser."""
        try:
            if sys.platform == 'linux':
                # QDesktopServices.openUrl() can segfault on some KDE/D-Bus
                # configurations — use xdg-open in a detached subprocess instead.
                subprocess.Popen(
                    ['xdg-open', uri],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                QDesktopServices.openUrl(QUrl(uri))
        except Exception:
            QDesktopServices.openUrl(QUrl(uri))

    async def _fire_action_event(self, action_id: str, action_data: Optional[dict]):
        """Report the clicked action back to HA (one retry on failure)."""
        ha_url = self.config.get('home_assistant', {}).get('url', '')
        mobile_cfg = self.config.get('mobile_app', {})
        webhook_id = mobile_cfg.get('webhook_id', '')
        if not ha_url or not webhook_id:
            logger.warning("[Notify] Cannot report notification action: HA URL or webhook_id missing")
            return

        event_data = build_notification_action_event_data(
            action_id, action_data,
            mobile_cfg.get('device_id', ''),
            mobile_cfg.get('device_name', ''),
        )
        ok = await fire_notification_action_event(ha_url, webhook_id, event_data)
        if not ok:
            ok = await fire_notification_action_event(ha_url, webhook_id, event_data)
        if not ok:
            logger.warning(f"[Notify] Notification action '{action_id}' delivery failed after retry")

    # ---- Image downloading ----

    async def _download_image(self, image_source: str) -> Optional[str]:
        """Download an image from HA to a temp file. Returns local path or None."""
        if not self.ha_client:
            return None
        try:
            if image_source.startswith('camera.'):
                image_bytes = await self.ha_client.get_camera_image(image_source)
            elif image_source.startswith('/api/') or image_source.startswith('http'):
                image_bytes = await self.ha_client.get_media_image(image_source)
            else:
                image_bytes = await self.ha_client.get_media_image(image_source)

            if not image_bytes:
                return None

            tmp = tempfile.NamedTemporaryFile(
                prefix='prism_notify_', suffix='.jpg', delete=False
            )
            tmp.write(image_bytes)
            tmp.close()
            return tmp.name
        except Exception as e:
            logger.warning(f"[Notify] Image download failed: {e}")
            return None

    # ---- Public API ----

    def show_ha_notification(self, payload: dict):
        """Show a Home Assistant notification (text, image and/or actions)."""
        if not isinstance(payload, dict):
            return

        title = payload.get('title', 'Home Assistant')
        message = payload.get('message', '')
        image_source = payload.get('image') or payload.get('entity_id', '')
        actions = sanitize_actions(payload.get('actions'))
        action_data = payload.get('action_data')
        if not isinstance(action_data, dict):
            action_data = None

        if image_source and self.ha_client:
            asyncio.ensure_future(
                self._show_with_image(title, message, image_source, actions, action_data)
            )
        else:
            self._show_notification(title, message, actions=actions, action_data=action_data)

    async def _show_with_image(self, title: str, message: str, image_source: str,
                               actions: Optional[list] = None,
                               action_data: Optional[dict] = None):
        """Download image, show notification, clean up."""
        image_path = await self._download_image(image_source)
        self._show_notification(title, message, image_path, actions, action_data)
        if image_path:
            await asyncio.sleep(15)
            try:
                os.remove(image_path)
            except Exception:
                pass
