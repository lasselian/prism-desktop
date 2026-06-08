"""
Notifications Manager for Prism Desktop
Native system notifications from Home Assistant.
Supports rich notifications with images (camera feeds etc).
"""

import asyncio
import platform
import subprocess
import logging
import os
import sys
import tempfile
from typing import Optional
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtCore import QObject

logger = logging.getLogger(__name__)


class NotificationManager(QObject):
    """Native system notifications with image support."""

    APP_NAME = "Prism Desktop"
    APP_ID = "com.prismdesktop.app"
    _windows_registered = False

    def __init__(self, tray_icon: QSystemTrayIcon = None, ha_client=None):
        super().__init__()
        self.tray_icon = tray_icon
        self.ha_client = ha_client

    def set_ha_client(self, client):
        """Update the HA client reference."""
        self.ha_client = client

    # ---- Platform dispatch ----

    def _show_notification(self, title: str, message: str, image_path: Optional[str] = None):
        """Show a native notification on the current platform."""
        system = platform.system()

        if system == 'Windows':
            self._show_windows(title, message, image_path)
        elif system == 'Linux':
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
            NotificationManager._windows_registered = True
            logger.info("[Notify] Registered Prism Desktop with Windows notification system")
        except Exception as e:
            logger.warning(f"[Notify] Windows app registration failed: {e}")

    def _show_windows(self, title: str, message: str, image_path: Optional[str] = None):
        """Windows: use win11toast (native Windows ToastNotification API)."""
        try:
            from win11toast import toast
            if not NotificationManager._windows_registered:
                self._register_windows_app()
            kwargs = {
                'app_id': self.APP_ID,
            }
            if image_path and os.path.isfile(image_path):
                kwargs['image'] = {
                    'src': os.path.abspath(image_path),
                    'placement': 'hero',
                }
            toast(title, message, **kwargs)
        except ImportError:
            logger.warning("[Notify] win11toast not installed, using fallback")
            self._show_fallback(title, message)
        except Exception as e:
            logger.warning(f"[Notify] win11toast error: {e}")
            self._show_fallback(title, message)

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

    def _show_fallback(self, title: str, message: str):
        """Fallback: Qt system tray balloon (no image support)."""
        if self.tray_icon and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                title, message,
                QSystemTrayIcon.MessageIcon.Information, 5000
            )

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
        """Show a Home Assistant notification (text or rich with image)."""
        if not isinstance(payload, dict):
            return

        title = payload.get('title', 'Home Assistant')
        message = payload.get('message', '')
        image_source = payload.get('image') or payload.get('entity_id', '')

        if image_source and self.ha_client:
            asyncio.ensure_future(self._show_with_image(title, message, image_source))
        else:
            self._show_notification(title, message)

    async def _show_with_image(self, title: str, message: str, image_source: str):
        """Download image, show notification, clean up."""
        image_path = await self._download_image(image_source)
        self._show_notification(title, message, image_path)
        if image_path:
            await asyncio.sleep(15)
            try:
                os.remove(image_path)
            except Exception:
                pass
