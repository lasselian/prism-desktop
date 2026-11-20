from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import (
    Qt,
    QRectF,
    QPointF,
    pyqtSignal,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QEasingCurve,
    pyqtProperty,
)
from PyQt6.QtGui import QPainter, QColor
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.constants import PAGE_INDICATOR_WIDTH, FOOTER_HEIGHT


class PageIndicatorButton(QPushButton):
    """Footer button showing page indicator dots. Scroll or click to change page."""

    page_scrolled = pyqtSignal(int)  # delta: +1 / -1
    page_jumped = pyqtSignal(int)  # absolute page index from click

    # Dot appearance
    ACTIVE_DIAMETER = 8
    INACTIVE_DIAMETER = 6
    DOT_SPACING = 14  # center-to-center
    ACTIVE_OPACITY = 255
    INACTIVE_OPACITY = 90  # ~35%

    button_style = 'Gradient'

    def __init__(self, page_count: int = 3, current_page: int = 0, parent=None):
        super().__init__(parent)
        self._page_count = page_count
        self._current_page = current_page
        self._is_light = False
        self._bounce_offset = 0.0

        self.setFixedSize(PAGE_INDICATOR_WIDTH, FOOTER_HEIGHT)
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Bounce: quick rise then settle back with a slight overshoot
        self._anim_up = QPropertyAnimation(self, b"bounce_offset")
        self._anim_up.setDuration(110)
        self._anim_up.setStartValue(0.0)
        self._anim_up.setEndValue(-3.5)
        self._anim_up.setEasingCurve(QEasingCurve.Type.OutQuad)

        self._anim_down = QPropertyAnimation(self, b"bounce_offset")
        self._anim_down.setDuration(280)
        self._anim_down.setStartValue(-3.5)
        self._anim_down.setEndValue(0.0)
        self._anim_down.setEasingCurve(QEasingCurve.Type.OutBounce)

        self._bounce_anim = QSequentialAnimationGroup(self)
        self._bounce_anim.addAnimation(self._anim_up)
        self._bounce_anim.addAnimation(self._anim_down)

    # ── Bounce property ──────────────────────────────────────────────

    def get_bounce_offset(self) -> float:
        return self._bounce_offset

    def set_bounce_offset(self, val: float) -> None:
        self._bounce_offset = val
        self.update()

    bounce_offset = pyqtProperty(float, get_bounce_offset, set_bounce_offset)

    # ── Public API ───────────────────────────────────────────────────

    def set_page(self, index: int) -> None:
        self._current_page = index
        self._trigger_bounce()
        self.update()

    def set_page_count(self, n: int) -> None:
        self._page_count = n
        self.update()

    def set_light_mode(self, is_light: bool) -> None:
        if self._is_light != is_light:
            self._is_light = is_light
            self.update()

    # ── Events ───────────────────────────────────────────────────────

    def _trigger_bounce(self) -> None:
        self._bounce_anim.stop()
        self.set_bounce_offset(0.0)
        self._bounce_anim.start()

    def enterEvent(self, event):
        self._trigger_bounce()
        super().enterEvent(event)

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._page_count < 1:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw button background manually (avoids Qt's hover highlight)
        painter.setBrush(self.palette().button())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(self.rect()), 4, 4)

        w = self.width()
        h = self.height()
        cy = h / 2

        total_span = (self._page_count - 1) * self.DOT_SPACING
        start_x = (w - total_span) / 2

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(self._page_count):
            cx = start_x + i * self.DOT_SPACING
            is_active = i == self._current_page

            diameter = self.ACTIVE_DIAMETER if is_active else self.INACTIVE_DIAMETER
            opacity = self.ACTIVE_OPACITY if is_active else self.INACTIVE_OPACITY
            radius = diameter / 2

            dot_cy = cy + (self._bounce_offset if is_active else 0.0)

            color = QColor(0, 0, 0, opacity) if self._is_light else QColor(255, 255, 255, opacity)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, dot_cy), radius, radius)

        DashboardButtonPainter.draw_button_bevel_edge(
            painter,
            QRectF(self.rect()),
            intensity_modifier=0.25,
            corner_radius=4,
        )

        painter.end()

    # ── Hit testing ──────────────────────────────────────────────────

    def _dot_index_at(self, x: float) -> int:
        """Return the dot index at position x, or -1 if none."""
        if self._page_count < 1:
            return -1
        total_span = (self._page_count - 1) * self.DOT_SPACING
        start_x = (self.width() - total_span) / 2
        hit_radius = self.DOT_SPACING / 2
        for i in range(self._page_count):
            cx = start_x + i * self.DOT_SPACING
            if abs(x - cx) <= hit_radius:
                return i
        return -1

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._dot_index_at(event.position().x())
            if idx >= 0 and idx != self._current_page:
                self.page_jumped.emit(idx)
                event.accept()
                return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.page_scrolled.emit(-1)
        elif delta < 0:
            self.page_scrolled.emit(1)
        event.accept()
