"""
Theme Manager for Prism Desktop
Handles light/dark/system theme switching with cross-platform integration.
"""

import platform
import subprocess
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import QObject, pyqtSignal


class ThemeManager(QObject):
    """Manages application theming with cross-platform system integration."""

    theme_changed = pyqtSignal(str)  # Emits 'light' or 'dark'

    # Dark mode color palette
    DARK_COLORS = {
        'window': '#1e1e1e',
        'window_text': '#ffffff',
        'base': '#2d2d2d',
        'alternate_base': '#353535',
        'text': '#ffffff',
        'button': '#3d3d3d',
        'button_text': '#ffffff',
        'highlight': '#0078d4',
        'highlight_text': '#ffffff',
        'border': '#555555',
        'accent': '#0078d4',
    }

    # Light mode color palette
    LIGHT_COLORS = {
        'window': '#eaeaea',
        'window_text': '#1e1e1e',
        'base': '#ffffff',
        'alternate_base': '#f5f5f5',
        'text': '#1e1e1e',
        'button': '#ffffff',
        'button_text': '#1e1e1e',
        'highlight': '#0078d4',
        'highlight_text': '#ffffff',
        'border': '#bebebe',
        'accent': '#0078d4',
    }

    def __init__(self):
        super().__init__()
        self._current_theme = 'system'
        self._effective_theme = 'dark'

    def get_system_theme(self) -> str:
        """Detect system theme preference across platforms."""
        detect = _THEME_DETECTORS.get(platform.system())
        return detect() if detect else 'dark'  # unknown platforms

    def set_theme(self, theme: str):
        """Set the application theme ('light', 'dark', or 'system')."""
        self._current_theme = theme

        if theme == 'system':
            effective = self.get_system_theme()
        else:
            effective = theme

        if effective != self._effective_theme:
            self._effective_theme = effective
            self._apply_theme(effective)
            self.theme_changed.emit(effective)

    def get_effective_theme(self) -> str:
        """Get the actual applied theme ('light' or 'dark')."""
        return self._effective_theme

    def get_colors(self) -> dict:
        """Get the current color palette."""
        if self._effective_theme == 'dark':
            return self.DARK_COLORS
        return self.LIGHT_COLORS

    def _apply_theme(self, theme: str):
        """Apply the theme to the Qt application."""
        app = QApplication.instance()
        if not app:
            return

        colors = self.DARK_COLORS if theme == 'dark' else self.LIGHT_COLORS

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors['window']))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors['window_text']))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors['base']))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors['alternate_base']))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors['text']))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors['button']))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors['button_text']))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors['highlight']))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors['highlight_text']))

        app.setPalette(palette)


def _settings_output(cmd: list[str]) -> str | None:
    """stdout of a quick settings query; None if it failed or the tool isn't installed."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _windows_theme() -> str:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return 'light' if value == 1 else 'dark'
    except Exception:
        return 'dark'


def _linux_theme() -> str:
    # KDE Plasma first, then GNOME's colour scheme, then the GTK theme name.
    scheme = (_settings_output(['kreadconfig5', '--group', 'General', '--key', 'ColorScheme']) or '').strip().lower()
    if scheme:
        return 'dark' if 'dark' in scheme else 'light'
    for key in ('color-scheme', 'gtk-theme'):
        out = _settings_output(['gsettings', 'get', 'org.gnome.desktop.interface', key])
        if out is not None:
            return 'dark' if 'dark' in out.lower() else 'light'
    return 'dark'


def _macos_theme() -> str:
    # AppleInterfaceStyle only exists (as "Dark") while dark mode is on.
    return 'dark' if _settings_output(['defaults', 'read', '-g', 'AppleInterfaceStyle']) is not None else 'light'


_THEME_DETECTORS = {'Windows': _windows_theme, 'Linux': _linux_theme, 'Darwin': _macos_theme}
