"""
Visual Effects module for Dashboard.
Contains border drawing and background capture logic.
"""

from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QConicalGradient
)
from PyQt6.QtCore import Qt, QRectF, QPoint
from PyQt6.QtWidgets import QApplication

def draw_aurora_border(painter: QPainter, rect: QRectF, progress: float, width: float = 3):
    """Draw the Aurora Borealis border effect."""
    if not painter.isActive():
        painter.begin(painter.device())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    angle = progress * 360.0 * 1.0

    opacity = 1.0
    if progress > 0.8:
        opacity = (1.0 - progress) / 0.2
    painter.setOpacity(opacity)

    colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]

    gradient = QConicalGradient(rect.center(), angle)
    for i, color in enumerate(colors):
        gradient.setColorAt(i / (len(colors) - 1), QColor(color))

    pen = QPen()
    pen.setWidthF(width)
    pen.setBrush(QBrush(gradient))

    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 12, 12)

def draw_rainbow_border(painter: QPainter, rect: QRectF, progress: float, width: float = 3):
    """Draw the rainbow border effect."""
    if not painter.isActive():
        painter.begin(painter.device())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    angle = progress * 360.0 * 1.5

    opacity = 1.0
    if progress > 0.8:
        opacity = (1.0 - progress) / 0.2
    painter.setOpacity(opacity)

    colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]

    gradient = QConicalGradient(rect.center(), angle)
    for i, color in enumerate(colors):
        gradient.setColorAt(i / (len(colors) - 1), QColor(color))

    pen = QPen()
    pen.setWidthF(width)
    pen.setBrush(QBrush(gradient))

    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 12, 12)

def draw_prism_shard_border(painter: QPainter, rect: QRectF, progress: float, width: float = 3):
    """Draw the Prism Shard border effect."""
    if not painter.isActive():
        painter.begin(painter.device())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    angle = progress * 360.0 * 0.9

    opacity = 1.0
    if progress > 0.8:
        opacity = (1.0 - progress) / 0.2
    painter.setOpacity(opacity)

    colors = ["#26C6DA", "#EC407A", "#FFCA28", "#CFD8DC", "#26C6DA"]

    gradient = QConicalGradient(rect.center(), angle)
    for i, color in enumerate(colors):
        gradient.setColorAt(i / (len(colors) - 1), QColor(color))

    pen = QPen()
    pen.setWidthF(width)
    pen.setBrush(QBrush(gradient))

    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 12, 12)

def draw_liquid_mercury_border(painter: QPainter, rect: QRectF, progress: float, width: float = 3):
    """Draw the Liquid Mercury border effect."""
    if not painter.isActive():
        painter.begin(painter.device())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    angle = progress * 360.0 * 1.2

    opacity = 1.0
    if progress > 0.8:
        opacity = (1.0 - progress) / 0.2
    painter.setOpacity(opacity)

    colors = ["#37474F", "#78909C", "#CFD8DC", "#ECEFF1", "#CFD8DC", "#78909C", "#37474F"]

    gradient = QConicalGradient(rect.center(), angle)
    for i, color in enumerate(colors):
        gradient.setColorAt(i / (len(colors) - 1), QColor(color))

    pen = QPen()
    pen.setWidthF(width)
    pen.setBrush(QBrush(gradient))

    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 12, 12)

def capture_glass_background(target_widget):
    """Capture and blur the desktop area behind the window for frosted glass."""
    screen = QApplication.primaryScreen()
    if not screen:
        return None, None
        
    c_x = target_widget.container.x()
    c_w = target_widget.container.width()
    
    screen_geo = screen.geometry()
    
    # Capture the entire vertical column for this window to avoid needing
    # to recapture when animating height changes
    grab_x = target_widget.x() + c_x
    grab_y = screen_geo.y()
    grab_w = c_w
    grab_h = screen_geo.height()
    
    # Safety: ensure valid dimensions
    if grab_w <= 0: grab_w = target_widget.width() - 20
    if grab_w <= 0: grab_w = 100 # Fallback
    if grab_h <= 0: grab_h = 1080 # Fallback
    
    # Grab the screen region
    desktop_pixmap = screen.grabWindow(0, int(grab_x), int(grab_y), int(grab_w), int(grab_h))
    
    if desktop_pixmap.isNull():
        return None, None
        
    # Apply downscale -> upscale blur
    blur_factor = 0.06  # Very heavy blur
    small = desktop_pixmap.scaled(
        max(1, int(grab_w * blur_factor)),
        max(1, int(grab_h * blur_factor)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )
    blurred = small.scaled(
        int(grab_w), int(grab_h),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )
    
    return blurred, QPoint(int(grab_x), int(grab_y))
