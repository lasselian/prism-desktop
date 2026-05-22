
"""
Settings Widget
Clean, minimalist, and bug-free implementation of the Settings panel.
"""

import os
import shutil
import subprocess
import sys
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QFormLayout,
    QFrame, QColorDialog, QApplication
)
from ui.widgets.toggle_switch import ToggleSwitch
from PyQt6.QtCore import Qt, pyqtSignal, pyqtProperty, pyqtSlot, QUrl, QTimer, QRectF, QPropertyAnimation, QEasingCurve, QThread
from PyQt6.QtGui import QFont, QColor, QDesktopServices, QIcon, QPixmap, QConicalGradient, QPen, QBrush, QPainter
from core.utils import SYSTEM_FONT
from core.localization_manager import t, current_language, supported_languages, init_localization

from core.build_info import APP_VERSION, get_display_version
from core.worker_threads import ConnectionTestThread
from ui.icons import Icons, get_mdi_font
from services.update_checker import UpdateCheckerThread
from services.location_manager import (
    is_geoclue2_available, ensure_desktop_file,
    get_distro_info, get_geoclue2_install_hint,
)
try:
    from services.wayland_global_shortcut import is_kde_wayland_session, is_wayland_session, supports_wayland_global_shortcuts
except Exception:
    def is_kde_wayland_session():
        return False

    def is_wayland_session():
        return False

    def supports_wayland_global_shortcuts():
        return False

class PinButton(QPushButton):
    """Pin toggle button with a one-shot border animation on each click."""

    _EFFECT_COLORS = {
        'Rainbow':        ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"],
        'Aurora Borealis':["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"],
        'Prism Shard':    ["#26C6DA", "#EC407A", "#FFCA28", "#CFD8DC", "#26C6DA"],
        'Liquid Mercury': ["#37474F", "#78909C", "#CFD8DC", "#ECEFF1", "#CFD8DC", "#78909C", "#37474F"],
    }

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._progress = 0.0
        self._colors = self._EFFECT_COLORS['Rainbow']
        self._anim = QPropertyAnimation(self, b"anim_progress", self)
        self._anim.setDuration(1500)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self.clicked.connect(self._play)

    def _get_progress(self): return self._progress
    def _set_progress(self, v):
        self._progress = v
        self.update()
    anim_progress = pyqtProperty(float, _get_progress, _set_progress)

    def set_effect(self, effect: str):
        self._colors = self._EFFECT_COLORS.get(effect)  # None → no animation

    def _play(self):
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._progress > 0.0 and self._colors:
            opacity = 1.0 if self._progress <= 0.8 else (1.0 - self._progress) / 0.2
            effect = next((k for k, v in self._EFFECT_COLORS.items() if v is self._colors), '')
            speed = 0.9 if effect == 'Prism Shard' else (1.2 if effect == 'Liquid Mercury' else 1.5)
            angle = self._progress * 360.0 * speed
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            gradient = QConicalGradient(rect.center(), angle)
            for i, color in enumerate(self._colors):
                gradient.setColorAt(i / (len(self._colors) - 1), QColor(color))
            pen = QPen(QBrush(gradient), 2)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setOpacity(opacity)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
            painter.end()


