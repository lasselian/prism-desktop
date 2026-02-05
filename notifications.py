"""
Notifications Manager for Prism Desktop
Handles system notifications from Home Assistant across platforms.
"""

import platform
import subprocess
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtCore import QObject


class NotificationManager(QObject):
    """Manages system notifications across platforms."""
    
    APP_NAME = "Prism Desktop"
    
    def __init__(self, tray_icon: QSystemTrayIcon = None):
        super().__init__()
        self.tray_icon = tray_icon
    
    def set_tray_icon(self, tray_icon: QSystemTrayIcon):
        """Set the system tray icon for showing notifications."""
        self.tray_icon = tray_icon
    
    def _show_linux_notification(self, title: str, message: str) -> bool:
        """Show notification on Linux using notify-send."""
        try:
            subprocess.run(
                ['notify-send', '-a', self.APP_NAME, title, message],
                check=False,
                timeout=5
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def _show_windows_notification(self, title: str, message: str) -> bool:
        """Show notification on Windows using winotify."""
        try:
            from winotify import Notification
            toast = Notification(
                app_id=self.APP_NAME,
                title=title,
                msg=message,
                duration="short"
            )
            toast.show()
            return True
        except ImportError:
            return False
        except Exception as e:
            print(f"winotify error: {e}")
            return False
    
    def _show_fallback_notification(self, title: str, message: str):
        """Show notification using Qt system tray as fallback."""
        if self.tray_icon and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000
            )
    
    def show_ha_notification(self, title: str, message: str):
        """Show a Home Assistant notification using the best available method."""
        print(f"🔔 Notification: {title} - {message}")
        
        system = platform.system()
        
        if system == 'Windows':
            if self._show_windows_notification(title, message):
                return
        elif system == 'Linux':
            if self._show_linux_notification(title, message):
                return
        
        # Fallback to Qt tray notification
        self._show_fallback_notification(title, message)
    
    def show_info(self, title: str, message: str):
        """Show an info notification."""
        self.show_ha_notification(title, message)
    
    def show_error(self, title: str, message: str):
        """Show an error notification."""
        print(f"❌ Error: {title} - {message}")
        self.show_ha_notification(title, message)
