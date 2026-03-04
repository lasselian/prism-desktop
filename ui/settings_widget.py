
"""
Settings Widget
Clean, minimalist, and bug-free implementation of the Settings panel.
"""

from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFormLayout, 
    QCheckBox, QGraphicsOpacityEffect, QFrame, QColorDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtProperty, pyqtSlot, QUrl
from PyQt6.QtGui import QFont, QColor, QDesktopServices, QIcon, QPixmap
from core.utils import SYSTEM_FONT

from core.worker_threads import ConnectionTestThread
from services.update_checker import UpdateCheckerThread

class SettingsWidget(QWidget):
    """
    Main settings screen.
    Uses QFormLayout for clean alignment of labels and fields.
    """
    
    settings_saved = pyqtSignal(dict)
    back_requested = pyqtSignal()
    
    settings_saved = pyqtSignal(dict)
    back_requested = pyqtSignal()
    
    def __init__(self, config: dict, theme_manager=None, input_manager=None, current_version="0.0.0", parent=None):
        super().__init__(parent)
        self.config = config
        self.current_version = current_version
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        
        self._test_thread: Optional[ConnectionTestThread] = None
        self._opacity = 1.0
        # Opacity effect for animations - DISABLED FOR DEBUGGING
        # self._opacity_effect = QGraphicsOpacityEffect(self)
        # self._opacity_effect.setOpacity(1.0)
        # self.setGraphicsEffect(self._opacity_effect)
        
        self.setup_ui()
        self.load_config()
        
        # Connect input manager if available
        if self.input_manager:
            self.input_manager.recorded.connect(self.on_shortcut_recorded)
        
    def get_opacity(self):
        return self._opacity
    
    def set_opacity(self, val):
        self._opacity = val
        if hasattr(self, '_opacity_effect'):
            self._opacity_effect.setOpacity(val)
        # self._opacity_effect.setOpacity(val)
        
    opacity = pyqtProperty(float, get_opacity, set_opacity)
    
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
            input_bg = "rgba(0, 0, 0, 0.05)"
            input_border = "rgba(0, 0, 0, 0.15)"
            input_focus_bg = "rgba(0, 0, 0, 0.08)"
            checkbox_bg = "rgba(0, 0, 0, 0.05)"
            checkbox_border = "#aaa"
            section_header_color = "#666666"  # Dark gray for light mode
        else:
            input_bg = "rgba(255, 255, 255, 0.08)"
            input_border = "rgba(255, 255, 255, 0.1)"
            input_focus_bg = "rgba(255, 255, 255, 0.12)"
            checkbox_bg = "rgba(255, 255, 255, 0.05)"
            checkbox_border = "#555"
            section_header_color = "#8e8e93"  # Apple gray for dark mode
            
        # Pill Background (Semi-transparent container for readability)
        if is_light:
            pill_bg = "rgba(255, 255, 255, 0.6)"
            pill_border = "rgba(0, 0, 0, 0.05)"
        else:
            pill_bg = "rgba(30, 30, 30, 0.6)"
            pill_border = "rgba(255, 255, 255, 0.05)"
            
        from ui.styles import Typography, Dimensions
        
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
                margin-top: 10px;
                margin-bottom: 2px;
            }}
            QLineEdit, QComboBox {{
                background-color: {input_bg};
                border: 1px solid {input_border};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: 6px 10px;
                color: {colors['text']};
                selection-background-color: {colors['accent']};
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
                padding: {Dimensions.PADDING_MEDIUM} {Dimensions.PADDING_LARGE};
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
            QCheckBox {{ spacing: 8px; color: {colors['text']}; }}
            QCheckBox::indicator {{
                width: 18px; height: 18px;
                border-radius: {Dimensions.RADIUS_SMALL};
                border: 1px solid {checkbox_border};
                background: {checkbox_bg};
            }}
            QCheckBox::indicator:checked {{
                background: {colors['accent']};
                border-color: {colors['accent']};
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
            
            QPushButton#coffeeBtn {{
                background-color: {colors['accent']};
                color: white;
                border: 1px solid {colors['accent']};
                font-weight: {Typography.WEIGHT_MEDIUM};
                font-size: {Typography.SIZE_BODY};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: {Dimensions.PADDING_MEDIUM} 12px;
            }}
            QPushButton#coffeeBtn:hover {{
                background-color: #006ce6;
            }}
            
            QPushButton#updateBtn {{
                background-color: {colors['button']};
                border: 1px solid {colors['border']};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                padding: {Dimensions.PADDING_MEDIUM} 12px;
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
        
        self.back_btn = QPushButton("← Back")
        self.back_btn.setMinimumWidth(70)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self.back_requested.emit)
        
        title = QLabel("Settings")
        title.setObjectName("headerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.save_btn = QPushButton("Save")
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
        pill_layout = QVBoxLayout(self.pill_frame)
        pill_layout.setContentsMargins(20, 20, 20, 20)
        pill_layout.setSpacing(10)
        
        layout.addWidget(self.pill_frame)
        
        # 3. Form Layout (Inside Pill)
        self.form = QFormLayout()
        self.form.setVerticalSpacing(14)
        self.form.setHorizontalSpacing(16)
        
        pill_layout.addLayout(self.form)
        
        # --- Home Assistant Section ---
        self._add_section_header("HOME ASSISTANT")
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://homeassistant.local:8123")
        self.form.addRow("URL:", self.url_input)
        
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("Long-Lived Access Token")
        self.form.addRow("Token:", self.token_input)
        
        # Test Connection Row
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setMinimumWidth(120)
        self.test_btn.clicked.connect(self.test_connection)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #aaa;")
        
        test_row.addWidget(self.test_btn)
        test_row.addSpacing(10)
        test_row.addWidget(self.status_label)
        test_row.addStretch()
        self.form.addRow("", test_row)
        
        # --- Appearance Section ---
        self._add_section_header("APPEARANCE")
        
        # Theme
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.setMinimumWidth(120)
        self.theme_combo.currentIndexChanged.connect(self.on_theme_preview)
        self.form.addRow("Theme:", self.theme_combo)

        # Border Effect
        from ui.widgets.effect_combobox import EffectComboBox
        self.border_effect_combo = EffectComboBox()
        self.border_effect_combo.addItems(["Rainbow", "Aurora Borealis", "Prism Shard", "Liquid Mercury", "None"])
        self.border_effect_combo.setMinimumWidth(120)
        # Connect change to self-update so user sees effect immediately
        self.border_effect_combo.currentTextChanged.connect(self.on_border_effect_changed)
        self.form.addRow("Border Effect:", self.border_effect_combo)
        
        # Button Style
        self.button_style_combo = QComboBox()
        self.button_style_combo.addItems(["Gradient", "Flat"])
        self.button_style_combo.setMinimumWidth(120)
        self.form.addRow("Button Style:", self.button_style_combo)
        

        
        # Show Dimming Option
        self.show_dimming_check = QCheckBox("Show dimming")
        self.show_dimming_check.setToolTip("Fade button color based on brightness level")
        self.show_dimming_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.form.addRow("", self.show_dimming_check)
        
        # Glass UI Option
        self.glass_ui_check = QCheckBox("Glass UI (EXPERIMENTAL)")
        self.glass_ui_check.setToolTip("Use a translucent glass background for the window")
        self.glass_ui_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.form.addRow("", self.glass_ui_check)
        
        # --- Shortcut Section ---
        self._add_section_header("SHORTCUT")

        
        shortcut_row = QHBoxLayout()
        self.shortcut_display = QLineEdit()
        self.shortcut_display.setReadOnly(True)
        self.shortcut_display.setPlaceholderText("None")
        
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
        
        self.form.addRow("App Toggle:", shortcut_row)
        
        if self.theme_manager:
            self.theme_manager.theme_changed.connect(self._update_stylesheet)
        
        # ... (Previous code)
        
        # layout.addLayout(self.form) - Already added to pill_layout
        
        # --- Support Section ---
        self._add_section_header("SUPPORT")
        
        # Update Check
        update_row = QHBoxLayout()
        update_row.setContentsMargins(0, 0, 0, 0)
        
        self.update_btn = QPushButton("Check for Updates")
        self.update_btn.setObjectName("updateBtn")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.check_for_updates)
        
        self.update_label = QLabel(f"v{self.current_version}")
        self.update_label.setStyleSheet("color: #aaa; font-size: 11px;")
        
        update_row.addWidget(self.update_btn)
        update_row.addSpacing(10)
        update_row.addWidget(self.update_label)
        update_row.addStretch()
        
        self.form.addRow("Update:", update_row)
        
        self.coffee_btn = QPushButton("Buy me a coffee ☕")
        self.coffee_btn.setObjectName("coffeeBtn")
        self.coffee_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # self.coffee_btn.setFixedHeight(36) # Removed to match Update button size
        self.coffee_btn.clicked.connect(self.open_coffee)
        
        # Add to form layout for consistent alignment
        # Align left (stretch on right)
        coffee_row = QHBoxLayout()
        coffee_row.addWidget(self.coffee_btn)
        coffee_row.addStretch()
        
        self.form.addRow("Donate:", coffee_row)
        
        layout.addStretch() # Push everything up
        
    def _add_section_header(self, text):
        """Helper to add spaced section header."""
        lbl = QLabel(text)
        lbl.setObjectName("sectionHeader")
        self.form.addRow(lbl)

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
             
        button_style = app.get('button_style', 'Gradient')
        style_idx = self.button_style_combo.findText(button_style)
        if style_idx >= 0:
            self.button_style_combo.setCurrentIndex(style_idx)
             
        self.show_dimming_check.setChecked(app.get('show_dimming', False))
        self.glass_ui_check.setChecked(app.get('glass_ui', False))
             
        self.border_effect_combo.blockSignals(False)
        
        
        sc = self.config.get('shortcut', {})
        self.shortcut_display.setText(sc.get('value', ''))
        
    def save_settings(self):
        """Save and emit config."""
        self._cleanup_threads()
        
        # HA
        if 'home_assistant' not in self.config: self.config['home_assistant'] = {}
        self.config['home_assistant']['url'] = self.url_input.text().strip()
        self.config['home_assistant']['token'] = self.token_input.text().strip()
        
        # Appearance
        theme_map = {0: 'system', 1: 'light', 2: 'dark'}
        self.config['appearance'].update({
            'theme': theme_map.get(self.theme_combo.currentIndex(), 'system'),
            'border_effect': self.border_effect_combo.currentText(),
            'button_style': self.button_style_combo.currentText(),
            'show_dimming': self.show_dimming_check.isChecked(),
            'glass_ui': self.glass_ui_check.isChecked(),
        })
        
        # Shortcut handled by record signal, but good to ensure consistency
        # (Shortcut saves immediately on record in config dict)
        if 'shortcut' not in self.config: self.config['shortcut'] = {}
        
        self.settings_saved.emit(self.config)

    # --- Logic ---

    def on_theme_preview(self, index):
        if self.theme_manager:
            theme_map = {0: 'system', 1: 'light', 2: 'dark'}
            self.theme_manager.set_theme(theme_map.get(index, 'system'))

    def on_border_effect_changed(self, text):
        self.border_effect_combo.set_effect(text)




    def toggle_recording(self, checked):
        if not self.input_manager:
            self.record_btn.setChecked(False)
            return
            
        if checked:
            # Stop State (Square)
            self.record_icon.setStyleSheet("background-color: white; border-radius: 2px;") 
            self.shortcut_display.setText("Press keys...")
            self.input_manager.start_recording()
        else:
            # Record State (Circle)
            self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
            self.input_manager.restore_shortcut()
            # Restore previous text if cancelled
            sc = self.config.get('shortcut', {})
            if self.shortcut_display.text() == "Press keys...":
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

    def test_connection(self):
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        
        if not url or not token:
            self.status_label.setText("⚠ Missing Info")
            return
            
        self.test_btn.setEnabled(False)
        self.status_label.setText("Testing...")
        
        if self._test_thread and self._test_thread.isRunning():
            self._test_thread.quit()
        
        # Run connection check in background to avoid freezing UI
        self._test_thread = ConnectionTestThread(url, token)
        self._test_thread.finished.connect(self.on_test_complete)
        self._test_thread.start()

    @pyqtSlot(bool, str)
    def on_test_complete(self, success, message):
        self.test_btn.setEnabled(True)
        icon = "✅" if success else "❌"
        # Display full message with wrap enabled on label
        self.status_label.setText(f"{icon} {message}")

    def check_for_updates(self):
        """Start update check."""
        self.update_btn.setEnabled(False)
        self.update_label.setText("Checking...")
        
        self._update_thread = UpdateCheckerThread(self.current_version)
        self._update_thread.update_available.connect(self.on_update_available)
        self._update_thread.up_to_date.connect(self.on_up_to_date)
        self._update_thread.error_occurred.connect(self.on_update_error)
        self._update_thread.start()
        
    @pyqtSlot(str)
    def on_update_available(self, tag):
        self.update_btn.setEnabled(True)
        self.update_label.setText(f"Update available: {tag}")
        self.update_label.setStyleSheet("color: #34A853; font-weight: bold; font-size: 11px;")
        
        # Optionally change button text to "Download"
        self.update_btn.setText("Download Update")
        self.update_btn.disconnect()
        self.update_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/lasselian/Prism-Desktop/releases/latest")))
        
    @pyqtSlot()
    def on_up_to_date(self):
        self.update_btn.setEnabled(True)
        self.update_label.setText("App is up to date")
        self.update_label.setStyleSheet("color: #aaa; font-size: 11px;")
        
    @pyqtSlot(str)
    def on_update_error(self, error):
        self.update_btn.setEnabled(True)
        self.update_label.setText("Check failed")
        self.update_label.setToolTip(error)

    def _cleanup_threads(self):
        if self._test_thread and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(500)

    def open_coffee(self):
        """Open Buy Me a Coffee link."""
        QDesktopServices.openUrl(QUrl("https://www.buymeacoffee.com/lasselian"))
