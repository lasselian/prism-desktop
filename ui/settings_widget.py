
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
    QFrame, QColorDialog, QApplication, QButtonGroup, QSizePolicy
)
from ui.widgets.toggle_switch import ToggleSwitch
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QUrl, QTimer, QThread, QSize, QPropertyAnimation, QEasingCurve, QObject, QEvent
from PyQt6.QtGui import QFont, QColor, QDesktopServices, QIcon, QPixmap, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect
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

class _ShortcutRow(QWidget):
    """A shortcut list row with hover-reveal edit and delete action buttons.

    Uses WA_Hover + a single stylesheet so Qt handles the hover state during
    paint — no enterEvent/leaveEvent required, works inside QScrollArea.
    """

    edit_clicked = pyqtSignal(dict)
    delete_clicked = pyqtSignal(dict)

    def __init__(self, btn_cfg: dict, is_light: bool, parent=None):
        super().__init__(parent)
        self.btn_cfg = btn_cfg
        self.setObjectName("shortcutRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        from ui.icons import get_icon, get_mdi_font as _mdi_font, get_icon_for_type

        rl = QHBoxLayout(self)
        rl.setContentsMargins(0, 4, 0, 4)
        rl.setSpacing(10)

        text_color = "#1e1e1e" if is_light else "#e0e0e0"

        _custom_icon = btn_cfg.get('icon', '')
        _icon_char = get_icon(_custom_icon) if _custom_icon else get_icon_for_type(btn_cfg.get('type', 'switch'))
        icon_lbl = QLabel(_icon_char)
        icon_lbl.setFont(_mdi_font(18))
        icon_lbl.setFixedWidth(24)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = btn_cfg.get('color', '')
        icon_lbl.setStyleSheet(f"color: {color if color else text_color}; font-size: 18px;")

        name_lbl = QLabel(btn_cfg.get('label') or btn_cfg.get('entity_id', '—'))
        name_lbl.setStyleSheet(f"color: {text_color}; font-size: 13px; background: transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        sc_val = btn_cfg.get('custom_shortcut', {}).get('value', '')
        sc_text = SettingsWidget._format_shortcut(sc_val)
        sc_lbl = QLabel(sc_text)
        sc_lbl.setStyleSheet("color: #888; font-size: 13px; background: transparent;")
        sc_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Action buttons — always in layout; CSS hides them at rest and reveals on hover
        edit_btn = QPushButton(Icons.PENCIL_OUTLINE)
        edit_btn.setObjectName("editBtn")
        edit_btn.setFont(_mdi_font(15))
        edit_btn.setFixedSize(24, 24)
        edit_btn.setToolTip("Edit shortcut")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self.btn_cfg))

        delete_btn = QPushButton(Icons.TRASH_CAN_OUTLINE)
        delete_btn.setObjectName("deleteBtn")
        delete_btn.setFont(_mdi_font(15))
        delete_btn.setFixedSize(24, 24)
        delete_btn.setToolTip("Remove shortcut")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.btn_cfg))

        rl.addWidget(icon_lbl)
        rl.addWidget(name_lbl)
        rl.addWidget(sc_lbl)
        rl.addWidget(edit_btn)
        rl.addWidget(delete_btn)

        self.setMinimumHeight(32)

        if is_light:
            row_hover_bg = "rgba(0,0,0,0.05)"
            btn_rest = "rgba(0,0,0,0.15)"
            btn_hover = "rgba(0,0,0,0.55)"
        else:
            row_hover_bg = "rgba(255,255,255,0.06)"
            btn_rest = "rgba(255,255,255,0.15)"
            btn_hover = "#888"
        self.setStyleSheet(f"""
            QWidget#shortcutRow {{
                background: transparent;
                border-radius: 6px;
            }}
            QWidget#shortcutRow:hover {{
                background: {row_hover_bg};
            }}
            QWidget#shortcutRow QPushButton#editBtn,
            QWidget#shortcutRow QPushButton#deleteBtn {{
                background: transparent;
                border: none;
                color: {btn_rest};
                border-radius: 4px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                padding: 0px;
            }}
            QWidget#shortcutRow:hover QPushButton#editBtn,
            QWidget#shortcutRow:hover QPushButton#deleteBtn {{
                background: transparent;
                color: {btn_hover};
            }}
            QWidget#shortcutRow QPushButton#editBtn:hover {{
                background: rgba(128,128,128,0.15);
                color: #bbb;
            }}
            QWidget#shortcutRow QPushButton#deleteBtn:hover {{
                background: rgba(220,80,80,0.15);
                color: #e05252;
            }}
        """)


