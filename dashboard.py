"""
Dashboard Widget for Prism Desktop
The main popup menu with 4x2 grid of buttons/widgets.
"""

import asyncio
import time
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QPushButton, QLabel, 
    QVBoxLayout, QHBoxLayout, QFrame, QApplication, QGraphicsDropShadowEffect, QMenu,
    QGraphicsOpacityEffect, QScrollArea
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, pyqtSignal, QPropertyAnimation, QEasingCurve, 
    QMimeData, QByteArray, QDataStream, QIODevice, pyqtProperty, QRectF, QTimer, QRect,
    pyqtSlot, QUrl, QSize

)
from PyQt6.QtGui import (
    QColor, QFont, QDrag, QPixmap, QPainter, QCursor,
    QPen, QBrush, QLinearGradient, QConicalGradient, QDesktopServices,
    QIcon

)
import base64
from icons import get_icon, get_mdi_font

# Custom MIME type for drag and drop
MIME_TYPE = "application/x-hatray-slot"


class DashboardButton(QFrame):
    """Button or widget in the grid."""
    
    clicked = pyqtSignal(dict)
    dropped = pyqtSignal(int, int) # (source, target)
    edit_requested = pyqtSignal(int) # slot
    clear_requested = pyqtSignal(int) # slot
    dimmer_requested = pyqtSignal(int, QRect) # slot, geometry
    climate_requested = pyqtSignal(int, QRect) # slot, geometry
    
    def __init__(self, slot: int, config: dict = None, theme_manager=None, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.config = config or {}
        self.theme_manager = theme_manager
        self._state = "off"
        self._value = ""
        self._drag_start_pos = None
        self._image_pixmap = None  # For image type buttons
        self._scaled_pixmap = None  # Cached scaled version
        self._scaled_size = (0, 0)  # Size the cache was created for
        
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
        font = QFont("Segoe UI", 16, QFont.Weight.Bold)
        self.value_label.setFont(font)
        
        # Name label
        self.name_label = QLabel()
        self.name_label.setObjectName("nameLabel")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_font = QFont("Segoe UI", 9)
        self.name_label.setFont(name_font)
        
        layout.addStretch()
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        layout.addStretch()
        
        self._slot_span = 1  # Default to 1 slot
        self.setFixedSize(90, 80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.update_content()
    
    def set_slot_span(self, span: int):
        """Set how many slots this button spans (square: width = height)."""
        self._slot_span = max(1, min(span, 4))
        # Width: 90px per slot + 8px spacing between slots
        new_width = (90 * self._slot_span) + (8 * (self._slot_span - 1))
        # Height: 80px per slot + 8px spacing between slots (square)
        new_height = (80 * self._slot_span) + (8 * (self._slot_span - 1))
        self.setFixedSize(new_width, new_height)
        self._scaled_pixmap = None  # Invalidate cache on resize
    
    def update_content(self):
        """Update button content from config."""
        from icons import Icons
        
        if not self.config:
            # Empty slot - show plus icon
            self.value_label.setFont(get_mdi_font(24))
            self.value_label.setText(Icons.PLUS)
            self.name_label.setText("Add")
            return
        
        btn_type = self.config.get('type', 'switch')
        label = self.config.get('label', '')
        custom_icon = self.config.get('icon')
        
        icon_char = get_icon(custom_icon) if custom_icon else None
        
        if btn_type == 'widget':
            # Show sensor value (no icon font, regular font)
            self.value_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
            
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
        elif btn_type == 'climate':
            # Show temperature value
            self.value_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
            self.value_label.setText(self._value or "--°C")
            self.name_label.setText(label)
            self.setProperty("type", "climate")
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
        elif btn_type == 'script':
            # Show script icon
            self.value_label.setFont(get_mdi_font(26))
            self.value_label.setText(icon_char if icon_char else Icons.SCRIPT)
            self.name_label.setText(label)
            self.name_label.setText(label)
            self.setProperty("type", "script")
        elif btn_type == 'scene':
            # Show scene icon
            self.value_label.setFont(get_mdi_font(26))
            # Use specific default if no icon configured, otherwise use resolved icon
            default_icon = Icons.SCENE_THEME
            self.value_label.setText(get_icon(custom_icon) if custom_icon else default_icon)
            self.name_label.setText(label)
            self.setProperty("type", "scene")
        elif btn_type == 'image':
            # Show image from base64 data or placeholder icon
            if hasattr(self, '_image_pixmap') and self._image_pixmap:
                # Image will be painted in paintEvent
                self.value_label.setText("")
            else:
                # Show placeholder icon
                self.value_label.setFont(get_mdi_font(26))
                self.value_label.setText(icon_char if icon_char else Icons.IMAGE)
            # Show entity value if available, otherwise use label
            if self._value:
                self.name_label.setText(self._value)
            else:
                self.name_label.setText(label)
            self.setProperty("type", "image")
        elif btn_type == 'camera':
            # Show camera stream (auto-refreshing image) or placeholder
            if hasattr(self, '_image_pixmap') and self._image_pixmap:
                # Image will be painted in paintEvent
                self.value_label.setText("")
            else:
                # Show placeholder icon
                self.value_label.setFont(get_mdi_font(26))
                self.value_label.setText(icon_char if icon_char else Icons.CAMERA)
            self.name_label.setText(label)
            self.setProperty("type", "camera")
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
    
    def set_image(self, base64_data: str):
        """Set the image from base64 data for image type buttons."""
        if not base64_data:
            self._image_pixmap = None
            self._scaled_pixmap = None  # Clear cache
            self.update_content()
            return
        
        try:
            # Handle data URI format (data:image/png;base64,...)
            if ',' in base64_data:
                base64_data = base64_data.split(',', 1)[1]
            
            image_bytes = base64.b64decode(base64_data)
            pixmap = QPixmap()
            pixmap.loadFromData(image_bytes)
            
            if not pixmap.isNull():
                # Store the original pixmap, we'll scale it in update_content based on button size
                self._image_pixmap = pixmap
                self._scaled_pixmap = None  # Invalidate cache for new image
            else:
                self._image_pixmap = None
                self._scaled_pixmap = None
        except Exception:
            self._image_pixmap = None
        
        self.update_content()
        self.update()  # Force repaint

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
        
        font_main = "Segoe UI"
        font_weight_val = "300" # Light
        font_size_val = "20px" # Increased from 18px
        
        font_label = "Segoe UI" 
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
                DashboardButton[type="scene"] QLabel#valueLabel {{
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
                /* Image type - hide value label when image is shown */
                DashboardButton[type="image"] QLabel#valueLabel {{
                     color: {icon_color};
                     font-weight: 400; 
                     font-size: 26px; 
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
                DashboardButton[type="scene"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 26px; /* Significantly larger icon */
                }}
                DashboardButton[type="climate"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 20px;
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
        super().paintEvent(event)
        
        # Draw image for image/camera type buttons
        if self.config.get('type') in ('image', 'camera') and self._image_pixmap:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            
            # Calculate available space for image (leave room for label at bottom and padding)
            padding = 8
            label_height = 20 if self.name_label.text() else 0
            available_width = self.width() - (padding * 2)
            available_height = self.height() - (padding * 2) - label_height
            
            # Use cached scaled pixmap if size hasn't changed
            target_size = (available_width, available_height)
            if self._scaled_pixmap is None or self._scaled_size != target_size:
                self._scaled_pixmap = self._image_pixmap.scaled(
                    available_width, available_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._scaled_size = target_size
            
            # Center the image in the available space
            x = padding + (available_width - self._scaled_pixmap.width()) // 2
            y = padding + (available_height - self._scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, self._scaled_pixmap)
            painter.end()
        
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
            painter.drawRoundedRect(rect, 10, 10)

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
            self._drag_start_pos = event.pos()
            self._ignore_release = False
            self._long_press_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle drag start."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._drag_start_pos:
            return
            
        dist = (event.pos() - self._drag_start_pos).manhattanLength()
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
             else:
                 self.clicked.emit(self.config)
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

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



from PyQt6.QtGui import QPainterPath

class DimmerOverlay(QWidget):
    """
    Overlay slider that morphs from a button.
    """
    value_changed = pyqtSignal(int)      # 0-100
    finished = pyqtSignal()
    morph_changed = pyqtSignal(float)    # 0.0 - 1.0
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Raise to draw on top of other widgets
        self.raise_()
        self.hide()
        
        self._value = 0 # 0-100 brightness
        self._text = "Dimmer"
        self._color = QColor("#FFD700") # Fill color
        self._base_color = QColor("#2d2d2d") # Background color
        
        # Animation
        self._morph_progress = 0.0
        self.anim = QPropertyAnimation(self, b"morph_progress")
        self.anim.setDuration(350) 
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic) 
        self.anim.finished.connect(self.on_anim_finished)

        # Border Spin Animation (Rainbow)
        self._border_progress = 0.0
        self.anim_border = QPropertyAnimation(self, b"border_progress")
        self.anim_border.setDuration(1500)
        self.anim_border.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self._is_closing = False
        self._border_effect = 'Rainbow'
        self._start_geom = QRect()
        self._target_geom = QRect()

    def get_morph_progress(self):
        return self._morph_progress
        
    def set_morph_progress(self, val):
        self._morph_progress = val
        self.morph_changed.emit(val)
        
        # Interpolate geometry
        current_rect = QRect(
            int(self._start_geom.x() + (self._target_geom.x() - self._start_geom.x()) * val),
            int(self._start_geom.y() + (self._target_geom.y() - self._start_geom.y()) * val),
            int(self._start_geom.width() + (self._target_geom.width() - self._start_geom.width()) * val),
            int(self._start_geom.height() + (self._target_geom.height() - self._start_geom.height()) * val)
        )
        self.setGeometry(current_rect)
        self.update()
        
    morph_progress = pyqtProperty(float, get_morph_progress, set_morph_progress)
    
    def get_border_progress(self):
        return self._border_progress
        
    def set_border_progress(self, val):
        self._border_progress = val
        self.update()
        
    border_progress = pyqtProperty(float, get_border_progress, set_border_progress)
    
    def start_morph(self, start_geo: QRect, target_geo: QRect, initial_value: int, text: str, color: QColor = None, base_color: QColor = None):
        """Start the morph animation sequence."""
        self._start_geom = start_geo
        self._target_geom = target_geo
        self._value = initial_value
        self._text = text
        self._color = color or QColor("#FFD700")
        self._base_color = base_color or QColor("#2d2d2d")
        self._is_closing = False
        
        self.setGeometry(start_geo)
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabMouse() # Hijack input immediately
        
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        
        # Start border spin
        self.anim_border.stop()
        self.anim_border.setStartValue(0.0)
        self.anim_border.setEndValue(1.0)
        self.anim_border.start()

    def close_morph(self):
        """Morph back to original and close."""
        self._is_closing = True
        self.releaseMouse()
        
        self.anim.stop()
        self.anim.setStartValue(self._morph_progress)
        self.anim.setEndValue(0.0)
        self.anim.start()
        
    def on_anim_finished(self):
        if self._is_closing:
            self.hide()
            self.finished.emit()
            
    def mousePressEvent(self, event):
        """Handle click: grab mouse and update value immediately."""
        event.accept()
        # Explicitly grab mouse to track movement outside widget
        self.grabMouse()
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        """Calculate value based on X position."""
        rect = self.rect()
        if rect.width() == 0: return
        
        # Use mapFromGlobal for robust out-of-bounds tracking
        local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
        x = local_pos.x()
        
        pct = x / rect.width()
        pct = max(0.0, min(1.0, pct))
        
        new_val = int(pct * 100) # HA uses 0-255 usually, but UI is 0-100 preferred
        if new_val != self._value:
            self._value = new_val
            self.update()
            self.value_changed.emit(self._value)

    def mouseReleaseEvent(self, event):
        """Commit value and close."""
        self.close_morph()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        
        # Background - Use base color to match button
        painter.setBrush(self._base_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 12, 12)
        
        if self._is_closing:
             pass
             
        # Progress Bar Fill
        # width based on value 
        fill_width = int(rect.width() * (self._value / 100.0))
        if fill_width > 0:
            fill_rect = QRect(0, 0, fill_width, rect.height())
            
            # Gradient for fill
            grad = QLinearGradient(0, 0, rect.width(), 0)
            grad.setColorAt(0, self._color.darker(120))
            grad.setColorAt(1, self._color)
            
            painter.setBrush(grad)
            
            # Clip to rounded rect
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), 12, 12)
            painter.setClipPath(path)
            
            painter.drawRect(fill_rect)
            
        # Draw Rainbow Border (Spin) if animating
        if self.anim_border.state() == QPropertyAnimation.State.Running:
            if self._border_effect == 'Rainbow':
                self._draw_rainbow_border(painter, rect)
            elif self._border_effect == 'Aurora Borealis':
                self._draw_aurora_border(painter, rect)
            
        # Text & Percent
        # Fade in text as we expand
        painter.setOpacity(1.0) # Reset opacity from border animation
        painter.setClipping(False) # Reset clip
        
        alpha = int(255 * (self._morph_progress if not self._is_closing else self._morph_progress))
        if alpha < 0: alpha = 0
        
        # Use Same Styles as DashboardButton
        painter.setPen(QColor(255, 255, 255, alpha))
        
        # Draw Label (Left)
        font_label = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        font_label.setCapitalization(QFont.Capitalization.AllUppercase)
        painter.setFont(font_label)
        
        # Adjust rect for padding
        text_rect = rect.adjusted(16, 0, -16, 0)
        painter.setPen(QColor(255, 255, 255, int(alpha * 0.7))) # Slightly dimmer label
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
            
        # Draw Percent (Right)
        font_val = QFont("Segoe UI", 20, QFont.Weight.Light)
        painter.setFont(font_val)
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self._value}%")

    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.update()

    def _draw_rainbow_border(self, painter, rect):
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        self._draw_gradient_border(painter, rect, colors)

    def _draw_aurora_border(self, painter, rect):
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        self._draw_gradient_border(painter, rect, colors)

    def _draw_gradient_border(self, painter, rect, colors):
        angle = self._border_progress * 360.0 * 1.5
        
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)

        gradient = QConicalGradient(QPointF(rect.center()), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(2) 
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        border_rect = QRectF(rect).adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(border_rect, 12, 12)


class ClimateOverlay(QWidget):
    """
    Overlay for climate control with +/- buttons.
    Stays open until explicitly closed.
    """
    value_changed = pyqtSignal(float)     # Temperature value
    mode_changed = pyqtSignal(str)        # HVAC mode
    fan_changed = pyqtSignal(str)         # Fan mode
    finished = pyqtSignal()
    morph_changed = pyqtSignal(float)     # 0.0 - 1.0
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.raise_()
        self.hide()
        
        self._value = 20.0  # Target temperature
        self._text = "Climate"
        self._color = QColor("#EA4335")  # Default red/warm
        self._base_color = QColor("#2d2d2d")
        self._min_temp = 5.0
        self._max_temp = 35.0
        self._step = 0.5
        
        # Advanced Mode State
        self._advanced_mode = False
        self._current_hvac_mode = 'off'
        self._current_fan_mode = 'auto'
        self._hvac_modes = [] # Available modes
        self._fan_modes = []  # Available fan modes
        
        # Animation
        self._morph_progress = 0.0
        self.anim = QPropertyAnimation(self, b"morph_progress")
        self.anim.setDuration(350)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self.on_anim_finished)
        
        # Border Spin Animation (Rainbow)
        self._border_progress = 0.0
        self.anim_border = QPropertyAnimation(self, b"border_progress")
        self.anim_border.setDuration(1500)
        self.anim_border.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self._border_effect = 'Rainbow'
        
        self._is_closing = False
        self._start_geom = QRect()
        self._target_geom = QRect()
        
        # Button rects (calculated in paintEvent)
        self._btn_minus = QRect()
        self._btn_plus = QRect()
        self._btn_close = QRect()
        
        # Advanced UI Rects
        self._mode_btns = [] # list of (rect, mode_name)
        self._fan_btns = []  # list of (rect, fan_name)
    
    def get_morph_progress(self):
        return self._morph_progress
        
    def set_morph_progress(self, val):
        self._morph_progress = val
        self.morph_changed.emit(val)
        
        # Interpolate geometry
        current_rect = QRect(
            int(self._start_geom.x() + (self._target_geom.x() - self._start_geom.x()) * val),
            int(self._start_geom.y() + (self._target_geom.y() - self._start_geom.y()) * val),
            int(self._start_geom.width() + (self._target_geom.width() - self._start_geom.width()) * val),
            int(self._start_geom.height() + (self._target_geom.height() - self._start_geom.height()) * val)
        )
        self.setGeometry(current_rect)
        self.update()
        
    morph_progress = pyqtProperty(float, get_morph_progress, set_morph_progress)
    
    def get_border_progress(self):
        return self._border_progress
        
    def set_border_progress(self, val):
        self._border_progress = val
        self.update()
        
    border_progress = pyqtProperty(float, get_border_progress, set_border_progress)

    def get_content_opacity(self):
        return self._content_opacity
        
    def set_content_opacity(self, val):
        self._content_opacity = val
        self.update()
        
    content_opacity = pyqtProperty(float, get_content_opacity, set_content_opacity)
    
    def start_morph(self, start_geo: QRect, target_geo: QRect, initial_value: float, text: str, 
                   color: QColor = None, base_color: QColor = None, advanced_mode: bool = False,
                   current_state: dict = None):
        """Start the morph animation sequence."""
        self._start_geom = start_geo
        
        # If advanced mode, force target height to accommodate UI
        self._target_geom = target_geo
        self._advanced_mode = advanced_mode
        
        if advanced_mode:
            # Expand height to cover two rows (168px)
            new_h = 168 
            
            # Align top with start_geo top
            self._target_geom.setHeight(new_h)
            self._target_geom.moveTop(start_geo.top())
            
            # Check bounds
            parent_h = self.parent().height() if self.parent() else 600
            
            # If expands past bottom, align bottom (Expand Up)
            if self._target_geom.bottom() > parent_h:
                self._target_geom.moveBottom(start_geo.bottom())
                
            # If still past top
            if self._target_geom.top() < 0:
                self._target_geom.moveTop(0)
            
            # Content Fade Animation Logic
            self._content_opacity = 0.0
            self.content_anim = QPropertyAnimation(self, b"content_opacity")
            self.content_anim.setDuration(300)
            self.content_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self.content_anim.setStartValue(0.0)
            self.content_anim.setEndValue(1.0)
            

            
            # Parse state for current modes
            self._hvac_modes = ['off', 'heat', 'cool', 'auto'] # Default
            self._fan_modes = ['auto', 'low', 'medium', 'high'] # Default
            
            if current_state:
                self._current_hvac_mode = current_state.get('state', 'off')
                attrs = current_state.get('attributes', {})
                self._current_fan_mode = attrs.get('fan_mode', 'auto')
                if attrs.get('hvac_modes'):
                    self._hvac_modes = attrs.get('hvac_modes')
                if attrs.get('fan_modes'):
                     # Filter out 'on'/'off' if they are just on/off generic
                    self._fan_modes = attrs.get('fan_modes')
        
        self._value = initial_value
        self._text = text
        self._color = color or QColor("#EA4335")
        self._base_color = base_color or QColor("#2d2d2d")
        self._is_closing = False
        
        self.setGeometry(start_geo)
        self.show()
        self.raise_()
        self.activateWindow()
        
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        
        # Start border spin
        self.anim_border.stop()
        self.anim_border.setStartValue(0.0)
        self.anim_border.setEndValue(1.0)
        self.anim_border.start()

    def close_morph(self):
        """Morph back to original and close."""
        self._is_closing = True
        
        # Fade out content immediately
        self._content_opacity = 0.0
        self.update()
        
        self.anim.stop()
        self.anim.setStartValue(self._morph_progress)
        self.anim.setEndValue(0.0)
        self.anim.start()
        
    def on_anim_finished(self):
        if self._is_closing:
            self.hide()
            self.finished.emit()
        elif self._advanced_mode:
            # Animation finished opening in advanced mode -> Fade in content
            if hasattr(self, 'content_anim'):
                self.content_anim.start()
    
    def adjust_temp(self, delta: float):
        """Adjust temperature by delta."""
        new_val = self._value + delta
        new_val = max(self._min_temp, min(self._max_temp, new_val))
        if new_val != self._value:
            self._value = new_val
            self.update()
            self.value_changed.emit(self._value)
    
    def mousePressEvent(self, event):
        """Handle button clicks."""
        pos = event.pos()
        
        if self._btn_close.contains(pos):
            self.close_morph()
        elif self._btn_minus_click.contains(pos):
            self.adjust_temp(-self._step)
        elif self._btn_plus_click.contains(pos):
            self.adjust_temp(self._step)
            
        # Check Advanced Controls
        if self._advanced_mode:
            for rect_btn, mode in self._mode_btns:
                if rect_btn.contains(pos):
                    self._current_hvac_mode = mode
                    self.mode_changed.emit(mode)
                    self.update()
                    return
            
            for rect_btn, mode in self._fan_btns:
                if rect_btn.contains(pos):
                    self._current_fan_mode = mode
                    self.fan_changed.emit(mode)
                    self.update()
                    return
            
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        
        # Background
        painter.setBrush(self._base_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 12, 12)
        
        # Draw Rainbow Border (Spin) if animating
        # Draw Rainbow Border (Spin) if animating
        if self.anim_border.state() == QPropertyAnimation.State.Running:
            if self._border_effect == 'Rainbow':
                self._draw_rainbow_border(painter, rect)
            elif self._border_effect == 'Aurora Borealis':
                self._draw_aurora_border(painter, rect)
        
        # Reset opacity
        painter.setOpacity(1.0)
        
        # Content alpha based on morph progress
        base_alpha = int(255 * (self._morph_progress if not self._is_closing else self._morph_progress))
        
        # If advanced mode, content ignores morph progress and waits for content_opacity
        if self._advanced_mode:
             alpha = int(base_alpha * self._content_opacity)
        else:
             alpha = base_alpha
             
        if alpha < 10:
            return  # Don't draw content if too faded
        
        # === Apple-like "Control Pill" Design ===
        
        # 1. Close Button (Top Right)
        close_size = 20
        self._btn_close = QRect(rect.width() - close_size - 12, 8, close_size, close_size)
        painter.setFont(get_mdi_font(18))
        painter.setPen(QColor(255, 255, 255, int(alpha * 0.5)))
        painter.drawText(self._btn_close, Qt.AlignmentFlag.AlignCenter, get_icon('close'))
        
        # 2. Header / Title (Top Left)
        # Small, uppercase, subtle
        if self._advanced_mode:
             title_rect = QRect(20, 8, rect.width() - 80, 20)
             painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
             painter.setPen(QColor(255, 255, 255, int(alpha * 0.4)))
             painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        else:
             # Standard centered title for simple mode
             painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
             painter.setPen(QColor(255, 255, 255, int(alpha * 0.5)))
             painter.drawText(QRect(0, 14, rect.width(), 16), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._text)

        # 3. Main Control Pill (Centered)
        # Layout:  ( - )   22.5°   ( + )
        
        # 3. Main Control Pill (Centered)
        # Layout:  ( - )   22.5°   ( + )
        
        center_y = 42 # Shifted up to avoid collision with Mode row (Y=78)
        if not self._advanced_mode: 
            center_y = rect.height() // 2 + 10
            
        
        btn_radius = 11 # 22x22 buttons (Was 30x30)
        spacing = 20
        
        # Temp Value
        font_val = QFont("Segoe UI", 16, QFont.Weight.Light) # Was 20.
        painter.setFont(font_val)
        fm = painter.fontMetrics()
        val_str = f"{self._value:.1f}°"
        text_w = fm.horizontalAdvance(val_str)
        text_h = fm.height()
        
        text_rect = QRect(0, 0, text_w + 10, text_h)
        text_rect.moveCenter(QPoint(rect.center().x(), center_y))
        
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, val_str)
        
        # Minus Button (Left of text)
        btn_x_minus = text_rect.left() - spacing - btn_radius
        self._btn_minus_center = QPoint(btn_x_minus, center_y)
        self._btn_minus_click = QRect(btn_x_minus - btn_radius, center_y - btn_radius, btn_radius*2, btn_radius*2)
        
        # Plus Button (Right of text)
        btn_x_plus = text_rect.right() + spacing + btn_radius
        self._btn_plus_center = QPoint(btn_x_plus, center_y)
        self._btn_plus_click = QRect(btn_x_plus - btn_radius, center_y - btn_radius, btn_radius*2, btn_radius*2)
        
        # Draw Buttons
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(get_mdi_font(12)) # Was 16
        
        
        # Minus
        # Soft background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(66, 133, 244, int(alpha * 0.8))) # Blue
        painter.drawEllipse(self._btn_minus_center, btn_radius, btn_radius)
        # Icon
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(self._btn_minus_click, Qt.AlignmentFlag.AlignCenter, get_icon('minus'))
        
        # Plus
        # Soft background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(234, 67, 53, int(alpha * 0.8))) # Red
        painter.drawEllipse(self._btn_plus_center, btn_radius, btn_radius)
        # Icon
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(self._btn_plus_click, Qt.AlignmentFlag.AlignCenter, get_icon('plus'))
        
        # === Advanced UI ===
        # Fade in using separate opacity
        if self._advanced_mode and self._content_opacity > 0.01:
             advanced_alpha = int(alpha * self._content_opacity)
             self._draw_advanced_controls(painter, rect, advanced_alpha)

    def _draw_advanced_controls(self, painter, rect, alpha):
        """Render HVAC Mode and Fan Speed controls."""
        self._mode_btns = []
        self._fan_btns = []
        
        # Ensure we have modes
        modes = self._hvac_modes or ['off', 'heat', 'cool']
        fan_modes = self._fan_modes or ['auto', 'low', 'high']
        
        # 1. HVAC Modes (Row 1) - Y = 65
        # Map modes to MDI icon names
        mode_icons = {
            'cool': 'snowflake',
            'heat': 'fire',
            'off': 'power',
            'auto': 'thermostat-auto', # or 'brightness-auto'
            'dry': 'water-percent',
            'fan_only': 'fan',
            'heat_cool': 'sun-snowflake-variant'
        }
        
        icon_size = 32
        spacing = 12
        spacing_sm = 8
        
        
        y_pos_1 = 78 # Was 60. Shifted down to clear the Control Pill.
        
        # Label
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, int(alpha * 0.4)))
        painter.drawText(QRect(20, y_pos_1, 60, icon_size), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "MODE")
        
        # Icons
        start_x = 80
        # Use MDI Font
        painter.setFont(get_mdi_font(20)) # Slightly larger icon font
        
        for i, mode in enumerate(modes):
            x = start_x + (i * (icon_size + spacing_sm))
            # Don't draw if out of bounds
            if x + icon_size > rect.width(): break
            
            btn_rect = QRect(x, y_pos_1, icon_size, icon_size)
            self._mode_btns.append((btn_rect, mode))
            
            is_active = (mode == self._current_hvac_mode)
            if is_active:
                painter.setBrush(QColor(255, 255, 255, 40))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(btn_rect, 6, 6)
            
            # Get icon char
            icon_name = mode_icons.get(mode, 'help-circle-outline')
            icon_char = get_icon(icon_name)
            
            painter.setPen(QColor(255, 255, 255, alpha if is_active else int(alpha * 0.5)))
            painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, icon_char)

        # 2. Fan Modes (Row 2) - Y = 110 -> Shift to 105 or 110? With H=168: 60+32=92. 168-32-margin.
        # Temp ends at 50. Mode: 60-92. Fan: 110-142. Spacing seems ok.
        fan_map = {
            'low': '1', 'medium': '2', 'high': '3',
            'mid': '2', 'middle': '2', 'min': '1', 'max': 'Max'
        }
        
        y_pos_2 = 122 # Was 110. Shifted down to spacing from Mode row.
        
        # Label
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, int(alpha * 0.4)))
        painter.drawText(QRect(20, y_pos_2, 60, icon_size), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "FAN")
        
        for i, mode in enumerate(fan_modes):
            x = start_x + (i * (icon_size + spacing_sm))
            if x + icon_size > rect.width(): break
            
            btn_rect = QRect(x, y_pos_2, icon_size, icon_size)
            self._fan_btns.append((btn_rect, mode))
            
            is_active = (mode == self._current_fan_mode)
            if is_active:
                painter.setBrush(QColor(255, 255, 255, 40))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(btn_rect, 6, 6)
            
            # Content: Icon (Auto) or Text (Numbers)
            mode_lower = mode.lower()
            if mode_lower == 'auto':
                 painter.setFont(get_mdi_font(20))
                 icon_char = get_icon('fan-auto')
                 painter.setPen(QColor(255, 255, 255, alpha if is_active else int(alpha * 0.5)))
                 painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, icon_char)
            else:
                 # Try to map to number, or use capitalized first letter/text
                 text = fan_map.get(mode_lower)
                 if not text:
                     # Try to see if it's already a number or specific string
                     if mode.isdigit():
                         text = mode
                     else:
                         # Fallback: Check for "speed 1" etc?
                         # Just use 1st char if not mapped? Or full text if short?
                         # User requested numbers. Let's try to infer or fallback to index?
                         # Simple map is safest. Fallback to Capitalized.
                         text = mode_lower.capitalize() if len(mode) > 3 else mode.upper()
                         
                 # Draw Text
                 painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
                 painter.setPen(QColor(255, 255, 255, alpha if is_active else int(alpha * 0.5)))
                 painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, text)

    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.update()

    def _draw_rainbow_border(self, painter, rect):
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        self._draw_gradient_border(painter, rect, colors)

    def _draw_aurora_border(self, painter, rect):
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        self._draw_gradient_border(painter, rect, colors)

    def _draw_gradient_border(self, painter, rect, colors):
        angle = self._border_progress * 360.0 * 1.5
        
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)

        gradient = QConicalGradient(QPointF(rect.center()), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(2) 
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        border_rect = QRectF(rect).adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(border_rect, 12, 12)


class Dashboard(QWidget):
    """Main dashboard popup widget with dynamic grid."""
    
    button_clicked = pyqtSignal(dict)  # Button config
    add_button_clicked = pyqtSignal(int)  # Slot index
    buttons_reordered = pyqtSignal(int, int) # (source, target)
    edit_button_requested = pyqtSignal(int) 
    clear_button_requested = pyqtSignal(int)
    rows_changed = pyqtSignal()  # Emitted after row count changes and UI rebuilds
    # Signal for when settings button is clicked
    settings_clicked = pyqtSignal()
    # Signal for image fetch requests (entity_id, url, access_token)
    image_fetch_requested = pyqtSignal(str, str, str)
    # Signals for visibility changes (for camera refresh control)
    dashboard_shown = pyqtSignal()
    dashboard_hidden = pyqtSignal()
    
    def __init__(self, config: dict, theme_manager=None, input_manager=None, version: str = "Unknown", rows: int = 2, parent=None):
        super().__init__(parent)
        self.config = config
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        self.version = version
        self._rows = rows
        self.buttons: list[DashboardButton] = []
        self._button_configs: list[dict] = []
        self._entity_states: dict = {} # Map entity_id -> full state dict
        self._entity_buttons: dict = {}  # Map entity_id -> button (for O(1) lookup)
        
        # Entrance Animation
        self._anim_progress = 0.0
        self.anim = QPropertyAnimation(self, b"anim_progress")
        self.anim.setDuration(1500)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.finished.connect(self._on_anim_finished)
        
        # Border Animation (Decoupled from entrance)
        self._border_progress = 0.0
        self.border_anim = QPropertyAnimation(self, b"glow_progress")
        self.border_anim.setDuration(1500) # Slower, elegant spin
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self.dimmer_overlay = DimmerOverlay(self)
        self.dimmer_overlay.value_changed.connect(self.on_dimmer_value_changed)
        self.dimmer_overlay.finished.connect(self.on_dimmer_finished)
        self.dimmer_overlay.morph_changed.connect(self.on_morph_changed)
        
        # Climate overlay
        self.climate_overlay = ClimateOverlay(self)
        self.climate_overlay.value_changed.connect(self.on_climate_value_changed)
        self.climate_overlay.mode_changed.connect(self.on_climate_mode_changed)
        self.climate_overlay.fan_changed.connect(self.on_climate_fan_changed)
        self.climate_overlay.finished.connect(self.on_climate_finished)
        self.climate_overlay.morph_changed.connect(self.on_climate_morph_changed)
        
        # Throttling
        self._last_dimmer_call = 0
        self._pending_dimmer_val = None
        self._active_dimmer_entity = None
        self.dimmer_timer = QTimer(self)
        self.dimmer_timer.setInterval(100) # 100ms throttle
        self.dimmer_timer.timeout.connect(self.process_pending_dimmer)
        
        # Climate throttling
        self._last_climate_call = 0
        self._pending_climate_val = None
        self._active_climate_entity = None
        self.climate_timer = QTimer(self)
        self.climate_timer.setInterval(500)  # 500ms throttle for climate
        self.climate_timer.timeout.connect(self.process_pending_climate)
        
        self._border_effect = 'Rainbow' # Default border effect
        
        self.setup_ui()
        
        # View switching (Grid vs Settings)
        self._current_view = 'grid'  # 'grid' or 'settings'
        self._grid_height = None  # Will be set after first show
        self._fixed_width = 428  # Fixed width to maintain
        
        
        # Height animation (Custom Timer Loop for smooth sync)
        self._anim_start_height = 0
        self._anim_target_height = 0
        self._anim_start_time = 0
        self._anim_duration = 0.25
        self._anchor_bottom_y = 0
        
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(16) # ~60 FPS
        self._animation_timer.timeout.connect(self._on_animation_frame)
        
        # SettingsWidget (created lazily to avoid circular import at module load)
        self.settings_widget = None
        
        if self.theme_manager:
            theme_manager.theme_changed.connect(self.on_theme_changed)

        # Window Height Animation
        self._anim_height = 0
        self.height_anim = QPropertyAnimation(self, b"anim_height")
        self.height_anim.setDuration(400)
        self.height_anim.setEasingCurve(QEasingCurve.Type.OutBack) # Slight bounce
            
    def get_anim_height(self):
        return self.height()
        
    def set_anim_height(self, h):
        h = int(h)
        # Anchor to bottom if we have captured an anchor point
        if hasattr(self, '_resize_anchor_y'):
             new_y = self._resize_anchor_y - h
             self.setGeometry(self.x(), new_y, self.width(), h)
        else:
             self.setFixedSize(self.width(), h)
        
    anim_height = pyqtProperty(float, get_anim_height, set_anim_height)

    def set_rows(self, rows: int):
        """Set number of rows and rebuild grid."""
        if self._rows != rows:
            # FIX: If we're currently showing settings, defer the rebuild until
            # the hide_settings animation completes.
            if self._current_view == 'settings':
                self._pending_rows = rows
                return
            
            self._do_set_rows(rows)
    
    def _do_set_rows(self, rows: int):
        """Update grid rows dynamically."""
        old_rows = self._rows
        self._rows = rows
        
        # Calculate new height
        grid_h = (self._rows * 80) + ((self._rows - 1) * 8)
        extras = 78 # Margins + footer
        new_height = grid_h + extras
        
        # Animate resize
        start_h = self.height()
        if start_h != new_height:
            # Capture bottom anchor for animation
            self._resize_anchor_y = self.y() + self.height()
            
            self.height_anim.stop()
            self.height_anim.setStartValue(float(start_h))
            self.height_anim.setEndValue(float(new_height))
            self.height_anim.start()
        
        # Update grid items
        current_slots = old_rows * 4
        new_slots = rows * 4
        
        if new_slots > current_slots:
            # Add buttons
            for i in range(current_slots, new_slots):
                row = i // 4
                col = i % 4
                button = DashboardButton(slot=i, theme_manager=self.theme_manager)
                button.clicked.connect(lambda cfg, idx=i: self._on_button_clicked(idx, cfg))
                button.dropped.connect(self.on_button_dropped)
                button.edit_requested.connect(self.edit_button_requested)
                button.clear_requested.connect(self.clear_button_requested)
                button.dimmer_requested.connect(self.start_dimmer)
                button.climate_requested.connect(self.start_climate)
                self.grid.addWidget(button, row, col)
                self.buttons.append(button)
        elif new_slots < current_slots:
            # Remove buttons
            for i in range(current_slots - 1, new_slots - 1, -1):
                button = self.buttons.pop()
                self.grid.removeWidget(button)
                button.deleteLater()
                
        # Update configs for all buttons to match new slot count
        if self._button_configs:
            self.set_buttons(self._button_configs)
            
        # Update styling
        self.update_style()
        
        # Store grid height
        self._grid_height = new_height
        
        self.rows_changed.emit()
    
    def setup_ui(self):
        """Setup the dashboard UI."""
        # Reset layout if exists (not clean, but works for refresh)
        if self.layout():
             QWidget().setLayout(self.layout())

        # Frameless window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Clear existing buttons
        self.buttons.clear()
        
        # Container
        existing_container = self.findChild(QFrame, "dashboardContainer")
        if existing_container:
            existing_container.deleteLater()
            
        self.container = QFrame(self)
        self.container.setObjectName("dashboardContainer")
        
        # Root layout for Window
        if not self.layout():
            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(10, 10, 10, 10)
        else:
            root_layout = self.layout()
            while root_layout.count():
                child = root_layout.takeAt(0)
                if child.widget(): child.widget().deleteLater()
        
        root_layout.addWidget(self.container)
        
        # Container Layout (Stack + Footer)
        content_layout = QVBoxLayout(self.container)
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked Widget for switching views (Grid / Settings)
        from PyQt6.QtWidgets import QStackedWidget
        self.stack_widget = QStackedWidget()
        content_layout.addWidget(self.stack_widget)
        
        # 1. Main Grid
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(12, 12, 12, 8)
        
        # FIX: Wrap Grid in ScrollArea for smooth animation
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setStyleSheet("background: transparent;")
        self.grid_scroll.setWidget(self.grid_widget)
        
        self.stack_widget.addWidget(self.grid_scroll)
        
        # Create grid buttons
        total_slots = self._rows * 4
        for i in range(total_slots):
            row = i // 4
            col = i % 4
            button = DashboardButton(slot=i, theme_manager=self.theme_manager)
            button.clicked.connect(lambda cfg, idx=i: self._on_button_clicked(idx, cfg))
            button.dropped.connect(self.on_button_dropped)
            button.edit_requested.connect(self.edit_button_requested)
            button.clear_requested.connect(self.clear_button_requested)
            button.dimmer_requested.connect(self.start_dimmer)
            button.climate_requested.connect(self.start_climate)
            self.grid.addWidget(button, row, col)
            self.buttons.append(button)
            
        # 2. Footer
        self.footer_widget = QWidget()
        
        # Note: Footer fade-in animation logic is handled dynamically in 
        # _fade_in_footer() to avoid "wrapped C/C++ object deleted" crashes.
        
        footer_layout = QHBoxLayout(self.footer_widget)
        footer_layout.setSpacing(8)
        footer_layout.setContentsMargins(12, 0, 12, 12)
        
        # Calc standard button width (approx)
        # Layout: 428 total width. Container inner: 408.
        # Grid margins: 12 left, 12 right -> 384 for buttons.
        # 4 buttons + 3 spaces (8px) -> 384 - 24 = 360. 360/4 = 90px per button.
        # Footer buttons: 2 buttons. Width should cover 2 grid buttons + spacing.
        # Width = 90 + 8 + 90 = 188px.
        # Height = 1/3 of 80px = ~26px.
        
        btn_width = 188
        btn_height = 26
        
        # Left Button (Home Assistant)
        self.btn_left = QPushButton("  HOME ASSISTANT") # Add space for spacing
        self.btn_left.setFixedSize(btn_width, btn_height)
        self.btn_left.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_left.clicked.connect(self.open_ha)
        
        # Create Custom HA Icon
        # Official Blue: #41bdf5
        # White Glyph
        ha_icon_char = get_icon("home-assistant")
        ha_pixmap = QPixmap(32, 32)
        ha_pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(ha_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Blue Rounded Rect
        painter.setBrush(QColor("#41BDF5"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 32, 32, 6, 6) # 6px radius for 32px is nice
        
        # 2. White Glyph
        painter.setFont(get_mdi_font(20))
        painter.setPen(QColor("white"))
        painter.drawText(ha_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ha_icon_char)
        painter.end()
        
        self.btn_left.setIcon(QIcon(ha_pixmap))
        self.btn_left.setIconSize(QSize(15, 15)) # Slightly smaller than button height (26)
        
        self.btn_left.setStyleSheet("background: rgba(255,255,255,0.1); border: none; border-radius: 4px; color: #888;")
        footer_layout.addWidget(self.btn_left)
        
        # Right Button (Settings) - now calls show_settings directly
        self.btn_settings = QPushButton("SETTINGS")
        self.btn_settings.setFixedSize(btn_width, btn_height)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self.show_settings)
        # Style handled in update_style or inline for now
        self.btn_settings.setStyleSheet("background: rgba(255,255,255,0.1); border: none; border-radius: 4px; color: #888;")
        footer_layout.addWidget(self.btn_settings)
        
        content_layout.addWidget(self.footer_widget)
        
        # FIX: Force visibility and repaint on startup/rebuild
        self.footer_widget.show()
        self.repaint()
        QTimer.singleShot(50, self.update)
        
        # Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)
        
        self.update_style()
        
        # Size Calculation
        width = 428
        # Height: Grid rows*80 + (rows-1)*8 + Grid top(12) + Grid bot(8) + Footer(26) + Footer bot(12) + Root margins(20)
        # = (rows*80) + (rows-1)*8 + 12 + 8 + 26 + 12 + 20
        # = (rows*80) + (rows*8) - 8 + 78
        grid_h = (self._rows * 80) + ((self._rows - 1) * 8)
        extras = 12 + 8 + 26 + 12 + 20   # 78
        height = grid_h + extras
        self.setFixedSize(width, height)
    def open_ha(self):
        """Open Home Assistant in default browser."""
        ha_cfg = self.config.get('home_assistant', {})
        url = ha_cfg.get('url', '').strip()
        if url:
             QDesktopServices.openUrl(QUrl(url))
             self.hide()

    def update_style(self):
        """Update dashboard style based on theme."""
        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {
                'window': '#1e1e1e',
                'border': '#555555',
            }
        
        self.container.setStyleSheet(f"""
            QFrame#dashboardContainer {{
                background-color: {colors['window']};
                border: 1px solid {colors['border']};
                border-radius: 12px;
            }}
            QMenu {{
                background-color: #2b2b2b;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                background: transparent;
                padding: 6px 24px 6px 12px;
                color: #e0e0e0;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: #007aff;
                color: white;
            }}
        """)
        
        for button in self.buttons:
            button.update_style()
            
        # Style Footer Buttons
        if hasattr(self, 'btn_left'):
            # Use safe defaults if keys missing
            bg = colors.get('alternate_base', '#353535')
            text = colors.get('text', '#aaaaaa')
            accent = colors.get('accent', '#4285F4')
            
            btn_style = f"""
                QPushButton {{
                    background-color: {bg};
                    border: none;
                    border-radius: 4px;
                    color: {text};
                    font-family: "Segoe UI";
                    font-size: 11px;
                    font-weight: 600;
                    text-transform: uppercase;
                }}
                QPushButton:hover {{
                    background-color: {accent};
                    color: white;
                }}
            """
            self.btn_left.setStyleSheet(btn_style)
            self.btn_settings.setStyleSheet(btn_style)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        # print(f"DEBUG: KeyPress {event.key()} Mods: {event.modifiers()}")
        
        # 1. Custom Shortcuts (Highest Priority)
        for button in self.buttons:
            sc = button.config.get('custom_shortcut', {})
            if sc.get('enabled') and sc.get('value'):
                if self.matches_pynput_shortcut(event, sc.get('value')):
                    # print(f"DEBUG: Triggering custom shortcut match for button {button.slot}")
                    button.simulate_click()
                    event.accept()
                    return

        # 2. Global Shortcuts (Modifier + Number)
        # Check modifier
        modifier_map = {
            'Alt': Qt.KeyboardModifier.AltModifier,
            'Ctrl': Qt.KeyboardModifier.ControlModifier,
            'Shift': Qt.KeyboardModifier.ShiftModifier
        }
        
        shortcut_config = self.config.get('shortcut', {}) if self.config else {}
        target_mod_str = shortcut_config.get('modifier', 'Alt')
        
        should_process = False
        if target_mod_str == 'None':
             # Only process if NO modifiers are pressed to perfectly match 'None'
             if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                 should_process = True
        else:
            target_mod = modifier_map.get(target_mod_str)
            # Strict match? Or just "contains"?
            # User wants "Alt+1". If I press "Ctrl+Alt+1", shoud it work? 
            # Usually strict is better to avoid conflict with complex global shortcuts.
            # But the original code was: (event.modifiers() & target_mod)
            # This allows extra modifiers.
            if target_mod and (event.modifiers() & target_mod):
                should_process = True
        
        if should_process:
            key = event.key()
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                slot = key - Qt.Key.Key_1
                if 0 <= slot < len(self.buttons):
                    # Check if this button has custom shortcut enabled
                    # If so, GLOBAL shortcut should be ignored for THIS button
                    btn = self.buttons[slot]
                    sc = btn.config.get('custom_shortcut', {})
                    if sc.get('enabled'):
                        # print(f"DEBUG: Ignoring global shortcut for button {slot} due to custom override")
                        pass 
                    else:
                        btn.simulate_click()
                        event.accept()
                        return

        super().keyPressEvent(event)
        
    def matches_pynput_shortcut(self, event, shortcut_str: str) -> bool:
        """Check if QKeyEvent matches pynput shortcut string."""
        if not shortcut_str: return False
        
        parts = shortcut_str.split('+')
        
        # Check modifiers
        has_ctrl = '<ctrl>' in parts
        has_alt = '<alt>' in parts
        has_shift = '<shift>' in parts
        # has_cmd/win ignored for simplicity or added if needed
        
        modifiers = event.modifiers()
        
        if has_ctrl != bool(modifiers & Qt.KeyboardModifier.ControlModifier): return False
        if has_alt != bool(modifiers & Qt.KeyboardModifier.AltModifier): return False
        if has_shift != bool(modifiers & Qt.KeyboardModifier.ShiftModifier): return False
        
        # Check key
        # Extract the non-modifier part
        target_key = None
        for p in parts:
            if p not in ['<ctrl>', '<alt>', '<shift>', '<cmd>']:
                target_key = p
                break
        
        if not target_key: return False # Modifier only?
        
        # Normalize target_key (pynput format) vs event
        # pynput: 'a', '1', '<esc>', '<space>', '<f1>'
        
        # Handle special keys
        key = event.key()
        text = event.text().lower()
        
        # 1. Single character match (letters, numbers)
        if len(target_key) == 1:
            # Prefer text() match for characters to handle layouts, 
            # BUT text() might be empty if modifiers are held (e.g. Ctrl+A might give \x01)
            # So fallback to Key code mapping if needed.
            
            # Simple check:
            if text and text == target_key: return True
            
            # Fallback: Check key code for letters/digits if text is control char
            if key >= 32 and key <= 126: # Ascii range roughly
                try:
                    # Qt Key to char
                    if chr(key).lower() == target_key: return True
                except: pass
                
            return False

        # 2. Special keys (<esc>, <f1>, etc)
        # Strip <>
        if target_key.startswith('<') and target_key.endswith('>'):
            clean_key = target_key[1:-1].lower()
            
            # Map common keys
            map_special = {
                'esc': Qt.Key.Key_Escape,
                'space': Qt.Key.Key_Space,
                'enter': Qt.Key.Key_Return,
                'backspace': Qt.Key.Key_Backspace,
                'tab': Qt.Key.Key_Tab,
                'up': Qt.Key.Key_Up,
                'down': Qt.Key.Key_Down,
                'left': Qt.Key.Key_Left,
                'right': Qt.Key.Key_Right,
                'f1': Qt.Key.Key_F1, 'f2': Qt.Key.Key_F2, 'f3': Qt.Key.Key_F3, 'f4': Qt.Key.Key_F4,
                'f5': Qt.Key.Key_F5, 'f6': Qt.Key.Key_F6, 'f7': Qt.Key.Key_F7, 'f8': Qt.Key.Key_F8,
                'f9': Qt.Key.Key_F9, 'f10': Qt.Key.Key_F10, 'f11': Qt.Key.Key_F11, 'f12': Qt.Key.Key_F12,
                'delete': Qt.Key.Key_Delete,
                'home': Qt.Key.Key_Home,
                'end': Qt.Key.Key_End,
                'page_up': Qt.Key.Key_PageUp,
                'page_down': Qt.Key.Key_PageDown
            }
            
            if map_special.get(clean_key) == key:
                return True
                
        return False
    
    def set_buttons(self, configs: list[dict], appearance_config: dict = None):
        """Set button configurations."""
        self._button_configs = configs
        if appearance_config:
            self._live_dimming = appearance_config.get('live_dimming', True)
            self._border_effect = appearance_config.get('border_effect', 'Rainbow')
        
        # Build a set of slots that are "covered" by multi-slot buttons (square)
        covered_slots = set()
        for cfg in configs:
            slot = cfg.get('slot', -1)
            slots_span = cfg.get('slots', 1)
            if slots_span > 1:
                start_row = slot // 4
                start_col = slot % 4
                # Mark all slots covered by this square button
                for row_offset in range(slots_span):
                    for col_offset in range(slots_span):
                        if row_offset == 0 and col_offset == 0:
                            continue  # Skip the origin slot itself
                        covered_col = start_col + col_offset
                        covered_row = start_row + row_offset
                        if covered_col < 4:  # Stay within grid columns
                            covered_slots.add(covered_row * 4 + covered_col)
        
        # Clear and rebuild grid with proper spanning
        for button in self.buttons:
            self.grid.removeWidget(button)
        
        for i, button in enumerate(self.buttons):
            config = next(
                (c for c in configs if c.get('slot', -1) == i),
                None
            )
            
            # Reset state if entity changed
            new_entity = config.get('entity_id') if config else None
            old_entity = button.config.get('entity_id')
            if new_entity != old_entity:
                button._state = "off"
                button._value = ""
            
            button.config = config or {}
            
            # Handle slot spanning
            slots_span = config.get('slots', 1) if config else 1
            row = i // 4
            col = i % 4
            
            # Check if this slot is covered by another button
            if i in covered_slots:
                button.hide()
                button.set_slot_span(1)
            else:
                button.show()
                # Clamp span to not exceed grid boundaries
                max_col_span = 4 - col
                actual_span = min(slots_span, max_col_span)
                button.set_slot_span(actual_span)
                # Square: rowSpan = colSpan
                self.grid.addWidget(button, row, col, actual_span, actual_span)
            
            button.update_content()
            button.update_style()
            # Propagate effect
            button.set_border_effect(self._border_effect)
        
        # Rebuild entity -> button lookup for O(1) access
        self._entity_buttons.clear()
        for button in self.buttons:
            entity_id = button.config.get('entity_id')
            if entity_id:
                self._entity_buttons[entity_id] = button


    # (Duplicate methods removed)


    def set_effect(self, effect_name: str):
        """Set the active border effect."""
        self._effect = effect_name
        self.update()

    def paintEvent(self, event):
        """Paint overlay and effects."""
        # Only draw if animating and effect is active
        # Use border_anim state to control drawing duration
        if self.border_anim.state() == QPropertyAnimation.State.Running:
            if self._border_effect == 'Rainbow':
                self._draw_rainbow_border()
            elif self._border_effect == 'Aurora Borealis':
                self._draw_aurora_border()

    def _draw_aurora_border(self):
        """Draw the Aurora Borealis border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Angle (Slower spin for aurora?)
        angle = self._border_progress * 360.0 * 1.0 
        
        # Fade out
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Aurora Colors: Green -> Blue -> Purple -> Blue -> Green
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)

    def _draw_rainbow_border(self):
        """Draw the rainbow border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Angle
        angle = self._border_progress * 360.0 * 1.5
        
        # Fade out
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        # Use container geometry to ensure tight fit
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Colors - Google Brand Colors
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)
            
    def start_dimmer(self, slot: int, global_rect: QRect):
        """Start the dimmer morph sequence."""
        # Find config
        config = next((c for c in self._button_configs if c.get('slot') == slot), None)
        if not config: return
        
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_dimmer_entity = entity_id
        self._active_dimmer_type = config.get('type', 'switch')  # Track type for service call
        
        # Get start value based on state
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        current_val = 0
        
        # Look up full state for attributes
        state_obj = self._entity_states.get(entity_id, {})
        attrs = state_obj.get('attributes', {})
        
        if self._active_dimmer_type == 'curtain':
            # Check for specific position attribute
            pos = attrs.get('current_position')
            if pos is not None:
                current_val = int(pos)
            elif source_btn:
                # Fallback to binary state
                current_val = 100 if source_btn._state == "open" else 0
        else:
            # Check for brightness (0-255)
            bri = attrs.get('brightness')
            if bri is not None:
                current_val = int((bri / 255.0) * 100)
            elif source_btn:
                # Fallback to binary state
                current_val = 100 if source_btn._state == "on" else 0
        
        # Colors - always use dark base for overlay visibility
        base_color = QColor("#2d2d2d")
        
        # Use button's custom color if set, otherwise theme accent
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#FFD700")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#FFD700'))
            
        # Calculate geometries
        start_rect = self.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        # Target: Full row
        row_idx = slot // 4
        
        # Identify siblings for fading
        self._dimmer_siblings = []
        self._dimmer_source_btn = None  # Track source button separately
        
        row_buttons = [b for b in self.buttons if (b.slot // 4) == row_idx]
        
        # Sort by slot to get left-most and right-most
        row_buttons.sort(key=lambda b: b.slot)
        
        if row_buttons:
            first_btn = row_buttons[0]
            last_btn = row_buttons[-1]
            
            # Map their positions to Dashboard
            p1 = self.container.mapTo(self, first_btn.pos())
            p2 = self.container.mapTo(self, last_btn.pos())
            
            height = first_btn.height()
            width = (p2.x() + last_btn.width()) - p1.x()
            
            target_rect = QRect(p1.x(), p1.y(), width, height)
            
            # Sibling fading setup
            for btn in row_buttons:
                if btn.slot != slot:
                    self._dimmer_siblings.append(btn)
                else:
                    self._dimmer_source_btn = btn  # Store source
                    btn.set_faded(0.0)  # Hide source button immediately
            
            # Start!
            self.dimmer_overlay.set_border_effect(self._border_effect)
            self.dimmer_overlay.start_morph(
                start_rect, 
                target_rect, 
                current_val, 
                config.get('label', 'Dimmer'),
                color=accent_color,
                base_color=base_color
            )
            
            self.dimmer_timer.start()

    def on_morph_changed(self, progress: float):
        """Update sibling opacity during morph."""
        opacity = 1.0 - progress
        for btn in getattr(self, '_dimmer_siblings', []):
            btn.set_faded(opacity)

    def on_dimmer_value_changed(self, value):
        """Queue dimming request."""
        self._pending_dimmer_val = value

    def on_dimmer_finished(self):
        """Cleanup after dimmer closes."""
        self.dimmer_timer.stop()
        
        # For curtains, send the final position only on release
        dimmer_type = getattr(self, '_active_dimmer_type', 'switch')
        final_val = getattr(self, '_final_dimmer_val', None)
        
        if dimmer_type == 'curtain' and final_val is not None and self._active_dimmer_entity:
            # Send final curtain position
            self.button_clicked.emit({
                "service": "cover.set_cover_position",
                "entity_id": self._active_dimmer_entity,
                "service_data": {"position": final_val}
            })
        
        elif dimmer_type != 'curtain' and final_val is not None and self._active_dimmer_entity:
            # Send final brightness for lights (ensures update if live dimming was off)
            self.button_clicked.emit({
                "service": "light.turn_on",
                "entity_id": self._active_dimmer_entity,
                "service_data": {"brightness_pct": final_val},
                "skip_debounce": True
            })
        
        self._active_dimmer_entity = None
        self._active_dimmer_type = None
        self._pending_dimmer_val = None
        self._final_dimmer_val = None
        
        # Reset siblings
        for btn in getattr(self, '_dimmer_siblings', []):
            btn.set_faded(1.0)
        
        # Restore source button
        if getattr(self, '_dimmer_source_btn', None):
            self._dimmer_source_btn.set_faded(1.0)
            self._dimmer_source_btn = None
             
        self._dimmer_siblings = []
        self.activateWindow() # Reclaim focus

    def process_pending_dimmer(self):
        """Throttled service call."""
        if self._pending_dimmer_val is None or not self._active_dimmer_entity:
            return
            
        val = self._pending_dimmer_val
        self._pending_dimmer_val = None # Clear pending
        
        # Call appropriate service based on entity type
        dimmer_type = getattr(self, '_active_dimmer_type', 'switch')
        
        # Always store the latest value as the potential final value
        self._final_dimmer_val = val
        
        if dimmer_type == 'curtain':
            # Curtains: Only store value, send on release (in on_dimmer_finished)
            return
        
        # Check live dimming setting (only applies to lights)
        if not getattr(self, '_live_dimming', True):
            return

        # Lights use light.turn_on with brightness
        self.button_clicked.emit({
            "service": "light.turn_on",
            "entity_id": self._active_dimmer_entity,
            "service_data": {"brightness_pct": val},
            "skip_debounce": True
        })

    # ============ CLIMATE CONTROL ============
    
    climate_value_changed = pyqtSignal(str, float)  # (entity_id, temperature)
    
    def start_climate(self, slot: int, global_rect: QRect):
        """Start the climate morph sequence."""
        # Find config
        config = next((c for c in self._button_configs if c.get('slot') == slot), None)
        if not config: return
        
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_climate_entity = entity_id
        
        # Get current target temp from button value or default
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        current_val = 20.0  # Default
        if source_btn and source_btn._value:
            try:
                # Parse temperature from value string like "20.5°C"
                temp_str = source_btn._value.replace('°C', '').replace('°', '').strip()
                current_val = float(temp_str)
            except:
                pass
        
        # Colors - always use dark base for overlay visibility
        base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#EA4335")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#EA4335'))
            
        # Calculate geometries
        start_rect = self.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        # Target: Full row
        row_idx = slot // 4
        
        # Identify siblings for fading
        self._climate_siblings = []
        self._climate_source_btn = None  # Track source button separately
        
        row_buttons = [b for b in self.buttons if (b.slot // 4) == row_idx]
        row_buttons.sort(key=lambda b: b.slot)
        
        if row_buttons:
            first_btn = row_buttons[0]
            last_btn = row_buttons[-1]
            
            p1 = self.container.mapTo(self, first_btn.pos())
            p2 = self.container.mapTo(self, last_btn.pos())
            
            height = first_btn.height()
            width = (p2.x() + last_btn.width()) - p1.x()
            
            target_rect = QRect(p1.x(), p1.y(), width, height)
            
            # Sibling fading setup (EXCLUDE source button - like dimmer)
            # Check if advanced mode (expands to 2 rows)
            advanced_mode = config.get('advanced_mode', False)
            rows_to_fade = {row_idx}
            
            if advanced_mode:
                # Determine expansion direction (logic matches ClimateOverlay.start_morph)
                # Default: Expand Down (Next Row)
                # If Last Row: Expand Up (Prev Row)
                if row_idx < self._rows - 1:
                    rows_to_fade.add(row_idx + 1)
                elif row_idx > 0:
                    rows_to_fade.add(row_idx - 1)
            
            # Collect buttons from all affected rows
            for btn in self.buttons:
                btn_row = btn.slot // 4
                if btn_row in rows_to_fade:
                    if btn.slot != slot:
                        self._climate_siblings.append(btn)
                    else:
                        self._climate_source_btn = btn  # Store source
                        btn.set_faded(0.0)  # Hide source button immediately
            
            # Start!
            self.climate_overlay.set_border_effect(self._border_effect)
            
            # Lookup full state for advanced controls
            state_obj = self._entity_states.get(entity_id, {})
            
            self.climate_overlay.start_morph(
                start_rect, 
                target_rect, 
                current_val, 
                config.get('label', 'Climate'),
                color=accent_color,
                base_color=base_color,
                advanced_mode=config.get('advanced_mode', False),
                current_state=state_obj
            )
            
            self.climate_timer.start()

    def on_climate_morph_changed(self, progress: float):
        """Update sibling opacity during morph."""
        opacity = 1.0 - progress
        for btn in getattr(self, '_climate_siblings', []):
            btn.set_faded(opacity)

    def on_climate_value_changed(self, value: float):
        """Queue climate temperature request."""
        self._pending_climate_val = value

    def on_climate_mode_changed(self, mode: str):
        """Handle HVAC mode change (immediate)."""
        if not self._active_climate_entity: return
        
        self.button_clicked.emit({
            "service": "climate.set_hvac_mode",
            "entity_id": self._active_climate_entity,
            "service_data": {"hvac_mode": mode}
        })
        
    def on_climate_fan_changed(self, mode: str):
        """Handle Fan mode change (immediate)."""
        if not self._active_climate_entity: return
        
        self.button_clicked.emit({
            "service": "climate.set_fan_mode",
            "entity_id": self._active_climate_entity,
            "service_data": {"fan_mode": mode}
        })

    def on_climate_finished(self):
        """Cleanup after climate closes."""
        self.climate_timer.stop()
        self._active_climate_entity = None
        self._pending_climate_val = None
        
        # Reset siblings
        for btn in getattr(self, '_climate_siblings', []):
             btn.set_faded(1.0)
        
        # Restore source button
        if getattr(self, '_climate_source_btn', None):
            self._climate_source_btn.set_faded(1.0)
            self._climate_source_btn = None
             
        self._climate_siblings = []
        self.activateWindow()

    def process_pending_climate(self):
        """Throttled climate service call."""
        if self._pending_climate_val is None or not self._active_climate_entity:
            return
            
        val = self._pending_climate_val
        self._pending_climate_val = None
        
        # Emit signal for main.py to handle
        self.climate_value_changed.emit(self._active_climate_entity, val)

    def update_entity_state(self, entity_id: str, state: dict):
        """Update a button/widget when entity state changes."""
        self._entity_states[entity_id] = state
        
        for button in self.buttons:
            if button.config.get('entity_id') == entity_id:
                btn_type = button.config.get('type', 'switch')
                
                if btn_type == 'widget':
                    # Update sensor value
                    value = state.get('state', '--')
                    unit = state.get('attributes', {}).get('unit_of_measurement', '')
                    button.set_value(f"{value}{unit}")
                elif btn_type == 'climate':
                    # Update climate target temperature
                    attrs = state.get('attributes', {})
                    temp = attrs.get('temperature', '--')
                    if temp != '--':
                        button.set_value(f"{temp}°C")
                    else:
                        button.set_value("--°C")
                    # Also update state for styling
                    hvac_action = state.get('state', 'off')
                    button.set_state('on' if hvac_action not in ['off', 'unavailable'] else 'off')
                elif btn_type == 'curtain':
                    # Update curtain state (open/closed/opening/closing)
                    cover_state = state.get('state', 'closed')
                    # "open" when cover is up/open, anything else is closed
                    button.set_state('open' if cover_state == 'open' else 'closed')
                elif btn_type == 'image':
                    # Update entity state value (shown in label)
                    entity_state = state.get('state', '')
                    if entity_state and entity_state not in ['unavailable', 'unknown']:
                        button.set_value(entity_state)
                    
                    # Update image from entity_picture attribute
                    attrs = state.get('attributes', {})
                    entity_picture = attrs.get('entity_picture', '')
                    access_token = attrs.get('access_token', '')
                    
                    if entity_picture:
                        # Build URL with access token if available
                        if access_token and not entity_picture.startswith('data:'):
                            # Store URL and token for fetching
                            button._image_url = entity_picture
                            button._image_access_token = access_token
                            # Emit signal to fetch image (handled by main.py)
                            self.image_fetch_requested.emit(
                                button.config.get('entity_id', ''),
                                entity_picture,
                                access_token
                            )
                        elif entity_picture.startswith('data:'):
                            # Direct base64 data
                            button.set_image(entity_picture)
                        else:
                            # URL without token - try to fetch directly
                            button._image_url = entity_picture
                            self.image_fetch_requested.emit(
                                button.config.get('entity_id', ''),
                                entity_picture,
                                ''
                            )
                    else:
                        button.set_image('')
                elif btn_type == 'camera':
                    # Camera stream - fetch image and set up auto-refresh
                    attrs = state.get('attributes', {})
                    entity_picture = attrs.get('entity_picture', '')
                    access_token = attrs.get('access_token', '')
                    
                    if entity_picture:
                        # Store for refresh
                        button._image_url = entity_picture
                        button._image_access_token = access_token
                        
                        # Emit signal to fetch image
                        self.image_fetch_requested.emit(
                            button.config.get('entity_id', ''),
                            entity_picture,
                            access_token
                        )
                    else:
                        button.set_image('')
                else:
                    # Update switch state
                    button.set_state(state.get('state', 'off'))
    
    def update_button_image(self, entity_id: str, base64_data: str):
        """Update a button's image from fetched base64 data."""
        button = self._entity_buttons.get(entity_id)
        if button and button.config.get('type') in ('image', 'camera'):
            button.set_image(base64_data)
    
    def _on_button_clicked(self, slot: int, config: dict):
        """Handle button click."""
        if not config:
            self.add_button_clicked.emit(slot)
        else:
            self.button_clicked.emit(config)

    def on_button_dropped(self, source: int, target: int):
        self.buttons_reordered.emit(source, target)
    
    def on_theme_changed(self, theme: str):
        self.update_style()
    
    def show_near_tray(self):
        """Position and show the dashboard near the system tray."""
        screen = QApplication.primaryScreen()
        if not screen:
            self.show()
            return
        
        screen_rect = screen.availableGeometry()
        
        # Calculate target position but don't move there yet
        target_x = screen_rect.right() - self.width() - 10
        target_y = screen_rect.bottom() - self.height() - 10
        
        self._target_pos = QPoint(target_x, target_y)
        
        # Ensure we are visible before animating
        super().show()
        self.activateWindow()
        
        # Start Entrance Animation
        self.anim.stop()
        self.anim.setDuration(250) # Fast, snappy
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        
        # Start Border Animation (Independent)
        self.border_anim.stop()
        self.border_anim.setStartValue(0.0)
        self.border_anim.setEndValue(1.0)
        self.border_anim.start()
    
    def toggle(self):
        if self.isVisible() and self.windowOpacity() > 0.1:
            self.close_animated()
        else:
            self.show_near_tray()
            self.dashboard_shown.emit()
    
    def close_animated(self):
        """Fade out and slide down, then hide."""
        self.anim.stop()
        self.border_anim.stop() # Stop the glow too
        
        # Recalculate target position from current window position
        self._target_pos = QPoint(self.x(), self.y())
        
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self.anim.setStartValue(self._anim_progress)
        self.anim.setEndValue(0.0)
        self.anim.start()
        
    def _on_anim_finished(self):
        """Handle animation completion (hide if closing)."""
        # Robust check for near-zero
        if self._anim_progress < 0.01:
            super().hide()
            self.dashboard_hidden.emit()

    def focusOutEvent(self, event):
        # We rely on changeEvent for robust window-level focus loss
        # but focusOutEvent is still good for some edge cases
        super().focusOutEvent(event)
    
    def changeEvent(self, event):
        """Handle window activation changes."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                # Window lost focus? Close it.
                # Use small delay to allow for things like dialogs or transient windows
                QTimer.singleShot(100, self._check_hide)
        super().changeEvent(event)
    
    def _check_hide(self):
        # If we are not the active window, close.
        if not self.isActiveWindow():
            self.close_animated()
            
    def get_anim_progress(self):
        return self._anim_progress
        
    def set_anim_progress(self, val):
        self._anim_progress = val
        
        # 1. Opacity
        self.setWindowOpacity(val)
        
        # 2. Slide Up (Entrance) / Slide Down (Exit)
        # Offset: 0 at 1.0 (open), +20 at 0.0 (closed)
        if hasattr(self, '_target_pos'):
            offset = int((1.0 - val) * 20)
            self.move(self._target_pos.x(), self._target_pos.y() + offset)
            
        self.update() # Trigger repaint for border effects
        
    anim_progress = pyqtProperty(float, get_anim_progress, set_anim_progress)

    def get_glow_progress(self):
        return self._border_progress
        
    @pyqtSlot(float)
    def set_glow_progress(self, val):
        self._border_progress = val
        self.update() 
        
    glow_progress = pyqtProperty(float, get_glow_progress, set_glow_progress)

    def showEvent(self, event):
        """Standard show event."""
        super().showEvent(event)
        # We handle animation in show_near_tray usually, but for safety:
        self.activateWindow()
        self.setFocus()
    
    # ============ VIEW SWITCHING (Grid <-> Settings) ============
    
    def _init_settings_widget(self, config: dict, input_manager=None):
        """Initialize the SettingsWidget (call from main.py after Dashboard creation)."""
        # Store for re-initialization after set_rows() rebuilds UI
        self._settings_config = config
        self._settings_input_manager = input_manager
        
        # IMPORT Settings Widget
        from settings_widget import SettingsWidget
        
        self.settings_widget = SettingsWidget(config, self.theme_manager, input_manager, self.version, self)
        self.settings_widget.back_requested.connect(self.hide_settings)
        self.settings_widget.settings_saved.connect(self._on_settings_saved)
        
        # Wrap in ScrollArea for smooth animation (avoids squashing)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Transparent background
        self.settings_scroll.setStyleSheet("background: transparent;")
        # Disable wheel scrolling - content should fit
        self.settings_scroll.wheelEvent = lambda e: e.ignore()
        self.settings_scroll.setWidget(self.settings_widget)
        
        # Add ScrollArea to stack (index 1)
        self.stack_widget.addWidget(self.settings_scroll)
        # Ensure grid SCROLL is visible
        self.stack_widget.setCurrentWidget(self.grid_scroll)
        
        # Clear cached height so it re-calculates with new settings widget
        self._cached_settings_height = None

        # Init Button Editor (Embedded)
        try:
            from button_edit_widget import ButtonEditWidget
            # Create a placeholder instance to be ready
            self.edit_widget = ButtonEditWidget([], theme_manager=self.theme_manager, input_manager=self.input_manager, parent=self)
            self.edit_widget.saved.connect(self._on_edit_saved)
            self.edit_widget.cancelled.connect(self._on_edit_cancelled)
            
            self.edit_scroll = QScrollArea()
            self.edit_scroll.setWidget(self.edit_widget)
            self.edit_scroll.setWidgetResizable(True)
            self.edit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.edit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.edit_scroll.setStyleSheet("background: transparent; border: none;")
            self.edit_scroll.setFrameShape(QFrame.Shape.NoFrame)
            # Disable wheel scrolling - content should fit
            self.edit_scroll.wheelEvent = lambda e: e.ignore()
            
            self.stack_widget.addWidget(self.edit_scroll)
        except ImportError:
            print("Could not import ButtonEditWidget")

    def _on_settings_saved(self, config: dict):
        """Handle settings saved - emit signal and return to grid."""
        if self.settings_widget:
            self.settings_widget.set_opacity(1.0) # Reset in case
            
        # Update local config immediately for visual feedback
        app = config.get('appearance', {})
        self._border_effect = app.get('border_effect', 'Rainbow')
        self._live_dimming = app.get('live_dimming', True)
        
        # Propagate to buttons
        for btn in self.buttons:
            btn.set_border_effect(self._border_effect)
        
        self.settings_saved.emit(config)
        self.hide_settings()
            
    def _on_edit_saved(self, config: dict):
        """Handle save from embedded editor."""
        # Find existing button config to update or append?
        # The main app handles actual saving, we just bubble up
        # BUT we need to close the view
        self.transition_to('grid')
        self.edit_button_saved.emit(config)
        
    def _on_edit_cancelled(self):
        self.transition_to('grid')
        
    edit_button_saved = pyqtSignal(dict) # Signals back to main
    
    def show_edit_button(self, slot: int, config: dict = None, entities: list = None):
        """Open the embedded button editor."""
        if self._current_view == 'edit_button': return
        
        # Update the widget content
        self.edit_widget.slot = slot
        self.edit_widget.config = config or {}
        self.edit_widget.entities = entities or []
        # IMPORTANT: Populate entities FIRST, then load config so entity_id can be selected
        self.edit_widget.populate_entities()
        self.edit_widget.load_config()
        
        # Transition
        self.transition_to('edit_button')

    settings_saved = pyqtSignal(dict)
    
    def _calculate_view_height(self, view_name: str) -> int:
        """Calculate target height for a given view."""
        if view_name == 'grid':
            # Use current height if available, or calculate from rows
            if self._grid_height:
                return self._grid_height
            # Fallback
            return (self._rows * 80) + ((self._rows - 1) * 8)
            
        elif view_name == 'settings':
            # Calculate dynamic settings height
            if self.settings_widget:
                # Always recalculate for accurate sizing
                content_h = self.settings_widget.get_content_height()
                settings_height = content_h + 30  # Small padding for container margins
                
                # Clamp against screen height
                screen = QApplication.primaryScreen()
                if screen:
                    max_h = screen.availableGeometry().height() * 0.9
                    settings_height = max(300, min(settings_height, int(max_h)))
                else:
                    settings_height = max(300, min(settings_height, 800))
                    
                return settings_height
            return 450
            
        elif view_name == 'edit_button':
            # Calculate dynamic editor height
            if hasattr(self, 'edit_widget'):
                content_h = self.edit_widget.get_content_height()
                # Add small padding for container margins
                h = content_h + 30
                return max(300, min(h, 600))
            return 400
            
        # Default fallback for unknown views
        return 400

    def _lock_view_sizes(self, target_view: str, target_height: int):
        """Lock widget sizes before animation to prevent jitter."""
        width = self.container.width() if self.container.width() > 0 else (self._fixed_width - 20)
        
        if target_view == 'settings':
            if self.settings_widget:
                self.settings_widget.setFixedSize(width, target_height)
        
        elif target_view == 'edit_button':
            if hasattr(self, 'edit_widget'):
                self.edit_widget.setFixedSize(width, target_height)
                
        elif target_view == 'grid':
            # Lock Grid Widget size to true grid height
            true_grid_h = getattr(self, '_captured_grid_widget_h', None)
            if not true_grid_h:
                true_grid_h = (self._rows * 80) + ((self._rows - 1) * 8)
            self.grid_widget.setFixedSize(width, true_grid_h)

    def transition_to(self, view_name: str):
        """
        Generic method to transition between views with smooth animation.
        view_name: 'grid', 'settings', 'edit_button', etc.
        """
        if self._current_view == view_name:
            return

        # 1. Capture state before transition
        if self._current_view == 'grid':
            self._grid_height = self.height()
            self._captured_grid_widget_h = self.grid_widget.height()
            
        # 2. Update view state
        self._current_view = view_name
        
        # 3. Calculate heights
        start_height = self.height()
        target_height = self._calculate_view_height(view_name)
        
        # 4. Prepare Animation
        self._anim_start_height = start_height
        self._anim_target_height = target_height
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.25
        self._anchor_bottom_y = self.geometry().y() + self.height()
        
        # 5. Handle Footer Visibility
        if view_name == 'grid':
            # Footer will be shown/faded-in after animation in _on_transition_done
            pass
        else:
            self.footer_widget.hide()
            
        # 6. Button Opacity (if leaving grid)
        if view_name != 'grid':
             # Optional: fade out buttons
             pass 
        else:
            # Returning to grid: restore opacity
            for btn in self.buttons:
                btn.set_faded(1.0)

        # 7. Unlock Window Constraints
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        
        # 8. Switch Stack & Lock Content
        self._lock_view_sizes(view_name, target_height)
        
        if view_name == 'settings':
            self.stack_widget.setCurrentWidget(self.settings_scroll)
        elif view_name == 'grid':
            self.stack_widget.setCurrentWidget(self.grid_scroll)
        elif view_name == 'edit_button':
            if hasattr(self, 'edit_scroll'):
                self.stack_widget.setCurrentWidget(self.edit_scroll)
            
        # 9. Start Animation
        self._animation_timer.start()

    def show_settings(self):
        """Morph from Grid view to Settings view."""
        self.transition_to('settings')
    
    def hide_settings(self):
        """Morph from Settings view back to Grid view."""
        self.transition_to('grid')

    def _on_animation_frame(self):
        """Custom high-precision animation loop."""
        now = time.perf_counter()
        elapsed = now - self._anim_start_time
        progress = min(1.0, elapsed / self._anim_duration)
        
        # Cubic Ease Out: 1 - pow(1 - x, 3)
        t = 1.0 - pow(1.0 - progress, 3)
        
        # Calculate current height
        current_h = int(self._anim_start_height + (self._anim_target_height - self._anim_start_height) * t)
        
        # Update Geometry
        # Anchor to bottom: new_y = bottom - new_height
        new_y = self._anchor_bottom_y - current_h
        current_x = self.x()
        
        # Single atomic update
        self.setGeometry(current_x, new_y, self._fixed_width, current_h)
        
        if progress >= 1.0:
            self._animation_timer.stop()
            if self._current_view == 'grid':
                # Special handling for returning to grid
                pass
            
            self._on_transition_done()

    def _fade_in_footer(self):
        """Fade in footer with dynamic effect creation to prevent crashes."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        
        # Create FRESH effect and animation each time
        effect = QGraphicsOpacityEffect(self.footer_widget)
        effect.setOpacity(0.0)
        self.footer_widget.setGraphicsEffect(effect)
        
        # Store refs to prevent garbage collection during anim
        self._current_footer_effect = effect
        self._current_footer_anim = QPropertyAnimation(effect, b"opacity")
        self._current_footer_anim.setDuration(300)
        self._current_footer_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._current_footer_anim.setStartValue(0.0)
        self._current_footer_anim.setEndValue(1.0)
        
        # Cleanup on finish
        self._current_footer_anim.finished.connect(self._on_footer_fade_finished)
        
        self.footer_widget.show()
        self._current_footer_anim.start()

    def _on_footer_fade_finished(self):
        """Remove opacity effect after fade-in to save resources/prevent bugs."""
        self.footer_widget.setGraphicsEffect(None)
        # Clear references
        self._current_footer_effect = None
        self._current_footer_anim = None
    
    def _on_transition_done(self):
        """After transition (morph), restore styles and cleanup."""
        try:
            # Not actually using QPropertyAnimation for window, so this might be old code
            # But just in case
            if hasattr(self, 'height_anim') and self.height_anim:
                 self.height_anim.finished.disconnect(self._on_transition_done)
        except:
            pass
            
        # Unlock grid size so it behaves normally (if we are in grid view)
        if self._current_view == 'grid':
            self.grid_widget.setMinimumSize(0, 0)
            self.grid_widget.setMaximumSize(16777215, 16777215)
        
            # FIX: Process pending row change if any (deferred from set_rows)
            pending = getattr(self, '_pending_rows', None)
            if pending is not None:
                self._pending_rows = None
                self._do_set_rows(pending)
                # Show footer after rebuild (with fade-in)
                self._fade_in_footer()
                
                # After rebuild, reposition
                self._reposition_after_morph()
                return
        
        # Re-lock the window to its final size
        # Use target height from animation vars or calculate fresh
        t_height = self._grid_height if self._current_view == 'grid' else self._anim_target_height
        
        # Safety fallback
        if not t_height: t_height = self.height()
            
        self.setFixedSize(self._fixed_width, int(t_height))
        
        # Show footer now that animation is complete (with fade-in) -- ONLY IF GRID
        if self._current_view == 'grid':
            self._fade_in_footer()
        
        # Reposition window to bottom-right corner
        self._reposition_after_morph()
    
    def _reposition_after_morph(self):
        """Reposition window to keep it anchored to bottom-right."""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_rect = screen.availableGeometry()
        x = screen_rect.right() - self.width() - 10
        y = screen_rect.bottom() - self.height() - 10
        self.move(x, y)

