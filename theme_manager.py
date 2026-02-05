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
        'window': '#f3f3f3',
        'window_text': '#1e1e1e',
        'base': '#ffffff',
        'alternate_base': '#f5f5f5',
        'text': '#1e1e1e',
        'button': '#e5e5e5',
        'button_text': '#1e1e1e',
        'highlight': '#0078d4',
        'highlight_text': '#ffffff',
        'border': '#d1d1d1',
        'accent': '#0078d4',
    }
    
    def __init__(self, config_manager=None):
        super().__init__()
        self.config_manager = config_manager
        self._current_theme = 'system'
        self._effective_theme = 'dark'
    
    def get_system_theme(self) -> str:
        """Detect system theme preference across platforms."""
        system = platform.system()
        
        if system == 'Windows':
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
                )
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                return 'light' if value == 1 else 'dark'
            except Exception:
                return 'dark'
        
        elif system == 'Linux':
            # Try KDE Plasma first
            try:
                result = subprocess.run(
                    ['kreadconfig5', '--group', 'General', '--key', 'ColorScheme'],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0 and result.stdout.strip():
                    scheme = result.stdout.strip().lower()
                    return 'dark' if 'dark' in scheme else 'light'
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            
            # Try GNOME
            try:
                result = subprocess.run(
                    ['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    return 'dark' if 'dark' in result.stdout.lower() else 'light'
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            
            # Fallback: check GTK theme name
            try:
                result = subprocess.run(
                    ['gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme'],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    return 'dark' if 'dark' in result.stdout.lower() else 'light'
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            
            return 'dark'  # Default to dark
        
        elif system == 'Darwin':
            # macOS
            try:
                result = subprocess.run(
                    ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                    capture_output=True, text=True, timeout=2
                )
                return 'dark' if result.returncode == 0 else 'light'
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return 'light'
        
        return 'dark'  # Default for unknown platforms
    
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
    
    def get_current_theme(self) -> str:
        """Get the current theme setting."""
        return self._current_theme
    
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
    
    def get_stylesheet(self) -> str:
        """Get the complete stylesheet for the current theme."""
        colors = self.get_colors()
        
        return f"""
            QWidget {{
                background-color: {colors['window']};
                color: {colors['text']};
                font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', 'DejaVu Sans', sans-serif;
                font-size: 12px;
            }}
            
            QPushButton {{
                background-color: {colors['button']};
                color: {colors['button_text']};
                border: 1px solid {colors['border']};
                border-radius: 6px;
                padding: 8px 16px;
                min-height: 32px;
            }}
            
            QPushButton:hover {{
                background-color: {colors['highlight']};
                color: {colors['highlight_text']};
                border-color: {colors['highlight']};
            }}
            
            QPushButton:pressed {{
                background-color: {colors['accent']};
            }}
            
            QPushButton:disabled {{
                background-color: {colors['alternate_base']};
                color: {colors['border']};
            }}
            
            QLineEdit, QComboBox {{
                background-color: {colors['base']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 4px;
                padding: 6px 10px;
                min-height: 24px;
            }}
            
            QLineEdit:focus, QComboBox:focus {{
                border-color: {colors['accent']};
            }}
            
            QComboBox::drop-down {{
                border: none;
                padding-right: 8px;
            }}
            
            QComboBox::down-arrow {{
                width: 12px;
                height: 12px;
            }}
            
            QLabel {{
                color: {colors['text']};
            }}
            
            QGroupBox {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {colors['text']};
            }}
            
            QListWidget {{
                background-color: {colors['base']};
                border: 1px solid {colors['border']};
                border-radius: 6px;
                padding: 4px;
            }}
            
            QListWidget::item {{
                padding: 8px;
                border-radius: 4px;
            }}
            
            QListWidget::item:selected {{
                background-color: {colors['highlight']};
                color: {colors['highlight_text']};
            }}
            
            QListWidget::item:hover {{
                background-color: {colors['alternate_base']};
            }}
            
            QTabWidget::pane {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                background-color: {colors['window']};
            }}
            
            QTabBar::tab {{
                background-color: {colors['button']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 16px;
                margin-right: 2px;
            }}
            
            QTabBar::tab:selected {{
                background-color: {colors['window']};
                border-bottom: 1px solid {colors['window']};
            }}
            
            QTabBar::tab:hover:!selected {{
                background-color: {colors['alternate_base']};
            }}
            
            QScrollBar:vertical {{
                background-color: {colors['base']};
                width: 12px;
                border-radius: 6px;
            }}
            
            QScrollBar::handle:vertical {{
                background-color: {colors['border']};
                border-radius: 6px;
                min-height: 30px;
            }}
            
            QScrollBar::handle:vertical:hover {{
                background-color: {colors['button']};
            }}
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """
    
    def get_dashboard_stylesheet(self) -> str:
        """Get stylesheet specifically for the dashboard widget."""
        colors = self.get_colors()
        
        return f"""
            QWidget#dashboard {{
                background-color: {colors['window']};
                border: 1px solid {colors['border']};
                border-radius: 12px;
            }}
            
            QPushButton.dashboard-button {{
                background-color: {colors['base']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 8px;
                font-size: 11px;
                font-weight: 500;
            }}
            
            QPushButton.dashboard-button:hover {{
                background-color: {colors['alternate_base']};
                border-color: {colors['accent']};
            }}
            
            QPushButton.dashboard-button[state="on"] {{
                background-color: {colors['accent']};
                color: {colors['highlight_text']};
                border-color: {colors['accent']};
            }}
            
            QLabel.widget-value {{
                font-size: 18px;
                font-weight: 600;
                color: {colors['text']};
            }}
            
            QLabel.widget-label {{
                font-size: 10px;
                color: {colors['border']};
            }}
        """
