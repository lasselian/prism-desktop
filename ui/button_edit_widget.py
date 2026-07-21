
"""
Embedded Button Editor Widget
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QFormLayout,
    QSpinBox, QDoubleSpinBox, QSizePolicy, QCompleter, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPen
from ui.widgets.toggle_switch import ToggleSwitch
from core.localization_manager import t
from ui.styles import Typography, Dimensions


class HueSlider(QWidget):
    hue_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0
        self.setFixedHeight(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def get_hue(self):
        return self._hue

    def set_hue(self, hue):
        self._hue = max(0, min(359, hue))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        grad = QLinearGradient(0, 0, rect.width(), 0)
        for i in range(7):
            grad.setColorAt(i / 6, QColor.fromHsv((i * 60) % 360, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(rect, 4, 4)
        x = int(self._hue / 359 * max(rect.width() - 1, 1))
        cy = rect.height() // 2
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.setBrush(QColor.fromHsv(self._hue, 255, 255))
        painter.drawEllipse(x - 7, cy - 7, 14, 14)

    def _set_from_x(self, x):
        hue = max(0, min(359, round(x / max(self.width() - 1, 1) * 359)))
        if hue != self._hue:
            self._hue = hue
            self.update()
            self.hue_changed.emit(self._hue)

    def mousePressEvent(self, e):
        self._set_from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_x(e.position().x())

class ButtonEditWidget(QWidget):
    """
    Editor for configuring button properties.
    Uses the same design style as SettingsWidgetV2.
    """
    
    # Internal type identifiers (display labels are looked up via t() at runtime)
    TYPE_DEFINITIONS = [
        ("Alarm Control Panel", "alarm_control_panel"),
        ("Automation", "automation"),
        ("Camera", "camera"),
        ("Climate", "climate"),
        ("Cover", "curtain"),
        ("Fan", "fan"),
        ("Input Number", "input_number"),
        ("Lawn Mower", "lawn_mower"),
        ("Light / Switch", "switch"),
        ("Lock", "lock"),
        ("Media Player", "media_player"),
        ("Pin Window", "pin_window"),
        ("Scene", "scene"),
        ("Script", "script"),
        ("Sensor", "widget"),
        ("Sun", "sun"),
        ("Vacuum", "vacuum"),
        ("Weather", "weather"),
        ("3D Printer", "3d_printer")
    ]

    @staticmethod
    def _get_type_definitions():
        return [
            (t("button_editor.type.alarm_control_panel"), "alarm_control_panel"),
            (t("button_editor.type.automation"), "automation"),
            (t("button_editor.type.camera"), "camera"),
            (t("button_editor.type.climate"), "climate"),
            (t("button_editor.type.cover"), "curtain"),
            (t("button_editor.type.fan"), "fan"),
            (t("button_editor.type.input_number"), "input_number"),
            (t("button_editor.type.lawn_mower"), "lawn_mower"),
            (t("button_editor.type.light_switch"), "switch"),
            (t("button_editor.type.lock"), "lock"),
            (t("button_editor.type.media_player"), "media_player"),
            (t("button_editor.type.pin_window"), "pin_window"),
            (t("button_editor.type.scene"), "scene"),
            (t("button_editor.type.script"), "script"),
            (t("button_editor.type.sensor"), "widget"),
            (t("button_editor.type.sun"), "sun"),
            (t("button_editor.type.vacuum"), "vacuum"),
            (t("button_editor.type.weather"), "weather"),
            (t("button_editor.type.3d_printer"), "3d_printer"),
        ]
    
    saved = pyqtSignal(dict)
    cancelled = pyqtSignal()
    size_changed = pyqtSignal()
    custom_colors_changed = pyqtSignal(list)
    
    # Class-level preference for entity display format (persists across instances)
    _global_show_friendly_names = True
    
    def __init__(self, entities: list, config: dict = None, slot: int = 0, theme_manager=None, input_manager=None, parent=None):
        super().__init__(parent)
        self.entities = entities or []
        self.config = config or {}
        self.slot = slot
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        self.custom_colors = []
        self._custom_swatch_btns = []
        self._editing_custom_hex = None

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
            custom_btn_ring = "rgba(0, 0, 0, 0.28)"
            pill_bg = "rgba(0, 0, 0, 0.05)"
        else:
            input_bg = "rgba(255, 255, 255, 0.08)"
            input_border = "rgba(255, 255, 255, 0.1)"
            input_focus_bg = "rgba(255, 255, 255, 0.12)"
            color_btn_border = "white"
            section_header_color = "#8e8e93"  # Apple gray for dark mode
            custom_btn_ring = "rgba(255, 255, 255, 0.3)"
            pill_bg = "rgba(255, 255, 255, 0.07)"
            
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
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
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
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
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

            QPushButton#colorBtnCustom {{
                border-radius: {Dimensions.RADIUS_SMALL};
                border: 2px solid {custom_btn_ring};
            }}
            QPushButton#colorBtnCustom:checked {{
                border: 2px solid {color_btn_border};
            }}

            QPushButton#colorSaveBtn {{
                background-color: {colors['accent']};
                color: white;
                border: none;
                border-radius: {Dimensions.RADIUS_MEDIUM};
                font-weight: {Typography.WEIGHT_SEMIBOLD};
                padding: 0px;
            }}
            QPushButton#colorSaveBtn:hover {{ background-color: #006ce6; }}

            QWidget#colorPickerPill {{
                background-color: {pill_bg};
                border-radius: 10px;
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
        
        self.cancel_btn = QPushButton(t("button_editor.cancel_btn"))
        self.cancel_btn.setMinimumWidth(70)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)

        title_text = t("button_editor.title_edit") if self.config else t("button_editor.title_add")
        title = QLabel(title_text)
        title.setObjectName("headerTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.save_btn = QPushButton(t("button_editor.save_btn"))
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
        self._add_section_header(t("button_editor.section.configuration"))

        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText(t("button_editor.label_placeholder"))
        self.label_input.returnPressed.connect(self.save)
        self.form.addRow(t("button_editor.label_label"), self.label_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems([td[0] for td in self._get_type_definitions()])
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.form.addRow(t("button_editor.type_label"), self.type_combo)

        # Display Toggle (Global across all entity dropdowns)
        self.friendly_toggle_btn = QPushButton(
            t("button_editor.show_ids_btn") if ButtonEditWidget._global_show_friendly_names else t("button_editor.show_names_btn")
        )
        self.friendly_toggle_btn.setObjectName("friendlyToggleBtn")
        self.friendly_toggle_btn.setMinimumWidth(110)
        self.friendly_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.friendly_toggle_btn.setToolTip(t("button_editor.show_ids_tooltip"))
        self.friendly_toggle_btn.clicked.connect(self._toggle_entity_display)
        self.form.addRow(t("button_editor.display_label"), self.friendly_toggle_btn)

        self.entity_combo = self._create_entity_combo()
        self.entity_combo.lineEdit().setPlaceholderText(t("button_editor.entity_placeholder"))
        self.entity_combo.lineEdit().returnPressed.connect(self.save)
        self.populate_entities()

        self.form.addRow(t("button_editor.entity_label"), self.entity_combo)
        
        # (Advanced mode toggle removed - climate now defaults to advanced)
        
        # Show Album Art (Media Player Only)
        self.show_album_art_check = ToggleSwitch(t("button_editor.album_art_toggle"))
        self.show_album_art_check.setToolTip(t("button_editor.album_art_tooltip"))
        self.show_album_art_check.setChecked(True)
        self.show_album_art_check.setVisible(False)
        self.form.addRow("", self.show_album_art_check)

        # Animated Background (Media Player Only)
        self.animated_bg_toggle = ToggleSwitch(t("button_editor.animated_bg_toggle"))
        self.animated_bg_toggle.setToolTip(t("button_editor.animated_bg_tooltip"))
        self.animated_bg_toggle.setChecked(True)
        self.animated_bg_toggle.setVisible(False)
        self.form.addRow("", self.animated_bg_toggle)

        # Precision (Widget/Sensor Only)
        self.precision_spin = QSpinBox()
        self.precision_spin.setRange(0, 5)
        self.precision_spin.setToolTip(t("button_editor.decimals_tooltip"))
        self.precision_spin.setVisible(False)
        self.form.addRow(t("button_editor.decimals_label"), self.precision_spin)

        # Display Style (Widget/Sensor Only) — Normal / Gauge / Bar
        self.display_style_combo = QComboBox()
        self.display_style_combo.addItems([
            t("button_editor.display_style_normal"),
            t("button_editor.display_style_gauge"),
            t("button_editor.display_style_bar"),
            t("button_editor.display_style_perimeter"),
        ])
        self.display_style_combo.setToolTip(t("button_editor.display_style_tooltip"))
        self.form.addRow(t("button_editor.display_style_label"), self.display_style_combo)
        self.form.setRowVisible(self.display_style_combo, False)

        self.sensor_min_spin = QDoubleSpinBox()
        self.sensor_min_spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        self.sensor_min_spin.setDecimals(2)
        self.sensor_min_spin.setValue(0.0)
        self.sensor_min_spin.setToolTip(t("button_editor.min_tooltip"))
        self.form.addRow(t("button_editor.min_label"), self.sensor_min_spin)
        self.form.setRowVisible(self.sensor_min_spin, False)

        self.sensor_max_spin = QDoubleSpinBox()
        self.sensor_max_spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        self.sensor_max_spin.setDecimals(2)
        self.sensor_max_spin.setValue(100.0)
        self.sensor_max_spin.setToolTip(t("button_editor.max_tooltip"))
        self.form.addRow(t("button_editor.max_label"), self.sensor_max_spin)
        self.form.setRowVisible(self.sensor_max_spin, False)

        self.entry_animation_toggle = ToggleSwitch(t("button_editor.entry_animation_toggle"))
        self.entry_animation_toggle.setToolTip(t("button_editor.entry_animation_tooltip"))
        self.entry_animation_toggle.setChecked(True)
        self.form.addRow("", self.entry_animation_toggle)
        self.form.setRowVisible(self.entry_animation_toggle, False)

        self.display_style_combo.currentIndexChanged.connect(self._on_display_style_changed)

        # Service (Switches only)
        self.service_label = QLabel(t("button_editor.service_label"))
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
        self.automation_action_label = QLabel(t("button_editor.action_label"))
        self.automation_action_combo = QComboBox()
        self.automation_action_combo.addItems([t("button_editor.automation_toggle"), t("button_editor.automation_trigger")])
        self.automation_action_combo.setToolTip(t("button_editor.automation_tooltip"))
        self.automation_action_label.setVisible(False)
        self.automation_action_combo.setVisible(False)
        self.form.addRow(self.automation_action_label, self.automation_action_combo)
        
        # Lock Action (Lock Only)
        self.lock_action_label = QLabel(t("button_editor.action_label"))
        self.lock_action_combo = QComboBox()
        self.lock_action_combo.addItems([t("button_editor.lock_toggle_smart"), t("button_editor.lock_lock"), t("button_editor.lock_unlock")])
        self.lock_action_combo.setToolTip(t("button_editor.lock_tooltip"))
        self.lock_action_label.setVisible(False)
        self.lock_action_combo.setVisible(False)
        self.form.addRow(self.lock_action_label, self.lock_action_combo)

        # Script Arguments (Script Only)
        self.script_args_label = QLabel(t("button_editor.arguments_label"))
        self.script_args_label.setVisible(False)
        self.script_arg_rows = []

        self.script_args_widget = QWidget()
        script_args_layout = QVBoxLayout(self.script_args_widget)
        script_args_layout.setContentsMargins(0, 0, 0, 0)
        script_args_layout.setSpacing(6)

        self.script_args_container = QVBoxLayout()
        self.script_args_container.setSpacing(4)
        script_args_layout.addLayout(self.script_args_container)

        self.script_add_arg_btn = QPushButton(t("button_editor.add_argument_btn"))
        self.script_add_arg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.script_add_arg_btn.clicked.connect(lambda: self._add_script_arg_row())
        script_args_layout.addWidget(self.script_add_arg_btn)

        self.script_args_widget.setVisible(False)
        self.form.addRow(self.script_args_label, self.script_args_widget)

        # 3D Printer specific fields
        self.printer_state_label = QLabel(t("button_editor.printer.state_label"))
        self.printer_state_combo = self._create_entity_combo()
        self.form.addRow(self.printer_state_label, self.printer_state_combo)

        self.printer_progress_label = QLabel(t("button_editor.printer.progress_label"))
        self.printer_progress_combo = self._create_entity_combo()
        self.form.addRow(self.printer_progress_label, self.printer_progress_combo)

        self.printer_camera_label = QLabel(t("button_editor.printer.camera_label"))
        self.printer_camera_combo = self._create_entity_combo()
        self.form.addRow(self.printer_camera_label, self.printer_camera_combo)

        self.printer_nozzle_label = QLabel(t("button_editor.printer.nozzle_label"))
        self.printer_nozzle_combo = self._create_entity_combo()
        self.form.addRow(self.printer_nozzle_label, self.printer_nozzle_combo)

        self.printer_nozzle_target_label = QLabel(t("button_editor.printer.nozzle_target_label"))
        self.printer_nozzle_target_combo = self._create_entity_combo()
        self.form.addRow(self.printer_nozzle_target_label, self.printer_nozzle_target_combo)

        self.printer_bed_label = QLabel(t("button_editor.printer.bed_label"))
        self.printer_bed_combo = self._create_entity_combo()
        self.form.addRow(self.printer_bed_label, self.printer_bed_combo)

        self.printer_bed_target_label = QLabel(t("button_editor.printer.bed_target_label"))
        self.printer_bed_target_combo = self._create_entity_combo()
        self.form.addRow(self.printer_bed_target_label, self.printer_bed_target_combo)

        self.printer_pause_label = QLabel(t("button_editor.printer.pause_label"))
        self.printer_pause_combo = self._create_entity_combo()
        self.form.addRow(self.printer_pause_label, self.printer_pause_combo)

        self.printer_stop_label = QLabel(t("button_editor.printer.stop_label"))
        self.printer_stop_combo = self._create_entity_combo()
        self.form.addRow(self.printer_stop_label, self.printer_stop_combo)

        
        # --- Appearance Section ---
        self.appearance_header = self._add_section_header(t("button_editor.section.appearance"))

        # Icon Input
        self.icon_input = QLineEdit()
        self.icon_input.setPlaceholderText(t("button_editor.icon_placeholder"))
        self.icon_input.returnPressed.connect(self.save)
        self.form.addRow(t("button_editor.icon_label"), self.icon_input)
        self.icon_label = self.form.labelForField(self.icon_input)
        
        # Color Picker
        color_widget = QWidget()
        color_outer_layout = QVBoxLayout(color_widget)
        color_outer_layout.setContentsMargins(0, 0, 0, 0)
        color_outer_layout.setSpacing(6)

        # --- Swatch row ---
        color_btn_row_widget = QWidget()
        color_btn_row_layout = QHBoxLayout(color_btn_row_widget)
        color_btn_row_layout.setContentsMargins(0, 0, 0, 0)
        color_btn_row_layout.setSpacing(8)
        self.color_button_row = color_btn_row_layout

        self.preset_colors = [
            ("#4285F4", t("button_editor.color.blue")),
            ("#34A853", t("button_editor.color.green")),
            ("#B71C1C", t("button_editor.color.red")),
            ("#E65100", t("button_editor.color.orange")),
            ("#6A1B9A", t("button_editor.color.purple")),
            ("#AD1457", t("button_editor.color.pink")),
            ("#607D8B", t("button_editor.color.gray")),
            ("#3C3C3C", t("button_editor.color.sensor_gray")),
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
                btn.setStyleSheet("""
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #ffffff, stop:0.49 #ffffff,
                        stop:0.51 #3c3c3c, stop:1 #3c3c3c);
                """)
            else:
                btn.setStyleSheet(f"background-color: {color_hex};")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=color_hex: self.select_color(c))
            color_btn_row_layout.addWidget(btn)
            self.color_buttons.append((btn, color_hex))

        # Rainbow button (opens custom picker)
        self.rainbow_btn = QPushButton()
        self.rainbow_btn.setObjectName("colorBtn")
        self.rainbow_btn.setFixedSize(24, 24)
        self.rainbow_btn.setCheckable(True)
        self.rainbow_btn.setToolTip(t("button_editor.color.custom_tooltip"))
        self.rainbow_btn.setStyleSheet("""
            background: qlineargradient(x1:0, y1:1, x2:1, y2:0,
                stop:0 #ff0000, stop:0.17 #ffff00, stop:0.33 #00ff00,
                stop:0.5 #00ffff, stop:0.67 #0000ff, stop:0.83 #ff00ff,
                stop:1 #ff0000);
            border-radius: 4px;
        """)
        self.rainbow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rainbow_btn.toggled.connect(self._on_rainbow_toggled)
        color_btn_row_layout.addWidget(self.rainbow_btn)
        color_btn_row_layout.addStretch()

        color_outer_layout.addWidget(color_btn_row_widget)

        # --- Inline custom color picker (hidden by default) ---
        self.color_picker_container = QWidget()
        self.color_picker_container.setObjectName("colorPickerPill")
        self.color_picker_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        picker_layout = QVBoxLayout(self.color_picker_container)
        picker_layout.setContentsMargins(10, 10, 10, 10)
        picker_layout.setSpacing(8)

        self.hue_slider = HueSlider()
        picker_layout.addWidget(self.hue_slider)

        picker_bottom = QWidget()
        picker_bottom_row = QHBoxLayout(picker_bottom)
        picker_bottom_row.setContentsMargins(0, 0, 0, 0)
        picker_bottom_row.setSpacing(6)

        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("#RRGGBB")
        self.hex_input.setMaximumWidth(100)
        picker_bottom_row.addWidget(self.hex_input)
        picker_bottom_row.addStretch()

        self.save_color_btn = QPushButton("+")
        self.save_color_btn.setObjectName("colorSaveBtn")
        self.save_color_btn.setFixedSize(32, 32)
        self.save_color_btn.setToolTip(t("button_editor.color.save_swatch_tooltip"))
        self.save_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        picker_bottom_row.addWidget(self.save_color_btn)

        picker_layout.addWidget(picker_bottom)
        self.color_picker_container.setVisible(False)
        color_outer_layout.addWidget(self.color_picker_container)

        # Connections
        self.hue_slider.hue_changed.connect(self._on_hue_changed)
        self.hex_input.editingFinished.connect(self._on_hex_input_finished)
        self.save_color_btn.clicked.connect(self._on_save_custom_color)

        self.form.addRow(t("button_editor.color_label"), color_widget)
        self.color_widget = color_widget
        self.color_label = self.form.labelForField(color_widget)
        
        # --- Shortcut Section ---
        self.shortcut_header = self._add_section_header(t("button_editor.section.shortcut"))

        self.custom_shortcut_check = ToggleSwitch(t("button_editor.custom_shortcut_toggle"))
        self.custom_shortcut_check.toggled.connect(self.on_custom_shortcut_toggled)
        self.form.addRow("", self.custom_shortcut_check)

        shortcut_keys_container = QWidget()
        shortcut_row = QHBoxLayout(shortcut_keys_container)
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
        self.record_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        btn_layout.addWidget(self.record_icon)

        # Add to row
        shortcut_row.addWidget(self.shortcut_display, 8)
        shortcut_row.addSpacing(12)
        shortcut_row.addWidget(self.record_btn)
        shortcut_row.addStretch(2)

        self.shortcut_keys_container = shortcut_keys_container
        self.form.addRow(t("button_editor.keys_label"), shortcut_keys_container)
        
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
            'alarm_control_panel': {'alarm_control_panel'},
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
            'camera': {'camera', 'image'},
            'weather': {'weather'},
            'lock': {'lock'},
            'lawn_mower': {'lawn_mower'},
            'vacuum': {'vacuum'},
            'sun': {'sun'}
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
        self.friendly_toggle_btn.setText(
            t("button_editor.show_ids_btn") if ButtonEditWidget._global_show_friendly_names else t("button_editor.show_names_btn")
        )
        self.populate_entities()

    def _current_type(self):
        idx = self.type_combo.currentIndex()
        return self.TYPE_DEFINITIONS[idx][1] if 0 <= idx < len(self.TYPE_DEFINITIONS) else 'switch'

    def _on_display_style_changed(self, *_):
        """Show Min/Max rows only when Gauge or Bar is selected AND type is sensor."""
        is_sensor = self._current_type() == 'widget'
        style_idx = self.display_style_combo.currentIndex()
        needs_range = is_sensor and style_idx in (1, 2, 3)
        self.form.setRowVisible(self.sensor_min_spin, needs_range)
        self.form.setRowVisible(self.sensor_max_spin, needs_range)

        needs_anim = is_sensor and style_idx in (1, 3)  # gauge=1, perimeter=3
        self.form.setRowVisible(self.entry_animation_toggle, needs_anim)

        # Hide color palette for sensor with Normal display style
        if is_sensor:
            show_color = style_idx != 0
            self.color_widget.setVisible(show_color)
            self.color_label.setVisible(show_color)

        self.size_changed.emit()

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
        self.form.setRowVisible(self.display_style_combo, is_sensor)
        self._on_display_style_changed()

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

        # Show script specific controls
        is_script = current_type == 'script'
        self.script_args_label.setVisible(is_script)
        self.script_args_widget.setVisible(is_script)

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
        
        is_pin = current_type == 'pin_window'

        # Hide standard entity picker if 3D printer or pin_window (no entity needed)
        self.entity_combo.setVisible(not is_printer and not is_pin)
        self.form.labelForField(self.entity_combo).setVisible(not is_printer and not is_pin)

        # Disable appearance section for camera (no icon/color needed)
        self._set_appearance_enabled(not is_camera)

        # Sun and pin_window have no label and no shortcut — hide those rows
        is_sun = current_type == 'sun'
        _hide_label_shortcut = is_sun or is_pin
        self.label_input.setVisible(not _hide_label_shortcut)
        if self.form.labelForField(self.label_input):
            self.form.labelForField(self.label_input).setVisible(not _hide_label_shortcut)

        self.shortcut_header.setVisible(not _hide_label_shortcut)
        self.custom_shortcut_check.setVisible(not _hide_label_shortcut)
        self.shortcut_keys_container.setVisible(not _hide_label_shortcut)
        if self.form.labelForField(self.shortcut_keys_container):
            self.form.labelForField(self.shortcut_keys_container).setVisible(not _hide_label_shortcut)

        # Icon Visibility
        # Remove option to choose icon for sensors, 3D printers, sun, and pin_window (icon is state-driven)
        show_icon = current_type not in ['widget', '3d_printer', 'sun', 'pin_window']
        self.icon_input.setVisible(show_icon)
        if hasattr(self, 'icon_label'):
            self.icon_label.setVisible(show_icon)

        # Sun: show the animate-on-open toggle (sensor gauge/perimeter are handled by _on_display_style_changed)
        if is_sun:
            self.form.setRowVisible(self.entry_animation_toggle, True)
        elif current_type != 'widget':
            self.form.setRowVisible(self.entry_animation_toggle, False)

        # Color Option Visibility
        # Remove for Weather, Camera, Sun, and pin_window (color is not applicable)
        show_color = current_type not in ['weather', 'camera', 'sun', 'pin_window']
        if hasattr(self, 'color_widget'):
            self.color_widget.setVisible(show_color)
        if hasattr(self, 'color_label'):
            self.color_label.setVisible(show_color)
            
        # Default color per type when coming from the generic blue default
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
    
    def _add_script_arg_row(self, key="", value=""):
        """Add a key-value argument row for script variables."""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        key_input = QLineEdit()
        key_input.setPlaceholderText(t("button_editor.arg_key_placeholder"))
        key_input.setText(key)
        row_layout.addWidget(key_input, 1)

        value_input = QLineEdit()
        value_input.setPlaceholderText(t("button_editor.arg_value_placeholder"))
        value_input.setText(value)
        row_layout.addWidget(value_input, 1)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row_layout.addWidget(remove_btn)

        row_entry = {'widget': row_widget, 'key_input': key_input, 'value_input': value_input}
        remove_btn.clicked.connect(lambda: self._remove_script_arg_row(row_entry))

        self.script_arg_rows.append(row_entry)
        self.script_args_container.addWidget(row_widget)
        self.size_changed.emit()

    def _remove_script_arg_row(self, row_entry):
        """Remove a script argument row."""
        if row_entry in self.script_arg_rows:
            self.script_arg_rows.remove(row_entry)
            row_entry['widget'].setParent(None)
            row_entry['widget'].deleteLater()
            self.size_changed.emit()

    def _clear_script_arg_rows(self):
        """Remove all script argument rows."""
        for row_entry in self.script_arg_rows[:]:
            row_entry['widget'].setParent(None)
            row_entry['widget'].deleteLater()
        self.script_arg_rows.clear()

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
        if hasattr(self, 'rainbow_btn'):
            self.rainbow_btn.setEnabled(enabled)
        if hasattr(self, 'hue_slider'):
            self.hue_slider.setEnabled(enabled)
        if hasattr(self, 'hex_input'):
            self.hex_input.setEnabled(enabled)
        if hasattr(self, 'save_color_btn'):
            self.save_color_btn.setEnabled(enabled)
        if hasattr(self, 'color_label') and self.color_label:
            self.color_label.setEnabled(enabled)

    def select_color(self, color_hex):
        self.selected_color = color_hex
        for btn, c in self.color_buttons:
            btn.setChecked(c == color_hex)
        if not hasattr(self, 'rainbow_btn'):
            return
        is_known = any(c == color_hex for _, c in self.color_buttons)
        # Block rainbow_btn signals while we set its state to avoid recursion
        self.rainbow_btn.blockSignals(True)
        self.rainbow_btn.setChecked(not is_known)
        self.rainbow_btn.blockSignals(False)
        if not is_known:
            qc = QColor(color_hex)
            if qc.isValid():
                h = qc.hsvHue()
                self.hue_slider.set_hue(h if h >= 0 else 0)
                self.hex_input.setText(color_hex.upper())
        elif hasattr(self, 'color_picker_container') and self.color_picker_container.isVisible():
            self.color_picker_container.setVisible(False)
            self.size_changed.emit()

    # --- Custom color picker methods ---

    def set_custom_colors(self, colors: list):
        self._editing_custom_hex = None
        self.custom_colors = list(colors)
        stale = set(id(b) for b in self._custom_swatch_btns)
        self.color_buttons = [(b, c) for b, c in self.color_buttons if id(b) not in stale]
        for btn in self._custom_swatch_btns:
            self.color_button_row.removeWidget(btn)
            btn.deleteLater()
        self._custom_swatch_btns.clear()
        for hex_color in self.custom_colors:
            self._add_custom_color_swatch(hex_color, save=False)

    def _add_custom_color_swatch(self, color_hex: str, save: bool = True):
        btn = QPushButton()
        btn.setObjectName("colorBtnCustom")
        btn.setFixedSize(24, 24)
        btn.setCheckable(True)
        btn.setToolTip(color_hex)
        btn.setStyleSheet(f"background-color: {color_hex};")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked, c=color_hex: self.select_color(c))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, c=color_hex, b=btn: self._show_custom_color_menu(pos, c, b)
        )
        # Insert before rainbow button (rainbow is second-to-last, stretch is last)
        insert_pos = self.color_button_row.count() - 2
        self.color_button_row.insertWidget(insert_pos, btn)
        self.color_buttons.append((btn, color_hex))
        self._custom_swatch_btns.append(btn)
        if save:
            self.custom_colors.append(color_hex)
            self.custom_colors_changed.emit(list(self.custom_colors))

    def _on_rainbow_toggled(self, checked: bool):
        self.color_picker_container.setVisible(checked)
        if checked:
            for btn, _ in self.color_buttons:
                btn.setChecked(False)
            self._update_color_from_slider()
        self.size_changed.emit()

    def _update_color_from_slider(self):
        color = QColor.fromHsv(self.hue_slider.get_hue(), 255, 255)
        self.selected_color = color.name().upper()
        self.hex_input.setText(self.selected_color)

    def _on_hue_changed(self, hue: int):
        self._update_color_from_slider()

    def _on_hex_input_finished(self):
        text = self.hex_input.text().strip().lstrip('#')
        if len(text) == 6:
            c = QColor(f"#{text}")
            if c.isValid():
                self.select_color(f"#{text.upper()}")

    def _on_save_custom_color(self):
        if not self.selected_color:
            return
        new_color = self.selected_color.upper()
        if self._editing_custom_hex is not None:
            old_hex = self._editing_custom_hex
            self._editing_custom_hex = None
            self._delete_custom_color_silent(old_hex)
        existing_upper = {c.upper() for _, c in self.color_buttons}
        if new_color not in existing_upper:
            self._add_custom_color_swatch(new_color, save=True)
        self.select_color(new_color)

    def _show_custom_color_menu(self, pos, color_hex: str, btn: QPushButton):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        edit_action = menu.addAction(t("context_menu.edit"))
        delete_action = menu.addAction(t("context_menu.clear"))
        action = menu.exec(btn.mapToGlobal(pos))
        if action == edit_action:
            self._edit_custom_color(color_hex)
        elif action == delete_action:
            self._delete_custom_color(color_hex)

    def _menu_stylesheet(self) -> str:
        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {'text': '#e0e0e0', 'accent': '#007aff'}
        is_light = colors.get('text', '#ffffff') == '#1e1e1e'
        bg, border, text = ('#f5f5f5', '#ddd', '#1e1e1e') if is_light else ('#2b2b2b', '#3d3d3d', '#e0e0e0')
        return f"""
            QMenu {{ background-color: {bg}; border: 1px solid {border}; border-radius: 6px; padding: 4px; }}
            QMenu::item {{ background: transparent; padding: 6px 20px 6px 12px; color: {text}; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: #007aff; color: white; }}
        """

    def _edit_custom_color(self, color_hex: str):
        self._editing_custom_hex = color_hex
        qc = QColor(color_hex)
        if qc.isValid():
            h = qc.hsvHue()
            self.hue_slider.set_hue(h if h >= 0 else 0)
            self.selected_color = color_hex.upper()
            self.hex_input.setText(self.selected_color)
        # Deselect all preset/custom buttons
        for btn, _ in self.color_buttons:
            btn.setChecked(False)
        self.rainbow_btn.blockSignals(True)
        self.rainbow_btn.setChecked(True)
        self.rainbow_btn.blockSignals(False)
        self.color_picker_container.setVisible(True)
        self.size_changed.emit()

    def _delete_custom_color(self, color_hex: str):
        self._delete_custom_color_silent(color_hex)
        self.custom_colors_changed.emit(list(self.custom_colors))
        if self.selected_color.upper() == color_hex.upper():
            self.select_color(self.preset_colors[0][0])

    def _delete_custom_color_silent(self, color_hex: str):
        btn_to_remove = next(
            (b for b in self._custom_swatch_btns if b.toolTip().upper() == color_hex.upper()), None
        )
        if btn_to_remove is None:
            return
        self.color_button_row.removeWidget(btn_to_remove)
        btn_to_remove.deleteLater()
        self._custom_swatch_btns = [b for b in self._custom_swatch_btns if b is not btn_to_remove]
        self.color_buttons = [(b, c) for b, c in self.color_buttons if b is not btn_to_remove]
        self.custom_colors = [c for c in self.custom_colors if c.upper() != color_hex.upper()]

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

        # Display style (sensor)
        style = self.config.get('display_style', 'normal')
        idx = {'normal': 0, 'gauge': 1, 'bar': 2, 'perimeter': 3}.get(style, 0)
        self.display_style_combo.setCurrentIndex(idx)
        self.sensor_min_spin.setValue(float(self.config.get('sensor_min', 0.0)))
        self.sensor_max_spin.setValue(float(self.config.get('sensor_max', 100.0)))
        self.entry_animation_toggle.setChecked(self.config.get('entry_animation', True))
        
        # Camera settings (Removed - always stream)
        # camera_mode = self.config.get('camera_mode', 'picture')
        # self.camera_mode_combo.setCurrentIndex(0 if camera_mode == 'picture' else 1)
        
        # Automation settings
        automation_action = self.config.get('action', 'toggle')
        self.automation_action_combo.setCurrentIndex(1 if automation_action == 'trigger' else 0)

        # Script arguments
        self._clear_script_arg_rows()
        for key, value in self.config.get('script_variables', {}).items():
            self._add_script_arg_row(key, str(value))

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

        if new_config['type'] == 'switch' and '.' in new_config.get('entity_id', ''):
            new_config['service'] = f"{new_config['entity_id'].split('.')[0]}.{self.service_combo.currentText()}"
        
        if new_config['type'] == 'widget':
            new_config['precision'] = self.precision_spin.value()
            new_config['display_style'] = ['normal', 'gauge', 'bar', 'perimeter'][self.display_style_combo.currentIndex()]
            new_config['sensor_min'] = self.sensor_min_spin.value()
            new_config['sensor_max'] = self.sensor_max_spin.value()
            new_config['entry_animation'] = self.entry_animation_toggle.isChecked()
        
        if new_config['type'] == 'sun':
            new_config['entry_animation'] = self.entry_animation_toggle.isChecked()

        if new_config['type'] == 'camera':
            new_config['camera_mode'] = 'stream'
            # Preserve existing size/span if set, otherwise default to 1x1 during creation
            if 'span_x' not in new_config: new_config['span_x'] = 1
            if 'span_y' not in new_config: new_config['span_y'] = 1
            # Sync camera_size to span for compatibility
            new_config['camera_size'] = new_config['span_x']
             
        if new_config['type'] == 'script':
            variables = {}
            for row in self.script_arg_rows:
                key = row['key_input'].text().strip()
                value = row['value_input'].text().strip()
                if key:
                    try:
                        value = int(value)
                    except ValueError:
                        try:
                            value = float(value)
                        except ValueError:
                            pass
                    variables[key] = value
            if variables:
                new_config['script_variables'] = variables
            else:
                new_config.pop('script_variables', None)

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
            self.shortcut_display.setText(t("settings.shortcut.recording"))
            self.input_manager.start_recording()
        else:
            # Record State (Circle)
            self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
            self.input_manager.restore_shortcut()
            # Restore if empty
            if self.shortcut_display.text() == t("settings.shortcut.recording"):
                sc = self.config.get('custom_shortcut', {}) if self.config else {}
                self.shortcut_display.setText(sc.get('value', ''))

    @pyqtSlot(dict)
    def on_shortcut_recorded(self, shortcut):
        if not self.record_btn.isChecked():
            return # Ignore if we aren't recording

        self.record_btn.setChecked(False)
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

