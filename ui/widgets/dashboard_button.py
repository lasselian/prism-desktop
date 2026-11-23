from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QApplication, QGraphicsDropShadowEffect, QMenu, QGraphicsEffect
from PyQt6.QtCore import (
    Qt,
    QPoint,
    pyqtSignal,
    QPropertyAnimation,
    QEasingCurve,
    QMimeData,
    QByteArray,
    QDataStream,
    QIODevice,
    pyqtProperty,
    QRectF,
    QTimer,
    QRect,
)
from PyQt6.QtGui import QColor, QFont, QDrag, QPixmap, QPainter, QCursor, QPainterPath
from ui.icons import get_icon, get_mdi_font, Icons, get_icon_for_type
from core.utils import SYSTEM_FONT
from core.localization_manager import t
from core.temperature_utils import format_temperature, is_temperature_entity
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.widgets.dashboard_button_styles import DashboardButtonStyleManager
import html
import math
import re
import sys
import time

# Custom MIME type for drag and drop
MIME_TYPE = "application/x-hatray-slot"

_MANTISSA_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_EXPONENT_RE = re.compile(r"[eE][+-]?\d+")
# A unit may not start with a character that would make the number just the
# front of something else ("12:34", "2024-05-01", "1.2.3").
_NOT_A_UNIT_START = frozenset("0123456789:.-/+")


def _split_number_and_unit(text: str):
    """(number, unit) text of a 'NUMBER [UNIT]' string, or None if it isn't one.

    An exponent belongs to the number unless that leaves an invalid unit, in
    which case it is read as the start of the unit ("6e1 +%" is 6 "e1 +%").
    """
    mantissa = _MANTISSA_RE.match(text)
    if not mantissa:
        return None
    exponent = _EXPONENT_RE.match(text, mantissa.end())
    cuts = (exponent.end(), mantissa.end()) if exponent else (mantissa.end(),)
    for cut in cuts:
        unit = text[cut:]
        rest = unit.lstrip()
        if not rest or rest[0] not in _NOT_A_UNIT_START:
            return text[:cut], unit
    return None


# Tile types that show the raw HA state, with the state to assume when it is missing
_RAW_STATE_DEFAULTS = {
    '3d_printer': 'unknown',  # the painter pulls the rest from the dashboard's _entity_states
    'lawn_mower': 'unknown',
    'vacuum': 'unknown',
    'alarm_control_panel': 'disarmed',
    'automation': 'off',
}

# Tile type -> (signal that opens its overlay, whether the signal also carries the config)
_OVERLAY_REQUESTS = {
    'climate': ('climate_requested', False),
    'weather': ('weather_requested', False),
    'camera': ('camera_requested', True),
    '3d_printer': ('printer_requested', True),
    'lawn_mower': ('mower_requested', False),
    'vacuum': ('vacuum_requested', False),
    'alarm_control_panel': ('alarm_requested', False),
}


def pip_camera_entity(cfg: dict | None) -> str:
    """The camera a tile config can pop out into picture-in-picture, or ''."""
    cfg = cfg or {}
    if cfg.get('type') == 'camera':
        entity_id = cfg.get('entity_id') or ''
    elif cfg.get('type') == '3d_printer':
        entity_id = cfg.get('printer_camera_entity') or ''
    else:
        return ''
    return entity_id if entity_id.startswith('camera.') else ''


def _media_thirds_action(x: float, width: float) -> str:
    """Left third: previous, right third: next, middle: play/pause."""
    if x < width / 3:
        return 'media_previous_track'
    if x > (width / 3) * 2:
        return 'media_next_track'
    return 'media_play_pause'


def _parse_sensor_value(raw, precision=1):
    """Split a sensor state string into (number, unit, formatted).

    Returns (None, '', str(raw)) for non-numeric values.
    """
    if raw is None:
        return None, '', '--'
    text = str(raw)
    parts = _split_number_and_unit(text)
    if parts is None:
        return None, '', text
    try:
        num = float(parts[0])
    except (ValueError, TypeError):
        return None, '', text
    unit = parts[1]
    if precision == 0:
        formatted_num = f"{num:.0f}"
    else:
        formatted_num = f"{num:.{precision}f}"
    return num, unit, f"{formatted_num}{unit}"


def _compute_fraction(num, vmin, vmax):
    """Return clamped (num - vmin)/(vmax - vmin) in [0, 1], or None if undefined."""
    if num is None:
        return None
    try:
        span = float(vmax) - float(vmin)
    except (TypeError, ValueError):
        return None
    if span <= 0:
        return None
    return max(0.0, min(1.0, (float(num) - float(vmin)) / span))


