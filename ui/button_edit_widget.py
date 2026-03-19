
"""
Embedded Button Editor Widget
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFormLayout,
    QSpinBox, QSizePolicy, QCompleter
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from ui.widgets.toggle_switch import ToggleSwitch

class ButtonEditWidget(QWidget):
    """
    Editor for configuring button properties.
    Uses the same design style as SettingsWidgetV2.
    """
    
    # (Display Label, Internal Type) - Alphabetically sorted by label
    TYPE_DEFINITIONS = [
        ("Automation", "automation"),
        ("Camera", "camera"),
        ("Climate", "climate"),
        ("Cover", "curtain"),
        ("Fan", "fan"),
        ("Input Number", "input_number"),
        ("Light / Switch", "switch"),
        ("Lock", "lock"),
        ("Media Player", "media_player"),
        ("Scene", "scene"),
        ("Script", "script"),
        ("Sensor", "widget"),
        ("Weather", "weather"),
        ("3D Printer", "3d_printer")
    ]
    
    saved = pyqtSignal(dict)
    cancelled = pyqtSignal()
    size_changed = pyqtSignal()  # Emitted when widget needs to resize
    
    # Class-level preference for entity display format (persists across instances)
    _global_show_friendly_names = True
    
    def __init__(self, entities: list, config: dict = None, slot: int = 0, theme_manager=None, input_manager=None, parent=None):
        super().__init__(parent)
        self.entities = entities or []
        self.config = config or {}
        self.slot = slot
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        # Removed instance-specific flag, now using class variable
        
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
            QLineEdit, QComboBox, QSpinBox {{
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
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
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
            
            QPushButton#colorBtn {{
                border-radius: {Dimensions.RADIUS_SMALL};
                border: 2px solid transparent;
            }}
            QPushButton#colorBtn:checked {{
                border: 2px solid {color_btn_border};
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
            
            QPushButton#friendlyToggleBtn {{
                padding: {Dimensions.PADDING_MEDIUM} {Dimensions.PADDING_SMALL};
                font-size: {Typography.SIZE_SMALL};
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
        self.cancel_btn.setMinimumWidth(70)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        
        title_text = "Edit Button" if self.config else "Add Button"
        title = QLabel(title_text)
        title.setObjectName("headerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.setMinimumWidth(70)
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
        self.label_input.returnPressed.connect(self.save)
        self.form.addRow("Label:", self.label_input)
        
        self.type_combo = QComboBox()
        self.type_combo.addItems([t[0] for t in self.TYPE_DEFINITIONS])
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.form.addRow("Type:", self.type_combo)
        
        # Display Toggle (Global across all entity dropdowns)
        self.friendly_toggle_btn = QPushButton("Show IDs" if ButtonEditWidget._global_show_friendly_names else "Show Names")
        self.friendly_toggle_btn.setObjectName("friendlyToggleBtn")
        self.friendly_toggle_btn.setMinimumWidth(110)
        self.friendly_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.friendly_toggle_btn.setToolTip("Switch between showing friendly names and entity IDs globally")
        self.friendly_toggle_btn.clicked.connect(self._toggle_entity_display)
        self.form.addRow("Display:", self.friendly_toggle_btn) # Moved here
        
        self.entity_combo = self._create_entity_combo()
        self.entity_combo.lineEdit().setPlaceholderText("Select or type entity ID...")
        self.entity_combo.lineEdit().returnPressed.connect(self.save)
        self.populate_entities()
        
        self.form.addRow("Entity:", self.entity_combo)
        
        # (Advanced mode toggle removed - climate now defaults to advanced)
        
        # Show Album Art (Media Player Only)
        self.show_album_art_check = ToggleSwitch("Show Album Art")
        self.show_album_art_check.setToolTip("Display album artwork as button background")
        self.show_album_art_check.setChecked(True)
        self.show_album_art_check.setVisible(False)
        self.form.addRow("", self.show_album_art_check)

        # Animated Background (Media Player Only)
        self.animated_bg_toggle = ToggleSwitch("Animated Background")
        self.animated_bg_toggle.setToolTip("Enable animated parallax background when no album art is shown")
        self.animated_bg_toggle.setChecked(True)
        self.animated_bg_toggle.setVisible(False)
        self.form.addRow("", self.animated_bg_toggle)

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
        
        # Camera Display Mode (Removed - always stream)
        # self.camera_mode_label = QLabel("Display:")
        # self.camera_mode_combo = QComboBox()
        # self.camera_mode_combo.addItems(["Picture", "Live Stream"])
        # self.camera_mode_combo.setToolTip("Picture refreshes periodically, Live Stream is continuous")
        # self.camera_mode_combo.setVisible(False)
        # self.camera_mode_label.setVisible(False)
        # self.form.addRow(self.camera_mode_label, self.camera_mode_combo)
        
        # Camera Size (Removed - handled by drag resize)
        # self.camera_size_label = QLabel("Size:")
        # self.camera_size_combo = QComboBox() ...
        
        # Automation Action (Automation Only)
        self.automation_action_label = QLabel("Action:")
        self.automation_action_combo = QComboBox()
        self.automation_action_combo.addItems(["Toggle", "Trigger"])
        self.automation_action_combo.setToolTip("Toggle enables/disables the automation. Trigger runs it immediately.")
        self.automation_action_label.setVisible(False)
        self.automation_action_combo.setVisible(False)
        self.form.addRow(self.automation_action_label, self.automation_action_combo)
        
        # Lock Action (Lock Only)
        self.lock_action_label = QLabel("Action:")
        self.lock_action_combo = QComboBox()
        self.lock_action_combo.addItems(["Toggle (Smart)", "Lock", "Unlock"])
        self.lock_action_combo.setToolTip("Toggle logic: If locked -> Unlock, If unlocked -> Lock.")
        self.lock_action_label.setVisible(False)
        self.lock_action_combo.setVisible(False)
        self.form.addRow(self.lock_action_label, self.lock_action_combo)
        
        # 3D Printer specific fields
        self.printer_state_label = QLabel("State Entity:")
        self.printer_state_combo = self._create_entity_combo()
        self.form.addRow(self.printer_state_label, self.printer_state_combo)
        
        self.printer_progress_label = QLabel("Progress Entity:")
        self.printer_progress_combo = self._create_entity_combo()
        self.form.addRow(self.printer_progress_label, self.printer_progress_combo)
        
        self.printer_camera_label = QLabel("Camera Entity:")
        self.printer_camera_combo = self._create_entity_combo()
        self.form.addRow(self.printer_camera_label, self.printer_camera_combo)
        
        self.printer_nozzle_label = QLabel("Nozzle Entity:")
        self.printer_nozzle_combo = self._create_entity_combo()
        self.form.addRow(self.printer_nozzle_label, self.printer_nozzle_combo)
        
        self.printer_nozzle_target_label = QLabel("Nozzle Target Entity:")
        self.printer_nozzle_target_combo = self._create_entity_combo()
        self.form.addRow(self.printer_nozzle_target_label, self.printer_nozzle_target_combo)
        
        self.printer_bed_label = QLabel("Bed Entity:")
        self.printer_bed_combo = self._create_entity_combo()
        self.form.addRow(self.printer_bed_label, self.printer_bed_combo)
        
        self.printer_bed_target_label = QLabel("Bed Target Entity:")
        self.printer_bed_target_combo = self._create_entity_combo()
        self.form.addRow(self.printer_bed_target_label, self.printer_bed_target_combo)
        
        self.printer_pause_label = QLabel("Pause/Resume Entity:")
        self.printer_pause_combo = self._create_entity_combo()
        self.form.addRow(self.printer_pause_label, self.printer_pause_combo)

        self.printer_stop_label = QLabel("Stop Entity:")
        self.printer_stop_combo = self._create_entity_combo()
        self.form.addRow(self.printer_stop_label, self.printer_stop_combo)

        
        # --- Appearance Section ---
        self.appearance_header = self._add_section_header("APPEARANCE")
        
        # Icon Input
        self.icon_input = QLineEdit()
        self.icon_input.setPlaceholderText("e.g. mdi:lightbulb")
        self.icon_input.returnPressed.connect(self.save)
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
            ("#B71C1C", "Red"),
            ("#E65100", "Orange"),
            ("#6A1B9A", "Purple"),
            ("#AD1457", "Pink"),
            ("#607D8B", "Gray"),
            ("#3C3C3C", "Sensor Gray"),
        ]
        
        self.color_buttons = []
        self.selected_color = "#4285F4"
        
        for color_hex, tooltip in self.preset_colors:
            btn = QPushButton()
            btn.setObjectName("colorBtn")
            btn.setFixedSize(24, 24)
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            if color_hex == "#3C3C3C":
                # Special diagonal split for Sensor Gray (Dynamic)
                btn.setStyleSheet("""
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #ffffff, stop:0.49 #ffffff, 
                        stop:0.51 #3c3c3c, stop:1 #3c3c3c);
                """)
            else:
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
        
        self.custom_shortcut_check = ToggleSwitch("Enable Custom Shortcut")
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
        self.adjustSize()
        
    def _create_entity_combo(self):
        """Create a combobox configured for long entity IDs, preventing layout clipping."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMaxVisibleItems(15)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        
        # Configure the completer for partial, case-insensitive substring matching
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = combo.completer()
        if completer:
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            
        return combo
        
    def _add_section_header(self, title):
        lbl = QLabel(title)
        lbl.setObjectName("sectionHeader")
        self.form.addRow(lbl)
        return lbl

    def populate_entities(self):
        """Fill entity dropdown based on the selected button type."""
        # Check if printer combos exist yet (populate_entities can be called
        # during setup_ui before they are created)
        has_printer_combos = hasattr(self, 'printer_state_combo')
        
        # Save current selections (entity IDs) to restore later
        current_entity = self._get_combo_entity_id(self.entity_combo)
        current_printer_ids = {}
        if has_printer_combos:
            current_printer_ids = {
                'state': self._get_combo_entity_id(self.printer_state_combo),
                'progress': self._get_combo_entity_id(self.printer_progress_combo),
                'camera': self._get_combo_entity_id(self.printer_camera_combo),
                'nozzle': self._get_combo_entity_id(self.printer_nozzle_combo),
                'bed': self._get_combo_entity_id(self.printer_bed_combo),
                'nozzle_target': self._get_combo_entity_id(self.printer_nozzle_target_combo),
                'bed_target': self._get_combo_entity_id(self.printer_bed_target_combo),
                'pause': self._get_combo_entity_id(self.printer_pause_combo),
                'stop': self._get_combo_entity_id(self.printer_stop_combo),
            }
        
        # Clear all combos
        self.entity_combo.clear()
        if has_printer_combos:
            self.printer_state_combo.clear()
            self.printer_progress_combo.clear()
            self.printer_camera_combo.clear()
            self.printer_nozzle_combo.clear()
            self.printer_bed_combo.clear()
            self.printer_nozzle_target_combo.clear()
            self.printer_bed_target_combo.clear()
            self.printer_pause_combo.clear()
            self.printer_stop_combo.clear()
        
        if not self.entities: return
        
        # Determine allowed domains based on selected type
        type_idx = self.type_combo.currentIndex()
        current_type = self.TYPE_DEFINITIONS[type_idx][1] if 0 <= type_idx < len(self.TYPE_DEFINITIONS) else None
        
        domain_map = {
            'automation': {'automation'},
            'switch': {'light', 'switch', 'input_boolean', 'input_button'},
            'widget': {'sensor', 'binary_sensor', 'number'},
            'input_number': {'input_number'},
            'climate': {'climate'},
            'curtain': {'cover'},
            'fan': {'fan'},
            'media_player': {'media_player'},
            'script': {'script'},
            'scene': {'scene'},
            'camera': {'camera'},
            'weather': {'weather'},
            'lock': {'lock'}
        }
        allowed_domains = domain_map.get(current_type)
        
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
        
        show_friendly = ButtonEditWidget._global_show_friendly_names # Use class-level variable
        
        for domain in sorted(domains.keys()):
            for eid, friendly in sorted(domains[domain], key=lambda x: x[0]):
                 # Display text depends on toggle; UserRole always stores entity_id
                 display = friendly if show_friendly else eid
                 tooltip = f"{friendly} ({eid})" if show_friendly else f"{eid} ({friendly})"
                 
                 self.entity_combo.addItem(display, eid)
                 self.entity_combo.setItemData(self.entity_combo.count()-1, tooltip, Qt.ItemDataRole.ToolTipRole)
                 
                 # All entities (unfiltered) go to printer combos
                 # They get populated with the full set in a separate pass below
        
        # Populate printer combos with ALL entities (unfiltered)
        if has_printer_combos:
            all_domains = {}
            for entity in self.entities:
                eid = entity.get('entity_id', '')
                domain = eid.split('.')[0] if '.' in eid else 'other'
                friendly = entity.get('attributes', {}).get('friendly_name', eid)
                if domain not in all_domains: all_domains[domain] = []
                all_domains[domain].append((eid, friendly))
            
            printer_combos = [
                self.printer_state_combo, self.printer_progress_combo,
                self.printer_camera_combo,
                self.printer_nozzle_combo, self.printer_bed_combo,
                self.printer_nozzle_target_combo, self.printer_bed_target_combo,
                self.printer_pause_combo, self.printer_stop_combo
            ]
            for domain in sorted(all_domains.keys()):
                for eid, friendly in sorted(all_domains[domain], key=lambda x: x[0]):
                    display = friendly if show_friendly else eid
                    tooltip = f"{friendly} ({eid})" if show_friendly else f"{eid} ({friendly})"
                    for combo in printer_combos:
                        combo.addItem(display, eid)
                        combo.setItemData(combo.count()-1, tooltip, Qt.ItemDataRole.ToolTipRole)
        
        # Restore previous selections by entity ID
        self._restore_combo_selection(self.entity_combo, current_entity)
        if has_printer_combos and current_printer_ids:
            self._restore_combo_selection(self.printer_state_combo, current_printer_ids.get('state', ''))
            self._restore_combo_selection(self.printer_progress_combo, current_printer_ids.get('progress', ''))
            self._restore_combo_selection(self.printer_camera_combo, current_printer_ids.get('camera', ''))
            self._restore_combo_selection(self.printer_nozzle_combo, current_printer_ids.get('nozzle', ''))
            self._restore_combo_selection(self.printer_bed_combo, current_printer_ids.get('bed', ''))
            self._restore_combo_selection(self.printer_nozzle_target_combo, current_printer_ids.get('nozzle_target', ''))
            self._restore_combo_selection(self.printer_bed_target_combo, current_printer_ids.get('bed_target', ''))
            self._restore_combo_selection(self.printer_pause_combo, current_printer_ids.get('pause', ''))
            self._restore_combo_selection(self.printer_stop_combo, current_printer_ids.get('stop', ''))
    
    def _get_combo_entity_id(self, combo):
        """Get the entity ID from a combo, whether from userData or typed text."""
        # If the user has typed nothing or it is cleared, return empty
        text = combo.currentText().strip()
        if not text:
            return ""
            
        # If it matches an item, return its data
        idx = combo.findText(text)
        if idx >= 0:
            return combo.itemData(idx)
            
        # Fallback: user typed a custom entity ID
        return text
    
    def _restore_combo_selection(self, combo, entity_id):
        """Restore a combo's selection by entity ID (stored in UserRole)."""
        if not entity_id:
            combo.setCurrentIndex(-1)
            combo.setCurrentText("")
            return
        # Search UserRole data for the entity_id
        for i in range(combo.count()):
            if combo.itemData(i) == entity_id:
                combo.setCurrentIndex(i)
                return
        # Not found in list — set as typed text (custom entity)
        combo.setCurrentText(entity_id)
    
    def _toggle_entity_display(self):
        """Toggle between friendly name and entity ID display in all combos."""
        ButtonEditWidget._global_show_friendly_names = not ButtonEditWidget._global_show_friendly_names
        self.friendly_toggle_btn.setText("Show IDs" if ButtonEditWidget._global_show_friendly_names else "Show Names")
        self.populate_entities()

    def on_type_changed(self, index):
        current_type = self.TYPE_DEFINITIONS[index][1] if 0 <= index < len(self.TYPE_DEFINITIONS) else 'switch'

        # (Advanced mode visibility logic removed)
        self.show_album_art_check.setVisible(current_type == 'media_player')
        self.animated_bg_toggle.setVisible(current_type == 'media_player')
        self.service_combo.setVisible(current_type == 'switch')
        self.service_label.setVisible(current_type == 'switch')
        
        # Show precision for widget/sensor
        is_sensor = current_type == 'widget'
        self.precision_spin.setVisible(is_sensor)
        
        # Show camera-specific controls
        is_camera = current_type == 'camera'
        # mode combo removed
        
        # Show automation specific controls
        is_automation = current_type == 'automation'
        self.automation_action_combo.setVisible(is_automation)
        self.automation_action_label.setVisible(is_automation)

        # Show lock specific controls
        is_lock = current_type == 'lock'
        self.lock_action_combo.setVisible(is_lock)
        self.lock_action_label.setVisible(is_lock)
        
        # Show 3D printer specific controls
        is_printer = current_type == '3d_printer'
        self.printer_state_label.setVisible(is_printer)
        self.printer_state_combo.setVisible(is_printer)
        self.printer_progress_label.setVisible(is_printer)
        self.printer_progress_combo.setVisible(is_printer)
        self.printer_camera_label.setVisible(is_printer)
        self.printer_camera_combo.setVisible(is_printer)
        self.printer_nozzle_label.setVisible(is_printer)
        self.printer_nozzle_combo.setVisible(is_printer)
        self.printer_nozzle_target_label.setVisible(is_printer)
        self.printer_nozzle_target_combo.setVisible(is_printer)
        self.printer_bed_label.setVisible(is_printer)
        self.printer_bed_combo.setVisible(is_printer)
        self.printer_bed_target_label.setVisible(is_printer)
        self.printer_bed_target_combo.setVisible(is_printer)
        self.printer_pause_label.setVisible(is_printer)
        self.printer_pause_combo.setVisible(is_printer)
        self.printer_stop_label.setVisible(is_printer)
        self.printer_stop_combo.setVisible(is_printer)
        
        # Hide standard entity picker if 3D printer
        self.entity_combo.setVisible(not is_printer)
        self.form.labelForField(self.entity_combo).setVisible(not is_printer)
        
        # Disable appearance section for camera (no icon/color needed)
        self._set_appearance_enabled(not is_camera)
        
        # Icon Visibility
        # Remove option to choose icon for sensors and 3D printers
        show_icon = current_type not in ['widget', '3d_printer']
        self.icon_input.setVisible(show_icon)
        if hasattr(self, 'icon_label'):
            self.icon_label.setVisible(show_icon)
        
        # Color Option Visibility
        # Remove for Weather and Camera. Sensors (widget) now have color choice.
        show_color = current_type not in ['weather', 'camera']
        if hasattr(self, 'color_widget'):
            self.color_widget.setVisible(show_color)
        if hasattr(self, 'color_label'):
            self.color_label.setVisible(show_color)
            
        # Default Color Logic for Sensors
        # If switching to Sensor and color is default Blue, switch to Sensor Gray
        if current_type == 'widget' and self.selected_color == "#4285F4":
            self.select_color("#3C3C3C")
        
        # Find the label associated with the widget and hide it too.
        # Layouts don't automatically hide labels for hidden widgets.
        if self.form.labelForField(self.precision_spin):
             self.form.labelForField(self.precision_spin).setVisible(is_sensor)
             
        if self.form.labelForField(self.service_combo):
             self.form.labelForField(self.service_combo).setVisible(current_type == 'switch')
        
        # Refresh entity list for the new type
        self.populate_entities()
        
        # Notify that the layout requirements may have changed
        self.size_changed.emit()
    
    def _set_appearance_enabled(self, enabled: bool):
        """Enable or disable appearance section widgets."""
        # Grey out appearance header
        if hasattr(self, 'appearance_header') and self.appearance_header:
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
            
            # Default to Light / Switch (switch)
            switch_idx = next((i for i, t in enumerate(self.TYPE_DEFINITIONS) if t[1] == 'switch'), 0)
            self.type_combo.setCurrentIndex(switch_idx)
            
            self.service_combo.setCurrentIndex(0)
            return
            
        self.label_input.setText(self.config.get('label', ''))
        self.icon_input.setText(self.config.get('icon', ''))
        
        # Find index by internal type name
        config_type = self.config.get('type', 'switch')
        type_idx = next((i for i, t in enumerate(self.TYPE_DEFINITIONS) if t[1] == config_type), 0)
        self.type_combo.setCurrentIndex(type_idx)
        
        eid = self.config.get('entity_id', '')
        if eid:
            self._restore_combo_selection(self.entity_combo, eid)
            
        service = self.config.get('service', 'toggle')
        svc_name = service.split('.')[-1]
        svc_idx = self.service_combo.findText(svc_name)
        if svc_idx >= 0: self.service_combo.setCurrentIndex(svc_idx)
        
        # (Advanced mode checked logic removed)
        self.show_album_art_check.setChecked(self.config.get('show_album_art', True))
        self.animated_bg_toggle.setChecked(self.config.get('animated_bg', True))

        # Precision
        self.precision_spin.setValue(self.config.get('precision', 1))
        
        # Camera settings (Removed - always stream)
        # camera_mode = self.config.get('camera_mode', 'picture')
        # self.camera_mode_combo.setCurrentIndex(0 if camera_mode == 'picture' else 1)
        
        # Automation settings
        automation_action = self.config.get('action', 'toggle')
        self.automation_action_combo.setCurrentIndex(1 if automation_action == 'trigger' else 0)

        # Lock settings
        lock_action = self.config.get('action', 'toggle')
        if lock_action == 'lock':
            self.lock_action_combo.setCurrentIndex(1)
        elif lock_action == 'unlock':
            self.lock_action_combo.setCurrentIndex(2)
        else:
            self.lock_action_combo.setCurrentIndex(0) # Toggle
            
        # 3D Printer Settings
        self._restore_combo_selection(self.printer_state_combo, self.config.get('printer_state_entity', ''))
        self._restore_combo_selection(self.printer_progress_combo, self.config.get('printer_progress_entity', ''))
        self._restore_combo_selection(self.printer_camera_combo, self.config.get('printer_camera_entity', ''))
        self._restore_combo_selection(self.printer_nozzle_combo, self.config.get('printer_nozzle_entity', ''))
        self._restore_combo_selection(self.printer_nozzle_target_combo, self.config.get('printer_nozzle_target_entity', ''))
        self._restore_combo_selection(self.printer_bed_combo, self.config.get('printer_bed_entity', ''))
        self._restore_combo_selection(self.printer_bed_target_combo, self.config.get('printer_bed_target_entity', ''))
        self._restore_combo_selection(self.printer_pause_combo, self.config.get('printer_pause_entity', ''))
        self._restore_combo_selection(self.printer_stop_combo, self.config.get('printer_stop_entity', ''))
        
        self.select_color(self.config.get('color', '#4285F4'))
        
        # Trigger type-specific UI updates
        self.on_type_changed(self.type_combo.currentIndex())
        
        # Shortcut
        shortcut = self.config.get('custom_shortcut', {})
        self.custom_shortcut_check.setChecked(shortcut.get('enabled', False))
        self.shortcut_display.setText(shortcut.get('value', ''))
        self.on_custom_shortcut_toggled(shortcut.get('enabled', False))
        
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
        
        type_idx = self.type_combo.currentIndex()
        new_config['type'] = self.TYPE_DEFINITIONS[type_idx][1] if 0 <= type_idx < len(self.TYPE_DEFINITIONS) else 'switch'
        
        # Get entity_id from combo data (UserRole), fallback to typed text
        new_config['entity_id'] = self._get_combo_entity_id(self.entity_combo)
        
        if new_config['type'] == 'climate':
            # Clean up deprecated legacy key for backwards compatibility
            new_config.pop('advanced_mode', None)
            
        if new_config['type'] == 'media_player':
            new_config['show_album_art'] = self.show_album_art_check.isChecked()
            new_config['animated_bg'] = self.animated_bg_toggle.isChecked()
            
        if new_config['type'] == 'switch':
             new_config['service'] = f"{new_config['entity_id'].split('.')[0]}.{self.service_combo.currentText()}"
        
        if new_config['type'] == 'widget':
            new_config['precision'] = self.precision_spin.value()
        
        if new_config['type'] == 'camera':
            new_config['camera_mode'] = 'stream'
            # Preserve existing size/span if set, otherwise default to 1x1 during creation
            if 'span_x' not in new_config: new_config['span_x'] = 1
            if 'span_y' not in new_config: new_config['span_y'] = 1
            # Sync camera_size to span for compatibility
            new_config['camera_size'] = new_config['span_x']
             
        if new_config['type'] == 'automation':
            new_config['action'] = 'trigger' if self.automation_action_combo.currentIndex() == 1 else 'toggle'

        if new_config['type'] == 'lock':
            idx = self.lock_action_combo.currentIndex()
            if idx == 1: new_config['action'] = 'lock'
            elif idx == 2: new_config['action'] = 'unlock'
            else: new_config['action'] = 'toggle'
            
        if new_config['type'] == '3d_printer':
            new_config['printer_state_entity'] = self._get_combo_entity_id(self.printer_state_combo)
            new_config['printer_progress_entity'] = self._get_combo_entity_id(self.printer_progress_combo)
            new_config['printer_camera_entity'] = self._get_combo_entity_id(self.printer_camera_combo)
            new_config['printer_nozzle_entity'] = self._get_combo_entity_id(self.printer_nozzle_combo)
            new_config['printer_nozzle_target_entity'] = self._get_combo_entity_id(self.printer_nozzle_target_combo)
            new_config['printer_bed_entity'] = self._get_combo_entity_id(self.printer_bed_combo)
            new_config['printer_bed_target_entity'] = self._get_combo_entity_id(self.printer_bed_target_combo)
            new_config['printer_pause_entity'] = self._get_combo_entity_id(self.printer_pause_combo)
            new_config['printer_stop_entity'] = self._get_combo_entity_id(self.printer_stop_combo)
            # Override standard entity_id with state entity for generic handling if needed
            new_config['entity_id'] = new_config['printer_state_entity']
        
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
                 self.input_manager.restore_shortcut()
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
            self.input_manager.restore_shortcut()
            # Restore if empty
            if self.shortcut_display.text() == "Press keys...":
                 sc = self.config.get('custom_shortcut', {}) if self.config else {}
                 self.shortcut_display.setText(sc.get('value', ''))

    @pyqtSlot(dict)
    def on_shortcut_recorded(self, shortcut):
        if not self.record_btn.isChecked():
            return # Ignore if we aren't recording
            
        self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
        self.shortcut_display.setText(shortcut.get('value', ''))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.save()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

