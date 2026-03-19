"""
System Tray Manager for Prism Desktop.
Uses QSystemTrayIcon (native Qt) for reliable click handling across all
desktop environments, including KDE/SNI where pystray's left-click
delivery is unreliable.
"""

import sys
from io import BytesIO
from typing import Callable, Optional

from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import QObject, Qt, pyqtSignal


class TraySignals(QObject):
    """Qt signals for tray icon events."""
    left_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    quit_clicked = pyqtSignal()


class TrayManager:
    """Manages the system tray icon using QSystemTrayIcon."""

    def __init__(
        self,
        on_left_click: Optional[Callable] = None,
        on_settings: Optional[Callable] = None,
        on_quit: Optional[Callable] = None,
        theme: str = 'dark',
    ):
        self.on_left_click = on_left_click
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.theme = theme

        self._tray: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None

        # Qt signals (same interface as before — callers connect to these)
        self.signals = TraySignals()

        if on_left_click:
            self.signals.left_clicked.connect(on_left_click)
        if on_settings:
            self.signals.settings_clicked.connect(on_settings)
        if on_quit:
            self.signals.quit_clicked.connect(on_quit)

    # ------------------------------------------------------------------
    # Icon creation (PIL → QIcon)
    # ------------------------------------------------------------------

    def create_icon_image(self, size: int = 64) -> Image.Image:
        """Create a stylized 'Prism Desktop' isometric cube icon."""
        scale = 4
        canvas_size = size * scale
        image = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        cx = canvas_size // 2
        cy = canvas_size // 2

        radius = 28 * scale
        h_span = int(radius * 0.866)
        v_half = int(radius * 0.5)

        p_center    = (cx, cy)
        p_top       = (cx, cy - radius)
        p_bot_left  = (cx - h_span, cy + v_half)
        p_bot_right = (cx + h_span, cy + v_half)

        color_left   = (0, 229, 255, 255)
        color_right  = (213, 0, 249, 255)
        color_bottom = (41, 98, 255, 255)
        bg_color     = (30, 30, 30, 255)

        if self.theme != 'dark':
            color_left   = (0, 180, 210, 255)
            color_right  = (180, 0, 200, 255)
            color_bottom = (25, 60, 200, 255)
            bg_color     = (255, 255, 255, 255)

        bg_pad    = 1 * scale
        bg_radius = 10 * scale
        if hasattr(draw, 'rounded_rectangle'):
            draw.rounded_rectangle(
                [bg_pad, bg_pad, canvas_size - bg_pad, canvas_size - bg_pad],
                radius=bg_radius,
                fill=bg_color,
            )
        else:
            draw.rectangle(
                [bg_pad, bg_pad, canvas_size - bg_pad, canvas_size - bg_pad],
                fill=bg_color,
            )

        draw.polygon([p_center, p_top, p_bot_left],  fill=color_left)
        draw.polygon([p_center, p_top, p_bot_right], fill=color_right)
        draw.polygon([p_center, p_bot_left, p_bot_right], fill=color_bottom)

        resampler = getattr(Image, 'Resampling', Image).LANCZOS
        return image.resize((size, size), resampler)

    def _to_qicon(self, pil_image: Image.Image) -> QIcon:
        """Convert a PIL Image to a QIcon."""
        buf = BytesIO()
        pil_image.save(buf, format='PNG')
        buf.seek(0)
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue())
        return QIcon(pixmap)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Create and show the tray icon. Must be called from the Qt main thread."""
        qicon = self._to_qicon(self.create_icon_image())

        self._tray = QSystemTrayIcon(qicon)
        self._tray.setToolTip("Prism Desktop - Home Assistant")

        # Context menu (shown on right-click).
        # WA_TranslucentBackground lets the rounded corners actually clip;
        # without it Qt draws the border-radius visually but the window stays
        # rectangular underneath.
        self._menu = QMenu()
        self._menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._menu.setStyleSheet(self._menu_stylesheet())
        show_action = self._menu.addAction("Show Dashboard")
        show_action.triggered.connect(self.signals.left_clicked)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.signals.quit_clicked)

        self._tray.setContextMenu(self._menu)

        # activated covers left-click (Trigger), double-click (DoubleClick),
        # and middle-click (MiddleClick) — all toggle the dashboard.
        self._tray.activated.connect(self._on_activated)

        self._tray.show()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """Handle tray icon activation (left-click, double-click, etc.)."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.signals.left_clicked.emit()

    def stop(self):
        """Hide and destroy the tray icon."""
        if self._tray:
            self._tray.hide()
            self._tray = None

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def set_theme(self, theme: str):
        """Update the icon and menu colours for the current theme."""
        self.theme = theme
        if self._tray:
            self._tray.setIcon(self._to_qicon(self.create_icon_image()))
        if self._menu:
            self._menu.setStyleSheet(self._menu_stylesheet())

    def update_title(self, title: str):
        """Update the tray icon tooltip."""
        if self._tray:
            self._tray.setToolTip(title)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _menu_stylesheet(self) -> str:
        """Return a theme-aware stylesheet for the tray context menu."""
        if self.theme == 'dark':
            bg        = '#1e1e1e'
            fg        = '#ffffff'
            highlight = '#0078d4'
            hl_text   = '#ffffff'
            border    = '#555555'
            separator = '#444444'
        else:
            bg        = '#ffffff'
            fg        = '#1e1e1e'
            highlight = '#0078d4'
            hl_text   = '#ffffff'
            border    = '#d1d1d1'
            separator = '#e0e0e0'

        return (
            f"QMenu {{"
            f"  background-color: {bg};"
            f"  color: {fg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 8px;"
            f"  padding: 4px;"
            f"}}"
            f"QMenu::item {{"
            f"  padding: 5px 20px 5px 12px;"
            f"  border-radius: 5px;"
            f"  margin: 1px 4px;"
            f"}}"
            f"QMenu::item:selected {{"
            f"  background-color: {highlight};"
            f"  color: {hl_text};"
            f"}}"
            f"QMenu::separator {{"
            f"  height: 1px;"
            f"  background: {separator};"
            f"  margin: 4px 8px;"
            f"}}"
        )
