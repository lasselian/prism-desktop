"""
EffectComboBox Widget
A QComboBox that supports animated border effects (Rainbow, Aurora Borealis).
"""

from PyQt6.QtWidgets import QComboBox
from PyQt6.QtCore import pyqtProperty, QPropertyAnimation, QEasingCurve, QRectF, QTimer
from PyQt6.QtGui import QPainter
from ui.widgets.dashboard_button_painter import DashboardButtonPainter

class EffectComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._effect = "None"
        self._border_progress = 0.0
        self._border_opacity = 0.0
        
        # Infinite looping animation
        self.anim = QPropertyAnimation(self, b"border_progress")
        self.anim.setDuration(2000) # 2 seconds per rotation
        self.anim.setLoopCount(-1) # Infinite loop
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        
        # Fade out animation
        self.fade_anim = QPropertyAnimation(self, b"border_opacity")
        self.fade_anim.setDuration(1000)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.fade_anim.finished.connect(self._on_fade_finished)
        
        # Timer to trigger fade out
        self.display_timer = QTimer(self)
        self.display_timer.setSingleShot(True)
        self.display_timer.setInterval(800) # Visible for 1.5 seconds
        self.display_timer.timeout.connect(self._start_fade_out)
        
    def _start_fade_out(self):
        self.fade_anim.setStartValue(1.0)
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.start()
        
    def _on_fade_finished(self):
        if self._border_opacity == 0.0:
            self.anim.stop()
        
    def get_border_progress(self):
        return self._border_progress
        
    def set_border_progress(self, val):
        self._border_progress = val
        self.update() # Trigger repaint
        
    border_progress = pyqtProperty(float, get_border_progress, set_border_progress)

    def get_border_opacity(self):
        return self._border_opacity
        
    def set_border_opacity(self, val):
        self._border_opacity = val
        self.update()
        
    border_opacity = pyqtProperty(float, get_border_opacity, set_border_opacity)
    
    def set_effect(self, effect_name: str, animate: bool = True):
        """Set the active border effect."""
        self._effect = effect_name
        
        # Stop pending actions
        self.display_timer.stop()
        self.fade_anim.stop()
        
        if effect_name in ["Rainbow", "Aurora Borealis", "Prism Shard", "Liquid Mercury"]:
            if animate:
                self._border_opacity = 1.0
                if self.anim.state() != QPropertyAnimation.State.Running:
                    self.anim.start()
                # Start timer to fade out
                self.display_timer.start()
            else:
                # Silent update (no animation, hidden state)
                self._border_opacity = 0.0
                self.anim.stop()
        else:
            self.anim.stop()
            self._border_opacity = 0.0
            self.update()

    def paintEvent(self, event):
        # 1. Draw standard combobox first
        super().paintEvent(event)
        
        # 2. Draw border overlay if active and visible
        if self._effect == "None" or self._border_opacity <= 0.0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._border_opacity)
        
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        angle = self._border_progress * 360.0
        
        if self._effect == "Rainbow":
            DashboardButtonPainter.draw_rainbow_border(painter, rect, angle)
        elif self._effect == "Aurora Borealis":
            DashboardButtonPainter.draw_aurora_border(painter, rect, angle)
        elif self._effect == "Prism Shard":
            speed = 0.9
            DashboardButtonPainter.draw_prism_shard_border(painter, rect, angle * speed)
        elif self._effect == "Liquid Mercury":
            speed = 1.2
            DashboardButtonPainter.draw_liquid_mercury_border(painter, rect, angle * speed)