class SettingsWidget(QWidget):
    """
    Main settings screen.
    Uses QFormLayout for clean alignment of labels and fields.
    """
    
    settings_saved = pyqtSignal(dict)
    back_requested = pyqtSignal()
    
    def __init__(self, config: dict, theme_manager=None, input_manager=None, current_version="0.0.0", parent=None):
        super().__init__(parent)
        self.config = config
        self.current_version = current_version
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        
        self._test_thread: Optional[ConnectionTestThread] = None
        self._update_thread = None
        self._auto_update_thread = None
        self._geoclue_thread = None
        self._pin_window = False

        self.setup_ui()
        self.load_config()
        self._update_shortcut_controls()
        
        # Connect input manager if available
        if self.input_manager:
            self.input_manager.recorded.connect(self.on_shortcut_recorded)
        
    def _update_stylesheet(self):
        """Build and apply theme-dependent stylesheet."""
        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            # Fallback to dark theme colors
            colors = {
                'text': '#e0e0e0',
                'window_text': '#ffffff',
                'border': '#555555',
                'base': '#2d2d2d',
                'button': '#3d3d3d',
                'button_text': '#ffffff',
                'accent': '#007aff',
            }
        
        # Determine if we're in light mode for input styling
        is_light = colors.get('text', '#ffffff') == '#1e1e1e'
        
        # Input backgrounds: slightly darker/lighter than base
        if is_light:
            input_bg = "rgba(0, 0, 0, 0.06)"
            input_border = "rgba(0, 0, 0, 0.25)"
            input_focus_bg = "rgba(0, 0, 0, 0.08)"
            section_header_color = "#555555"  # Dark gray for light mode
        else:
            input_bg = "rgba(255, 255, 255, 0.08)"
            input_border = "rgba(255, 255, 255, 0.1)"
            input_focus_bg = "rgba(255, 255, 255, 0.12)"
            section_header_color = "#8e8e93"  # Apple gray for dark mode
            
        # Pill Background (Semi-transparent container for readability)
        if is_light:
            pill_bg = "rgba(255, 255, 255, 0.85)"
            pill_border = "rgba(0, 0, 0, 0.12)"
        else:
            pill_bg = "rgba(30, 30, 30, 0.6)"
            pill_border = "rgba(255, 255, 255, 0.05)"
            
        from ui.styles import Typography, Dimensions
        
        # Push accent + text color into any ToggleSwitch children already created
        accent = colors['accent']
        text   = colors['text']
        for toggle in self.findChildren(ToggleSwitch):
            toggle.set_accent(accent)
            toggle.set_text_color(text)

        self.setStyleSheet(f"""
            QWidget {{ 
                font-family: {Typography.FONT_FAMILY_UI}; 
                font-size: {Typography.SIZE_BODY};
                color: {colors['text']};
            }}
            QLabel#headerTitle {{
                font-size: {Typography.SIZE_HEADER};
                font-weight: {Typography.WEIGHT_SEMIBOLD};
                color: {colors['window_text']};
            }}
            QLabel#sectionHeader {{
                font-size: {Typography.SIZE_SMALL};
                font-weight: {Typography.WEIGHT_BOLD};
                color: {section_header_color};
            }}
            QLineEdit, QComboBox {{
                background-color: {input_bg};
                border: 1px solid {input_border};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: 0px 10px;
                min-height: 32px;
                max-height: 32px;
                color: {colors['text']};
                selection-background-color: {colors['accent']};
            }}
            QLineEdit[locked="true"] {{
                background-color: rgba(0, 0, 0, 0.18);
                border: 1px solid rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.55);
            }}
            QComboBox QAbstractItemView {{
                background-color: {colors['base']};
                border: 1px solid {colors['border']};
                color: {colors['text']};
                selection-background-color: {colors['accent']};
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1px solid {colors['accent']};
                background-color: {input_focus_bg};
            }}
            QPushButton {{
                background-color: {colors['button']};
                color: {colors['button_text']};
                border: 1px solid {colors['border']};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: 0px {Dimensions.PADDING_LARGE};
                min-height: 32px;
                max-height: 32px;
                font-weight: {Typography.WEIGHT_MEDIUM};
            }}
            QPushButton:hover {{ background-color: {colors['accent']}; color: white; }}
            QPushButton:pressed {{ background-color: {colors['accent']}; }}
            
            QPushButton#primaryBtn {{
                background-color: {colors['accent']};
                color: white;
                border: none;
            }}
            QPushButton#primaryBtn:hover {{ background-color: #006ce6; }}
            
            QPushButton#rowBtn {{
                min-width: 42px;
                max-width: 42px;
                min-height: 32px;
                max-height: 32px;
                border-radius: {Dimensions.RADIUS_SMALL};
                background-color: transparent;
                border: 1px solid {colors['border']};
                color: {colors['text']};
                font-size: 11px;
                padding: 0px;
            }}
            QPushButton#rowBtn:checked {{
                background-color: {colors['accent']};
                border: 1px solid {colors['accent']};
                color: white;
            }}
            QPushButton#recordBtn {{
                background-color: #C62828;
                border: none;
                border-radius: {Dimensions.RADIUS_MEDIUM};
            }}
            QPushButton#recordBtn:hover {{
                background-color: #B71C1C;
            }}
            QPushButton#recordBtn:checked {{
                background-color: #8E0000;
            }}
            
            QWidget#recordIcon {{
                background-color: white;
                border-radius: {Dimensions.RADIUS_MEDIUM};
            }}
            

            QPushButton#updateBtn {{
                background-color: {colors['button']};
                border: 1px solid {colors['border']};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: 0px 12px;
            }}
            QPushButton#updateBtn:hover {{
                background-color: {colors['accent']};
                color: white;
                border-color: {colors['accent']};
            }}

            QFrame#settingsPill {{
                background-color: {pill_bg};
                border: 1px solid {pill_border};
                border-radius: 16px;
            }}

            QPushButton#pinBtn {{
                background-color: transparent;
                border: 1px solid {colors['border']};
                border-radius: 6px;
                color: {colors['text']};
                font-size: 14px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0px;
            }}
            QPushButton#pinBtn:hover {{
                border-color: {colors['accent']};
                color: {colors['accent']};
                background-color: transparent;
            }}
            QPushButton#pinBtn:checked {{
                background-color: {colors['accent']};
                border-color: {colors['accent']};
                color: white;
            }}
        """)
        
    def setup_ui(self):
        # Apply dynamic theming
        self._update_stylesheet()
        
        # Main Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Listen for theme changes
        if self.theme_manager:
            self.theme_manager.theme_changed.connect(self._update_stylesheet)
        

        
        # 1. Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 10)
        
        self.back_btn = QPushButton(t("settings.back_btn"))
        self.back_btn.setMinimumWidth(70)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self.back_requested.emit)

        title = QLabel(t("settings.title"))
        title.setObjectName("headerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.save_btn = QPushButton(t("settings.save_btn"))
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setMinimumWidth(70)
        self.save_btn.clicked.connect(self.save_settings)
        
        header_layout.addWidget(self.back_btn)
        header_layout.addWidget(title)
        header_layout.addWidget(self.save_btn)
        
        layout.addLayout(header_layout)
        
        # 2. Pill Container for Form Content
        self.pill_frame = QFrame()
        self.pill_frame.setObjectName("settingsPill")
        self.pill_layout = QVBoxLayout(self.pill_frame)
        self.pill_layout.setContentsMargins(20, 20, 20, 20)
        self.pill_layout.setSpacing(10)

        layout.addWidget(self.pill_frame)

        self.form = None  # Created fresh by each _add_section_header call
        self._form_sections = []  # Track all section forms for label-width sync
        
        # --- Home Assistant Section ---
        self._add_section_header(t("settings.section.home_assistant"))

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(t("settings.ha.url_placeholder"))
        self.form.addRow(t("settings.ha.url_label"), self.url_input)

        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText(t("settings.ha.token_placeholder"))
        self.form.addRow(t("settings.ha.token_label"), self.token_input)

        # Full-width Test Connection button
        self.test_btn = QPushButton(t("settings.ha.test_btn"))
        self.test_btn.clicked.connect(self.test_connection)
        self.form.addRow("", self.test_btn)

        # Location tracking (Windows + Linux)
        if sys.platform in ('win32', 'linux'):
            self.location_check = ToggleSwitch(t("settings.ha.location_toggle"))
            self.location_check.setToolTip(t("settings.ha.location_tooltip"))
            self.form.addRow("", self.location_check)

        # --- Appearance Section ---
        self._add_section_header(t("settings.section.appearance"))

        # Theme
        self.theme_combo = QComboBox()
        self.theme_combo.addItems([
            t("settings.appearance.theme_system"),
            t("settings.appearance.theme_light"),
            t("settings.appearance.theme_dark"),
        ])
        self.theme_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.theme_label"), self.theme_combo)

        from ui.widgets.effect_combobox import EffectComboBox
        self.border_effect_combo = EffectComboBox()
        self.border_effect_combo.addItems(["Rainbow", "Aurora Borealis", "Prism Shard", "Liquid Mercury", "None"])
        self.border_effect_combo.setMinimumWidth(120)
        self.border_effect_combo.currentTextChanged.connect(self.on_border_effect_changed)
        self.form.addRow(t("settings.appearance.border_label"), self.border_effect_combo)

        self.button_style_combo = QComboBox()
        self.button_style_combo.addItems([
            t("settings.appearance.button_style_gradient"),
            t("settings.appearance.button_style_flat"),
        ])
        self.button_style_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.button_style_label"), self.button_style_combo)

        # Language selector
        self._language_codes = list(supported_languages().keys())
        self.language_combo = QComboBox()
        self.language_combo.addItems(list(supported_languages().values()))
        self.language_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.language_label"), self.language_combo)

        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

        self.tray_position_combo = QComboBox()
        self.tray_position_combo.addItems([
            t("settings.appearance.tray_bottom"),
            t("settings.appearance.tray_top"),
        ])
        self.tray_position_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.tray_label"), self.tray_position_combo)

        self.temperature_unit_combo = QComboBox()
        self.temperature_unit_combo.addItems([
            t("settings.appearance.temp_celsius"),
            t("settings.appearance.temp_fahrenheit"),
        ])
        self.temperature_unit_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.temp_label"), self.temperature_unit_combo)

        self.pages_combo = QComboBox()
        self.pages_combo.addItems(["1", "2", "3", "4"])
        self.pages_combo.setMinimumWidth(120)
        self.form.addRow(t("settings.appearance.pages_label"), self.pages_combo)

        # Toggles
        self.show_dimming_check = ToggleSwitch(t("settings.appearance.dimming_toggle"))
        self.show_dimming_check.setToolTip(t("settings.appearance.dimming_tooltip"))

        self.glass_ui_check = ToggleSwitch(t("settings.appearance.glass_toggle"))
        self.glass_ui_check.setToolTip(t("settings.appearance.glass_tooltip"))
        if sys.platform.startswith('linux'):
            self.glass_ui_check.setVisible(False)

        self.form.addRow("", self.show_dimming_check)
        self.form.addRow("", self.glass_ui_check)
        
        # --- Shortcut Section ---
        self._add_section_header(t("settings.section.shortcut"))

        shortcut_container = QWidget()
        shortcut_container_layout = QVBoxLayout(shortcut_container)
        shortcut_container_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_container_layout.setSpacing(2)

        shortcut_row = QHBoxLayout()
        shortcut_row.setContentsMargins(0, 0, 0, 0)
        self.shortcut_display = QLineEdit()
        self.shortcut_display.setReadOnly(True)
        self.shortcut_display.setPlaceholderText(t("settings.shortcut.placeholder"))
        
        self.record_btn = QPushButton()
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setCheckable(True)
        self.record_btn.setFixedSize(40, 32)
        self.record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_btn.clicked.connect(self.toggle_recording)
        
        # Inner Icon Widget
        btn_layout = QHBoxLayout(self.record_btn)
        btn_layout.setContentsMargins(0,0,0,0)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.record_icon = QWidget()
        self.record_icon.setObjectName("recordIcon")
        self.record_icon.setFixedSize(12, 12)
        self.record_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) # Let clicks pass
        btn_layout.addWidget(self.record_icon)
        
        # Layout: Input (80%) - Gap - Button - Gap (10%)
        shortcut_row.addWidget(self.shortcut_display, 8)
        shortcut_row.addSpacing(12)
        shortcut_row.addWidget(self.record_btn)
        shortcut_row.addStretch(2) 
        shortcut_container_layout.addLayout(shortcut_row)

        self.shortcut_aux = QWidget()
        shortcut_aux_layout = QVBoxLayout(self.shortcut_aux)
        shortcut_aux_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_aux_layout.setSpacing(1)

        self.shortcut_hint = QLabel("")
        self.shortcut_hint.setWordWrap(True)
        self.shortcut_hint.setStyleSheet("color: #aaa; font-size: 11px;")
        self.shortcut_hint.hide()
        shortcut_aux_layout.addWidget(self.shortcut_hint)

        self.kde_shortcuts_btn = QPushButton(t("settings.shortcut.kde_btn"))
        self.kde_shortcuts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kde_shortcuts_btn.clicked.connect(self.open_kde_shortcuts)
        self.kde_shortcuts_btn.hide()
        shortcut_aux_layout.addWidget(self.kde_shortcuts_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.shortcut_aux.hide()
        shortcut_container_layout.addWidget(self.shortcut_aux)
        self.form.addRow(t("settings.shortcut.label"), shortcut_container)
        
        # --- Support Section ---
        self._add_section_header(t("settings.section.support"))

        # Update Check
        update_row = QHBoxLayout()
        update_row.setContentsMargins(0, 0, 0, 0)

        self.update_btn = QPushButton(t("settings.support.update_btn"))
        self.update_btn.setObjectName("updateBtn")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.check_for_updates)

        self.update_label = QLabel()
        self.update_label.setTextFormat(Qt.TextFormat.RichText)
        self.update_label.setOpenExternalLinks(False)
        self.update_label.linkActivated.connect(self._on_version_label_clicked)
        self._set_version_label_collapsed()

        self.pin_btn = PinButton(Icons.PIN)
        self.pin_btn.setFont(get_mdi_font(16))
        self.pin_btn.setObjectName("pinBtn")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setFixedSize(28, 28)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip(t("settings.appearance.pin_tooltip"))
        self.pin_btn.clicked.connect(self._on_pin_toggled)
        self.border_effect_combo.currentTextChanged.connect(self.pin_btn.set_effect)

        update_row.addWidget(self.update_btn)
        update_row.addSpacing(10)
        update_row.addWidget(self.update_label)
        update_row.addStretch()
        update_row.addWidget(self.pin_btn)

        self.form.addRow(t("settings.support.update_label"), update_row)
        self._sync_form_label_widths()
        self._update_stylesheet()  # re-run now that toggles exist

    def _sync_form_label_widths(self):
        """Force all section forms to use the same label column width."""
        max_w = 0
        for form in self._form_sections:
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item and item.widget():
                    item.widget().ensurePolished()
                    max_w = max(max_w, item.widget().sizeHint().width())
        for form in self._form_sections:
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item and item.widget():
                    item.widget().setMinimumWidth(max_w)

    def _add_section_header(self, text):
        """Add a section header label and start a fresh form layout for that section."""
        lbl = QLabel(text)
        lbl.setObjectName("sectionHeader")
        self.pill_layout.addWidget(lbl)

        self.form = QFormLayout()
        self.form.setVerticalSpacing(8)
        self.form.setHorizontalSpacing(16)
        self.pill_layout.addLayout(self.form)
        self._form_sections.append(self.form)

    def get_content_height(self):
        """
        Calculate the exact height needed to show all settings without scrolling.
        Used by the Dashboard to resize the window appropriately when switching views.
        """
        # Force layout update to get accurate size
        self.adjustSize()
        return self.sizeHint().height()
        
    def load_config(self):
        """Load current config values."""
        ha = self.config.get('home_assistant', {})
        self.url_input.setText(ha.get('url', ''))
        self.token_input.setText(ha.get('token', ''))
        
        app = self.config.get('appearance', {})
        theme_map = {'system': 0, 'light': 1, 'dark': 2}
        idx = theme_map.get(app.get('theme', 'system'), 0)
        self.theme_combo.setCurrentIndex(idx)

        tray_position_map = {'bottom': 0, 'top': 1}
        self.tray_position_combo.setCurrentIndex(
            tray_position_map.get(app.get('tray_position', 'bottom'), 0)
        )
        temperature_unit_map = {'celsius': 0, 'fahrenheit': 1}
        self.temperature_unit_combo.setCurrentIndex(
            temperature_unit_map.get(app.get('temperature_unit', 'celsius'), 0)
        )
        
        effect = app.get('border_effect', 'Rainbow')
        
        effect_idx = self.border_effect_combo.findText(effect)
        
        # Prevent animation trigger on initial load
        self.border_effect_combo.blockSignals(True)
        if effect_idx >= 0:
            self.border_effect_combo.setCurrentIndex(effect_idx)
            self.border_effect_combo.set_effect(effect, animate=False)
        else:
             self.border_effect_combo.setCurrentIndex(0)
             self.border_effect_combo.set_effect("Rainbow", animate=False)
             
        button_style_map = {'gradient': 0, 'flat': 1}
        self.button_style_combo.setCurrentIndex(
            button_style_map.get(app.get('button_style', 'gradient'), 0)
        )
             
        self.show_dimming_check.setChecked(app.get('show_dimming', False))
        self.glass_ui_check.setChecked(app.get('glass_ui', False) and not sys.platform.startswith('linux'))
        pinned = app.get('pin_window', False)
        self._pin_window = pinned
        self.pin_btn.set_effect(app.get('border_effect', 'Rainbow'))
        self.pin_btn.setChecked(pinned)
        pages = app.get('pages', 3)
        self.pages_combo.setCurrentIndex(max(0, min(pages - 1, self.pages_combo.count() - 1)))

        saved_lang = app.get('language', current_language())
        lang_idx = self._language_codes.index(saved_lang) if saved_lang in self._language_codes else 0
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(lang_idx)
        self.language_combo.blockSignals(False)

        if sys.platform in ('win32', 'linux'):
            self.location_check.setChecked(
                self.config.get('mobile_app', {}).get('location_enabled', False)
            )
             
        self.border_effect_combo.blockSignals(False)
        
        
        sc = self.config.get('shortcut', {})
        self.shortcut_display.setText(sc.get('value', ''))
        self._update_shortcut_controls()
        
    def save_settings(self):
        """Save and emit config."""
        self._cleanup_threads()
        
        # HA
        if 'home_assistant' not in self.config: self.config['home_assistant'] = {}
        self.config['home_assistant']['url'] = self.url_input.text().strip()
        self.config['home_assistant']['token'] = self.token_input.text().strip()
        
        # Appearance
        theme_map = {0: 'system', 1: 'light', 2: 'dark'}
        if self.theme_manager:
            self.theme_manager.set_theme(theme_map.get(self.theme_combo.currentIndex(), 'system'))
        tray_position_map = {0: 'bottom', 1: 'top'}
        temperature_unit_map = {0: 'celsius', 1: 'fahrenheit'}
        old_language = self.config.get('appearance', {}).get('language', 'en')
        new_language = self._language_codes[self.language_combo.currentIndex()]

        self.config.setdefault('appearance', {})
        self.config['appearance'].update({
            'theme': theme_map.get(self.theme_combo.currentIndex(), 'system'),
            'tray_position': tray_position_map.get(self.tray_position_combo.currentIndex(), 'bottom'),
            'temperature_unit': temperature_unit_map.get(self.temperature_unit_combo.currentIndex(), 'celsius'),
            'border_effect': self.border_effect_combo.currentText(),
            'button_style': {0: 'Gradient', 1: 'Flat'}.get(self.button_style_combo.currentIndex(), 'Gradient'),
            'show_dimming': self.show_dimming_check.isChecked(),
            'glass_ui': self.glass_ui_check.isChecked(),
            'pin_window': self._pin_window,
            'pages': self.pages_combo.currentIndex() + 1,
            'language': new_language,
        })

        if sys.platform in ('win32', 'linux'):
            new_location_enabled = self.location_check.isChecked()
            self.config.setdefault('mobile_app', {})['location_enabled'] = new_location_enabled

            # On Linux, verify GeoClue2 is available when first enabling
            if sys.platform == 'linux' and new_location_enabled:
                self._check_geoclue2_and_setup()

        # Shortcut handled by record signal, but good to ensure consistency
        # (Shortcut saves immediately on record in config dict)
        if 'shortcut' not in self.config: self.config['shortcut'] = {}
        
        self.settings_saved.emit(self.config)

    # --- Linux location helpers ---

    def _check_geoclue2_and_setup(self):
        """Check GeoClue2 availability on Linux and create .desktop file."""
        import asyncio

        class _Worker(QThread):
            done = pyqtSignal(bool, str)

            def run(self):
                available = asyncio.run(is_geoclue2_available())
                cmd = "" if available else get_geoclue2_install_hint(get_distro_info()["id"])
                self.done.emit(available, cmd)

        def _on_done(available, cmd):
            if not available:
                self.location_check.setChecked(False)
                self.config.setdefault('mobile_app', {})['location_enabled'] = False
                dashboard = self.window()
                if hasattr(dashboard, 'show_toast'):
                    from ui.notifications import notify_geoclue2_missing
                    QTimer.singleShot(350, lambda: notify_geoclue2_missing(dashboard, cmd))
            else:
                ensure_desktop_file()

        self._geoclue_thread = _Worker()
        self._geoclue_thread.done.connect(_on_done)
        self._geoclue_thread.start()

    # --- Logic ---

    def _on_language_changed(self, index: int):
        selected_lang = self._language_codes[index]
        init_localization(selected_lang)

    def on_border_effect_changed(self, text):
        self.border_effect_combo.set_effect(text)




    def _on_pin_toggled(self, checked: bool):
        self._pin_window = checked
        self.config.setdefault('appearance', {})['pin_window'] = checked

    def toggle_recording(self, checked):
        if self._should_delegate_shortcuts_to_kde():
            self.record_btn.setChecked(False)
            return

        if self._is_unsupported_wayland_shortcut_env():
            self.record_btn.setChecked(False)
            return

        if not self.input_manager:
            self.record_btn.setChecked(False)
            return
            
        if checked:
            # Stop State (Square)
            self.record_icon.setStyleSheet("background-color: white; border-radius: 2px;")
            self.shortcut_display.setText(t("settings.shortcut.recording"))
            self.input_manager.start_recording()
        else:
            # Record State (Circle)
            self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
            self.input_manager.restore_shortcut()
            # Restore previous text if cancelled
            sc = self.config.get('shortcut', {})
            if self.shortcut_display.text() == t("settings.shortcut.recording"):
                self.shortcut_display.setText(sc.get('value', ''))

    @pyqtSlot(dict)
    def on_shortcut_recorded(self, shortcut):
        if not self.record_btn.isChecked():
            return
            
        self.record_btn.setChecked(False)
        # Reset Icon
        self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
        self.shortcut_display.setText(shortcut.get('value', ''))
        if 'shortcut' not in self.config: self.config['shortcut'] = {}
        self.config['shortcut'] = shortcut
        
        # Immediately re-register the new shortcut so it works without needing Save
        self.input_manager.update_shortcut(shortcut)

    def _should_delegate_shortcuts_to_kde(self) -> bool:
        """Return whether KDE owns global shortcut changes on this system."""
        return sys.platform == 'linux' and is_kde_wayland_session()

    def _is_unsupported_wayland_shortcut_env(self) -> bool:
        """Return whether app-toggle shortcuts are unsupported on this Wayland desktop."""
        return sys.platform == 'linux' and is_wayland_session() and not supports_wayland_global_shortcuts()

    def _update_shortcut_controls(self):
        """Adjust app-toggle shortcut controls for the current desktop."""
        if self._should_delegate_shortcuts_to_kde():
            self.record_btn.setChecked(False)
            self.record_btn.setEnabled(False)
            self.record_btn.hide()
            self.shortcut_display.setEnabled(False)
            self.shortcut_display.setProperty("locked", True)
            self.shortcut_display.setText(t("settings.shortcut.disabled"))
            self.shortcut_display.setToolTip("")
            self.shortcut_hint.setText(t("settings.shortcut.kde_hint"))
            self.shortcut_aux.show()
            self.shortcut_hint.show()
            self.kde_shortcuts_btn.show()
        elif self._is_unsupported_wayland_shortcut_env():
            self.record_btn.setChecked(False)
            self.record_btn.setEnabled(False)
            self.record_btn.hide()
            self.shortcut_display.setEnabled(False)
            self.shortcut_display.setProperty("locked", True)
            self.shortcut_display.setText(t("settings.shortcut.disabled"))
            self.shortcut_display.setToolTip("")
            self.shortcut_hint.setText(t("settings.shortcut.wayland_hint"))
            self.shortcut_aux.show()
            self.shortcut_hint.show()
            self.kde_shortcuts_btn.hide()
        else:
            self.record_btn.show()
            self.shortcut_display.setEnabled(True)
            self.shortcut_display.setProperty("locked", False)
            sc = self.config.get('shortcut', {})
            self.shortcut_display.setText(sc.get('value', ''))
            self.shortcut_display.setToolTip("")
            self.record_btn.setEnabled(True)
            self.record_btn.setToolTip("")
            self.shortcut_aux.hide()
            self.shortcut_hint.hide()
            self.kde_shortcuts_btn.hide()

        self.style().unpolish(self.shortcut_display)
        self.style().polish(self.shortcut_display)
        self.shortcut_display.update()

    def open_kde_shortcuts(self):
        """Open KDE's shortcut settings module when possible."""
        # Strip AppImage library overrides so system KDE tools use their own libs.
        env = os.environ.copy()
        for key in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
            env.pop(key, None)

        for program in ("kcmshell6", "systemsettings"):
            exe = shutil.which(program, path=env.get("PATH"))
            if exe:
                try:
                    subprocess.Popen([exe, "kcm_keys"], env=env)
                    return
                except OSError:
                    continue

        QDesktopServices.openUrl(QUrl("settings://keyboard/shortcuts"))

    def test_connection(self):
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()

        if not url or not token:
            from ui.notifications import notify_missing_credentials
            notify_missing_credentials(self.window())
            return

        self.test_btn.setEnabled(False)

        if self._test_thread and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(500)

        # Run connection check in background to avoid freezing UI
        self._test_thread = ConnectionTestThread(url, token)
        self._test_thread.finished.connect(self.on_test_complete)
        self._test_thread.start()

    @pyqtSlot(bool, str)
    def on_test_complete(self, success, message):
        self.test_btn.setEnabled(True)
        from ui.notifications import notify_connection_test_result
        notify_connection_test_result(self.window(), success, message)

    _VERSION_STYLE = 'style="color: #aaa; font-size: 11px; text-decoration: none;"'
    _HASH_STYLE = 'style="color: #FFC90E; font-size: 11px; text-decoration: none;"'

    def _set_version_label_collapsed(self):
        full = get_display_version()
        has_commit = full != APP_VERSION
        if has_commit:
            self.update_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update_label.setText(
                f'<span style="color: #aaa; font-size: 11px;"><a href="expand" {self._VERSION_STYLE}>v{APP_VERSION}</a></span>'
            )
        else:
            self.update_label.setCursor(Qt.CursorShape.ArrowCursor)
            self.update_label.setText(
                f'<span style="color: #aaa; font-size: 11px;">v{APP_VERSION}</span>'
            )

    def _set_version_label_expanded(self):
        full = get_display_version()
        suffix = full[len(APP_VERSION):]
        if suffix:
            self.update_label.setCursor(Qt.CursorShape.PointingHandCursor)
            commit = suffix.strip(" ()")
            self.update_label.setText(
                f'<span style="color: #aaa; font-size: 11px;"><a href="collapse" {self._VERSION_STYLE}>v{APP_VERSION}</a>'
                f' - <a href="copy" {self._HASH_STYLE}>({commit})</a></span>'
            )

    def _on_version_label_clicked(self, href: str):
        if href == "expand":
            self._set_version_label_expanded()
        elif href == "collapse":
            self._set_version_label_collapsed()
        elif href == "copy":
            full = get_display_version()
            QApplication.clipboard().setText(f"v{full}")
            suffix = full[len(APP_VERSION):]
            commit = suffix.strip(" ()")
            self.update_label.setText(
                f'<span style="color: #aaa; font-size: 11px;"><a href="collapse" {self._VERSION_STYLE}>v{APP_VERSION}</a>'
                f' <a href="copy" {self._VERSION_STYLE}>({commit})</a>'
                f' - {t("settings.support.copied")}</span>'
            )
            QTimer.singleShot(3000, self._set_version_label_expanded)


    def check_for_updates(self):
        """Start update check."""
        self._cleanup_threads()
        self.update_btn.setEnabled(False)
        self.update_label.setText(t("settings.support.checking"))
        
        self._update_thread = UpdateCheckerThread(self.current_version)
        self._update_thread.update_available.connect(self.on_update_available)
        self._update_thread.up_to_date.connect(self.on_up_to_date)
        self._update_thread.error_occurred.connect(self.on_update_error)
        self._update_thread.start()
        
    @pyqtSlot(str)
    def on_update_available(self, tag):
        self.update_btn.setEnabled(True)
        self.update_label.setText(t("settings.support.update_available", tag=tag))
        self.update_label.setStyleSheet("color: #FF8C00; font-weight: bold; font-size: 11px;")

        self.update_btn.setText(t("settings.support.install_btn"))
        self.update_btn.clicked.disconnect()
        self.update_btn.clicked.connect(self._start_auto_update)

    def _start_auto_update(self):
        from services.auto_updater import AutoUpdateThread
        self.update_btn.setEnabled(False)
        self.update_label.setText(t("settings.support.updating"))
        self.update_label.setStyleSheet("color: #aaa; font-size: 11px;")

        self._auto_update_thread = AutoUpdateThread()
        self._auto_update_thread.progress.connect(
            lambda msg: self.update_label.setText(msg)
        )
        self._auto_update_thread.success.connect(self._on_auto_update_success)
        self._auto_update_thread.error.connect(self._on_auto_update_error)
        self._auto_update_thread.start()

    @pyqtSlot(str)
    def _on_auto_update_success(self, result):
        if result == "already_up_to_date":
            self.on_up_to_date()
            return
        self.update_label.setText(t("settings.support.restarting"))
        self.update_label.setStyleSheet("color: #34A853; font-size: 11px;")
        from services.auto_updater import restart_app
        QTimer.singleShot(1200, restart_app)

    @pyqtSlot(str)
    def _on_auto_update_error(self, error):
        self.update_label.setText(t("settings.support.update_failed"))
        self.update_label.setStyleSheet("color: #e53935; font-size: 11px;")
        self.update_label.setToolTip(error)
        self.update_btn.setEnabled(True)
        self.update_btn.setText(t("settings.support.install_btn"))
        self.update_btn.clicked.disconnect()
        self.update_btn.clicked.connect(self._start_auto_update)

    @pyqtSlot()
    def on_up_to_date(self):
        self.update_btn.setEnabled(True)
        self.update_label.setText(t("settings.support.up_to_date"))
        self.update_label.setStyleSheet("color: #34A853; font-size: 11px;")
        QTimer.singleShot(3000, self._set_version_label_collapsed)
        
    @pyqtSlot(str)
    def on_update_error(self, error):
        self.update_btn.setEnabled(True)
        self.update_label.setText(t("settings.support.check_failed"))
        self.update_label.setToolTip(error)

    def _cleanup_threads(self):
        if self._test_thread and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(500)
        if self._update_thread and self._update_thread.isRunning():
            self._update_thread.quit()
            self._update_thread.wait(500)
        if self._auto_update_thread and self._auto_update_thread.isRunning():
            self._auto_update_thread.quit()
            self._auto_update_thread.wait(0)
        if self._geoclue_thread and self._geoclue_thread.isRunning():
            self._geoclue_thread.quit()
            self._geoclue_thread.wait(500)

