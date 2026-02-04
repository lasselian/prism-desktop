
"""
Embedded Button Editor Widget
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFormLayout,
    QCheckBox, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont

class ButtonEditWidget(QWidget):
    """
    Editor for configuring button properties.
    Uses the same design style as SettingsWidgetV2.
    """
    
    saved = pyqtSignal(dict)
    cancelled = pyqtSignal()
    
    def __init__(self, entities: list, config: dict = None, slot: int = 0, theme_manager=None, input_manager=None, parent=None):
        super().__init__(parent)
        self.entities = entities or []
        self.config = config or {}
        self.slot = slot
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        
        # Connect input manager if available
        if self.input_manager:
            self.input_manager.recorded.connect(self.on_shortcut_recorded)
        
        self.setup_ui()
        self.load_config()
    
    def _update_stylesheet(self):
        """Update the stylesheet matching the active theme."""
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
        
        # Check if using light or dark text to determine background contrast
        is_light = colors.get('text', '#ffffff') == '#1e1e1e'
        
        # Input backgrounds: slightly darker/lighter than base
        if is_light:
            input_bg = "rgba(0, 0, 0, 0.05)"
            input_border = "rgba(0, 0, 0, 0.15)"
            input_focus_bg = "rgba(0, 0, 0, 0.08)"
            color_btn_border = "#333"
            section_header_color = "#666666"  # Dark gray for light mode
        else:
            input_bg = "rgba(255, 255, 255, 0.08)"
            input_border = "rgba(255, 255, 255, 0.1)"
            input_focus_bg = "rgba(255, 255, 255, 0.12)"
            color_btn_border = "white"
            section_header_color = "#8e8e93"  # Apple gray for dark mode
        
        self.setStyleSheet(f"""
            QWidget {{ 
                font-family: 'Segoe UI', sans-serif; 
                font-size: 13px;
                color: {colors['text']};
            }}
            QLabel#headerTitle {{
                font-size: 18px;
                font-weight: 600;
                color: {colors['window_text']};
            }}
            QLabel#sectionHeader {{
                font-size: 11px;
                font-weight: 700;
                color: {section_header_color};
                margin-top: 10px;
                margin-bottom: 2px;
            }}
            QLineEdit, QComboBox, QSpinBox {{
                background-color: {input_bg};
                border: 1px solid {input_border};
                border-radius: 6px;
                padding: 6px 10px;
                color: {colors['text']};
                selection-background-color: {colors['accent']};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                subcontrol-origin: border;
                width: 20px;
                border: none;
                background: transparent;
            }}
            QSpinBox::up-button {{
                subcontrol-position: top right;
            }}
            QSpinBox::down-button {{
                subcontrol-position: bottom right;
            }}
            QSpinBox::up-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 6px solid {colors['text']};
                width: 0;
                height: 0;
            }}
            QSpinBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid {colors['text']};
                width: 0;
                height: 0;
            }}
            QSpinBox::up-arrow:hover, QSpinBox::down-arrow:hover {{
                border-bottom-color: {colors['accent']};
                border-top-color: {colors['accent']};
            }}
            QComboBox QAbstractItemView {{
                background-color: {colors['base']};
                border: 1px solid {colors['border']};
                color: {colors['text']};
                selection-background-color: {colors['accent']};
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border: 1px solid {colors['accent']};
                background-color: {input_focus_bg};
            }}
            QPushButton {{
                background-color: {colors['button']};
                color: {colors['button_text']};
                border: 1px solid {colors['border']};
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {colors['accent']}; color: white; }}
            QPushButton:pressed {{ background-color: {colors['accent']}; }}
            
            QPushButton#primaryBtn {{
                background-color: {colors['accent']};
                color: white;
                border: none;
            }}
            QPushButton#primaryBtn:hover {{ background-color: #006ce6; }}
            
            QPushButton#colorBtn {{
                border-radius: 4px;
                border: 2px solid transparent;
            }}
            QPushButton#colorBtn:checked {{
                border: 2px solid {color_btn_border};
            }}
            
            QPushButton#recordBtn {{
                background-color: #EA4335;
                border: none;
                border-radius: 6px;
            }}
            QPushButton#recordBtn:hover {{
                background-color: #D33428;
            }}
            QPushButton#recordBtn:checked {{
                background-color: #B71C1C;
            }}
            
            QWidget#recordIcon {{
                background-color: white;
                border-radius: 6px;
            }}
        """)
        
    def setup_ui(self):
        # Update styling
        self._update_stylesheet()
        
        # Listen for theme changes
        if self.theme_manager:
            self.theme_manager.theme_changed.connect(self._update_stylesheet)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        
        # 1. Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 10)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedWidth(70)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        
        title_text = "Edit Button" if self.config else "Add Button"
        title = QLabel(title_text)
        title.setObjectName("headerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.setFixedWidth(70)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self.save)
        
        header_layout.addWidget(self.cancel_btn)
        header_layout.addWidget(title)
        header_layout.addWidget(self.save_btn)
        
        layout.addLayout(header_layout)
        
        # 2. Form
        self.form = QFormLayout()
        self.form.setVerticalSpacing(14)
        self.form.setHorizontalSpacing(16)
        
        # --- Config Section ---
        self._add_section_header("CONFIGURATION")
        
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("e.g. Living Room")
        self.form.addRow("Label:", self.label_input)
        
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Light / Switch", "Sensor Widget", "Climate", "Curtain", "Script", "Scene", "Image", "Camera"])
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.form.addRow("Type:", self.type_combo)
        
        self.entity_combo = QComboBox()
        self.entity_combo.setEditable(True)
        self.entity_combo.setMaxVisibleItems(15)
        self.entity_combo.lineEdit().setPlaceholderText("Select or type entity ID...")
        self.entity_combo.lineEdit().setPlaceholderText("Select or type entity ID...")
        self.populate_entities()
        self.form.addRow("Entity:", self.entity_combo)
        
        # Size (square span - width and height)
        self.slots_spin = QSpinBox()
        self.slots_spin.setRange(1, 4)
        self.slots_spin.setValue(1)
        self.slots_spin.setToolTip("Button size (width x height in grid slots)")
        self.slots_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.form.addRow("Size:", self.slots_spin)
        
        # Advanced Mode (Climate Only)
        self.advanced_mode_check = QCheckBox("Advanced Mode")
        self.advanced_mode_check.setToolTip("Enable fan and mode controls")
        self.advanced_mode_check.setVisible(False)
        self.form.addRow("", self.advanced_mode_check)
        
        # Precision (Widget/Sensor Only)
        self.precision_spin = QSpinBox()
        self.precision_spin.setRange(0, 5)
        self.precision_spin.setToolTip("Decimal places")
        self.precision_spin.setVisible(False)
        self.form.addRow("Decimals:", self.precision_spin)
        
        # Service (Switches only)
        self.service_label = QLabel("Service:")
        self.service_combo = QComboBox()
        self.service_combo.addItems(["toggle", "turn_on", "turn_off"])
        self.form.addRow(self.service_label, self.service_combo)
        
        # --- Appearance Section ---
        self.appearance_header = self._add_section_header("APPEARANCE")
        
        # Icon Input
        self.icon_input = QLineEdit()
        self.icon_input.setPlaceholderText("e.g. mdi:lightbulb")
        self.form.addRow("Icon:", self.icon_input)
        self.icon_label = self.form.labelForField(self.icon_input)
        
        # Color Picker
        color_widget = QWidget()
        color_layout = QHBoxLayout(color_widget)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        
        self.preset_colors = [
            ("#4285F4", "Blue"),
            ("#34A853", "Green"),
            ("#EA4335", "Red"),
            ("#9C27B0", "Purple"),
            ("#E91E63", "Pink"),
            ("#607D8B", "Gray"),
        ]
        
        self.color_buttons = []
        self.selected_color = "#4285F4"
        
        for color_hex, tooltip in self.preset_colors:
            btn = QPushButton()
            btn.setObjectName("colorBtn")
            btn.setFixedSize(24, 24)
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(f"background-color: {color_hex};")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=color_hex: self.select_color(c))
            color_layout.addWidget(btn)
            self.color_buttons.append((btn, color_hex))
            
        color_layout.addStretch()
        self.form.addRow("Color:", color_widget)
        self.color_widget = color_widget
        self.color_label = self.form.labelForField(color_widget)
        
        # --- Shortcut Section ---
        self._add_section_header("SHORTCUT")
        
        self.custom_shortcut_check = QCheckBox("Enable Custom Shortcut")
        self.custom_shortcut_check.toggled.connect(self.on_custom_shortcut_toggled)
        self.form.addRow("", self.custom_shortcut_check)
        
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
        self.record_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        btn_layout.addWidget(self.record_icon)
        
        # Add to row
        shortcut_row.addWidget(self.shortcut_display, 8)
        shortcut_row.addSpacing(12)
        shortcut_row.addWidget(self.record_btn)
        shortcut_row.addStretch(2) 
        
        self.form.addRow("Keys:", shortcut_row)
        
        layout.addLayout(self.form)

    def _add_section_header(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("sectionHeader")
        self.form.addRow(lbl)
        return lbl

    def populate_entities(self):
        """Fill entity dropdown based on the selected button type."""
        # Save current selection to restore it later
        current_entity = self.entity_combo.currentText()
        
        self.entity_combo.clear()
        if not self.entities: return
        
        # Determine allowed domains based on selected type
        type_idx = self.type_combo.currentIndex()
        if type_idx == 0:  # Switch
            allowed_domains = {'light', 'switch', 'input_boolean'}
        elif type_idx == 1:  # Sensor Widget
            allowed_domains = {'sensor', 'binary_sensor'}
        elif type_idx == 2:  # Climate
            allowed_domains = {'climate'}
        elif type_idx == 3:  # Curtain
            allowed_domains = {'cover'}
        elif type_idx == 4:  # Script
            allowed_domains = {'script'}
        elif type_idx == 5:  # Scene
            allowed_domains = {'scene'}
        elif type_idx == 6:  # Image
            allowed_domains = {'image'}
        elif type_idx == 7:  # Camera
            allowed_domains = {'camera'}
        else:
            allowed_domains = None  # Show all
        
        # Group by domain (filtered)
        domains = {}
        for entity in self.entities:
            eid = entity.get('entity_id', '')
            domain = eid.split('.')[0] if '.' in eid else 'other'
            
            # Filter by allowed domains
            if allowed_domains and domain not in allowed_domains:
                continue
                
            friendly = entity.get('attributes', {}).get('friendly_name', eid)
            
            if domain not in domains: domains[domain] = []
            domains[domain].append((eid, friendly))
            
        for domain in sorted(domains.keys()):
            for eid, friendly in sorted(domains[domain], key=lambda x: x[0]):
                 self.entity_combo.addItem(eid, friendly)
                 self.entity_combo.setItemData(self.entity_combo.count()-1, friendly, Qt.ItemDataRole.ToolTipRole)
        
        # Try to restore previous selection
        if current_entity:
            idx = self.entity_combo.findText(current_entity)
            if idx >= 0:
                self.entity_combo.setCurrentIndex(idx)

    def on_type_changed(self, index):
        types = ["switch", "widget", "climate", "curtain", "script", "scene", "image", "camera"]
        current_type = types[index]

        # Show/Hide fields based on type
        self.advanced_mode_check.setVisible(current_type == 'climate')
        self.service_combo.setVisible(current_type == 'switch')
        self.service_label.setVisible(current_type == 'switch')
        
        # Show precision for widget/sensor
        is_sensor = current_type == 'widget'
        self.precision_spin.setVisible(is_sensor)
        
        # Find the label associated with the widget and hide it too.
        # Layouts don't automatically hide labels for hidden widgets.
        # Simple hack: iterate rows regarding precision
        if self.form.labelForField(self.precision_spin):
             self.form.labelForField(self.precision_spin).setVisible(is_sensor)
             
        if self.form.labelForField(self.service_combo):
             self.form.labelForField(self.service_combo).setVisible(current_type == 'switch')
        
        # Disable appearance settings for image/camera types (they show images, not icons)
        is_image_type = current_type in ('image', 'camera')
        self._set_appearance_enabled(not is_image_type)
        
        # Refresh entity list for the new type
        self.populate_entities()
    
    def _set_appearance_enabled(self, enabled: bool):
        """Enable or disable appearance section widgets."""
        # Grey out appearance header
        if hasattr(self, 'appearance_header'):
            self.appearance_header.setEnabled(enabled)
        
        # Icon input and label
        self.icon_input.setEnabled(enabled)
        if hasattr(self, 'icon_label') and self.icon_label:
            self.icon_label.setEnabled(enabled)
        
        # Color widget and label
        if hasattr(self, 'color_widget'):
            self.color_widget.setEnabled(enabled)
            for btn, _ in self.color_buttons:
                btn.setEnabled(enabled)
        if hasattr(self, 'color_label') and self.color_label:
            self.color_label.setEnabled(enabled)
        
    def select_color(self, color_hex):
        self.selected_color = color_hex
        for btn, c in self.color_buttons:
            btn.setChecked(c == color_hex)
            
    def load_config(self):
        if not self.config:
            self.select_color("#4285F4")
            self.entity_combo.setCurrentIndex(-1)
            self.entity_combo.setCurrentText("")
            self.label_input.clear()
            self.icon_input.clear()
            self.type_combo.setCurrentIndex(0)
            self.service_combo.setCurrentIndex(0)
            return
            
        self.label_input.setText(self.config.get('label', ''))
        self.icon_input.setText(self.config.get('icon', ''))
        
        types = {'switch': 0, 'widget': 1, 'climate': 2, 'curtain': 3, 'script': 4, 'scene': 5, 'image': 6, 'camera': 7}
        self.type_combo.setCurrentIndex(types.get(self.config.get('type'), 0))
        
        eid = self.config.get('entity_id', '')
        if eid:
            self.entity_combo.setCurrentText(eid)
            # Try to match in combo
            idx = self.entity_combo.findText(eid)
            if idx >= 0: self.entity_combo.setCurrentIndex(idx)
            
        service = self.config.get('service', 'toggle')
        svc_name = service.split('.')[-1]
        svc_idx = self.service_combo.findText(svc_name)
        if svc_idx >= 0: self.service_combo.setCurrentIndex(svc_idx)
        
        self.advanced_mode_check.setChecked(self.config.get('advanced_mode', False))
        
        # Precision
        self.precision_spin.setValue(self.config.get('precision', 1))
        
        # Slots
        self.slots_spin.setValue(self.config.get('slots', 1))
        
        self.select_color(self.config.get('color', '#4285F4'))
        
        # Shortcut
        shortcut = self.config.get('custom_shortcut', {})
        self.custom_shortcut_check.setChecked(shortcut.get('enabled', False))
        self.shortcut_display.setText(shortcut.get('value', ''))
        self.on_custom_shortcut_toggled(shortcut.get('enabled', False))
        
        # Trigger type-specific UI updates (appearance, service visibility, etc.)
        self.on_type_changed(self.type_combo.currentIndex())
        
    def get_content_height(self):
        # Force layout update to get accurate size after content changes
        self.adjustSize()
        return self.sizeHint().height()

    def save(self):
        """Save changes and emit config."""
        entity_id = self.entity_combo.currentText().strip()
        type_idx = self.type_combo.currentIndex()
        
        new_config = self.config.copy() if self.config else {}
        new_config['slot'] = self.slot
        new_config['label'] = self.label_input.text().strip()
        
        types = ["switch", "widget", "climate", "curtain", "script", "scene", "image", "camera"]
        new_config['type'] = types[self.type_combo.currentIndex()]
        
        # Extract ID from entity_id (e.g., "light.kitchen_light (Kitchen Light)" -> "light.kitchen_light")
        entity_text = self.entity_combo.currentText()
        new_config['entity_id'] = entity_text.split(" ")[0] if entity_text else ""
        
        if new_config['type'] == 'climate':
            new_config['advanced_mode'] = self.advanced_mode_check.isChecked()
            
        if new_config['type'] == 'switch':
             new_config['service'] = f"{new_config['entity_id'].split('.')[0]}.{self.service_combo.currentText()}"
        
        if new_config['type'] == 'widget':
            new_config['precision'] = self.precision_spin.value()
        
        # Always save slots (default to 1 if not changed)
        new_config['slots'] = self.slots_spin.value()
             
        new_config['icon'] = self.icon_input.text().strip()
        new_config['color'] = self.selected_color
        
        # Save shortcut
        new_config['custom_shortcut'] = {
            'enabled': self.custom_shortcut_check.isChecked(),
            'value': self.shortcut_display.text()
        }
        
        self.saved.emit(new_config)

    def on_custom_shortcut_toggled(self, checked):
        self.record_btn.setEnabled(checked)
        self.shortcut_display.setEnabled(checked)
        if not checked:
             self.record_btn.setChecked(False)
             if self.input_manager:
                 self.input_manager.stop_listening()
                 self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")

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
            self.input_manager.stop_listening()
            # Restore if empty
            if self.shortcut_display.text() == "Press keys...":
                 sc = self.config.get('custom_shortcut', {}) if self.config else {}
                 self.shortcut_display.setText(sc.get('value', ''))

    @pyqtSlot(dict)
    def on_shortcut_recorded(self, shortcut):
        if not self.record_btn.isChecked():
            return # Ignore if we aren't recording
            
        self.record_btn.setChecked(False)
        # Reset Icon
        self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
        self.shortcut_display.setText(shortcut.get('value', ''))