class _IconHoverFilter(QObject):
    """Swaps a button's icon between normal and hover variants on Enter/Leave."""
    def __init__(self, btn: QPushButton, normal: QIcon, hover: QIcon):
        super().__init__(btn)
        self._btn = btn
        self._normal = normal
        self._hover = hover

    def eventFilter(self, obj, event):
        if obj is self._btn:
            if event.type() == QEvent.Type.Enter:
                self._btn.setIcon(self._hover)
            elif event.type() == QEvent.Type.Leave:
                self._btn.setIcon(self._normal)
        return False


class SettingsWidget(QWidget):
    """
    Main settings screen.
    Category pills at the top; clicking one shows only that panel below.
    Window re-animates its height to fit the active panel's content.
    """

    settings_saved = pyqtSignal(dict)
    back_requested = pyqtSignal()
    content_height_changed = pyqtSignal()
    open_button_editor_requested = pyqtSignal(dict)
    request_delete_shortcut = pyqtSignal(dict)
    shortcut_deleted = pyqtSignal()

    def __init__(self, config: dict, theme_manager=None, input_manager=None, current_version="0.0.0", parent=None, dashboard=None):
        super().__init__(parent)
        self.config = config
        self.current_version = current_version
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        self.dashboard = dashboard

        self._test_thread: Optional[ConnectionTestThread] = None
        self._update_thread = None
        self._auto_update_thread = None
        self._geoclue_thread = None
        self._panels: list = []
        self._active_panel_idx = 0
        self._pill_buttons: list = []
        self._delete_anims: list = []  # keep in-flight QPropertyAnimations alive

        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(1200)
        self._debounce_timer.timeout.connect(self._trigger_auto_test)

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(30_000)
        self._poll_timer.timeout.connect(self._trigger_auto_test)

        self.setup_ui()
        self.load_config()
        self.url_input.textChanged.connect(self._on_credential_changed)
        self.token_input.textChanged.connect(self._on_credential_changed)
        if self.url_input.text().strip() and self.token_input.text().strip():
            QTimer.singleShot(600, self._trigger_auto_test)
        self._update_shortcut_controls()

        if self.input_manager:
            self.input_manager.recorded.connect(self.on_shortcut_recorded)

    def _update_stylesheet(self):
        """Build and apply theme-dependent stylesheet."""
        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {
                'text': '#e0e0e0',
                'window_text': '#ffffff',
                'border': '#555555',
                'base': '#2d2d2d',
                'button': '#3d3d3d',
                'button_text': '#ffffff',
                'accent': '#007aff',
            }

        is_light = colors.get('text', '#ffffff') == '#1e1e1e'

        if is_light:
            input_bg = "rgba(0, 0, 0, 0.06)"
            input_border = "rgba(0, 0, 0, 0.25)"
            input_focus_bg = "rgba(0, 0, 0, 0.08)"
            section_header_color = "#555555"
            pill_bg = "rgba(255, 255, 255, 0.85)"
            pill_border = "rgba(0, 0, 0, 0.12)"
            pill_hover_bg = "rgba(0, 0, 0, 0.07)"
            pill_bar_bg = "rgba(0, 0, 0, 0.06)"
            pill_bar_border = "rgba(0, 0, 0, 0.14)"
            pill_text_inactive = "rgba(0, 0, 0, 0.52)"
        else:
            input_bg = "rgba(255, 255, 255, 0.08)"
            input_border = "rgba(255, 255, 255, 0.1)"
            input_focus_bg = "rgba(255, 255, 255, 0.12)"
            section_header_color = "#8e8e93"
            pill_bg = "rgba(30, 30, 30, 0.6)"
            pill_border = "rgba(255, 255, 255, 0.05)"
            pill_hover_bg = "rgba(255, 255, 255, 0.09)"
            pill_bar_bg = "rgba(255, 255, 255, 0.07)"
            pill_bar_border = "rgba(255, 255, 255, 0.12)"
            pill_text_inactive = "rgba(255, 255, 255, 0.52)"

        from ui.styles import Typography, Dimensions

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

            QFrame#pillBar {{
                background-color: {pill_bar_bg};
                border: 1px solid {pill_bar_border};
                border-radius: 12px;
            }}

            QPushButton#categoryPill {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                min-height: 32px;
                font-size: 12px;
                font-weight: {Typography.WEIGHT_MEDIUM};
                color: {pill_text_inactive};
                text-align: left;
            }}
            QPushButton#categoryPill:checked {{
                background-color: {colors['accent']};
                color: white;
            }}
            QPushButton#categoryPill:hover:!checked {{
                background-color: {pill_hover_bg};
                color: {colors['text']};
            }}

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

            QPushButton#bugBtn {{
                background-color: {"rgba(230, 81, 0, 0.07)" if is_light else "rgba(230, 81, 0, 0.08)"};
                border: 1px solid {"rgba(230, 81, 0, 0.30)" if is_light else "rgba(230, 81, 0, 0.35)"};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                color: {"#C84000" if is_light else "#E65100"};
                padding: 0px 14px;
                min-height: 34px;
                max-height: 34px;
                font-size: 13px;
                font-weight: {Typography.WEIGHT_SEMIBOLD};
                text-align: left;
            }}
            QPushButton#bugBtn:hover {{
                background-color: #E65100;
                border-color: #E65100;
                color: white;
            }}
            QPushButton#bugBtn:pressed {{
                background-color: #BF360C;
                border-color: #BF360C;
                color: white;
            }}

            QPushButton#bmcBtn {{
                background-color: {"rgba(180, 120, 0, 0.07)" if is_light else "rgba(255, 187, 51, 0.08)"};
                border: 1px solid {"rgba(180, 120, 0, 0.30)" if is_light else "rgba(255, 187, 51, 0.35)"};
                border-radius: {Dimensions.RADIUS_MEDIUM};
                color: {"#8A6000" if is_light else "#FFBB33"};
                padding: 0px 14px;
                min-height: 34px;
                max-height: 34px;
                font-size: 13px;
                font-weight: {Typography.WEIGHT_SEMIBOLD};
                text-align: left;
            }}
            QPushButton#bmcBtn:hover {{
                background-color: {"#B87800" if is_light else "#CC8800"};
                border-color: {"#B87800" if is_light else "#CC8800"};
                color: white;
            }}
            QPushButton#bmcBtn:pressed {{
                background-color: {"#9A6400" if is_light else "#AA7000"};
                border-color: {"#9A6400" if is_light else "#AA7000"};
                color: white;
            }}
        """)

    def showEvent(self, event):
        super().showEvent(event)
        self._poll_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._poll_timer.stop()

    def setup_ui(self):
        self._update_stylesheet()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

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

        # 2. Body: left sidebar + right content area
        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        # Left sidebar
        pill_frame = QFrame()
        pill_frame.setObjectName("pillBar")
        pill_frame.setFixedWidth(140)
        pill_col = QVBoxLayout(pill_frame)
        pill_col.setContentsMargins(5, 5, 5, 5)
        pill_col.setSpacing(3)

        self._pill_group = QButtonGroup(self)
        self._pill_group.setExclusive(True)

        pill_labels = [
            t("settings.section.home_assistant").title(),
            t("settings.section.appearance").title(),
            t("settings.section.shortcut").title(),
            t("settings.section.support").title(),
        ]

        for i, label in enumerate(pill_labels):
            btn = QPushButton(label)
            btn.setObjectName("categoryPill")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            btn.clicked.connect(lambda checked, idx=i: self._switch_panel(idx))
            self._pill_group.addButton(btn, i)
            self._pill_buttons.append(btn)
            pill_col.addWidget(btn)

        pill_col.addStretch()
        self._pill_buttons[0].setChecked(True)
        body_layout.addWidget(pill_frame)

        # Right content area
        content_widget = QWidget()
        self._content_widget = content_widget
        content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        panel_builders = [
            self._build_ha_panel,
            self._build_appearance_panel,
            self._build_shortcut_panel,
            self._build_support_panel,
        ]
        for i, builder in enumerate(panel_builders):
            panel = builder()
            panel.setVisible(i == 0)
            self._panels.append(panel)
            content_layout.addWidget(panel)

        content_layout.addStretch(1)
        body_layout.addWidget(content_widget)
        layout.addLayout(body_layout)

        self._active_panel_idx = 0

        self._update_stylesheet()

    # -------------------------------------------------------------------------
    # Panel builders
    # -------------------------------------------------------------------------

    _SECTION_HEADER_STYLE = (
        "font-size: 11px; font-weight: 600; color: #8e8e93; "
        "letter-spacing: 0.5px; margin-bottom: 4px;"
    )

    def _make_pill_panel(self):
        """Return a (QFrame, QFormLayout) pair styled as a settingsPill."""
        frame = QFrame()
        frame.setObjectName("settingsPill")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(8)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(16)
        vbox.addLayout(form)
        return frame, form

    def _make_section_header(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet(self._SECTION_HEADER_STYLE)
        return label

    def _build_ha_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Credentials pill
        cred_frame, cred_form = self._make_pill_panel()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(t("settings.ha.url_placeholder"))
        cred_form.addRow(t("settings.ha.url_label"), self.url_input)

        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText(t("settings.ha.token_placeholder"))
        cred_form.addRow(t("settings.ha.token_label"), self.token_input)

        self._conn_status_label = QLabel()
        self._conn_status_label.setWordWrap(True)
        self._conn_status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self._conn_status_label.hide()
        cred_form.addRow("", self._conn_status_label)

        vbox.addWidget(cred_frame)

        # Toggles pill — all data-sending toggles grouped together
        toggle_frame, toggle_form = self._make_pill_panel()

        send_label = self._make_section_header(t("settings.ha.send_to_ha_label"))
        toggle_frame.layout().insertWidget(0, send_label)

        if sys.platform in ('win32', 'linux'):
            self.location_check = ToggleSwitch(t("settings.ha.location_toggle"))
            self.location_check.setToolTip(t("settings.ha.location_tooltip"))
            toggle_form.addRow("", self.location_check)

        self.send_logged_in_check = ToggleSwitch(t("settings.ha.send_logged_in_toggle"))
        self.send_logged_in_check.setToolTip(t("settings.ha.send_logged_in_tooltip"))
        toggle_form.addRow("", self.send_logged_in_check)

        self.send_cpu_check = ToggleSwitch(t("settings.ha.send_cpu_toggle"))
        self.send_cpu_check.setToolTip(t("settings.ha.send_cpu_tooltip"))
        toggle_form.addRow("", self.send_cpu_check)

        self.send_ram_check = ToggleSwitch(t("settings.ha.send_ram_toggle"))
        self.send_ram_check.setToolTip(t("settings.ha.send_ram_tooltip"))
        toggle_form.addRow("", self.send_ram_check)

        vbox.addWidget(toggle_frame)

        return container

    def _build_appearance_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Language pill (top)
        # Compute label column width to match the settings pill below
        _fm = self.fontMetrics()
        _widest = max(
            t("settings.appearance.theme_label"),
            t("settings.appearance.border_label"),
            t("settings.appearance.button_style_label"),
            t("settings.appearance.tray_label"),
            t("settings.appearance.temp_label"),
            t("settings.appearance.pages_label"),
            key=lambda s: _fm.horizontalAdvance(s),
        )
        _label_col_w = _fm.horizontalAdvance(_widest) + 4

        lang_frame, lang_form = self._make_pill_panel()
        self._language_codes = list(supported_languages().keys())
        self.language_combo = QComboBox()
        self.language_combo.addItems(list(supported_languages().values()))
        self.language_combo.setMinimumWidth(120)
        _lang_lbl = QLabel(t("settings.appearance.language_label"))
        _lang_lbl.setMinimumWidth(_label_col_w)
        lang_form.addRow(_lang_lbl, self.language_combo)

        self._language_restart_note = QLabel(t("settings.appearance.language_restart_note"))
        self._language_restart_note.setStyleSheet("color: #aaa; font-size: 11px;")
        self._language_restart_note.hide()
        lang_form.addRow("", self._language_restart_note)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        vbox.addWidget(lang_frame)

        # Settings pill
        settings_frame, form = self._make_pill_panel()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems([
            t("settings.appearance.theme_system"),
            t("settings.appearance.theme_light"),
            t("settings.appearance.theme_dark"),
        ])
        self.theme_combo.setMinimumWidth(120)
        form.addRow(t("settings.appearance.theme_label"), self.theme_combo)

        from ui.widgets.effect_combobox import EffectComboBox
        self.border_effect_combo = EffectComboBox()
        self.border_effect_combo.addItems(["Rainbow", "Aurora Borealis", "Prism Shard", "Liquid Mercury", "None"])
        self.border_effect_combo.setMinimumWidth(120)
        self.border_effect_combo.currentTextChanged.connect(self.on_border_effect_changed)
        form.addRow(t("settings.appearance.border_label"), self.border_effect_combo)

        self.button_style_combo = QComboBox()
        self.button_style_combo.addItems([
            t("settings.appearance.button_style_gradient"),
            t("settings.appearance.button_style_flat"),
        ])
        self.button_style_combo.setMinimumWidth(120)
        form.addRow(t("settings.appearance.button_style_label"), self.button_style_combo)

        self.tray_position_combo = QComboBox()
        self.tray_position_combo.addItems([
            t("settings.appearance.tray_bottom"),
            t("settings.appearance.tray_top"),
        ])
        self.tray_position_combo.setMinimumWidth(120)
        form.addRow(t("settings.appearance.tray_label"), self.tray_position_combo)

        self.temperature_unit_combo = QComboBox()
        self.temperature_unit_combo.addItems([
            t("settings.appearance.temp_celsius"),
            t("settings.appearance.temp_fahrenheit"),
        ])
        self.temperature_unit_combo.setMinimumWidth(120)
        form.addRow(t("settings.appearance.temp_label"), self.temperature_unit_combo)

        self.pages_combo = QComboBox()
        self.pages_combo.addItems(["1", "2", "3", "4"])
        self.pages_combo.setMinimumWidth(120)
        form.addRow(t("settings.appearance.pages_label"), self.pages_combo)

        vbox.addWidget(settings_frame)

        # Toggles pill
        toggle_frame, toggle_form = self._make_pill_panel()

        toggle_label = self._make_section_header(t("settings.appearance.toggles_label"))
        toggle_frame.layout().insertWidget(0, toggle_label)

        self.show_dimming_check = ToggleSwitch(t("settings.appearance.dimming_toggle"))
        self.show_dimming_check.setToolTip(t("settings.appearance.dimming_tooltip"))
        toggle_form.addRow("", self.show_dimming_check)

        self.glass_ui_check = ToggleSwitch(t("settings.appearance.glass_toggle"))
        self.glass_ui_check.setToolTip(t("settings.appearance.glass_tooltip"))
        if sys.platform.startswith('linux'):
            self.glass_ui_check.setVisible(False)
        toggle_form.addRow("", self.glass_ui_check)

        self.pin_check = ToggleSwitch(t("settings.appearance.pin_toggle"))
        self.pin_check.setToolTip(t("settings.appearance.pin_tooltip"))
        self.pin_check.toggled.connect(self._on_pin_toggled)
        toggle_form.addRow("", self.pin_check)

        vbox.addWidget(toggle_frame)

        return container

    def _build_shortcut_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # App toggle shortcut pill
        shortcut_frame, form = self._make_pill_panel()

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

        btn_layout = QHBoxLayout(self.record_btn)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.record_icon = QWidget()
        self.record_icon.setObjectName("recordIcon")
        self.record_icon.setFixedSize(12, 12)
        self.record_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        btn_layout.addWidget(self.record_icon)

        shortcut_row.addWidget(self.shortcut_display)
        shortcut_row.addSpacing(8)
        shortcut_row.addWidget(self.record_btn)
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

        form.addRow(t("settings.shortcut.label"), shortcut_container)
        vbox.addWidget(shortcut_frame)

        # Button shortcuts pill
        btn_sc_frame, _ = self._make_pill_panel()
        btn_sc_vbox = btn_sc_frame.layout()

        sc_header = self._make_section_header(t("settings.shortcut.button_shortcuts"))
        btn_sc_vbox.insertWidget(0, sc_header)

        self._btn_shortcuts_container = QWidget()
        self._btn_shortcuts_layout = QVBoxLayout(self._btn_shortcuts_container)
        self._btn_shortcuts_layout.setContentsMargins(0, 0, 0, 0)
        self._btn_shortcuts_layout.setSpacing(4)
        btn_sc_vbox.addWidget(self._btn_shortcuts_container)

        self._refresh_button_shortcuts()
        vbox.addWidget(btn_sc_frame)

        return container

    def _build_support_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Update pill
        update_frame, form = self._make_pill_panel()

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

        update_row.addWidget(self.update_btn)
        update_row.addSpacing(10)
        update_row.addWidget(self.update_label)
        update_row.addStretch()

        form.addRow(t("settings.support.update_label"), update_row)
        vbox.addWidget(update_frame)

        # Links pill
        links_frame, _ = self._make_pill_panel()
        links_vbox = links_frame.layout()

        bug_pix = QPixmap(18, 18)
        bug_pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(bug_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(get_mdi_font(15))
        p.setPen(QColor("#E65100"))
        p.drawText(bug_pix.rect(), Qt.AlignmentFlag.AlignCenter, Icons.ALERT_CIRCLE_OUTLINE)
        p.end()

        bug_pix_hover = QPixmap(18, 18)
        bug_pix_hover.fill(Qt.GlobalColor.transparent)
        p = QPainter(bug_pix_hover)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(get_mdi_font(15))
        p.setPen(QColor("white"))
        p.drawText(bug_pix_hover.rect(), Qt.AlignmentFlag.AlignCenter, Icons.ALERT_CIRCLE_OUTLINE)
        p.end()

        self.bug_btn = QPushButton(f"  {t('settings.support.bug_btn')}")
        self.bug_btn.setObjectName("bugBtn")
        self.bug_btn.setIcon(QIcon(bug_pix))
        self.bug_btn.setIconSize(QSize(16, 16))
        self.bug_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bug_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/lasselian/prism-desktop/issues"))
        )
        self.bug_btn.installEventFilter(
            _IconHoverFilter(self.bug_btn, QIcon(bug_pix), QIcon(bug_pix_hover))
        )
        links_vbox.addWidget(self.bug_btn)

        coffee_pix = QPixmap(18, 18)
        coffee_pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(coffee_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(get_mdi_font(15))
        p.setPen(QColor("#FFBB33"))
        p.drawText(coffee_pix.rect(), Qt.AlignmentFlag.AlignCenter, Icons.COFFEE)
        p.end()

        coffee_pix_hover = QPixmap(18, 18)
        coffee_pix_hover.fill(Qt.GlobalColor.transparent)
        p = QPainter(coffee_pix_hover)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(get_mdi_font(15))
        p.setPen(QColor("white"))
        p.drawText(coffee_pix_hover.rect(), Qt.AlignmentFlag.AlignCenter, Icons.COFFEE)
        p.end()

        self.bmc_btn = QPushButton(f"  {t('settings.support.bmc_btn')}")
        self.bmc_btn.setObjectName("bmcBtn")
        self.bmc_btn.setIcon(QIcon(coffee_pix))
        self.bmc_btn.setIconSize(QSize(16, 16))
        self.bmc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bmc_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://buymeacoffee.com/lasselian"))
        )
        self.bmc_btn.installEventFilter(
            _IconHoverFilter(self.bmc_btn, QIcon(coffee_pix), QIcon(coffee_pix_hover))
        )
        links_vbox.addWidget(self.bmc_btn)

        vbox.addWidget(links_frame)

        return container

    # -------------------------------------------------------------------------
    # Panel switching
    # -------------------------------------------------------------------------

    def _switch_panel(self, idx: int):
        for i, panel in enumerate(self._panels):
            panel.setVisible(i == idx)
        self._active_panel_idx = idx
        if idx == 2:  # Shortcut panel — refresh list in case buttons changed
            self._refresh_button_shortcuts()
        # Defer one event-loop tick so Qt finishes measuring new/hidden widgets
        QTimer.singleShot(0, self.content_height_changed.emit)

    def _refresh_button_shortcuts(self):
        """Rebuild the button-shortcut list from current config."""
        layout = self._btn_shortcuts_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)  # immediate removal, no deferred cleanup

        buttons = self.config.get('buttons', [])
        assigned = [
            b for b in buttons
            if b.get('custom_shortcut', {}).get('enabled') and b.get('custom_shortcut', {}).get('value')
        ]

        if not assigned:
            hint = QLabel(t("settings.shortcut.no_button_shortcuts"))
            hint.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(hint)
            return

        is_light = self.theme_manager.get_effective_theme() == 'light' if self.theme_manager else False
        for btn_cfg in assigned:
            row = _ShortcutRow(btn_cfg, is_light=is_light, parent=self)
            row.edit_clicked.connect(self._on_edit_shortcut)
            row.delete_clicked.connect(self._on_delete_shortcut)
            layout.addWidget(row)

    def _on_edit_shortcut(self, btn_cfg: dict):
        self.open_button_editor_requested.emit(btn_cfg)

    def _on_delete_shortcut(self, btn_cfg: dict):
        self.request_delete_shortcut.emit(btn_cfg)

    def apply_shortcut_delete(self, btn_cfg: dict):
        """Called by dashboard after the confirm banner is accepted."""
        for btn in self.config.get('buttons', []):
            if (btn.get('row') == btn_cfg.get('row') and
                    btn.get('col') == btn_cfg.get('col') and
                    btn.get('page', 0) == btn_cfg.get('page', 0)):
                btn['custom_shortcut'] = {'enabled': False, 'value': ''}
                break

        self.shortcut_deleted.emit()

        target = self._find_row_widget(btn_cfg)
        if target:
            eff = QGraphicsOpacityEffect(target)
            target.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity")
            anim.setDuration(200)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            self._delete_anims.append(anim)
            def _on_done(a=anim):
                self._delete_anims.remove(a)
                self._refresh_button_shortcuts()
            anim.finished.connect(_on_done)
            anim.start()
        else:
            self._refresh_button_shortcuts()

    def _find_row_widget(self, btn_cfg: dict):
        layout = self._btn_shortcuts_layout
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w and hasattr(w, 'btn_cfg'):
                bc = w.btn_cfg
                if (bc.get('row') == btn_cfg.get('row') and
                        bc.get('col') == btn_cfg.get('col') and
                        bc.get('page', 0) == btn_cfg.get('page', 0)):
                    return w
        return None

    @staticmethod
    def _format_shortcut(value: str) -> str:
        """Convert a pynput shortcut string to a human-readable label."""
        if not value:
            return ""
        result = value
        for token, display in (
            ('<ctrl>', 'Ctrl'), ('<alt>', 'Alt'), ('<shift>', 'Shift'),
            ('<cmd>', 'Cmd'), ('<super>', 'Super'), ('<meta>', 'Meta'),
        ):
            result = result.replace(token, display)
        result = result.replace('<', '').replace('>', '')
        return result

    # -------------------------------------------------------------------------
    # Height query
    # -------------------------------------------------------------------------

    def _activate_all_layouts(self):
        if hasattr(self, '_content_widget') and self._content_widget.layout():
            self._content_widget.layout().activate()
        self.layout().activate()

    def get_content_height(self):
        """
        Calculate the exact height needed to show the active panel without scrolling.
        Activates nested layouts so measurements include newly-added widgets.
        """
        self._activate_all_layouts()
        return self.sizeHint().height()

    def get_max_content_height(self) -> int:
        """Return the height required by the tallest panel.
        Temporarily cycles through each panel to measure it, then restores the active one.
        """
        original_idx = self._active_panel_idx
        max_h = 0
        for i in range(len(self._panels)):
            for j, p in enumerate(self._panels):
                p.setVisible(j == i)
            self._activate_all_layouts()
            h = self.sizeHint().height()
            if h > max_h:
                max_h = h
        for j, p in enumerate(self._panels):
            p.setVisible(j == original_idx)
        self._activate_all_layouts()
        return max_h

    # -------------------------------------------------------------------------
    # Config load / save
    # -------------------------------------------------------------------------

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
        self.pin_check.setChecked(app.get('pin_window', False))

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

        mobile_cfg = self.config.get('mobile_app', {})
        self.send_logged_in_check.setChecked(mobile_cfg.get('send_logged_in', True))
        self.send_cpu_check.setChecked(mobile_cfg.get('send_cpu', False))
        self.send_ram_check.setChecked(mobile_cfg.get('send_ram', False))

        self.border_effect_combo.blockSignals(False)

        sc = self.config.get('shortcut', {})
        self.shortcut_display.setText(sc.get('value', ''))
        self._update_shortcut_controls()

    def save_settings(self):
        """Save and emit config."""
        self._cleanup_threads()

        if 'home_assistant' not in self.config: self.config['home_assistant'] = {}
        self.config['home_assistant']['url'] = self.url_input.text().strip()
        self.config['home_assistant']['token'] = self.token_input.text().strip()

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
            'pin_window': self.pin_check.isChecked(),
            'pages': self.pages_combo.currentIndex() + 1,
            'language': new_language,
        })

        if sys.platform in ('win32', 'linux'):
            new_location_enabled = self.location_check.isChecked()
            self.config.setdefault('mobile_app', {})['location_enabled'] = new_location_enabled

            if sys.platform == 'linux' and new_location_enabled:
                self._check_geoclue2_and_setup()

        self.config.setdefault('mobile_app', {})['send_logged_in'] = self.send_logged_in_check.isChecked()
        self.config.setdefault('mobile_app', {})['send_cpu'] = self.send_cpu_check.isChecked()
        self.config.setdefault('mobile_app', {})['send_ram'] = self.send_ram_check.isChecked()

        if 'shortcut' not in self.config: self.config['shortcut'] = {}

        self.settings_saved.emit(self.config)

        if new_language != old_language:
            from ui.notifications import notify_language_restart
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(300, lambda: notify_language_restart(self.window()))

    # -------------------------------------------------------------------------
    # Linux location helpers
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # Logic / event handlers
    # -------------------------------------------------------------------------

    def _on_language_changed(self, index: int):
        selected_lang = self._language_codes[index]
        init_localization(selected_lang)
        self._language_restart_note.setText(t("settings.appearance.language_restart_note"))
        self._language_restart_note.show()

    def on_border_effect_changed(self, text):
        self.border_effect_combo.set_effect(text)

    def _on_pin_toggled(self, checked: bool):
        pass  # save_settings() reads pin_check.isChecked() directly

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
            self.record_icon.setStyleSheet("background-color: white; border-radius: 2px;")
            self.shortcut_display.setText(t("settings.shortcut.recording"))
            self.input_manager.start_recording()
        else:
            self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
            self.input_manager.restore_shortcut()
            sc = self.config.get('shortcut', {})
            if self.shortcut_display.text() == t("settings.shortcut.recording"):
                self.shortcut_display.setText(sc.get('value', ''))

    @pyqtSlot(dict)
    def on_shortcut_recorded(self, shortcut):
        if not self.record_btn.isChecked():
            return

        self.record_btn.setChecked(False)
        self.record_icon.setStyleSheet("background-color: white; border-radius: 6px;")
        self.shortcut_display.setText(shortcut.get('value', ''))
        if 'shortcut' not in self.config: self.config['shortcut'] = {}
        self.config['shortcut'] = shortcut

        self.input_manager.update_shortcut(shortcut)

    def _should_delegate_shortcuts_to_kde(self) -> bool:
        return sys.platform == 'linux' and is_kde_wayland_session()

    def _is_unsupported_wayland_shortcut_env(self) -> bool:
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

    def _on_credential_changed(self):
        self._debounce_timer.stop()
        if not self.url_input.text().strip() or not self.token_input.text().strip():
            self._set_conn_status("idle")
        else:
            self._debounce_timer.start()

    def _trigger_auto_test(self):
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        if not url or not token:
            self._set_conn_status("idle")
            return
        if self._test_thread and self._test_thread.isRunning():
            return
        self._set_conn_status("checking")
        self._test_thread = ConnectionTestThread(url, token)
        self._test_thread.finished.connect(self.on_test_complete)
        self._test_thread.start()

    def _set_conn_status(self, state: str, detail: str = ""):
        if state == "idle":
            self._conn_status_label.hide()
            return
        color = {"checking": "#8e8e93", "ok": "#34A853", "error": "#e53935"}.get(state, "#8e8e93")
        if state == "ok":
            text = "●  Connected"
        elif state == "error":
            text = f"●  {detail}" if detail else "●  Connection failed"
        else:
            text = "●  Checking…"
        self._conn_status_label.setStyleSheet(f"font-size: 11px; color: {color};")
        self._conn_status_label.setText(text)
        self._conn_status_label.show()

    @pyqtSlot(bool, str)
    def on_test_complete(self, success: bool, message: str):
        try:
            self._set_conn_status("ok" if success else "error", message)
            if not success and self.dashboard:
                from ui.notifications import notify_connection_test_result
                notify_connection_test_result(self.dashboard, success, message)
        except RuntimeError:
            pass  # slot fired after widget was destroyed (save-then-close race)

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
        if href == "open_releases":
            QDesktopServices.openUrl(QUrl("https://github.com/lasselian/prism-desktop/releases"))
            return
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
        link_style = "color: #e53935; font-size: 11px;"
        releases_url = "https://github.com/lasselian/prism-desktop/releases"
        label_html = (
            f'<span style="{link_style}">'
            f'{t("settings.support.update_failed")} — '
            f'<a href="open_releases" style="color: #e57373;">'
            f'{t("settings.support.visit_releases")}</a>'
            f'</span>'
        )
        self.update_label.setText(label_html)
        self.update_label.setStyleSheet("")
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
        self._debounce_timer.stop()
        self._poll_timer.stop()
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
