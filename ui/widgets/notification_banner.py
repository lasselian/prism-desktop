"""
In-app notification banner that replaces native QMessageBox dialogs.
A standalone floating window that appears above/below the dashboard.
Supports toast (auto-dismiss) and confirm (Yes/No) modes.
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, pyqtProperty, QTimer, QPropertyAnimation, QEasingCurve, QRectF, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont, QPainterPath, QPen
from core.utils import SYSTEM_FONT
from core.localization_manager import t
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.constants import BANNER_HEIGHT, BANNER_VERTICAL_MARGIN, GRID_MARGIN_LEFT, GRID_MARGIN_RIGHT, ROOT_MARGIN
from ui.visuals.dashboard_effects import (
    draw_aurora_border, draw_rainbow_border, draw_prism_shard_border, draw_liquid_mercury_border
)


GAP = 5  # px gap between dashboard edge and banner


class _CountdownButton(QPushButton):
    """QPushButton that draws a depleting arc around its border as a countdown."""

    def __init__(self, *args, is_light=False, corner_radius=4, x_icon=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._countdown = 0.0
        self._is_light = is_light
        self._corner_radius = corner_radius
        self._x_icon = x_icon

    def get_countdown(self):
        return self._countdown

    def set_countdown(self, val):
        self._countdown = val
        self.update()

    countdown = pyqtProperty(float, get_countdown, set_countdown)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._x_icon:
            xp = QPainter(self)
            xp.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(255, 255, 255, 185), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            xp.setPen(pen)
            r = QRectF(self.rect())
            pad = r.width() * 0.35
            inner = r.adjusted(pad, pad, -pad, -pad)
            xp.drawLine(inner.topLeft(), inner.bottomRight())
            xp.drawLine(inner.topRight(), inner.bottomLeft())
            xp.end()
        if self._countdown <= 0.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen_w = 2.0
        rect = QRectF(self.rect()).adjusted(pen_w / 2, pen_w / 2, -pen_w / 2, -pen_w / 2)
        r = self._corner_radius

        # Build the full border path and measure it
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        total = path.length()
        if total <= 0:
            painter.end()
            return

        drawn = total * self._countdown
        color = QColor(0, 0, 0, 110) if self._is_light else QColor(255, 255, 255, 150)
        pen = QPen(color, pen_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        # Dash pattern: show `drawn` px then skip the rest (values in pen-width units)
        pen.setDashPattern([drawn / pen_w, max(0.001, (total - drawn) / pen_w)])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class NotificationBanner(QWidget):
    """Floating notification banner that sits just outside the dashboard window."""

    confirmed = pyqtSignal()
    rejected = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, message: str, banner_type: str = "toast", auto_dismiss_ms: int = 4000,
                 button_style: str = "Gradient", border_effect: str = "None",
                 text_color: str = "#ffffff", glass_ui: bool = False,
                 glass_is_light: bool = False):
        super().__init__(None)
        self.banner_type = banner_type
        self._auto_dismiss_ms = auto_dismiss_ms
        self._border_effect = border_effect
        self._slide_origin = 0
        self._target_y = 0
        self._glass_ui = glass_ui
        self._glass_is_light = glass_is_light
        self._glass_pixmap = None

        # Border effect animation (same pattern as dashboard)
        self._border_progress = 0.0
        self.border_anim = QPropertyAnimation(self, b"glow_progress")
        self.border_anim.setDuration(2000)
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Window flags — frameless, on top, no taskbar entry
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._is_light = glass_is_light  # used for non-glass styling too
        self._hovered = False  # once hovered, disable auto-dismiss

        # Auto-dismiss timer (stoppable)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._on_dismiss)

        # Countdown arc animation — wired up in show_at once the button exists
        self._timer_anim = None

        # Slide + fade animation
        self._anim_progress = 0.0
        self._slide_anim = QPropertyAnimation(self, b"windowOpacity")
        self._slide_anim.setDuration(250)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._pos_anim = QPropertyAnimation(self, b"pos")
        self._pos_anim.setDuration(250)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            GRID_MARGIN_LEFT + ROOT_MARGIN,
            BANNER_VERTICAL_MARGIN,
            GRID_MARGIN_RIGHT + ROOT_MARGIN,
            BANNER_VERTICAL_MARGIN,
        )
        layout.setSpacing(8)

        # Message label
        self.label = QLabel(message)
        self.label.setFont(QFont(SYSTEM_FONT, 11))
        self.label.setStyleSheet(f"color: {text_color}; background: transparent;")
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        if glass_is_light:
            btn_style = (
                "QPushButton {"
                "  background: rgba(0,0,0,0.08);"
                "  border: none;"
                "  border-radius: 4px;"
                "  color: #555;"
                "  padding: 2px 10px;"
                f"  font-family: '{SYSTEM_FONT}';"
                "  font-size: 10px;"
                "}"
                "QPushButton:hover {"
                "  background: rgba(0,0,0,0.15);"
                "}"
            )
        else:
            btn_style = (
                "QPushButton {"
                "  background: rgba(255,255,255,0.12);"
                "  border: none;"
                "  border-radius: 4px;"
                "  color: #ccc;"
                "  padding: 2px 10px;"
                f"  font-family: '{SYSTEM_FONT}';"
                "  font-size: 10px;"
                "}"
                "QPushButton:hover {"
                "  background: rgba(255,255,255,0.2);"
                "}"
            )

        if banner_type == "confirm":
            btn_h = int((BANNER_HEIGHT - 8) * 0.75)
            use_gradient = button_style == "Gradient"

            if glass_is_light:
                # Slightly desaturated for light backgrounds
                if use_gradient:
                    yes_bg       = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(68,160,68),stop:1 rgb(48,135,48))")
                    yes_bg_hover = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(80,180,80),stop:1 rgb(60,155,60))")
                    no_bg        = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(185,58,58),stop:1 rgb(155,42,42))")
                    no_bg_hover  = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(210,68,68),stop:1 rgb(178,50,50))")
                else:
                    yes_bg       = "rgb(52,148,52)"
                    yes_bg_hover = "rgb(65,170,65)"
                    no_bg        = "rgb(168,52,52)"
                    no_bg_hover  = "rgb(195,62,62)"
            else:
                if use_gradient:
                    yes_bg       = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(75,175,75),stop:1 rgb(50,145,50))")
                    yes_bg_hover = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(90,200,90),stop:1 rgb(65,165,65))")
                    no_bg        = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(195,65,65),stop:1 rgb(160,45,45))")
                    no_bg_hover  = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                    "stop:0 rgb(220,75,75),stop:1 rgb(185,55,55))")
                else:
                    yes_bg       = "rgb(55,155,55)"
                    yes_bg_hover = "rgb(70,180,70)"
                    no_bg        = "rgb(175,55,55)"
                    no_bg_hover  = "rgb(205,65,65)"

            yes_style = (
                f"QPushButton {{ background: {yes_bg}; border: none; border-radius: 6px;"
                f"  color: #fff; padding: 2px 14px;"
                f"  font-family: '{SYSTEM_FONT}'; font-size: 11px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: {yes_bg_hover}; }}"
            )
            self.btn_yes = QPushButton(t("notification_banner.yes"))
            self.btn_yes.setFixedHeight(btn_h)
            self.btn_yes.setMinimumWidth(54)
            self.btn_yes.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_yes.setStyleSheet(yes_style)
            self.btn_yes.clicked.connect(self._on_confirm)
            layout.addWidget(self.btn_yes)

            no_style = (
                f"QPushButton {{ background: {no_bg}; border: none; border-radius: 6px;"
                f"  color: #fff; padding: 2px 14px;"
                f"  font-family: '{SYSTEM_FONT}'; font-size: 11px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: {no_bg_hover}; }}"
            )
            self.btn_no = _CountdownButton(t("notification_banner.no"), is_light=glass_is_light, corner_radius=6)
            self.btn_no.setFixedHeight(btn_h)
            self.btn_no.setMinimumWidth(54)
            self.btn_no.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_no.setStyleSheet(no_style)
            self.btn_no.clicked.connect(self._on_reject)
            layout.addWidget(self.btn_no)
            self._countdown_btn = self.btn_no
        else:
            btn_h = int((BANNER_HEIGHT - 8) * 0.75)
            close_style = (
                f"QPushButton {{ background: rgb(175,55,55); border: none; border-radius: 4px; }}"
                f"QPushButton:hover {{ background: rgb(205,65,65); }}"
            )
            self.btn_close = _CountdownButton("", is_light=glass_is_light, corner_radius=4, x_icon=True)
            self.btn_close.setFixedSize(btn_h, btn_h)
            self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_close.setStyleSheet(close_style)
            self.btn_close.clicked.connect(self._on_dismiss)
            layout.addWidget(self.btn_close)
            self._countdown_btn = self.btn_close

    def set_text(self, message: str):
        """Update the banner text in-place without recreating the widget."""
        self.label.setText(message)

    def _compute_content_height(self, width: int) -> int:
        """Explicitly compute the banner's pixel height from its content.

        Qt's layout engine gives wrong results for a word-wrapped QLabel
        inside a QHBoxLayout — sizeHint() reflects single-line text and
        doesn't integrate with heightForWidth() for layout purposes.  So
        we compute the height ourselves using the label's heightForWidth()
        (which *is* reliable) and the known button dimensions.
        """
        btn_h = int((BANNER_HEIGHT - 8) * 0.75)
        h_margin = (GRID_MARGIN_LEFT + ROOT_MARGIN) + (GRID_MARGIN_RIGHT + ROOT_MARGIN)
        spacing = 8  # QHBoxLayout spacing

        if self.banner_type == "confirm":
            # Make sure stylesheet-derived sizeHints are current.
            self.btn_yes.ensurePolished()
            self.btn_no.ensurePolished()
            yes_w = max(self.btn_yes.sizeHint().width(), self.btn_yes.minimumWidth())
            no_w = max(self.btn_no.sizeHint().width(), self.btn_no.minimumWidth())
            # label + spacing + yes + spacing + no
            button_block = yes_w + spacing + no_w + spacing
        else:
            # label + spacing + close button (fixed btn_h × btn_h)
            button_block = btn_h + spacing

        label_width = max(50, width - h_margin - button_block)

        # heightForWidth returns the wrapped pixel height for this width.
        self.label.ensurePolished()
        label_h = self.label.heightForWidth(label_width)
        if label_h <= 0:
            label_h = self.label.fontMetrics().height()

        content_h = max(label_h, btn_h)
        return content_h + BANNER_VERTICAL_MARGIN * 2

    def _capture_glass_background(self, x: int, y: int, w: int, h: int):
        """Capture and blur the desktop region that will sit behind the banner."""
        screen = QApplication.primaryScreen()
        if not screen or w <= 0 or h <= 0:
            return None
        pixmap = screen.grabWindow(0, x, y, w, h)
        if pixmap.isNull():
            return None
        blur_factor = 0.06
        small = pixmap.scaled(
            max(1, int(w * blur_factor)), max(1, int(h * blur_factor)),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return small.scaled(
            w, h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def show_at(self, x: int, width: int, container_edge_y: int, above: bool):
        """Position and show the banner with a slide+fade animation.

        The banner's height is computed explicitly from its content rather
        than read from Qt's layout (which is unreliable with word-wrapped
        labels in horizontal layouts).
        """
        self.ensurePolished()
        self.setFixedWidth(width)

        banner_h = self._compute_content_height(width)
        self.setFixedHeight(banner_h)

        if above:
            y = container_edge_y - banner_h - GAP
            slide_from_y = y + 8
        else:
            y = container_edge_y + GAP
            slide_from_y = y - 8

        self._target_y = y
        self._banner_x = x
        self._above = above

        # Capture glass background before the window appears so we grab clean desktop.
        if self._glass_ui:
            vis_x = x + ROOT_MARGIN
            vis_w = width - ROOT_MARGIN * 2
            vis_y = y + 2   # matches the 2 px top inset in paintEvent
            vis_h = banner_h - 4
            self._glass_pixmap = self._capture_glass_background(vis_x, vis_y, vis_w, vis_h)

        # Position invisible at slide-start, then animate in.
        self.move(x, slide_from_y)
        self.setWindowOpacity(0.0)
        self.show()

        self._pos_anim.setStartValue(QPoint(x, slide_from_y))
        self._pos_anim.setEndValue(QPoint(x, y))
        self._pos_anim.start()

        self._slide_anim.setStartValue(0.0)
        self._slide_anim.setEndValue(1.0)
        self._slide_anim.start()

        # Auto-dismiss timer + countdown arc (toast = dismiss, confirm = reject on timeout)
        if self._auto_dismiss_ms > 0:
            if self.banner_type == "confirm":
                self._dismiss_timer.timeout.disconnect(self._on_dismiss)
                self._dismiss_timer.timeout.connect(self._on_reject)
            self._dismiss_timer.start(self._auto_dismiss_ms)
            self._timer_anim = QPropertyAnimation(self._countdown_btn, b"countdown")
            self._timer_anim.setDuration(self._auto_dismiss_ms)
            self._timer_anim.setEasingCurve(QEasingCurve.Type.Linear)
            self._timer_anim.setStartValue(1.0)
            self._timer_anim.setEndValue(0.0)
            self._timer_anim.start()

        # Border effect animation
        if self._border_effect and self._border_effect != "None":
            self.border_anim.stop()
            self.border_anim.setStartValue(0.0)
            self.border_anim.setEndValue(1.0)
            self.border_anim.start()

    def get_glow_progress(self):
        return self._border_progress

    @pyqtSlot(float)
    def set_glow_progress(self, val):
        self._border_progress = val
        self.update()

    glow_progress = pyqtProperty(float, get_glow_progress, set_glow_progress)

    def enterEvent(self, event):
        """Pause auto-dismiss on hover; once hovered it won't auto-dismiss."""
        super().enterEvent(event)
        self._hovered = True
        self._dismiss_timer.stop()
        if self._timer_anim:
            self._timer_anim.stop()
            self._countdown_btn.set_countdown(0.0)

    def leaveEvent(self, event):
        """Leave does nothing — user must click X after hovering."""
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(
            ROOT_MARGIN, 2,
            -ROOT_MARGIN, -2,
        )

        # Background — glass blur+tint or solid dark
        painter.setPen(Qt.PenStyle.NoPen)
        if self._glass_ui and self._glass_pixmap:
            clip = QPainterPath()
            clip.addRoundedRect(rect, 8, 8)
            painter.setClipPath(clip)
            painter.drawPixmap(int(rect.x()), int(rect.y()), self._glass_pixmap)
            painter.setClipping(False)
            if self._glass_is_light:
                painter.setBrush(QColor(240, 240, 240, 120))
            else:
                painter.setBrush(QColor(20, 20, 20, 100))
            painter.drawRoundedRect(rect, 8, 8)
        else:
            if self._is_light:
                painter.setBrush(QColor(240, 240, 240, 255))
            else:
                painter.setBrush(QColor(40, 40, 40, 255))
            painter.drawRoundedRect(rect, 8, 8)

        DashboardButtonPainter.draw_image_edge_effects(
            painter, rect,
            is_light=self._is_light,
            corner_radius=8,
        )

        # Border effect (runs once on open, same as dashboard)
        if self.border_anim.state() == QPropertyAnimation.State.Running:
            painter.setOpacity(0.7)
            if self._border_effect == 'Rainbow':
                draw_rainbow_border(painter, rect, self._border_progress, width=1)
            elif self._border_effect == 'Aurora Borealis':
                draw_aurora_border(painter, rect, self._border_progress, width=1)
            elif self._border_effect == 'Prism Shard':
                draw_prism_shard_border(painter, rect, self._border_progress, width=1)
            elif self._border_effect == 'Liquid Mercury':
                draw_liquid_mercury_border(painter, rect, self._border_progress, width=1)

        painter.end()

    def _animate_out(self, callback):
        """Fade + slide away, then call callback."""
        self._dismiss_timer.stop()
        if self._timer_anim:
            self._timer_anim.stop()

        slide_to_y = self._target_y + (8 if self._above else -8)

        self._pos_anim.stop()
        self._pos_anim.setStartValue(QPoint(self._banner_x, self._target_y))
        self._pos_anim.setEndValue(QPoint(self._banner_x, slide_to_y))
        self._pos_anim.setDuration(220)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._pos_anim.start()

        self._slide_anim.stop()
        self._slide_anim.setStartValue(self.windowOpacity())
        self._slide_anim.setEndValue(0.0)
        self._slide_anim.setDuration(220)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._slide_anim.finished.connect(callback)
        self._slide_anim.start()

    def _on_confirm(self):
        self._animate_out(self.confirmed.emit)

    def _on_reject(self):
        self._animate_out(self.rejected.emit)

    def _on_dismiss(self):
        self._animate_out(self.dismissed.emit)