class ButtonMorphEffect(QGraphicsEffect):
    """Single compositing effect that handles both drag-fade opacity and spring-resize scale.

    Qt only allows one QGraphicsEffect per widget, so this combines both concerns.
    Disabled by default; auto-enabled when either opacity < 1 or scale != 1.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity = 1.0
        self._sx = 1.0
        self._sy = 1.0

    # --- pyqtProperty accessors so QPropertyAnimation can drive them ---

    def get_sx(self):
        return self._sx

    def set_sx(self, v):
        self._sx = v
        self.update()

    sx = pyqtProperty(float, get_sx, set_sx)

    def get_sy(self):
        return self._sy

    def set_sy(self, v):
        self._sy = v
        self.update()

    sy = pyqtProperty(float, get_sy, set_sy)

    def get_opacity(self):
        return self._opacity

    def set_opacity_prop(self, v):
        self._opacity = v
        self.update()

    opacity_prop = pyqtProperty(float, get_opacity, set_opacity_prop)

    def _refresh_enabled(self):
        needs = self._opacity < 0.999 or abs(self._sx - 1.0) > 0.003 or abs(self._sy - 1.0) > 0.003
        self.setEnabled(needs)

    def boundingRectFor(self, source_rect):
        # Small extra margin so a brief spring overshoot isn't clipped.
        m = max(source_rect.width(), source_rect.height()) * 0.08 + 4
        return source_rect.adjusted(-m, -m, m, m)

    def draw(self, painter):
        px, offset = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        if px.isNull():
            return
        w = float(px.width())
        h = float(px.height())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._opacity)
        sx, sy = self._sx, self._sy
        if abs(sx - 1.0) < 0.003 and abs(sy - 1.0) < 0.003:
            painter.drawPixmap(offset, px)
        else:
            # Anchor to top-left so the button grows toward the resize handle (bottom-right)
            painter.drawPixmap(
                QRectF(float(offset.x()), float(offset.y()), w * sx, h * sy),
                px,
                QRectF(0.0, 0.0, w, h),
            )


class DashboardButton(QFrame):
    """Button or widget in the grid."""

    clicked = pyqtSignal(dict)
    dropped = pyqtSignal(int, int)  # target_slot, source_slot
    edit_requested = pyqtSignal(int)
    duplicate_requested = pyqtSignal(int)
    clear_requested = pyqtSignal(int)
    move_to_page_requested = pyqtSignal(int, int)  # slot, target_page
    dimmer_requested = pyqtSignal(int, QRect)  # slot, geometry
    climate_requested = pyqtSignal(int, QRect)  # slot, geometry
    weather_requested = pyqtSignal(int, QRect)  # slot, geometry
    printer_requested = pyqtSignal(int, QRect, dict)  # slot, geometry, config
    camera_requested = pyqtSignal(int, QRect, dict)  # slot, geometry, config
    pip_requested = pyqtSignal(str, str)  # camera entity_id, title
    volume_requested = pyqtSignal(int, QRect)  # slot, geometry (for volume overlay)
    mower_requested = pyqtSignal(int, QRect)  # slot, geometry (for mower overlay)
    vacuum_requested = pyqtSignal(int, QRect)  # slot, geometry (for vacuum overlay)
    alarm_requested = pyqtSignal(int, QRect)  # slot, geometry (for alarm control panel overlay)
    volume_scroll = pyqtSignal(str, float)  # entity_id, new_volume (for scroll wheel)
    media_command_requested = pyqtSignal(dict)
    resize_requested = pyqtSignal(int, int, int)  # slot, span_x, span_y
    resize_finished = pyqtSignal()

    def __init__(self, slot: int, config: dict | None = None, theme_manager=None, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.config = config or {}
        # Load span from config or default to 1
        self.span_x = self.config.get('span_x', 1)
        self.span_y = self.config.get('span_y', 1)

        self._page_count = 1
        self._current_page = 0

        self.theme_manager = theme_manager
        self._show_border_effect = False
        self._show_dimming = False
        self.refresh_indicators_enabled = False
        self._brightness = 255
        self.temperature_unit_preference = "celsius"

        # Internal state
        self._state = "off"
        self._value = None
        self._ha_icon = None  # Icon from Home Assistant state
        self._media_state = {}  # Full media player state

        # Sun entity state
        self.sun_state = 'unknown'
        self.sun_next_rising = None
        self.sun_next_setting = None
        self.sun_elevation = 0.0
        self.sun_rising = False
        self.sun_pulse_phase = 0.0
        self._album_art = None  # QPixmap for album art
        self._drag_start_pos = None
        self._is_resizing = False
        self._resize_start_span = (1, 1)

        # input_number interaction state
        self._input_changing = False
        self._input_scrub_mode = False
        self._input_drag_start_pos = None
        self._input_start_val = 0.0
        self._hovering = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Camera resize optimization
        self._last_camera_pixmap = None
        self._cached_display_pixmap = None

        # Resize handle animation
        self._resize_handle_opacity = 0.0
        self.resize_anim = QPropertyAnimation(self, b"resize_handle_opacity")
        self.resize_anim.setDuration(200)  # Fast fade
        self.resize_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Input number blink animation
        self._input_blink_opacity = 0.0
        self.input_blink_anim = QPropertyAnimation(self, b"input_blink_opacity")
        self.input_blink_anim.setDuration(200)
        self.input_blink_anim.setKeyValueAt(0, 0.0)
        self.input_blink_anim.setKeyValueAt(0.5, 0.3)
        self.input_blink_anim.setKeyValueAt(1, 0.0)

        # Input number arrow hover animation
        self._arrow_opacity = 0.0
        self.arrow_anim = QPropertyAnimation(self, b"arrow_opacity")
        self.arrow_anim.setDuration(200)
        self.arrow_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Click feedback animation
        self._content_opacity = 0.0
        self._anim_progress = 0.0
        self.anim = QPropertyAnimation(self, b"anim_progress")
        self.anim.setDuration(1500)  # Slower, more elegant
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Script pulse animation
        self._pulse_opacity = 0.0
        self.pulse_anim = QPropertyAnimation(self, b"pulse_opacity")
        self.pulse_anim.setDuration(2000)
        self.pulse_anim.setKeyValueAt(0, 0.0)
        self.pulse_anim.setKeyValueAt(0.5, 0.8)
        self.pulse_anim.setKeyValueAt(1, 0.0)

        # Per-tile refresh indicator (opt-in, Settings > Appearance > Options):
        # a quick fade marking a live push from Home Assistant landed on this tile.
        self._refresh_flash_opacity = 0.0  # current dot opacity (0 = invisible, 1 = fully lit); animated below
        self.refresh_flash_anim = QPropertyAnimation(self, b"refresh_flash_opacity")
        self.refresh_flash_anim.setDuration(
            500
        )  # <-- HOW LONG THE FLASH LASTS, in milliseconds. Raise this to make it linger longer (e.g. 10000 = 10s for screenshots), lower it for a quicker blink.
        self.refresh_flash_anim.setStartValue(1.0)  # opacity at the moment of the flash: 1.0 = fully visible
        self.refresh_flash_anim.setEndValue(0.0)  # opacity once the fade finishes: 0.0 = fully faded out
        self.refresh_flash_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )  # shape of the fade over time: OutCubic = fast at first, then eases out gently (not a constant/linear fade)

        # Perimeter progress animation
        self._perimeter_fraction = 0.0
        self.perimeter_anim = QPropertyAnimation(self, b"perimeter_fraction")
        self.perimeter_anim.setDuration(800)
        self.perimeter_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Gauge entry animation
        self._gauge_fraction = 0.0
        self.gauge_anim = QPropertyAnimation(self, b"gauge_fraction")
        self.gauge_anim.setDuration(800)
        self.gauge_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Sun entity entry animation
        self._sun_entry_fraction = 1.0
        self.sun_entry_anim = QPropertyAnimation(self, b"sun_entry_fraction")
        self.sun_entry_anim.setDuration(800)
        self.sun_entry_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Bounce animation on click
        self._bounce_offset = 0.0
        self.bounce_anim = QPropertyAnimation(self, b"bounce_offset")

        # Long press timer
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(300)  # 300ms hold
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._ignore_release = False

        # Lock hold-to-unlock: unlocking requires a deliberate hold, locking stays instant.
        self._lock_holding = False
        self._lock_hold_progress = 0.0
        self.lock_hold_anim = QPropertyAnimation(self, b"lock_hold_progress")
        self.lock_hold_anim.setDuration(550)
        self.lock_hold_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._lock_hold_timer = QTimer(self)
        self._lock_hold_timer.setSingleShot(True)
        self._lock_hold_timer.setInterval(550)
        self._lock_hold_timer.timeout.connect(self._on_lock_hold_complete)

        self._border_effect = 'Rainbow'

        # Animated background state (prismatic light field)
        self._anim_bg_timer = QTimer(self)
        self._anim_bg_timer.setInterval(33)  # ~30fps
        self._anim_bg_timer.timeout.connect(self._tick_animated_bg)
        self._anim_bg_frame = 0
        self._anim_bg_layers = None
        self._anim_bg_cache_key = None
        self._anim_bg_anchors = None
        self._anim_bg_tiny = None  # Cached tiny QPixmap (reused every frame)

        self._style_fp = None
        self._content_fp = None

        self.setup_ui()
        self.update_style()

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Disable dropping (handled by parent Dashboard)
        self.setAcceptDrops(False)
        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Single compositing effect — handles both drag-fade and spring-resize
        self._morph_eff = ButtonMorphEffect(self)
        self._morph_eff.setEnabled(False)
        self.setGraphicsEffect(self._morph_eff)

        # Spring morph animations (must come after _morph_eff is created)
        _morph_easing = QEasingCurve(QEasingCurve.Type.OutBack)
        _morph_easing.setOvershoot(0.8)
        self.morph_x_anim = QPropertyAnimation(self._morph_eff, b"sx")
        self.morph_x_anim.setDuration(260)
        self.morph_x_anim.setEasingCurve(_morph_easing)
        self.morph_y_anim = QPropertyAnimation(self._morph_eff, b"sy")
        self.morph_y_anim.setDuration(260)
        self.morph_y_anim.setEasingCurve(_morph_easing)
        self.morph_x_anim.finished.connect(self._on_morph_finished)
        self.morph_y_anim.finished.connect(self._on_morph_finished)

    def set_faded(self, opacity: float):
        """Set drag-fade level (0.0–1.0). Auto-enables the morph effect when needed."""
        if not hasattr(self, '_morph_eff'):
            self._morph_eff = ButtonMorphEffect(self)
            self._morph_eff.setEnabled(False)
            self.setGraphicsEffect(self._morph_eff)
        # Go through the property setter (not the raw field) so the effect
        # schedules a repaint on every opacity change — otherwise mid-fade
        # frames only render where something else invalidates the region,
        # making the fade look stepped/janky.
        self._morph_eff.set_opacity_prop(max(0.0, min(1.0, opacity)))
        self._morph_eff._refresh_enabled()

    def set_opacity(self, opacity: float):
        """Standard public method for setting overall button opacity."""
        self.set_faded(opacity)

    def get_anim_progress(self):
        return self._anim_progress

    def set_anim_progress(self, val):
        self._anim_progress = val
        self.update()  # Trigger repaint

    anim_progress = pyqtProperty(float, get_anim_progress, set_anim_progress)

    def get_pulse_opacity(self):
        return self._pulse_opacity

    def set_pulse_opacity(self, val):
        self._pulse_opacity = val
        self.update()

    pulse_opacity = pyqtProperty(float, get_pulse_opacity, set_pulse_opacity)

    def get_refresh_flash_opacity(self):
        return self._refresh_flash_opacity

    def set_refresh_flash_opacity(self, val):
        self._refresh_flash_opacity = val
        self.update()

    refresh_flash_opacity = pyqtProperty(float, get_refresh_flash_opacity, set_refresh_flash_opacity)

    def flash_refresh_indicator(self):
        """Briefly light the corner dot marking a live Home Assistant push.

        Opt-in (Settings > Appearance > Options). No-ops while a fade is
        already in flight, so a fast-updating entity reads as a steady soft
        pulse rather than a permanently-lit dot.
        """
        if not self.refresh_indicators_enabled:
            return
        if self.refresh_flash_anim.state() == QPropertyAnimation.State.Running:
            return
        self.refresh_flash_anim.start()

    def get_resize_handle_opacity(self):
        return self._resize_handle_opacity

    def set_resize_handle_opacity(self, val):
        self._resize_handle_opacity = val
        self.update()

    resize_handle_opacity = pyqtProperty(float, get_resize_handle_opacity, set_resize_handle_opacity)

    def get_input_blink_opacity(self):
        return self._input_blink_opacity

    def set_input_blink_opacity(self, val):
        self._input_blink_opacity = val
        self.update()

    input_blink_opacity = pyqtProperty(float, get_input_blink_opacity, set_input_blink_opacity)

    def get_arrow_opacity(self):
        return self._arrow_opacity

    def set_arrow_opacity(self, val):
        self._arrow_opacity = val
        self.update()

    arrow_opacity = pyqtProperty(float, get_arrow_opacity, set_arrow_opacity)

    def get_perimeter_fraction(self):
        return self._perimeter_fraction

    def set_perimeter_fraction(self, val):
        self._perimeter_fraction = val
        self.update()

    perimeter_fraction = pyqtProperty(float, get_perimeter_fraction, set_perimeter_fraction)

    def get_lock_hold_progress(self):
        return self._lock_hold_progress

    def set_lock_hold_progress(self, val):
        self._lock_hold_progress = val
        self.update()

    lock_hold_progress = pyqtProperty(float, get_lock_hold_progress, set_lock_hold_progress)

    def get_gauge_fraction(self):
        return self._gauge_fraction

    def set_gauge_fraction(self, val):
        self._gauge_fraction = val
        self.update()

    gauge_fraction = pyqtProperty(float, get_gauge_fraction, set_gauge_fraction)

    def get_sun_entry_fraction(self):
        return self._sun_entry_fraction

    def set_sun_entry_fraction(self, val):
        self._sun_entry_fraction = val
        self.update()

    sun_entry_fraction = pyqtProperty(float, get_sun_entry_fraction, set_sun_entry_fraction)

    def get_bounce_offset(self):
        return self._bounce_offset

    def set_bounce_offset(self, val):
        self._bounce_offset = val
        m = 8
        left = getattr(self, '_content_left_margin', m)
        self.layout().setContentsMargins(left, m + int(val), m, m - int(val))

    bounce_offset = pyqtProperty(float, get_bounce_offset, set_bounce_offset)

    def get_show_dimming(self):
        return self._show_dimming

    def set_show_dimming(self, val):
        if self._show_dimming != val:
            self._show_dimming = val
            self.update_style()

    show_dimming = pyqtProperty(bool, get_show_dimming, set_show_dimming)

    def _start_size_morph(self, old_w: int, old_h: int, w: int, h: int):
        """Spring from the size the button visually has right now to its new size.

        The start scale accounts for a morph that is still running, so chained
        resizes stay smooth.
        """
        start_sx = (self._morph_eff._sx * old_w) / w if w > 0 else 1.0
        start_sy = (self._morph_eff._sy * old_h) / h if h > 0 else 1.0
        self.morph_x_anim.stop()
        self.morph_y_anim.stop()
        self._morph_eff._sx = start_sx
        self._morph_eff._sy = start_sy
        self._morph_eff.setEnabled(True)
        for anim, start, axis in ((self.morph_x_anim, start_sx, '_sx'), (self.morph_y_anim, start_sy, '_sy')):
            if abs(start - 1.0) > 0.01:
                anim.setStartValue(start)
                anim.setEndValue(1.0)
                anim.start()
            else:
                setattr(self._morph_eff, axis, 1.0)

    def set_spans(self, x, y, animate=False):
        """Update spans and resize widget."""
        old_w = self.width()
        old_h = self.height()
        self.span_x = x
        self.span_y = y
        # Calculate new size based on grid units (90x80) + spacing (8)
        w = 90 * x + (8 * (x - 1))
        h = 80 * y + (8 * (y - 1))
        self.setFixedSize(w, h)

        _will_animate = animate and (old_w != w or old_h != h)

        if _will_animate:
            self._start_size_morph(old_w, old_h, w, h)

        # Re-apply camera image to fit new size immediately
        if self._last_camera_pixmap and not self._last_camera_pixmap.isNull():
            self.set_camera_image(self._last_camera_pixmap)

        # Force content update to adapt layout (1x1 -> 2x1 etc)
        self.update_content()

        if _will_animate:
            # Qt already repositioned the labels inside the new larger widget, so
            # sourcePixmap() during the morph shows text in its final position but
            # scaled down — causing a mid-transition glitch.  Hide labels now so
            # the morph only animates the background; restore them in _on_morph_finished.
            self.value_label.hide()
            self.name_label.hide()
            self._relabel_after_morph = True

        self.update()

    def _on_morph_finished(self):
        """Disable the morph effect once both animations have settled to 1.0."""
        if (
            self.morph_x_anim.state() != QPropertyAnimation.State.Running
            and self.morph_y_anim.state() != QPropertyAnimation.State.Running
        ):
            self._morph_eff._sx = 1.0
            self._morph_eff._sy = 1.0
            self._morph_eff._refresh_enabled()
            if getattr(self, '_relabel_after_morph', False):
                self._relabel_after_morph = False
                self._content_fp = None  # bypass cache so labels are restored
                self.update_content()

    def trigger_feedback(self):
        """Start the feedback animation."""
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def setup_ui(self):
        """Setup the button UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Value label (for widgets) or icon area
        self.value_label = QLabel()
        self.value_label.setObjectName("valueLabel")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setTextFormat(Qt.TextFormat.PlainText)  # Security: Prevent HTML injection
        font = QFont(SYSTEM_FONT, 16, QFont.Weight.Bold)
        self.value_label.setFont(font)
        self.value_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Name label
        self.name_label = QLabel()
        self.name_label.setObjectName("nameLabel")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)  # Security: Prevent HTML injection
        self.name_label.setWordWrap(True)  # Enable text wrapping for long labels
        self.name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        name_font = QFont(SYSTEM_FONT, 9)
        self.name_label.setFont(name_font)

        # Add drop shadows for readability on colored backgrounds
        self._shadow_val = QGraphicsDropShadowEffect()
        self._shadow_val.setBlurRadius(4)
        self._shadow_val.setColor(QColor(0, 0, 0, 140))
        self._shadow_val.setOffset(0, 1)
        self.value_label.setGraphicsEffect(self._shadow_val)

        self._shadow_name = QGraphicsDropShadowEffect()
        self._shadow_name.setBlurRadius(4)
        self._shadow_name.setColor(QColor(0, 0, 0, 140))
        self._shadow_name.setOffset(0, 1)
        self.name_label.setGraphicsEffect(self._shadow_name)

        layout.addStretch(1)
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        layout.addStretch(1)

        self.setFixedSize(90 * self.span_x + (8 * (self.span_x - 1)), 80 * self.span_y + (8 * (self.span_y - 1)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.update_content()

    def play_entry_animation(self):
        """Sweep gauge/perimeter/sun from zero to their current value."""
        style = self.config.get('display_style', '')
        btn_type = self.config.get('type', '')
        if self.config.get('entry_animation', True):
            if style == 'gauge' and self._sensor_fraction is not None:
                self.gauge_anim.stop()
                self._gauge_fraction = 0.0
                self.gauge_anim.setStartValue(0.0)
                self.gauge_anim.setEndValue(self._sensor_fraction)
                self.gauge_anim.start()
            elif style == 'perimeter' and self._sensor_fraction is not None:
                self.perimeter_anim.stop()
                self._perimeter_fraction = 0.0
                self.perimeter_anim.setStartValue(0.0)
                self.perimeter_anim.setEndValue(self._sensor_fraction)
                self.perimeter_anim.start()
        if btn_type == 'sun' and self.config.get('entry_animation', True):
            self.sun_entry_anim.stop()
            self._sun_entry_fraction = 0.0
            self.sun_entry_anim.setStartValue(0.0)
            self.sun_entry_anim.setEndValue(1.0)
            self.sun_entry_anim.start()

    def update_content(self):
        """Update button content from config."""
        fp = self._content_fingerprint()
        if fp == self._content_fp:
            return
        self._content_fp = fp
        self._reset_layout_overrides()

        btn_type = self.config.get('type', 'switch') if self.config else None
        if btn_type is None:
            self._update_empty_view()
            return
        if btn_type == 'forbidden':
            self._update_forbidden_view()
            return

        updater = self._VIEW_UPDATERS.get(btn_type)
        if updater is None:
            self._update_default_view(btn_type)
        else:
            updater(self)
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_anim_bg_timer()

    def _content_fingerprint(self) -> tuple:
        """Everything the content depends on; an unchanged fingerprint skips the rebuild."""
        cfg = self.config
        keys = (
            'type',
            'entity_id',
            'label',
            'display_style',
            'icon',
            'animated_bg',
            'precision',
            'sensor_min',
            'sensor_max',
        )
        return (
            *(cfg.get(key) for key in keys),
            self._state,
            str(self._value),
            self._ha_icon,
            self.span_x,
            self.span_y,
            self.temperature_unit_preference,
        )

    def _reset_layout_overrides(self):
        """Undo the bar/gauge layout tweaks; each view updater sets its own again.

        Runs after the fingerprint check so that a no-op call doesn't tear down a
        layout that won't be re-applied.
        """
        if getattr(self, '_bar_layout_active', False):
            self._bar_layout_active = False
            self.layout().setStretch(0, 1)
            self.layout().setStretch(3, 1)
        if getattr(self, '_content_left_margin', 8) != 8:
            self._content_left_margin = 8
            self.layout().setContentsMargins(8, 8, 8, 8)

    def set_temperature_unit_preference(self, preference: str):
        if self.temperature_unit_preference != preference:
            self.temperature_unit_preference = preference
            self.update_content()

    # --- Animated background helpers ---

    def _update_anim_bg_timer(self):
        """Start or stop the animated background timer based on current state."""
        should_run = (
            self.config.get('type') == 'media_player' and self.config.get('animated_bg', True)
        ) or self.config.get('type') == 'sun'
        if should_run and not self._anim_bg_timer.isActive():
            self._anim_bg_timer.start()
        elif not should_run and self._anim_bg_timer.isActive():
            self._anim_bg_timer.stop()

    def _tick_animated_bg(self):
        """Advance animated background by one frame."""
        self._anim_bg_frame += 1
        if self.config.get('type') == 'sun':
            self.sun_pulse_phase = (self.sun_pulse_phase + 1.0 / 90.0) % 1.0
        self.update()

    def _ensure_anim_bg_layers(self, seed: int):
        """Regenerate animated background layers if cache is stale."""
        light_mode = bool(self.theme_manager and self.theme_manager.get_effective_theme() == 'light')
        cache_key = (seed, self.width(), self.height(), light_mode)
        if self._anim_bg_cache_key == cache_key and self._anim_bg_layers is not None:
            return
        from ui.visuals.background_generator import BackgroundGenerator

        self._anim_bg_layers = BackgroundGenerator.generate_layers(
            self.width(), self.height(), seed=seed, light_mode=light_mode
        )
        self._anim_bg_anchors = self._anim_bg_layers["anchors"]
        # Cache a tiny pixmap for reuse each frame (avoids allocation per frame)
        scale = 0.15
        tw = max(20, int(self.width() * scale))
        th = max(16, int(self.height() * scale))
        self._anim_bg_tiny = QPixmap(tw, th)
        self._anim_bg_cache_key = cache_key
        self._anim_bg_frame = 0

    def showEvent(self, event):
        super().showEvent(event)
        self._update_anim_bg_timer()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim_bg_timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._anim_bg_cache_key = None  # force regen on next paint

        # Gauge 2x1+ reserves a left margin sized from the current width/height.
        # Recompute on resize so the right-hand QLabels don't overlap the arc.
        if self.config.get('type') == 'widget' and self.config.get('display_style') == 'gauge' and self.span_x >= 2:
            arc_size = max(min(self.height() - 16, self.width() // 2 - 10), 32)
            new_left = arc_size + 16
            if new_left != getattr(self, '_content_left_margin', 8):
                self._content_left_margin = new_left
                m = self.layout().contentsMargins()
                self.layout().setContentsMargins(new_left, m.top(), m.right(), m.bottom())

    # --- View Helpers ---

    def _update_empty_view(self):
        """Show add button."""
        self.value_label.setFont(get_mdi_font(24))
        self.value_label.setText(Icons.PLUS)
        self.name_label.setText(t("dashboard_button.add"))
        self.value_label.show()
        self.name_label.show()
        self._cached_display_pixmap = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def _update_forbidden_view(self):
        """Show forbidden icon."""
        self.value_label.setFont(get_mdi_font(22))
        self.value_label.setText(get_icon("block-helper"))
        self.name_label.hide()
        self.value_label.show()
        self._cached_display_pixmap = None
        self.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.update()

    def _update_weather_view(self):
        """Update weather widget content."""
        label = self.config.get('label', '')
        state_obj = self._value if isinstance(self._value, dict) else {}
        attrs = state_obj.get('attributes', {})
        emoji = self._get_weather_emoji(state_obj.get('state', 'unknown'))
        temp_str = format_temperature(
            attrs.get('temperature', '--'),
            attrs.get('temperature_unit'),
            self.temperature_unit_preference,
            precision=1,
        )

        if self.span_x >= 2 and self.span_y >= 2:
            text, text_format, font = _weather_huge_text(emoji, temp_str, attrs)
        elif self.span_y >= 2:
            text, text_format, font = _weather_tall_text(emoji, temp_str)
        elif self.span_x >= 2:
            text, text_format, font = _weather_wide_text(emoji, temp_str)
        else:
            # Small buttons: just the temperature
            text, text_format, font = temp_str, Qt.TextFormat.PlainText, QFont(SYSTEM_FONT, 22, QFont.Weight.Bold)
        self.value_label.setTextFormat(text_format)
        self.value_label.setText(text)
        self.value_label.setFont(font)

        if label:
            self.name_label.setText(label)
            self.name_label.show()
        else:
            self.name_label.hide()
        self.setProperty("type", "weather")
        self.value_label.show()

    def _update_widget_view(self):
        """Update generic sensor widget."""
        label = self.config.get('label', '')
        style = self.config.get('display_style', 'normal')
        num, _unit, formatted = _parse_sensor_value(self._value, self.config.get('precision', 1))
        vmin = self.config.get('sensor_min', 0.0)
        vmax = self.config.get('sensor_max', 100.0)
        self._sensor_fraction = _compute_fraction(num, vmin, vmax)
        if num is not None:
            self._sensor_text = formatted
        else:
            self._sensor_text = str(self._value) if self._value is not None else '--'
        self._animate_sensor_fraction(style)

        self.value_label.setText(self._sensor_text)
        self.name_label.setText(label)
        self.setProperty("type", "widget")
        top_stretch, bottom_stretch = self._layout_sensor_labels(style, label)

        # Layout: [stretch, value_label, name_label, stretch]. Only touch a stretch
        # that changes, to avoid needless invalidations.
        if self.layout().stretch(0) != top_stretch:
            self.layout().setStretch(0, top_stretch)
        if self.layout().stretch(3) != bottom_stretch:
            self.layout().setStretch(3, bottom_stretch)
        self.layout().setContentsMargins(self._content_left_margin, 8, 8, 8)
        self.update()

    def _animate_sensor_fraction(self, style: str):
        if self._sensor_fraction is None:
            return
        if style == 'perimeter':
            self.perimeter_anim.stop()
            self.perimeter_anim.setStartValue(self._perimeter_fraction)
            self.perimeter_anim.setEndValue(self._sensor_fraction)
            self.perimeter_anim.start()
        elif style == 'gauge' and self.gauge_anim.state() != self.gauge_anim.State.Running:
            self._gauge_fraction = self._sensor_fraction

    def _layout_sensor_labels(self, style: str, label: str) -> tuple:
        """Show or hide the labels for a display style; return the (top, bottom) stretches."""
        if style == 'bar':
            # Painter draws the pill in the upper area; the name sits at the very bottom.
            self.value_label.hide()
            self.name_label.setVisible(bool(label))
            self._bar_layout_active = True
            self._content_left_margin = 8
            return 1, 0
        if style == 'gauge' and self.span_x >= 2:
            # Arc on the left (a square region is reserved for it); labels flow into the right half.
            self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
            self.value_label.show()
            self.name_label.setVisible(bool(label))
            arc_size = max(min(self.height() - 16, self.width() // 2 - 10), 32)
            self._content_left_margin = arc_size + 16
            return 1, 1

        self._bar_layout_active = False
        self._content_left_margin = 8
        if style == 'gauge':
            # 1x1 and tall one-column gauges: the painter draws arc, value and label.
            self.value_label.hide()
            self.name_label.hide()
        else:
            # Normal text mode
            self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
            self.value_label.show()
            self.name_label.show()
        return 1, 1

    def _update_input_number_view(self):
        """Update view for input_number entities."""
        label = self.config.get('label', '')
        self.value_label.setFont(QFont(SYSTEM_FONT, 18, QFont.Weight.Bold))

        state_obj = self._value if isinstance(self._value, dict) else {}
        val = state_obj.get('state', '--')
        attrs = state_obj.get('attributes', {})
        unit = attrs.get('unit_of_measurement', '')

        try:
            f_val = float(val)
            step = float(attrs.get('step', 1.0))
            precision = 0 if step.is_integer() else len(str(step).split('.')[-1])
            if is_temperature_entity(attrs):
                display_val = format_temperature(
                    f_val,
                    unit,
                    self.temperature_unit_preference,
                    precision=precision,
                )
            else:
                formatted_num = f"{f_val:.{precision}f}"
                display_val = f"{formatted_num}{unit}"
        except (ValueError, TypeError):
            display_val = f"{val}{unit}"

        self.value_label.setText(display_val)
        self.name_label.setText(label)
        self.setProperty("type", "input_number")
        self.value_label.show()
        self.name_label.show()

    def _update_climate_view(self):
        label = self.config.get('label', '')
        self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        self.value_label.setText(self._value or "--")
        self.name_label.setText(label)
        self.setProperty("type", "climate")
        self.value_label.show()
        self.name_label.show()

    def _update_curtain_view(self):
        label = self.config.get('label', '')
        self.value_label.setFont(get_mdi_font(26))

        icon_name = self.config.get('icon') or self._ha_icon
        icon_char = get_icon(icon_name) if icon_name else None

        if icon_char:
            icon = icon_char
        else:
            icon = Icons.BLINDS_OPEN if self._state == "open" else Icons.BLINDS

        self.value_label.setText(icon)
        self.name_label.setText(label)
        self.setProperty("type", "curtain")
        self.value_label.show()
        self.name_label.show()

    def _update_script_view(self):
        self._update_simple_icon_view(Icons.SCRIPT, "script")

    def _update_automation_view(self):
        self._update_simple_icon_view(Icons.AUTOMATION, "automation")

    def _update_scene_view(self):
        self._update_simple_icon_view(Icons.SCENE_THEME, "scene")

    def _update_fan_view(self):
        label = self.config.get('label', '')
        self.value_label.setFont(get_mdi_font(26))
        self.value_label.setText(Icons.FAN)
        self.name_label.setText(label)
        self.setProperty("type", "fan")
        self.value_label.show()
        self.name_label.show()

    def _update_simple_icon_view(self, default_icon, type_name):
        """Helper for simple icon+label buttons."""
        label = self.config.get('label', '')
        self.value_label.setFont(get_mdi_font(26))

        icon_name = self.config.get('icon') or self._ha_icon
        icon_char = get_icon(icon_name) if icon_name else None

        self.value_label.setText(icon_char if icon_char else default_icon)
        self.name_label.setText(label)
        self.setProperty("type", type_name)
        self.value_label.show()
        self.name_label.show()

    def _update_media_player_view(self):
        self.value_label.hide()
        self.name_label.hide()
        self.setProperty("type", "media_player")
        self.update()

    def _update_camera_view(self):
        self.value_label.hide()
        self.name_label.hide()
        self.setProperty("type", "camera")

        if not self._cached_display_pixmap or self._cached_display_pixmap.isNull():
            self.value_label.show()
            self.value_label.setFont(get_mdi_font(26))
            self.value_label.setText(Icons.VIDEO)

    def _update_3d_printer_view(self):
        self.value_label.hide()
        self.name_label.hide()
        self.setProperty("type", "3d_printer")
        self.update()
        if not self._cached_display_pixmap or self._cached_display_pixmap.isNull():
            self.value_label.show()
            self.value_label.setFont(get_mdi_font(26))
            self.value_label.setText(Icons.VIDEO)

    def _update_lawn_mower_view(self):
        """Update lawn mower widget — sensor-style with state text + label."""
        label = self.config.get('label', '')
        self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        state_str = self._state or 'unknown'
        self.value_label.setText(state_str.replace('_', ' ').capitalize())
        self.name_label.setText(label)
        self.setProperty("type", "lawn_mower")
        self.value_label.show()
        self.name_label.show()

    def _update_vacuum_view(self):
        """Update vacuum widget — sensor-style with state text + label."""
        label = self.config.get('label', '')
        self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        state_str = self._state or 'unknown'
        self.value_label.setText(state_str.replace('_', ' ').capitalize())
        self.name_label.setText(label)
        self.setProperty("type", "vacuum")
        self.value_label.show()
        self.name_label.show()

    def _update_alarm_view(self):
        """Alarm control panel — shield icon reflecting arm state, same layout as lock/curtain."""
        label = self.config.get('label', '')
        self.value_label.setFont(get_mdi_font(26))

        icon_name = self.config.get('icon') or self._ha_icon
        icon_char = get_icon(icon_name) if icon_name else None
        icon = icon_char if icon_char else get_icon_for_type('alarm_control_panel', self._state or 'disarmed')

        self.value_label.setText(icon)
        self.name_label.setText(label)
        self.setProperty("type", "alarm_control_panel")
        self.value_label.show()
        self.name_label.show()

    def _update_sun_view(self):
        """Sun entity: all rendering is done by the painter."""
        self._state = 'on'  # always active so QSS renders at full opacity
        self.value_label.hide()
        self.name_label.hide()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setProperty("type", "sun")
        self.update()

    def _update_pin_window_view(self):
        """Pin Window button — icon and state reflect current app pin setting."""
        is_pinned = getattr(self, '_app_config', {}).get('appearance', {}).get('pin_window', False)
        self._state = 'on' if is_pinned else 'off'
        self.value_label.setFont(get_mdi_font(26))
        self.value_label.setText(Icons.PIN if is_pinned else get_icon('pin-off'))
        self.name_label.hide()
        self.setProperty("type", "pin_window")
        self.value_label.show()

    def _update_default_view(self, btn_type):
        """Default view for switch, light, lock, etc."""
        label = self.config.get('label', '')
        self.value_label.setFont(get_mdi_font(26))

        icon_name = self.config.get('icon') or self._ha_icon
        icon_char = get_icon(icon_name) if icon_name else None

        if icon_char:
            icon = icon_char
        else:
            icon = get_icon_for_type(btn_type, self._state)

        self.value_label.setText(icon)
        self.name_label.setText(label)
        self.setProperty("type", btn_type)
        self.value_label.show()
        self.name_label.show()

    _VIEW_UPDATERS = {
        'weather': _update_weather_view,
        'widget': _update_widget_view,
        'climate': _update_climate_view,
        'curtain': _update_curtain_view,
        'script': _update_script_view,
        'automation': _update_automation_view,
        'scene': _update_scene_view,
        'fan': _update_fan_view,
        'media_player': _update_media_player_view,
        'camera': _update_camera_view,
        '3d_printer': _update_3d_printer_view,
        'lawn_mower': _update_lawn_mower_view,
        'vacuum': _update_vacuum_view,
        'alarm_control_panel': _update_alarm_view,
        'input_number': _update_input_number_view,
        'sun': _update_sun_view,
        'pin_window': _update_pin_window_view,
    }

    def set_state(self, state: str):
        """Set the state (on/off) for switches."""
        self._state = state
        self.update_content()
        self.update_style()

    def set_value(self, value):
        """Update button value (sensor reading, etc)."""
        if self._value != value:
            self._value = value
            self.update_content()

    def set_ha_icon(self, icon_name: str):
        """Update the icon from Home Assistant state."""
        if self._ha_icon != icon_name:
            self._ha_icon = icon_name
            # Only update content if we are NOT using a custom icon
            if not self.config.get('icon'):
                self.update_content()

    def set_media_state(self, state: dict):
        """Set the full media player state."""
        self._media_state = state
        self._state = state.get('state', 'idle')

        # Update tooltip
        attrs = state.get('attributes', {})
        title = attrs.get('media_title', '')
        artist = attrs.get('media_artist', '')
        if title and artist:
            self.setToolTip(t("dashboard_button.media.now_playing", info=f"{artist} \u2014 {title}"))
        elif title:
            self.setToolTip(t("dashboard_button.media.now_playing", info=title))
        else:
            self.setToolTip('')

        self.update_content()
        self.update_style()
        self.update()  # Trigger repaint for custom painting

    def set_album_art(self, pixmap):
        """Set the album art pixmap."""
        self._album_art = pixmap
        self.update()  # Trigger repaint

    def reset_state(self):
        """Reset internal state to default."""
        self._state = "off"
        self._value = None
        self._brightness = 255
        self._ha_icon = None
        self._media_state = {}
        self._album_art = None
        self._last_camera_pixmap = None
        self._cached_display_pixmap = None

        # Stop animations and reset counters
        self.anim.stop()
        self._anim_progress = 0.0

        self.input_blink_anim.stop()
        self._input_blink_opacity = 0.0

        self.arrow_anim.stop()
        self._arrow_opacity = 0.0

        self.pulse_anim.stop()
        self._pulse_opacity = 0.0

        self.refresh_flash_anim.stop()
        self._refresh_flash_opacity = 0.0

        self.resize_anim.stop()
        self._resize_handle_opacity = 0.0

        self.bounce_anim.stop()
        self.set_bounce_offset(0.0)

        self.update_content()
        self.update_style()

    def apply_ha_state(self, state: dict):
        """Apply Home Assistant state to the button."""
        if not state:
            return
        attributes = state.get('attributes', {})
        btn_type = self.config.get('type', 'switch')
        icon = attributes.get('icon')
        if icon:
            self.set_ha_icon(icon)

        if btn_type in _RAW_STATE_DEFAULTS:
            # The raw HA state drives both the view text and the "on" styling.
            self.set_state(state.get('state', _RAW_STATE_DEFAULTS[btn_type]))
            return
        applier = self._STATE_APPLIERS.get(btn_type)
        if applier is None:
            self._apply_toggle_state(state, attributes)  # switch/light/fan/script/scene/lock
        else:
            applier(self, state, attributes)

    def _apply_widget_state(self, state: dict, attributes: dict):
        value = state.get('state', '--')
        unit = attributes.get('unit_of_measurement', '')
        if is_temperature_entity(attributes):
            self.set_value(
                format_temperature(
                    value, unit, self.temperature_unit_preference, precision=self.config.get('precision', 1)
                )
            )
            return
        try:
            float(value)
            numeric = True
        except (TypeError, ValueError):
            numeric = False
        # Only numbers carry the unit; "unavailable"/"on"/"12:34" are shown as-is
        self.set_value(f"{value}{unit}" if numeric else str(value))

    def _apply_climate_state(self, state: dict, attributes: dict):
        # The target temperature, and on/off styling from the HVAC state
        self.set_value(
            format_temperature(
                attributes.get('temperature', '--'),
                attributes.get('temperature_unit'),
                self.temperature_unit_preference,
                precision=1,
            )
        )
        self.set_state('on' if state.get('state', 'off') not in ['off', 'unavailable'] else 'off')

    def _apply_curtain_state(self, state: dict, attributes: dict):
        # "open" when the cover is up/open; opening, closing and closed all count as closed
        self.set_state('open' if state.get('state', 'closed') == 'open' else 'closed')

    def _apply_weather_state(self, state: dict, attributes: dict):
        self.set_weather_state(state)  # needs the full object for the forecast attributes

    def _apply_input_number_state(self, state: dict, attributes: dict):
        self._value = state
        self.update_content()

    def _apply_media_state(self, state: dict, attributes: dict):
        self.set_media_state(state)

    def _apply_sun_state(self, state: dict, attributes: dict):
        self.sun_state = state.get('state', 'unknown')
        self.sun_next_rising = attributes.get('next_rising')
        self.sun_next_setting = attributes.get('next_setting')
        self.sun_elevation = float(attributes.get('elevation', 0.0))
        self.sun_rising = bool(attributes.get('rising', False))
        self.update()

    def _apply_toggle_state(self, state: dict, attributes: dict):
        self.set_state(state.get('state', 'off'))
        # Brightness drives the optional dimming effect
        self._brightness = attributes.get('brightness', 255)
        if self._show_dimming:
            self.update_style()

    _STATE_APPLIERS = {
        'widget': _apply_widget_state,
        'climate': _apply_climate_state,
        'curtain': _apply_curtain_state,
        'weather': _apply_weather_state,
        'input_number': _apply_input_number_state,
        'media_player': _apply_media_state,
        'sun': _apply_sun_state,
    }

    def set_weather_state(self, state_obj: dict):
        """Set full weather state object."""
        self._value = state_obj
        self.update_content()
        self.update_style()

    def _get_weather_emoji(self, state: str) -> str:
        """Map HA weather state to emoji (or MDI icon on Linux)."""
        is_linux = sys.platform.startswith('linux')

        if is_linux:
            # Map to MDI icons
            mapping = {
                'clear-night': Icons.WEATHER_NIGHT,
                'cloudy': Icons.WEATHER_CLOUDY,
                'fog': Icons.WEATHER_FOG,
                'hail': Icons.WEATHER_HAIL,
                'lightning': Icons.WEATHER_LIGHTNING,
                'lightning-rainy': Icons.WEATHER_LIGHTNING_RAINY,
                'partlycloudy': Icons.WEATHER_PARTLY_CLOUDY,
                'pouring': Icons.WEATHER_POURING,
                'rainy': Icons.WEATHER_RAINY,
                'snowy': Icons.WEATHER_SNOWY,
                'snowy-rainy': Icons.WEATHER_SNOWY_RAINY,
                'sunny': Icons.WEATHER_SUNNY,
                'windy': Icons.WEATHER_WINDY,
                'windy-variant': Icons.WEATHER_WINDY_VARIANT,
                'exceptional': Icons.ALERT_CIRCLE,
            }
            return mapping.get(state, Icons.WEATHER_CLOUDY)  # Default to cloudy/unknown
        else:
            # Simple mapping
            mapping = {
                'clear-night': '🌙',
                'cloudy': '☁️',
                'fog': '🌫️',
                'hail': '🌨️',
                'lightning': '🌩️',
                'lightning-rainy': '⛈️',
                'partlycloudy': '⛅',
                'pouring': '🌧️',
                'rainy': '🌧️',
                'snowy': '❄️',
                'snowy-rainy': '🌨️',
                'sunny': '☀️',
                'windy': '💨',
                'windy-variant': '🌬️',
                'exceptional': '⚠️',
            }
            return mapping.get(state, 'Unknown')

    def set_camera_image(self, pixmap):
        """Set camera image from QPixmap."""
        self._last_camera_pixmap = pixmap

        if not pixmap or pixmap.isNull():
            return

        # Scale and crop to fill button with rounded corners
        btn_size = self.size()
        if btn_size.isEmpty():
            return

        # 1. Cache Path (Avoid QPainterPath recreation)
        if not hasattr(self, '_cached_path') or getattr(self, '_cached_path_size', None) != btn_size:
            self._cached_path = QPainterPath()
            self._cached_path.addRoundedRect(QRectF(0, 0, btn_size.width(), btn_size.height()), 12, 12)
            self._cached_path_size = btn_size
            # Invalidate pixmap cache if size changed
            self._cached_display_pixmap = None

        # 2. Reuse Pixmap (Avoid QPixmap recreation)
        if self._cached_display_pixmap and self._cached_display_pixmap.size() == btn_size:
            rounded = self._cached_display_pixmap
        else:
            rounded = QPixmap(btn_size)
            self._cached_display_pixmap = rounded

        rounded.fill(Qt.GlobalColor.transparent)

        # 3. Direct Draw (Avoid scaled/cropped intermediate Pixmaps)
        # Calculate aspect-ratio-preserving crop (Cover mode)
        img_w = pixmap.width()
        img_h = pixmap.height()
        btn_w = btn_size.width()
        btn_h = btn_size.height()

        # Scale to strictly cover the button
        scale = max(btn_w / img_w, btn_h / img_h)

        # Calculate the source rectangle (portion of image to use)
        src_w = btn_w / scale
        src_h = btn_h / scale
        src_x = (img_w - src_w) / 2
        src_y = (img_h - src_h) / 2

        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(self._cached_path)
        # Draw directly from source to target, letting QPainter handle scaling/cropping
        painter.drawPixmap(QRectF(0, 0, btn_w, btn_h), pixmap, QRectF(src_x, src_y, src_w, src_h))
        painter.end()

        self.value_label.hide()
        self.name_label.hide()
        self.update()

    def update_style(self):
        """Update visual style based on state and theme."""
        effective_theme = self.theme_manager.get_effective_theme() if self.theme_manager else 'dark'
        fp = (
            self.config.get('type'),
            self.config.get('color'),
            bool(self.config.get('entity_id')),
            self._state,
            self._brightness,
            self._show_dimming,
            getattr(self, 'button_style', 'Gradient'),
            effective_theme,
        )
        if fp == self._style_fp:
            return
        self._style_fp = fp
        DashboardButtonStyleManager.apply_style(self)
        self._update_label_shadows()

    def invalidate_style_cache(self):
        self._style_fp = None
        self._content_fp = None

    def _update_label_shadows(self):
        """Adjust drop shadow intensity for light vs dark theme."""
        is_light = self.theme_manager and self.theme_manager.get_effective_theme() == 'light'
        if is_light:
            # Light mode: very subtle shadow to avoid blurry text
            shadow_color = QColor(0, 0, 0, 30)
            blur = 2
        else:
            # Dark mode: stronger shadow for readability on colored backgrounds
            shadow_color = QColor(0, 0, 0, 140)
            blur = 4

        if hasattr(self, '_shadow_val'):
            self._shadow_val.setColor(shadow_color)
            self._shadow_val.setBlurRadius(blur)
        if hasattr(self, '_shadow_name'):
            self._shadow_name.setColor(shadow_color)
            self._shadow_name.setBlurRadius(blur)

    def paintEvent(self, event):
        """Custom paint event for effects."""
        # First draw normal style (background)
        super().paintEvent(event)
        # Delegate painting logic to helper
        DashboardButtonPainter.paint(self, event)

    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.update()

    def _on_long_press(self):
        """Handle long press: Start dimmer, climate, or volume if applicable."""
        if not self.config:
            return

        btn_type = self.config.get('type', 'switch')

        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()

        # Get absolute coordinates
        global_pos = self.mapToGlobal(QPoint(0, 0))
        rect = QRect(global_pos, self.size())

        if btn_type == 'switch':
            # Lights show dimmer overlay
            entity_id = self.config.get('entity_id', '')
            if entity_id.startswith('light.'):
                self._ignore_release = True
                self.dimmer_requested.emit(self.slot, rect)
        elif btn_type == 'curtain':
            # Long press on curtain -> Position slider (uses same dimmer overlay)
            self._ignore_release = True
            self.dimmer_requested.emit(self.slot, rect)
        elif btn_type == 'climate':
            # Long press on climate -> Climate overlay
            self._ignore_release = True
            self.climate_requested.emit(self.slot, rect)
        elif btn_type == 'weather':
            self._ignore_release = True
            self.weather_requested.emit(self.slot, rect)
        elif btn_type == 'media_player':
            # Long press on media player -> Volume overlay
            self._ignore_release = True
            self.volume_requested.emit(self.slot, rect)
        elif btn_type == 'lawn_mower':
            self._ignore_release = True
            self.mower_requested.emit(self.slot, rect)
        elif btn_type == 'vacuum':
            self._ignore_release = True
            self.vacuum_requested.emit(self.slot, rect)
        elif btn_type == 'alarm_control_panel':
            self._ignore_release = True
            self.alarm_requested.emit(self.slot, rect)
        elif btn_type == 'input_number':
            # Long press on input_number -> Enable value scrub mode
            self._input_scrub_mode = True

    def _lock_resolve_action(self):
        """What a tap on this lock button would do right now: 'lock', 'unlock', or None."""
        if not self.config or self.config.get('type') != 'lock':
            return None
        configured = self.config.get('action')
        if configured in ('lock', 'unlock'):
            return configured  # tile is set to always lock / always unlock
        return 'unlock' if self._state == 'locked' else 'lock'

    def _cancel_lock_hold(self):
        """Release happened before the unlock hold completed — snap the ring back, do nothing."""
        self._lock_holding = False
        self._lock_hold_timer.stop()
        self.lock_hold_anim.stop()
        self.lock_hold_anim.setStartValue(self._lock_hold_progress)
        self.lock_hold_anim.setDuration(180)
        self.lock_hold_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.lock_hold_anim.setEndValue(0.0)
        self.lock_hold_anim.start()

    def _on_lock_hold_complete(self):
        """Held long enough — commit the unlock."""
        if not self._lock_holding or not self.config:
            return
        self._lock_holding = False
        self.lock_hold_anim.stop()
        self.lock_hold_anim.setDuration(180)
        self.lock_hold_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.lock_hold_anim.setStartValue(self._lock_hold_progress)
        self.lock_hold_anim.setEndValue(0.0)
        self.lock_hold_anim.start()
        self.trigger_feedback()
        self.clicked.emit({**self.config, 'action': 'unlock'})

    def mousePressEvent(self, event):
        """Track click start."""
        # Forbidden buttons are completely non-interactive
        if self.config and self.config.get('type') == 'forbidden':
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._start_press(event):
            return  # an input_number arrow took the press
        super().mousePressEvent(event)

    def _start_press(self, event) -> bool:
        """Begin a left press; True when an input_number arrow handled it completely."""
        # Global position, so resizing survives the widget moving during reflow
        self._drag_start_pos = event.globalPosition().toPoint()
        if self._in_resize_corner(event.pos()) and self._resize_handle_opacity > 0.0:
            self._is_resizing = True
            self._resize_start_span = (self.span_x, self.span_y)
            return False  # no long press while resizing

        self._is_resizing = False
        self._ignore_release = False
        if self.config and self.config.get('type') == 'input_number' and self._press_input_number_arrow(event.pos()):
            self._ignore_release = True
            return True
        if self.config and self._lock_resolve_action() == 'unlock':
            self._start_unlock_hold()
        else:
            self._long_press_timer.start()
        if self.config:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(40)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self.bounce_anim.setEndValue(1.0)
            self.bounce_anim.start()
        return False

    def _in_resize_corner(self, pos) -> bool:
        """Whether pos is on the resize handle: a 28px square in the bottom-right corner."""
        rect = self.rect()
        return pos.x() >= rect.width() - 28 and pos.y() >= rect.height() - 28

    def _press_input_number_arrow(self, pos) -> bool:
        """Remember the value at press time, and step it if pos is on an arrow; True if it was."""
        state_obj = self._value if isinstance(self._value, dict) else {}
        try:
            self._input_start_val = float(state_obj.get('state', 0.0))
            step = self._input_arrow_step(pos)
            if step:
                self._step_input_number(step)
                return True
        except (ValueError, TypeError):
            pass
        return False

    def _input_arrow_step(self, pos) -> int:
        """+1/-1 on an arrow zone, else 0.

        One row tall and wider than one column: left/right quarters step down/up.
        Two rows or taller: top/bottom quarters step up/down. A 1x1 tile only drags.
        """
        if self.span_y == 1:
            if self.span_x == 1:
                return 0
            coord, size, low_end_step = pos.x(), self.width(), -1
        else:
            coord, size, low_end_step = pos.y(), self.height(), 1
        if coord < size * 0.25:
            return low_end_step
        if coord > size * 0.75:
            return -low_end_step
        return 0

    def _start_unlock_hold(self):
        # Unlocking needs a deliberate hold — locking (the safe direction) stays instant.
        self._ignore_release = True
        self._lock_holding = True
        self.lock_hold_anim.stop()
        self.lock_hold_anim.setStartValue(self._lock_hold_progress)
        self.lock_hold_anim.setDuration(550)
        self.lock_hold_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self.lock_hold_anim.setEndValue(1.0)
        self.lock_hold_anim.start()
        self._lock_hold_timer.start()

    def _fade_resize_handle(self, target: float) -> None:
        """Fade the resize handle toward *target* opacity unless it is already heading there."""
        current = self.resize_anim.endValue()  # None until the first fade
        if current is not None and math.isclose(current, target):
            return
        self.resize_anim.stop()
        self.resize_anim.setEndValue(target)
        self.resize_anim.start()

    def mouseMoveEvent(self, event):
        """Handle drag start and hover effects."""
        if not self._drag_start_pos:
            self._update_resize_hover(event.pos())
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self.config:
            return  # no button held, or an "Add" button, which is never dragged
        global_pos = event.globalPosition().toPoint()
        if self._scrubbing_input_number():
            self._scrub_input_number(global_pos)
        elif self._is_resizing:
            self._drag_resize(global_pos)
        elif (global_pos - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
            self._start_tile_drag(event.pos())

    def _update_resize_hover(self, pos):
        """Fade the resize handle in while hovering its corner (configured tiles only)."""
        is_configured = self.config and (self.config.get('entity_id') or self.config.get('type') == 'pin_window')
        if is_configured and self._in_resize_corner(pos):
            self._fade_resize_handle(1.0)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self._fade_resize_handle(0.0)
            self.unsetCursor()  # back to the parent's cursor rather than forcing one

    def _scrubbing_input_number(self) -> bool:
        """An input_number in scrub mode (switched on by a long press)."""
        return (
            self.config.get('type') == 'input_number'
            and getattr(self, '_input_scrub_mode', False)
            and not self._is_resizing
            and not self._ignore_release
        )

    def _scrub_input_number(self, global_pos):
        dy = self._drag_start_pos.y() - global_pos.y()  # up is positive
        dx = global_pos.x() - self._drag_start_pos.x()  # right is positive
        if not self._input_changing and max(abs(dx), abs(dy)) > QApplication.startDragDistance():
            self._input_changing = True
            self._long_press_timer.stop()
            self._snap_bounce_back()
        if self._input_changing:
            # Whichever direction moved more wins.
            self._set_scrubbed_value(dy if abs(dy) > abs(dx) else dx)

    def _set_scrubbed_value(self, delta: float):
        """One step per 15 px dragged from the value at press time, kept within the entity's range."""
        state_obj = self._value if isinstance(self._value, dict) else {}
        attrs = state_obj.get('attributes', {})
        try:
            step = float(attrs.get('step', 1.0))
            min_val = float(attrs.get('min', 0.0))
            max_val = float(attrs.get('max', 100.0))
            new_val = max(min_val, min(max_val, self._input_start_val + int(delta / 15.0) * step))
            if abs(new_val - float(state_obj.get('state', 0.0))) > 0.0001:
                self._value = {**state_obj, 'state': str(new_val)}
                self.update_content()
                self.input_blink_anim.stop()
                self.input_blink_anim.start()
        except (ValueError, TypeError):
            pass

    def _snap_bounce_back(self):
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.set_bounce_offset(0.0)

    def _drag_resize(self, global_pos):
        """Resize in whole cells (about 90px each, gap included) as the handle is dragged."""
        diff = global_pos - self._drag_start_pos
        max_x, max_y = (2, 1) if self.config.get('type', '') == 'sun' else (4, 2)
        new_span_x = max(1, min(max_x, self._resize_start_span[0] + round(diff.x() / 90.0)))
        new_span_y = max(1, min(max_y, self._resize_start_span[1] + round(diff.y() / 90.0)))
        if (new_span_x, new_span_y) != (self.span_x, self.span_y):
            self.resize_requested.emit(self.slot, new_span_x, new_span_y)

    def _start_tile_drag(self, hotspot):
        """Drag this tile to another slot; the original fades to half while it moves."""
        self._long_press_timer.stop()
        if self._lock_holding:
            self._cancel_lock_hold()
        self._snap_bounce_back()

        drag = QDrag(self)
        data = QByteArray()
        QDataStream(data, QIODevice.OpenModeFlag.WriteOnly).writeInt32(self.slot)
        mime_data = QMimeData()
        mime_data.setData(MIME_TYPE, data)
        drag.setMimeData(mime_data)
        drag.setPixmap(self._drag_ghost())
        drag.setHotSpot(hotspot)

        fade_timer = self._fade_out_while_dragging()
        drag.exec(Qt.DropAction.MoveAction)
        fade_timer.stop()
        self.set_faded(1.0)
        self.repaint()

    def _drag_ghost(self) -> QPixmap:
        """The tile's content clipped to its rounded rect."""
        ghost = QPixmap(self.size())
        ghost.fill(Qt.GlobalColor.transparent)
        painter = QPainter(ghost)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(clip_path)
        self.render(painter)
        painter.setClipping(False)
        painter.end()
        return ghost

    def _fade_out_while_dragging(self) -> QTimer:
        """Fade this tile to 50% over 400 ms; the caller stops the returned timer."""
        start = time.monotonic()
        timer = QTimer(self)

        def step():
            progress = min(1.0, (time.monotonic() - start) / 0.4)
            self.set_faded(1.0 - 0.5 * progress)
            self.repaint()
            if progress >= 1.0:
                timer.stop()

        timer.timeout.connect(step)
        timer.start(16)
        return timer

    def mouseReleaseEvent(self, event):
        """Handle click."""
        self._long_press_timer.stop()
        self._input_scrub_mode = False
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()

        if self._ignore_release:
            self._finish_consumed_press()
            return
        if self.config and self.config.get('type') == 'input_number' and self._input_changing:
            self._commit_input_number_scrub()
            return
        if self._is_resizing:
            self._is_resizing = False
            self.resize_finished.emit()
            return

        if self._drag_start_pos and event.button() == Qt.MouseButton.LeftButton:
            self._handle_click(event.pos())
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _finish_consumed_press(self):
        """A long press or an arrow click already handled this press."""
        self._ignore_release = False
        self._drag_start_pos = None
        self._input_changing = False
        if self._lock_holding:
            # Released before the unlock hold completed — cancel, don't unlock.
            self._cancel_lock_hold()

    def _commit_input_number_scrub(self):
        self._input_changing = False
        self._drag_start_pos = None
        state_obj = self._value if isinstance(self._value, dict) else {}
        try:
            final_val = float(state_obj.get('state', 0.0))
            self.clicked.emit({**self.config, 'action': 'set_input_number', 'value': final_val})
        except (ValueError, TypeError):
            pass

    def _handle_click(self, pos):
        if self.config:
            self.trigger_feedback()  # Show feedback BEFORE emit
        btn_type = self.config.get('type') if self.config else None
        if btn_type == 'sun':
            return  # informational only: feedback, no service call
        if btn_type == 'media_player':
            self.clicked.emit({**self.config, 'action': self._media_action_at(pos)})
            return
        if btn_type in ('script', 'scene'):
            self.pulse_anim.stop()
            self.pulse_anim.start()

        overlay = _OVERLAY_REQUESTS.get(btn_type)
        if overlay:
            signal_name, with_config = overlay
            rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
            args = (self.slot, rect, self.config) if with_config else (self.slot, rect)
            getattr(self, signal_name).emit(*args)
        elif btn_type == 'lock':
            self.clicked.emit({**self.config, 'action': self._lock_resolve_action()})
        else:
            self.clicked.emit(self.config)

    def _media_action_at(self, pos) -> str:
        """The media command for a click; the zones depend on the tile's shape."""
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        if self.span_x >= 2 and self.span_y >= 2:
            # 2x2+: previous/play/next across the middle band (centre +/- 30px)
            return _media_thirds_action(x, w) if abs(y - h // 2) <= 30 else 'media_play_pause'
        if self.span_y >= 2:
            # 1x2: top half plays/pauses, bottom half is previous | next
            if y < h / 2:
                return 'media_play_pause'
            return 'media_previous_track' if x < w / 2 else 'media_next_track'
        if self.span_x >= 2:
            return _media_thirds_action(x, w)  # 2x1
        return 'media_play_pause'  # 1x1

    def _step_input_number(self, direction: int):
        """Step the input number up (1) or down (-1) and trigger API call."""
        state_obj = self._value if isinstance(self._value, dict) else {}
        attrs = state_obj.get('attributes', {})
        try:
            current_val = float(state_obj.get('state', 0.0))
            step = float(attrs.get('step', 1.0))
            min_val = float(attrs.get('min', 0.0))
            max_val = float(attrs.get('max', 100.0))

            new_val = current_val + (direction * step)
            new_val = max(min_val, min(max_val, new_val))

            if abs(new_val - current_val) > 0.0001:
                # Update local state
                new_state_obj = dict(state_obj)
                new_state_obj['state'] = str(new_val)
                self._value = new_state_obj
                self.update_content()

                # Trigger blink
                self.input_blink_anim.stop()
                self.input_blink_anim.start()

                # Emit API call
                self.clicked.emit({**self.config, 'action': 'set_input_number', 'value': new_val})
        except (ValueError, TypeError):
            pass

    def wheelEvent(self, event):
        """Handle scroll wheel for media player volume."""
        if self.config and self.config.get('type') == 'media_player':
            entity_id = self.config.get('entity_id', '')
            if entity_id:
                attrs = self._media_state.get('attributes', {})
                current_vol = attrs.get('volume_level', 0.5)
                # Each step ~ 5% volume
                delta = event.angleDelta().y()
                step = 0.05 if delta > 0 else -0.05
                new_vol = max(0.0, min(1.0, current_vol + step))
                if new_vol != current_vol:
                    self.volume_scroll.emit(entity_id, new_vol)
            event.accept()
            return
        super().wheelEvent(event)

    def leaveEvent(self, event):
        """Reset handle when mouse leaves."""
        self._hovering = False
        if self.config and self.config.get('type') == 'input_number':
            if self.arrow_anim.state() == QPropertyAnimation.State.Running or self._arrow_opacity > 0.0:
                self.arrow_anim.stop()
                self.arrow_anim.setEndValue(0.0)
                self.arrow_anim.start()
            self.update()

        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()

        if self.resize_anim.state() == QPropertyAnimation.State.Running or self._resize_handle_opacity > 0.0:
            self.resize_anim.stop()
            self.resize_anim.setEndValue(0.0)
            self.resize_anim.start()
        self.unsetCursor()
        super().leaveEvent(event)

    def enterEvent(self, event):
        """Check resize handle on re-entry (e.g. after drop)."""
        self._hovering = True
        if self.config and self.config.get('type') == 'input_number':
            if self._arrow_opacity < 1.0:
                self.arrow_anim.stop()
                self.arrow_anim.setEndValue(1.0)
                self.arrow_anim.start()
            self.update()

        # We need to check if mouse is already in the corner
        pos = self.mapFromGlobal(QCursor.pos())
        rect = self.rect()
        size = 28
        in_corner = (pos.x() >= rect.width() - size) and (pos.y() >= rect.height() - size)

        if in_corner:
            # Fast restore if we just dropped or entered directly
            self.resize_anim.stop()
            self.resize_anim.setEndValue(1.0)
            self.resize_anim.start()
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)

        super().enterEvent(event)

    def show_context_menu(self, pos):
        """Show context menu for right click."""
        # Forbidden buttons have no context menu
        if self.config and self.config.get('type') == 'forbidden':
            return
        menu = QMenu(self)
        # The button lives under the LTR-pinned grid; align the menu (and its
        # submenus) with the application direction so it mirrors under RTL.
        menu.setLayoutDirection(QApplication.layoutDirection())
        menu.setStyleSheet(_CONTEXT_MENU_STYLE)
        if self.config:
            self._add_tile_actions(menu)
        else:
            add_action = menu.addAction(t("context_menu.add"))
            add_action.triggered.connect(lambda: self.clicked.emit(self.config))  # Trigger click (add)
        menu.exec(self.mapToGlobal(pos))

    def _add_tile_actions(self, menu):
        """Picture in picture (cameras), edit, duplicate, move to page and clear."""
        pip_entity = self._pip_camera_entity()
        if pip_entity:
            pip_action = menu.addAction(t("context_menu.picture_in_picture"))
            pip_action.triggered.connect(lambda: self.pip_requested.emit(pip_entity, self.config.get('label') or ''))

        edit_action = menu.addAction(t("context_menu.edit"))
        edit_action.triggered.connect(lambda: self.edit_requested.emit(self.slot))

        dup_action = menu.addAction(t("context_menu.duplicate"))
        dup_action.triggered.connect(lambda: self.duplicate_requested.emit(self.slot))

        self._add_move_to_page(menu)
        clear_action = menu.addAction(t("context_menu.clear"))
        clear_action.triggered.connect(lambda: self.clear_requested.emit(self.slot))

    def _add_move_to_page(self, menu):
        """A "Move to" submenu listing the other pages (disabled with a single page)."""
        if self._page_count <= 1:
            menu.addAction(t("context_menu.move_to")).setEnabled(False)
            return
        move_menu = menu.addMenu(t("context_menu.move_to"))
        move_menu.setStyleSheet(menu.styleSheet())
        for p in range(self._page_count):
            if p != self._current_page:
                action = move_menu.addAction(t("context_menu.page", num=p + 1))
                action.triggered.connect(lambda checked, page=p: self.move_to_page_requested.emit(self.slot, page))

    def _pip_camera_entity(self) -> str:
        """The camera this tile can pop out into picture-in-picture, or ''."""
        return pip_camera_entity(self.config)

    def simulate_click(self):
        """Programmatically trigger a click."""
        if not self.config:
            return

        self.trigger_feedback()

        # Script/Scene: Trigger pulse animation
        if self.config.get('type') in ['script', 'scene']:
            self.pulse_anim.stop()
            self.pulse_anim.start()

        # Climate widgets open overlay
        if self.config.get('type') == 'climate':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.climate_requested.emit(self.slot, rect)
        elif self.config.get('type') == 'weather':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.weather_requested.emit(self.slot, rect)
        elif self.config.get('type') == '3d_printer':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.printer_requested.emit(self.slot, rect, self.config)
        elif self.config.get('type') == 'lawn_mower':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.mower_requested.emit(self.slot, rect)
        elif self.config.get('type') == 'vacuum':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.vacuum_requested.emit(self.slot, rect)
        elif self.config.get('type') == 'alarm_control_panel':
            global_pos = self.mapToGlobal(QPoint(0, 0))
            rect = QRect(global_pos, self.size())
            self.alarm_requested.emit(self.slot, rect)
        else:
            self.clicked.emit(self.config)


# ── Weather tile text ──
# On Linux the condition icons are MDI glyphs, which need their own font family
# (elsewhere they are emoji). HA-derived values are escaped before they go into
# rich text; the icon comes from a fixed internal mapping.


def _mdi_icon_html(icon: str, tag: str, style: str) -> str:
    return f"<{tag} style='font-family: \"{get_mdi_font().family()}\"; {style}'>{icon}</{tag}>"


def _weather_huge_text(icon: str, temp_str: str, attrs: dict):
    """2x2+: icon and temperature, then humidity and wind (HA reports km/h; shown in m/s)."""
    humidity = attrs.get('humidity', '--')
    wind = attrs.get('wind_speed', '--')
    try:
        wind_display = f"{round(float(wind) / 3.6, 1)} m/s"
    except (ValueError, TypeError):
        wind_display = f"{wind} m/s"
    icon_html = _mdi_icon_html(icon, 'span', 'font-size: 40px;') if sys.platform.startswith('linux') else icon
    line = "<div style='font-size: 11px; color: #aaaaaa; font-weight: 600;'>{}</div>"
    text = (
        f"<div style='font-size: 22px; font-weight: 300; margin-bottom: 4px;'>{icon_html} {html.escape(str(temp_str))}</div>"
        + line.format(t('dashboard_button.weather.humidity', value=html.escape(str(humidity))))
        + line.format(t('dashboard_button.weather.wind', value=html.escape(str(wind_display))))
    )
    return text, Qt.TextFormat.RichText, QFont(SYSTEM_FONT, 12)


def _weather_tall_text(icon: str, temp_str: str):
    """1x2: icon above the temperature."""
    if sys.platform.startswith('linux'):
        icon_html = _mdi_icon_html(icon, 'div', 'font-size: 40px; margin-bottom: 5px;')
        return f"{icon_html}{html.escape(str(temp_str))}", Qt.TextFormat.RichText, QFont(SYSTEM_FONT, 24)
    return f"{icon}\n{temp_str}", Qt.TextFormat.PlainText, QFont(SYSTEM_FONT, 24)


def _weather_wide_text(icon: str, temp_str: str):
    """2x1: icon beside the temperature."""
    if sys.platform.startswith('linux'):
        icon_html = _mdi_icon_html(icon, 'span', 'font-size: 32px;')
        return f"{icon_html} {html.escape(str(temp_str))}", Qt.TextFormat.RichText, QFont(SYSTEM_FONT, 28)
    return f"{icon} {temp_str}", Qt.TextFormat.PlainText, QFont(SYSTEM_FONT, 28)


_CONTEXT_MENU_STYLE = """
    QMenu {
        background-color: #2b2b2b;
        border: 1px solid #3d3d3d;
        border-radius: 6px;
        padding: 4px;
    }
    QMenu::item {
        background: transparent;
        padding: 6px 24px 6px 12px;
        color: #e0e0e0;
        border-radius: 4px;
    }
    QMenu::item:selected {
        background-color: #007aff;
        color: white;
    }
"""
