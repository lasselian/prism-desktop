from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QApplication, QGraphicsDropShadowEffect, QMenu,
    QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, pyqtSignal, QPropertyAnimation, QEasingCurve, 
    QMimeData, QByteArray, QDataStream, QIODevice, pyqtProperty, QRectF, QTimer, QRect,
    pyqtSlot, QUrl, QSize
)
from PyQt6.QtGui import (
    QColor, QFont, QDrag, QPixmap, QPainter, QCursor,
    QPen, QBrush, QLinearGradient, QConicalGradient, QDesktopServices,
    QIcon, QPainterPath
)
from ui.icons import get_icon, get_mdi_font, Icons, get_icon_for_type
from core.utils import SYSTEM_FONT
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.widgets.dashboard_button_styles import DashboardButtonStyleManager
import sys

# Custom MIME type for drag and drop
MIME_TYPE = "application/x-hatray-slot"

class DashboardButton(QFrame):
    """Button or widget in the grid."""
    
    clicked = pyqtSignal(dict)
    dropped = pyqtSignal(int, int) # target_slot, source_slot
    edit_requested = pyqtSignal(int)
    duplicate_requested = pyqtSignal(int)
    clear_requested = pyqtSignal(int)
    dimmer_requested = pyqtSignal(int, QRect) # slot, geometry
    climate_requested = pyqtSignal(int, QRect) # slot, geometry
    weather_requested = pyqtSignal(int, QRect) # slot, geometry
    printer_requested = pyqtSignal(int, QRect, dict) # slot, geometry, config
    volume_requested = pyqtSignal(int, QRect) # slot, geometry (for volume overlay)
    volume_scroll = pyqtSignal(str, float) # entity_id, new_volume (for scroll wheel)
    media_command_requested = pyqtSignal(dict)
    resize_requested = pyqtSignal(int, int, int) # slot, span_x, span_y
    resize_finished = pyqtSignal()
    
    def __init__(self, slot: int, config: dict = None, theme_manager=None, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.config = config or {}
        # Load span from config or default to 1
        self.span_x = self.config.get('span_x', 1)
        self.span_y = self.config.get('span_y', 1)
        
        self.theme_manager = theme_manager
        self._show_border_effect = False
        self._show_dimming = False
        self._brightness = 255
        
        # Internal state
        self._state = "off"
        self._value = None
        self._ha_icon = None  # Icon from Home Assistant state
        self._media_state = {}  # Full media player state
        self._album_art = None  # QPixmap for album art
        self._drag_start_pos = None
        self._is_resizing = False
        self._resize_start_span = (1, 1)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        
        # Camera resize optimization
        self._last_camera_pixmap = None
        self._cached_display_pixmap = None
        
        # Resize handle animation
        self._resize_handle_opacity = 0.0
        self.resize_anim = QPropertyAnimation(self, b"resize_handle_opacity")
        self.resize_anim.setDuration(200) # Fast fade
        self.resize_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        # Click feedback animation
        self._content_opacity = 0.0
        self._anim_progress = 0.0
        self.anim = QPropertyAnimation(self, b"anim_progress")
        self.anim.setDuration(1500) # Slower, more elegant
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        # Script pulse animation
        self._pulse_opacity = 0.0
        self.pulse_anim = QPropertyAnimation(self, b"pulse_opacity")
        self.pulse_anim.setDuration(2000)
        self.pulse_anim.setKeyValueAt(0, 0.0)
        self.pulse_anim.setKeyValueAt(0.5, 0.8)
        self.pulse_anim.setKeyValueAt(1, 0.0)
        
        # Bounce animation on click
        self._bounce_offset = 0.0
        self.bounce_anim = QPropertyAnimation(self, b"bounce_offset")
        
        # Long press timer
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(300) # 300ms hold
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._ignore_release = False
        
        self._border_effect = 'Rainbow'
        self.setup_ui()
        self.update_style()
        
        # Disable dropping (handled by parent Dashboard)
        self.setAcceptDrops(False)
        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Pre-warm opacity effect for smoother animations
        self._opacity_eff = QGraphicsOpacityEffect(self)
        self._opacity_eff.setOpacity(1.0)
        self._opacity_eff.setEnabled(False) # Disable by default to save cost
        self.setGraphicsEffect(self._opacity_eff)
        
    def set_faded(self, opacity: float):
        """Set fade level (0.0 - 1.0). Auto-enables effect if needed."""
        # Lazy init if missing (fixes AttributeError if init failed or caching issues)
        if not hasattr(self, '_opacity_eff'):
            self._opacity_eff = QGraphicsOpacityEffect(self)
            self._opacity_eff.setOpacity(1.0)
            self._opacity_eff.setEnabled(False)
            self.setGraphicsEffect(self._opacity_eff)

        if opacity >= 1.0:
            self._opacity_eff.setEnabled(False)
        else:
            self._opacity_eff.setEnabled(True)
            self._opacity_eff.setOpacity(opacity)
            
    def set_opacity(self, opacity: float):
        """Standard public method for setting overall button opacity."""
        self.set_faded(opacity)
        
    def get_anim_progress(self):
        return self._anim_progress
        
    def set_anim_progress(self, val):
        self._anim_progress = val
        self.update() # Trigger repaint
        
    anim_progress = pyqtProperty(float, get_anim_progress, set_anim_progress)

    def get_pulse_opacity(self):
        return self._pulse_opacity
        
    def set_pulse_opacity(self, val):
        self._pulse_opacity = val
        self.update() 
        
    pulse_opacity = pyqtProperty(float, get_pulse_opacity, set_pulse_opacity)
    
    def get_resize_handle_opacity(self):
        return self._resize_handle_opacity
        
    def set_resize_handle_opacity(self, val):
        self._resize_handle_opacity = val
        self.update() 
        
    resize_handle_opacity = pyqtProperty(float, get_resize_handle_opacity, set_resize_handle_opacity)
    
    def get_bounce_offset(self):
        return self._bounce_offset

    def set_bounce_offset(self, val):
        self._bounce_offset = val
        m = 8
        self.layout().setContentsMargins(m, m + int(val), m, m - int(val))

    bounce_offset = pyqtProperty(float, get_bounce_offset, set_bounce_offset)
    
    def get_show_dimming(self):
        return self._show_dimming

    def set_show_dimming(self, val):
        if self._show_dimming != val:
            self._show_dimming = val
            self.update_style()

    show_dimming = pyqtProperty(bool, get_show_dimming, set_show_dimming)

    def set_spans(self, x, y):
        """Update spans and resize widget."""
        self.span_x = x
        self.span_y = y
        # Calculate new size based on grid units (90x80) + spacing (8)
        w = 90 * x + (8 * (x - 1))
        h = 80 * y + (8 * (y - 1))
        self.setFixedSize(w, h)
        
        # Re-apply camera image to fit new size immediately
        if self._last_camera_pixmap and not self._last_camera_pixmap.isNull():
             self.set_camera_image(self._last_camera_pixmap)
             
        # Force content update to adapt layout (1x1 -> 2x1 etc)
        self.update_content()
        self.update()

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
        self.value_label.setTextFormat(Qt.TextFormat.PlainText) # Security: Prevent HTML injection
        font = QFont(SYSTEM_FONT, 16, QFont.Weight.Bold)
        self.value_label.setFont(font)
        self.value_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        # Name label
        self.name_label = QLabel()
        self.name_label.setObjectName("nameLabel")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText) # Security: Prevent HTML injection
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
        
        layout.addStretch()
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        layout.addStretch()
        

        self.setFixedSize(90 * self.span_x + (8 * (self.span_x - 1)), 80 * self.span_y + (8 * (self.span_y - 1)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.update_content()
    
    def update_content(self):
        """Update button content from config."""
        if not self.config:
            self._update_empty_view()
            return
        
        # Forbidden slot
        if self.config.get('type') == 'forbidden':
            self._update_forbidden_view()
            return
        
        btn_type = self.config.get('type', 'switch')
        
        # Dispatch to specific view updaters
        if btn_type == 'weather':
            self._update_weather_view()
        elif btn_type == 'widget':
            self._update_widget_view()
        elif btn_type == 'climate':
            self._update_climate_view()
        elif btn_type == 'curtain':
            self._update_curtain_view()
        elif btn_type == 'script':
            self._update_script_view()
        elif btn_type == 'automation':
            self._update_automation_view()
        elif btn_type == 'scene':
            self._update_scene_view()
        elif btn_type == 'fan':
            self._update_fan_view()
        elif btn_type == 'media_player':
            self._update_media_player_view()
        elif btn_type == 'camera':
            self._update_camera_view()
        elif btn_type == '3d_printer':
            self._update_3d_printer_view()
        else:
            self._update_default_view(btn_type)
            
        self.style().unpolish(self)
        self.style().polish(self)

    # --- View Helpers ---

    def _update_empty_view(self):
        """Show add button."""
        self.value_label.setFont(get_mdi_font(24))
        self.value_label.setText(Icons.PLUS)
        self.name_label.setText("Add")
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
        state_str = state_obj.get('state', 'unknown')
        attrs = state_obj.get('attributes', {})
        
        temp = attrs.get('temperature', '--')
        emoji = self._get_weather_emoji(state_str)
        
        # Build temperature unit suffix (e.g. "°C", "°F")
        raw_unit = attrs.get('temperature_unit', '°')
        # HA may return "°C", "°F", or just the symbol — normalize
        if raw_unit in ('°C', '°F'):
            unit = raw_unit
        elif raw_unit in ('C', 'F'):
            unit = f'°{raw_unit}'
        else:
            unit = '°'
        
        # Clean temp display
        try:
            temp_clean = f"{float(temp):.1f}".replace('.0', '')
        except (ValueError, TypeError):
            temp_clean = str(temp)
        
        temp_str = f"{temp_clean}{unit}"
        
        is_huge = self.span_x >= 2 and self.span_y >= 2
        is_wide = self.span_x >= 2
        is_tall = self.span_y >= 2
        
        # On Linux, we use MDI icons which need to be in a specific font family
        # We wrap them in a span with the correct font family
        is_linux = sys.platform.startswith('linux')
        
        if is_huge:
            humidity = attrs.get('humidity', '--')
            wind = attrs.get('wind_speed', '--')
            try:
                wind_ms = round(float(wind) / 3.6, 1)
                wind_display = f"{wind_ms} m/s"
            except (ValueError, TypeError):
                wind_display = f"{wind} m/s"

            # Use different font size/styling for MDI vs Emoji
            mdi_family = get_mdi_font().family()
            emoji_html = f"<span style='font-family: \"{mdi_family}\"; font-size: 40px;'>{emoji}</span>" if is_linux else f"{emoji}"

            text = (
                f"<div style='font-size: 22px; font-weight: 300; margin-bottom: 4px;'>{emoji_html} {temp_str}</div>"
                f"<div style='font-size: 11px; color: #aaaaaa; font-weight: 600;'>Humidity: {humidity}%</div>"
                f"<div style='font-size: 11px; color: #aaaaaa; font-weight: 600;'>Wind: {wind_display}</div>"
            )
            self.value_label.setTextFormat(Qt.TextFormat.RichText)
            self.value_label.setText(text)
            self.value_label.setFont(QFont(SYSTEM_FONT, 12)) 
        elif is_tall and not is_wide:
            self.value_label.setTextFormat(Qt.TextFormat.RichText if is_linux else Qt.TextFormat.PlainText)
            if is_linux:
                mdi_family = get_mdi_font().family()
                emoji_html = f"<div style='font-family: \"{mdi_family}\"; font-size: 40px; margin-bottom: 5px;'>{emoji}</div>"
                self.value_label.setText(f"{emoji_html}{temp_str}")
            else:
                self.value_label.setText(f"{emoji}\n{temp_str}")
            self.value_label.setFont(QFont(SYSTEM_FONT, 24))
        elif is_wide:
            self.value_label.setTextFormat(Qt.TextFormat.RichText if is_linux else Qt.TextFormat.PlainText)
            if is_linux:
                mdi_family = get_mdi_font().family()
                emoji_html = f"<span style='font-family: \"{mdi_family}\"; font-size: 32px;'>{emoji}</span>"
                self.value_label.setText(f"{emoji_html} {temp_str}")
            else:
                self.value_label.setText(f"{emoji} {temp_str}")
            self.value_label.setFont(QFont(SYSTEM_FONT, 28))
        else:
            self.value_label.setTextFormat(Qt.TextFormat.PlainText)
            # Small buttons: just temp
            self.value_label.setText(temp_str)
            self.value_label.setFont(QFont(SYSTEM_FONT, 22, QFont.Weight.Bold))
        
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
        self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        
        val = self._value
        if val is not None:
            precision = self.config.get('precision', 1)
            try:
                import re
                match = re.match(r"([+-]?\d*\.?\d+)(.*)", str(val))
                if match:
                    num_str, unit_str = match.groups()
                    f_val = float(num_str)
                    if precision == 0:
                        formatted_num = f"{f_val:.0f}"
                    else:
                        formatted_num = f"{f_val:.{precision}f}"
                    val = f"{formatted_num}{unit_str}"
            except (ValueError, TypeError):
                pass
                    
        self.value_label.setText(val or "--")
        self.name_label.setText(label)
        self.setProperty("type", "widget")
        self.value_label.show()
        self.name_label.show()

    def _update_climate_view(self):
        label = self.config.get('label', '')
        self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        self.value_label.setText(self._value or "--°C")
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
         self._update_simple_icon_view(Icons.FAN, "fan")

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
            self.setToolTip(f"Now Playing: {artist} \u2014 {title}")
        elif title:
            self.setToolTip(f"Now Playing: {title}")
        else:
            self.setToolTip('')
        
        self.update_content()
        self.update_style()
        self.update()  # Trigger repaint for custom painting

    def set_album_art(self, pixmap):
        """Set the album art pixmap."""
        self._album_art = pixmap
        self.update()  # Trigger repaint

    def set_camera_image(self, pixmap):
        """Set the camera image pixmap."""
        self._last_camera_pixmap = pixmap
        # If we are currently in camera mode, force update
        if self.config.get('type') == 'camera':
             self.update()

    def reset_state(self):
        """Reset internal state to default."""
        self._state = "off"
        self._value = None
        self._brightness = 255
        self._ha_icon = None
        self._media_state = {}
        self._album_art = None
        self._last_camera_pixmap = None
        
        # Stop animations and reset counters
        self.anim.stop()
        self._anim_progress = 0.0
        
        self.pulse_anim.stop()
        self._pulse_opacity = 0.0
        
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
        
        # Pass HA icon to button (if available)
        icon = attributes.get('icon')
        if icon:
            self.set_ha_icon(icon)
        
        if btn_type == 'widget':
            # Update sensor value
            value = state.get('state', '--')
            unit = attributes.get('unit_of_measurement', '')
            self.set_value(f"{value}{unit}")
        elif btn_type == 'climate':
            # Update climate target temperature
            temp = attributes.get('temperature', '--')
            if temp != '--':
                self.set_value(f"{temp}°C")
            else:
                self.set_value("--°C")
            # Also update state for styling
            hvac_action = state.get('state', 'off')
            self.set_state('on' if hvac_action not in ['off', 'unavailable'] else 'off')
        elif btn_type == 'curtain':
            # Update curtain state (open/closed/opening/closing)
            cover_state = state.get('state', 'closed')
            # "open" when cover is up/open, anything else is closed
            self.set_state('open' if cover_state == 'open' else 'closed')
        elif btn_type == 'weather':
            # Update weather state - pass full object for attributes
            self.set_weather_state(state)
        elif btn_type == 'media_player':
            # Media player gets full state
            self.set_media_state(state)
        elif btn_type == '3d_printer':
            # State entity logic (e.g. Printing, Paused)
            self.set_state(state.get('state', 'unknown'))
            # Let the painter pull the rest from the dashboard's _entity_states directly
        elif btn_type == 'automation':
            # Update automation state (on/off)
            self.set_state(state.get('state', 'off'))
        else:
            # Update switch/light/fan/script/scene/lock state
            self.set_state(state.get('state', 'off'))
            
            # Capture brightness for dimming effect
            self._brightness = attributes.get('brightness', 255)
            # Re-apply style if dimming enabled and brightness changed
            if self._show_dimming:
                self.update_style()

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
                'exceptional': Icons.ALERT_CIRCLE
            }
            return mapping.get(state, Icons.WEATHER_CLOUDY) # Default to cloudy/unknown
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
                'exceptional': '⚠️'
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
        painter.drawPixmap(
            QRectF(0, 0, btn_w, btn_h), 
            pixmap, 
            QRectF(src_x, src_y, src_w, src_h)
        )
        painter.end()
        
        self.value_label.hide()
        self.name_label.hide()
        self.update()
    
    def update_style(self):
        """Update visual style based on state and theme."""
        DashboardButtonStyleManager.apply_style(self)
        self._update_label_shadows()
    
    def _update_label_shadows(self):
        """Adjust drop shadow intensity for light vs dark theme."""
        is_light = (self.theme_manager and self.theme_manager.get_effective_theme() == 'light')
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
        if not self.config: return
        
        btn_type = self.config.get('type', 'switch')
        
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()
        
        # Get absolute coordinates
        global_pos = self.mapToGlobal(QPoint(0,0))
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
            
    def mousePressEvent(self, event):
        """Track click start."""
        # Forbidden buttons are completely non-interactive
        if self.config and self.config.get('type') == 'forbidden':
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Use global position for resizing to handle widget movement reflow
            self._drag_start_pos = event.globalPosition().toPoint()
            
            # Check if clicking functionality (handle)
            rect = self.rect()
            size = 28
            in_corner = (event.pos().x() >= rect.width() - size) and (event.pos().y() >= rect.height() - size)
            
            if in_corner and self._resize_handle_opacity > 0.0:
                self._is_resizing = True
                self._resize_start_span = (self.span_x, self.span_y)
                # Don't trigger long press if resizing
            else:
                self._is_resizing = False
                self._ignore_release = False
                self._long_press_timer.start()
                
                if self.config:
                    self.bounce_anim.stop()
                    self.bounce_anim.setDuration(40)
                    self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
                    self.bounce_anim.setEndValue(1.0)
                    self.bounce_anim.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle drag start and hover effects."""
        # Check for resize handle hover (only for configured buttons, not Add buttons)
        is_configured = self.config and self.config.get('entity_id')
        
        if not self._drag_start_pos:
            rect = self.rect()
            size = 28 
            in_corner = is_configured and (event.pos().x() >= rect.width() - size) and (event.pos().y() >= rect.height() - size)
            
            # Simplified Logic: If in corner, fade in. If not, fade out.
            # Only trigger if not already at target state or moving towards it.
            
            if in_corner:
                 if self.resize_anim.endValue() != 1.0 or self.resize_anim.state() == QPropertyAnimation.State.Stopped:
                     # Only start if we aren't already going to 1.0
                     if self.resize_anim.endValue() != 1.0:
                         self.resize_anim.stop()
                         self.resize_anim.setEndValue(1.0)
                         self.resize_anim.start()
                 self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                 if self.resize_anim.endValue() != 0.0 or self.resize_anim.state() == QPropertyAnimation.State.Stopped:
                     # Only start if we aren't already going to 0.0
                     if self.resize_anim.endValue() != 0.0:
                         self.resize_anim.stop()
                         self.resize_anim.setEndValue(0.0)
                         self.resize_anim.start()
                 self.unsetCursor() # Use unsetCursor to revert to parent/default instead of forcing Hand
        
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._drag_start_pos:
            return
            
        # Prevent dragging "Add" buttons (empty config)
        if not self.config:
            return
            
        # Resize Logic
        if self._is_resizing:
            # Use distinct global pos diff
            current_global_pos = event.globalPosition().toPoint()
            diff = current_global_pos - self._drag_start_pos
            
            dx_steps = round(diff.x() / 90.0) # Approx cell width + gap
            dy_steps = round(diff.y() / 90.0) 
            
            # Clamp: max 4 wide, max 3 tall explicitly for 3d printer
            new_span_x = max(1, min(4, self._resize_start_span[0] + dx_steps))
            max_y_allowed = 3 if self.config.get('type') == '3d_printer' else 2
            new_span_y = max(1, min(max_y_allowed, self._resize_start_span[1] + dy_steps))
            
            if new_span_x != self.span_x or new_span_y != self.span_y:
                self.resize_requested.emit(self.slot, new_span_x, new_span_y)
            return

        dist = (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength()
        if dist < QApplication.startDragDistance():
            return
            
        # Drag started -> Cancel long press
        self._long_press_timer.stop()
        
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.set_bounce_offset(0.0) # immediate snap back for drag
        
        # Proceed with drag
        drag = QDrag(self)
        mime_data = QMimeData()
        
        data = QByteArray()
        stream = QDataStream(data, QIODevice.OpenModeFlag.WriteOnly)
        stream.writeInt32(self.slot)
        mime_data.setData(MIME_TYPE, data)
        
        drag.setMimeData(mime_data)
        
        # Create transparent pixmap with rounded corners
        from PyQt6.QtGui import QPainterPath
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Create rounded clip path
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(path)
        
        # Render widget into clipped area
        self.render(painter)
        painter.end()
        
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        
        drag.exec(Qt.DropAction.MoveAction)
        
    def mouseReleaseEvent(self, event):
        """Handle click."""
        self._long_press_timer.stop()
        
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()
        
        if self._ignore_release:
            # Long press consumed the event
            self._ignore_release = False
            self._drag_start_pos = None
            return

        # Handle resize release
        if self._is_resizing:
            self._is_resizing = False
            self.resize_finished.emit()
            return

        if self._drag_start_pos and event.button() == Qt.MouseButton.LeftButton:
             if self.config:
                 self.trigger_feedback() # Show feedback BEFORE emit
             
             # Media Player Logic
             if self.config and self.config.get('type') == 'media_player':
                 x = event.pos().x()
                 y = event.pos().y()
                 w = self.width()
                 h = self.height()
                 is_huge = self.span_x >= 2 and self.span_y >= 2
                 is_tall = self.span_y >= 2 and self.span_x < 2
                 is_wide = self.span_x >= 2
                 
                 if is_huge:
                     # 2x2: Controls in center (redesigned)
                     # Hit area: +/- 30px from center
                     center_y = h // 2
                     if center_y - 30 <= y <= center_y + 30:
                         if x < w / 3:
                             self.clicked.emit({**self.config, 'action': 'media_previous_track'})
                         elif x > (w / 3) * 2:
                             self.clicked.emit({**self.config, 'action': 'media_next_track'})
                         else:
                             self.clicked.emit({**self.config, 'action': 'media_play_pause'})
                     else:
                         # Top/Bottom click -> play/pause (or future detail view)
                         self.clicked.emit({**self.config, 'action': 'media_play_pause'})
                 elif is_tall:
                     # 1x2: Top half = play/pause, bottom half = prev/next
                     if y < h / 2:
                         self.clicked.emit({**self.config, 'action': 'media_play_pause'})
                     else:
                         if x < w / 2:
                             self.clicked.emit({**self.config, 'action': 'media_previous_track'})
                         else:
                             self.clicked.emit({**self.config, 'action': 'media_next_track'})
                 elif is_wide:
                     # 2x1: Thirds
                     if x < w / 3:
                         self.clicked.emit({**self.config, 'action': 'media_previous_track'})
                     elif x > (w / 3) * 2:
                         self.clicked.emit({**self.config, 'action': 'media_next_track'})
                     else:
                         self.clicked.emit({**self.config, 'action': 'media_play_pause'})
                 else:
                     # 1x1: Play/Pause
                     self.clicked.emit({**self.config, 'action': 'media_play_pause'})
                 
                 self._drag_start_pos = None
                 super().mouseReleaseEvent(event)
                 return

             # Script/Scene: Trigger pulse animation
             if self.config and self.config.get('type') in ['script', 'scene']:
                 self.pulse_anim.stop()
                 self.pulse_anim.start()
             
             # Climate widgets open overlay on normal click
             if self.config and self.config.get('type') == 'climate':
                 global_pos = self.mapToGlobal(QPoint(0,0))
                 rect = QRect(global_pos, self.size())
                 self.climate_requested.emit(self.slot, rect)
             elif self.config and self.config.get('type') == 'weather':
                 global_pos = self.mapToGlobal(QPoint(0,0))
                 rect = QRect(global_pos, self.size())
                 self.weather_requested.emit(self.slot, rect)
             elif self.config and self.config.get('type') == 'camera':
                 # Camera buttons have no click action
                 pass
             elif self.config and self.config.get('type') == '3d_printer':
                 global_pos = self.mapToGlobal(QPoint(0,0))
                 rect = QRect(global_pos, self.size())
                 self.printer_requested.emit(self.slot, rect, self.config)
             elif self.config and self.config.get('type') == 'lock':
                 # Toggle lock state
                 action = 'unlock' if self._state == 'locked' else 'lock'
                 self.clicked.emit({**self.config, 'action': action})
             else:
                 self.clicked.emit(self.config)
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

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
        if hasattr(self, 'bounce_anim') and self._bounce_offset > 0:
            self.bounce_anim.stop()
            self.bounce_anim.setDuration(300)
            self.bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self.bounce_anim.setEndValue(0.0)
            self.bounce_anim.start()

        if self._resize_handle_opacity > 0.0:
            self.resize_anim.stop()
            self.resize_anim.setEndValue(0.0)
            self.resize_anim.start()
            self.unsetCursor()
        super().leaveEvent(event)

    def enterEvent(self, event):
        """Check resize handle on re-entry (e.g. after drop)."""
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
        menu.setStyleSheet("""
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
        """)
        
        if self.config:
            edit_action = menu.addAction("Edit")
            edit_action.triggered.connect(lambda: self.edit_requested.emit(self.slot))
            
            dup_action = menu.addAction("Duplicate")
            dup_action.triggered.connect(lambda: [print(f"DEBUG: Duplicate action triggered for slot {self.slot}"), self.duplicate_requested.emit(self.slot)])
            
            clear_action = menu.addAction("Clear")
            clear_action.triggered.connect(lambda: self.clear_requested.emit(self.slot))
        else:
            add_action = menu.addAction("Add")
            add_action.triggered.connect(lambda: self.clicked.emit(self.config)) # Trigger click (add)
        
        menu.exec(self.mapToGlobal(pos))

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
             global_pos = self.mapToGlobal(QPoint(0,0))
             rect = QRect(global_pos, self.size())
             self.climate_requested.emit(self.slot, rect)
        elif self.config.get('type') == 'weather':
             global_pos = self.mapToGlobal(QPoint(0,0))
             rect = QRect(global_pos, self.size())
             self.weather_requested.emit(self.slot, rect)
        elif self.config.get('type') == '3d_printer':
             global_pos = self.mapToGlobal(QPoint(0,0))
             rect = QRect(global_pos, self.size())
             self.printer_requested.emit(self.slot, rect, self.config)
        else:
             self.clicked.emit(self.config)
