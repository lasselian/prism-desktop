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
    QIcon, QAction, QPainterPath
)
from ui.icons import get_icon, get_mdi_font, Icons
from core.utils import SYSTEM_FONT

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
        self._state = "off"
        self._value = ""
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
        
        # Long press timer
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(300) # 300ms hold
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._ignore_release = False
        
        self._border_effect = 'Rainbow'
        self.setup_ui()
        self.update_style()
        
        # Enable dropping
        self.setAcceptDrops(True)
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
        
        layout.addStretch()
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        layout.addStretch()
        
        # self.setFixedSize(90, 80) # Removed fixed size
        self.setFixedSize(90 * self.span_x + (8 * (self.span_x - 1)), 80 * self.span_y + (8 * (self.span_y - 1)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.update_content()
    
    def update_content(self):
        """Update button content from config."""
        
        if not self.config:
            # Empty slot - show plus icon
            self.value_label.setFont(get_mdi_font(24))
            self.value_label.setText(Icons.PLUS)
            self.name_label.setText("Add")
            self.value_label.show()
            self.name_label.show()
            self.value_label.show()
            self.name_label.show()
            # Clear camera image
            self._cached_display_pixmap = None
            self.update()
            return
        
        btn_type = self.config.get('type', 'switch')
        label = self.config.get('label', '')
        custom_icon = self.config.get('icon')
        
        icon_char = get_icon(custom_icon) if custom_icon else None

        if btn_type == 'weather':
            # Weather Widget Logic
            state_obj = self._value if isinstance(self._value, dict) else {}
            state_str = state_obj.get('state', 'unknown')
            attrs = state_obj.get('attributes', {})
            
            # Data extraction
            temp = attrs.get('temperature', '--')
            unit = attrs.get('temperature_unit', '°C') # HA usually provides this in attributes or unit_of_measurement
            # fallback to global unit if not in attrs? usually in attrs for weather entity
            
            # Map Emoji
            emoji = self._get_weather_emoji(state_str)
            
            # Layout based on size
            is_huge = self.span_x >= 2 and self.span_y >= 2
            is_wide = self.span_x >= 2
            is_tall = self.span_y >= 2
            
            if is_huge:
                # 2x2+: Detailed View
                humidity = attrs.get('humidity', '--')
                wind = attrs.get('wind_speed', '--')
                
                # Multiline text with HTML for styling
                text = (
                    f"<div style='font-size: 22px; font-weight: 300; margin-bottom: 4px;'>{emoji} {temp}°</div>"
                    f"<div style='font-size: 11px; color: #aaaaaa; font-weight: 600;'>Humidity: {humidity}%</div>"
                    f"<div style='font-size: 11px; color: #aaaaaa; font-weight: 600;'>Wind: {wind} km/h</div>"
                )
                self.value_label.setTextFormat(Qt.TextFormat.RichText)
                self.value_label.setText(text)
                # Font size set in HTML, but keep base font for metrics
                self.value_label.setFont(QFont(SYSTEM_FONT, 12)) 
                
            elif is_tall and not is_wide:
                # 1x2 (Tall): Stacked
                self.value_label.setText(f"{emoji}\n{temp}°")
                self.value_label.setFont(QFont(SYSTEM_FONT, 24))
                
            elif is_wide:
                # 2x1 (Wide): Side by side
                self.value_label.setTextFormat(Qt.TextFormat.PlainText)
                self.value_label.setText(f"{emoji} {temp}°")
                self.value_label.setFont(QFont(SYSTEM_FONT, 28))
                
            else:
                # 1x1: Temp Only (Compact)
                self.value_label.setTextFormat(Qt.TextFormat.PlainText)
                self.value_label.setText(f"{temp}°")
                self.value_label.setFont(QFont(SYSTEM_FONT, 22, QFont.Weight.Bold))
            
            if label:
                self.name_label.setText(label)
                self.name_label.show()
            else:
                self.name_label.hide()
                
            self.setProperty("type", "weather")
            self.value_label.show()

        elif btn_type == 'widget':
            # Show sensor value (no icon font, regular font)
            self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
            
            val = self._value
            if val is not None:
                # Precision Formatting (Default to 1 if not set)
                precision = self.config.get('precision', 1)
                
                try:
                    import re
                    # Extract number and unit (e.g., "21.5" and "°C")
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
                    pass # Keep original string if parsing fails
                        
            self.value_label.setText(val or "--")
            self.name_label.setText(label)
            self.setProperty("type", "widget")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'climate':
            # Show temperature value
            self.value_label.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
            self.value_label.setText(self._value or "--°C")
            self.name_label.setText(label)
            self.setProperty("type", "climate")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'curtain':
            # Show curtain icon (MDI blinds)
            self.value_label.setFont(get_mdi_font(26))
            if icon_char:
                icon = icon_char
            else:
                icon = Icons.BLINDS_OPEN if self._state == "open" else Icons.BLINDS
            self.value_label.setText(icon)
            self.name_label.setText(label)
            self.setProperty("type", "curtain")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'script':
            # Show script icon
            self.value_label.setFont(get_mdi_font(26))
            self.value_label.setText(icon_char if icon_char else Icons.SCRIPT)
            self.name_label.setText(label)
            self.name_label.setText(label)
            self.setProperty("type", "script")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'scene':
            # Show scene icon
            self.value_label.setFont(get_mdi_font(26))
            # Use specific default if no icon configured, otherwise use resolved icon
            default_icon = Icons.SCENE_THEME
            self.value_label.setText(get_icon(custom_icon) if custom_icon else default_icon)
            self.name_label.setText(label)
            self.setProperty("type", "scene")
            self.value_label.show()
            self.name_label.show()
            self.setProperty("type", "scene")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'fan':
            # Fan (Switch-like)
            self.value_label.setFont(get_mdi_font(26))
            if icon_char:
                icon = icon_char
            else:
                icon = Icons.FAN
            self.value_label.setText(icon)
            self.name_label.setText(label)
            self.setProperty("type", "fan")
            self.value_label.show()
            self.name_label.show()
        elif btn_type == 'camera':
            # Camera shows image, hide text labels
            self.value_label.hide()
            self.name_label.hide()
            self.setProperty("type", "camera")
            
            # Set placeholder if no image yet
            if not self._cached_display_pixmap or self._cached_display_pixmap.isNull():
                self.value_label.show()
                self.value_label.setFont(get_mdi_font(26))
                self.value_label.setText(Icons.VIDEO)
        else:
            # Show switch/light icon (MDI lightbulb)
            self.value_label.setFont(get_mdi_font(26))
            if icon_char:
                icon = icon_char
            else:
                icon = Icons.LIGHTBULB if self._state == "on" else Icons.LIGHTBULB_OFF
            self.value_label.setText(icon)
            self.name_label.setText(label)
            self.setProperty("type", "switch")
            self.value_label.show()
            self.name_label.show()
            
        self.style().unpolish(self)
        self.style().polish(self)
    
    def set_state(self, state: str):
        """Set the state (on/off) for switches."""
        self._state = state
        self.update_content()
        self.update_style()
    
    def set_value(self, value: str):
        """Set the value for sensor widgets."""
        self._value = value
        self.update_content()

    def set_weather_state(self, state: dict):
        """Set full weather state object."""
        self._value = state
        self.update_content()
        self.update_style()

    def _get_weather_emoji(self, state: str) -> str:
        """Map HA weather state to emoji."""
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
            from PyQt6.QtGui import QPainterPath
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
        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {
                'base': '#2d2d2d',
                'accent': '#0078d4',
                'text': '#ffffff',
                'border': '#555555',
                'alternate_base': '#353535',
                'text': '#e0e0e0', # High contrast text
                'border': '#555555',
                'alternate_base': '#353535',
                'subtext': '#888888', # For units/secondary info
            }
        
        # Use cleaner, Apple-style typography
        # Main Value: Large, Thin/Light
        # Label: Small, Uppercase, Tracking
        
        font_main = SYSTEM_FONT
        font_weight_val = "300" # Light
        font_size_val = "20px" # Increased from 18px
        
        font_label = SYSTEM_FONT 
        font_size_label = "11px" # Increased from 10px
        font_weight_label = "600" # Semi-Bold

        if not self.config:
            # Empty
            self.setStyleSheet(f"""
                DashboardButton {{
                    background-color: {colors['alternate_base']};
                    border-radius: 10px;
                }}
                QLabel {{ color: {colors['border']}; background: transparent; }}
            """)
        elif self._state == "on" or self._state == "open":
             # On - Use button's custom color if set, otherwise theme accent
             button_color = self.config.get('color', colors['accent'])
             
             # Icons: Lower opacity = Stronger Tint (background bleeds through)
             icon_color = "rgba(255, 255, 255, 0.65)"
             # Text: Pure White (Crisp)
             text_color = "rgba(255, 255, 255, 1.0)"
             
             self.setStyleSheet(f"""
                DashboardButton {{
                    background-color: {button_color};
                    border-radius: 12px;
                }}
                QLabel#valueLabel {{ 
                    color: {icon_color}; 
                    background: transparent; 
                    font-family: "{font_main}"; font-size: {font_size_val}; font-weight: {font_weight_val};
                }}
                /* Beefier font for Icons (Switch/Light/Script) */
                DashboardButton[type="switch"] QLabel#valueLabel,
                DashboardButton[type="script"] QLabel#valueLabel,
                DashboardButton[type="scene"] QLabel#valueLabel,
                DashboardButton[type="fan"] QLabel#valueLabel {{
                     color: {icon_color};
                     font-weight: 400; 
                     font-size: 26px; /* Significantly larger icon */
                }}
                /* Climate shows temperature - keep it readable like text */
                DashboardButton[type="climate"] QLabel#valueLabel {{
                     color: {text_color};
                     font-weight: 400; 
                     font-size: 20px;
                }}
                /* Curtain uses icon */
                DashboardButton[type="curtain"] QLabel#valueLabel {{
                     color: {icon_color};
                     font-weight: 400; 
                     font-size: 26px; 
                }}
                /* Weather style handled dynamically in code but defaults here */
                DashboardButton[type="weather"] QLabel#valueLabel {{
                     color: {text_color};
                     font-weight: 400;
                     /* flow: multiline */
                }}
                QLabel#nameLabel {{ 
                    color: {text_color}; 
                    background: transparent;
                    opacity: 0.9;
                    font-family: "{font_label}"; font-size: {font_size_label}; font-weight: {font_weight_label}; text-transform: uppercase;
                }}
            """)
        else:
            # Off / Widget (Default dark state)
            self.setStyleSheet(f"""
                DashboardButton {{
                    background-color: {colors['base']};
                    border-radius: 12px;
                }}
                DashboardButton:hover {{
                    background-color: {colors['alternate_base']};
                }}
                QLabel#valueLabel {{ 
                    color: {colors['text']}; 
                    background: transparent;
                    font-family: "{font_main}"; font-size: {font_size_val}; font-weight: {font_weight_val};
                }}
                DashboardButton[type="switch"] QLabel#valueLabel,
                DashboardButton[type="script"] QLabel#valueLabel,
                DashboardButton[type="scene"] QLabel#valueLabel,
                DashboardButton[type="fan"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 26px; /* Significantly larger icon */
                }}
                DashboardButton[type="climate"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 20px;
                }}
                DashboardButton[type="weather"] QLabel#valueLabel {{
                     font-weight: 400;
                }}
                QLabel#nameLabel {{ 
                    color: {colors.get('subtext', '#888888')}; 
                    background: transparent;
                    font-family: "{font_label}"; font-size: {font_size_label}; font-weight: {font_weight_label}; text-transform: uppercase;
                }}
            """)
    
    def paintEvent(self, event):
        """Custom paint event for effects."""
        # First draw normal style (background)
        # First draw normal style (background)
        super().paintEvent(event)
        
        # Draw Camera Image (if applicable)
        if self.config and self.config.get('type') == 'camera' and self._cached_display_pixmap:
             painter = QPainter(self)
             painter.setRenderHint(QPainter.RenderHint.Antialiasing)
             painter.drawPixmap(0, 0, self._cached_display_pixmap)
        
        # Pulse Animation (Script)
        if self._pulse_opacity > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Use custom color or accent
            c = QColor("#0078d4")
            if self.theme_manager:
                c = QColor(self.theme_manager.get_colors()['accent'])
            
            # Allow custom color override
            if self.config and 'color' in self.config:
                c = QColor(self.config['color'])
                
            c.setAlphaF(self._pulse_opacity)
            
            # Draw rounded rect overlay
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        
        # Only draw special border if animating
        if self.anim.state() == QPropertyAnimation.State.Running:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Interactive 'Press' feedback
            angle = self._anim_progress * 360.0 * 1.5
            
            opacity = 1.0
            if self._anim_progress > 0.8:
                opacity = (1.0 - self._anim_progress) / 0.2
            
            painter.setOpacity(opacity)
            rect = self.rect().adjusted(1, 1, -1, -1)
            
            if self._border_effect == 'Rainbow':
                self._draw_rainbow_border(painter, rect, opacity, angle)
            elif self._border_effect == 'Aurora Borealis':
                self._draw_aurora_border(painter, rect, opacity, angle)

        elif not self.config:
            # Dashed border for empty slots (drawn over stylesheet bg)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(1, 1, -1, -1)
            
            if not hasattr(self, '_dashed_pen'):
                self._dashed_pen = QPen(QColor("#555555")) 
                self._dashed_pen.setStyle(Qt.PenStyle.DashLine)
                self._dashed_pen.setWidth(2)
                
            painter.setPen(self._dashed_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(self._dashed_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 10, 10)


        # Draw Resize Handle (Glass-like)
        if self._resize_handle_opacity > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Opacity control
            painter.setOpacity(self._resize_handle_opacity)
            
            # Bottom-right corner
            r = self.rect()
            radius = 12 # Match button border radius
            handle_size = 28 # Bigger handle
            
            path = QPainterPath()
            
            # 1. Start on bottom edge, left of corner
            path.moveTo(r.right() - handle_size, r.bottom())
            
            # 2. Outer Edge: Follow the button's rounded corner exactly
            # We want the bottom-right arc of the rounded rect
            # rect for the arc is the 2*radius square at the corner
            corner_rect = QRectF(r.right() - 2*radius, r.bottom() - 2*radius, 2*radius, 2*radius)
            # Start angle 270 (bottom) span -90 (counter-clockwise to right) -> checks out? 
            # Qt angles: 0 is right (3 o'clock). 270 is bottom (6 o'clock). 
            # We want from Bottom (270) to Right (360/0).
            # 其实 270 is 6 o'clock. 
            # arcTo(rect, startAngle, sweepLength)
            # We connect from bottom edge.
            
            # Simpler approach: Line to corner start, then arc?
            # actually, just using addRoundedRect clip is easier, but let's draw the shape explicitly.
            
            # Point A: (Right - handle, Bottom)
            # Point B: (Right, Bottom - handle)
            # Outer edge is the corner.
            
            path.lineTo(r.right() - radius, r.bottom()) # Line to start of arc
            path.arcTo(corner_rect, 270, 90) # The corner itself
            path.lineTo(r.right(), r.bottom() - handle_size) # Line up to handle top
            
            # 3. Inner Edge: Curve inward back to start
            # This creates the "extruded" look
            # Control point near the inner corner
            path.quadTo(r.right() - 4, r.bottom() - 4, r.right() - handle_size, r.bottom())
            
            path.closeSubpath()
            
            # Glass Style
            painter.setPen(Qt.PenStyle.NoPen)
            
            # Gradient for glass/shiny look
            # Fix: Start gradient at handle's top-left, not button's top-left
            grad = QLinearGradient(QPointF(r.right() - handle_size, r.bottom() - handle_size), QPointF(r.bottomRight()))
            grad.setColorAt(0.0, QColor(255, 255, 255, 120)) # Start brighter 
            grad.setColorAt(1.0, QColor(255, 255, 255, 10))  # Fade out
            
            painter.setBrush(QBrush(grad))
            painter.drawPath(path)
            
            # Accent line (Inner Edge only) for sharpness
            # We re-stroke just the inner curve? 
            # Or just stroke the whole path?
            pen = QPen(QColor(255, 255, 255, 70))
            pen.setWidthF(2.0)
            painter.strokePath(path, pen)

    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.update()

    def _draw_rainbow_border(self, painter, rect, opacity, angle):
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        self._draw_gradient_border(painter, rect, opacity, angle, colors)

    def _draw_aurora_border(self, painter, rect, opacity, angle):
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        self._draw_gradient_border(painter, rect, opacity, angle, colors)

    def _draw_gradient_border(self, painter, rect, opacity, angle, colors):
        gradient = QConicalGradient(QPointF(rect.center()), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(2)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 9, 9)

    def _on_long_press(self):
        """Handle long press: Start dimmer or climate if applicable."""
        if not self.config: return
        
        btn_type = self.config.get('type', 'switch')
        
        # Get absolute coordinates
        global_pos = self.mapToGlobal(QPoint(0,0))
        rect = QRect(global_pos, self.size())
        
        if btn_type == 'switch':
            # Lights show dimmer overlay
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
            
    def mousePressEvent(self, event):
        """Track click start."""
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
            
        # Resize Logic
        if self._is_resizing:
            # Use distinct global pos diff
            current_global_pos = event.globalPosition().toPoint()
            diff = current_global_pos - self._drag_start_pos
            
            dx_steps = round(diff.x() / 90.0) # Approx cell width + gap
            dy_steps = round(diff.y() / 90.0) 
            
            # Clamp: max 4 wide, max 2 tall
            new_span_x = max(1, min(4, self._resize_start_span[0] + dx_steps))
            new_span_y = max(1, min(2, self._resize_start_span[1] + dy_steps))
            
            if new_span_x != self.span_x or new_span_y != self.span_y:
                self.resize_requested.emit(self.slot, new_span_x, new_span_y)
            return

        dist = (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength()
        if dist < QApplication.startDragDistance():
            return
            
        # Drag started -> Cancel long press
        self._long_press_timer.stop()
        
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
             self.trigger_feedback() # Show feedback BEFORE emit
             
             # Script/Scene: Trigger pulse animation
             if self.config and self.config.get('type') in ['script', 'scene']:
                 self.pulse_anim.stop()
                 self.pulse_anim.start()
             
             # Climate widgets open overlay on normal click
             if self.config and self.config.get('type') == 'climate':
                 global_pos = self.mapToGlobal(QPoint(0,0))
                 rect = QRect(global_pos, self.size())
                 self.climate_requested.emit(self.slot, rect)
             elif self.config and self.config.get('type') == 'camera':
                 # Camera buttons have no click action
                 pass
             else:
                 self.clicked.emit(self.config)
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        """Reset handle when mouse leaves."""
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

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_TYPE):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME_TYPE):
            data = event.mimeData().data(MIME_TYPE)
            stream = QDataStream(data, QIODevice.OpenModeFlag.ReadOnly)
            source_slot = stream.readInt32()
            
            if source_slot != self.slot:
                self.dropped.emit(source_slot, self.slot)
            event.accept()
        else:
            event.ignore()

    def show_context_menu(self, pos):
        """Show context menu for right click."""
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
        else:
             self.clicked.emit(self.config)
