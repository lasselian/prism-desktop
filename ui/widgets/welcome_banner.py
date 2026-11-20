"""
Multi-step welcome banner shown on first launch.
Visually identical to NotificationBanner — same floating position, slide+fade
animation, specular highlight, and glassmorphism support.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, pyqtProperty, QPropertyAnimation, QEasingCurve, QRectF, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont, QPainterPath, QPen

from core.utils import SYSTEM_FONT
from core.localization_manager import t
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.constants import BANNER_VERTICAL_MARGIN, GRID_MARGIN_LEFT, GRID_MARGIN_RIGHT, ROOT_MARGIN
from ui.visuals.dashboard_effects import (
    draw_aurora_border,
    draw_rainbow_border,
    draw_prism_shard_border,
    draw_liquid_mercury_border,
)

GAP = 5  # px gap between dashboard edge and banner


def _get_steps():
    return [
        (t("welcome.step_1.title"), t("welcome.step_1.body")),
        (t("welcome.step_2.title"), t("welcome.step_2.body")),
        (t("welcome.step_3.title"), t("welcome.step_3.body")),
        (t("welcome.step_4.title"), t("welcome.step_4.body")),
        (t("welcome.step_5.title"), t("welcome.step_5.body")),
    ]


class _DotsWidget(QWidget):
    """Row of step-indicator dots."""

    def __init__(self, count: int, is_light: bool = False, parent=None):
        super().__init__(parent)
        self._count = count
        self._step = 0
        self._is_light = is_light
        self.setFixedHeight(16)

    def set_step(self, step: int):
        self._step = step
        self.update()

    def sizeHint(self):
        from PyQt6.QtCore import QSize

        spacing = 10
        w = self._count * 6 + (self._count - 1) * (spacing - 6)
        return QSize(w, 16)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dia = 6
        spacing = 10
        total_w = self._count * dia + (self._count - 1) * (spacing - dia)
        x0 = (self.width() - total_w) // 2
        cy = self.height() // 2
        for i in range(self._count):
            cx = x0 + i * spacing + dia // 2
            if i == self._step:
                color = QColor(0, 0, 0, 200) if self._is_light else QColor(255, 255, 255, 220)
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                color = QColor(0, 0, 0, 80) if self._is_light else QColor(255, 255, 255, 90)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color, 1.2))
            painter.drawEllipse(QPoint(cx, cy), dia // 2, dia // 2)
        painter.end()


class WelcomeBanner(QWidget):
    """Multi-step onboarding banner, shown once on first launch."""

    finished = pyqtSignal()

    def __init__(
        self,
        *,
        button_style: str = "Gradient",
        border_effect: str = "None",
        text_color: str = "#ffffff",
        glass_ui: bool = False,
        glass_is_light: bool = False,
    ):
        super().__init__(None)

        self._border_effect = border_effect
        self._glass_ui = glass_ui
        self._glass_is_light = glass_is_light
        self._glass_pixmap = None
        self._is_light = glass_is_light
        self._step = 0
        self._steps = _get_steps()
        self._target_y = 0
        self._banner_x = 0
        self._above = True

        # Window flags — frameless, on top, no taskbar entry
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Border effect animation
        self._border_progress = 0.0
        self.border_anim = QPropertyAnimation(self, b"glow_progress")
        self.border_anim.setDuration(2000)
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Slide + fade animations
        self._slide_anim = QPropertyAnimation(self, b"windowOpacity")
        self._slide_anim.setDuration(250)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._pos_anim = QPropertyAnimation(self, b"pos")
        self._pos_anim.setDuration(250)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Button styles
        if glass_is_light:
            muted_style = (
                f"QPushButton {{ background: rgba(0,0,0,0.08); border: none; border-radius: 4px;"
                f"  color: #555; padding: 2px 10px; font-family: '{SYSTEM_FONT}'; font-size: 10px; }}"
                f"QPushButton:hover {{ background: rgba(0,0,0,0.15); }}"
            )
            next_bg = "qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgb(0,100,200),stop:1 rgb(0,75,165))"
            next_bg_hover = "qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgb(20,120,220),stop:1 rgb(0,95,185))"
        else:
            muted_style = (
                f"QPushButton {{ background: rgba(255,255,255,0.12); border: none; border-radius: 4px;"
                f"  color: #ccc; padding: 2px 10px; font-family: '{SYSTEM_FONT}'; font-size: 10px; }}"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.2); }}"
            )
            next_bg = "qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgb(30,120,215),stop:1 rgb(0,90,180))"
            next_bg_hover = "qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgb(50,140,235),stop:1 rgb(20,110,200))"

        next_style = (
            f"QPushButton {{ background: {next_bg}; border: none; border-radius: 4px;"
            f"  color: #fff; padding: 2px 14px; font-family: '{SYSTEM_FONT}'; font-size: 10px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {next_bg_hover}; }}"
        )

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            GRID_MARGIN_LEFT + ROOT_MARGIN,
            BANNER_VERTICAL_MARGIN,
            GRID_MARGIN_RIGHT + ROOT_MARGIN,
            BANNER_VERTICAL_MARGIN,
        )
        layout.setSpacing(5)

        # Title
        self.lbl_title = QLabel(self._steps[0][0])
        title_font = QFont(SYSTEM_FONT, 11)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.lbl_title.setFont(title_font)
        self.lbl_title.setStyleSheet(f"color: {text_color}; background: transparent;")
        layout.addWidget(self.lbl_title)

        # Body
        self.lbl_body = QLabel(self._steps[0][1])
        self.lbl_body.setFont(QFont(SYSTEM_FONT, 10))
        self.lbl_body.setStyleSheet(f"color: {text_color}; background: transparent;")
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.lbl_body)

        # Nav row
        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(8)
        nav_layout.setContentsMargins(0, 2, 0, 0)

        btn_h = 22

        self.btn_skip = QPushButton(t("welcome.btn_skip"))
        self.btn_skip.setFixedHeight(btn_h)
        self.btn_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skip.setStyleSheet(muted_style)
        self.btn_skip.clicked.connect(self._on_skip)
        nav_layout.addWidget(self.btn_skip)

        self.dot_widget = _DotsWidget(len(self._steps), is_light=glass_is_light)
        nav_layout.addWidget(self.dot_widget, 1)

        self.btn_next = QPushButton(t("welcome.btn_next"))
        self.btn_next.setFixedHeight(btn_h)
        self.btn_next.setMinimumWidth(54)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setStyleSheet(next_style)
        self.btn_next.clicked.connect(self._on_next)
        nav_layout.addWidget(self.btn_next)

        layout.addLayout(nav_layout)

    # ── Border effect property ───────────────────────────────────────────────

    def get_glow_progress(self):
        return self._border_progress

    @pyqtSlot(float)
    def set_glow_progress(self, val):
        self._border_progress = val
        self.update()

    glow_progress = pyqtProperty(float, get_glow_progress, set_glow_progress)

    # ── Height calculation ───────────────────────────────────────────────────

    def _compute_height(self, width: int) -> int:
        h_margin = (GRID_MARGIN_LEFT + ROOT_MARGIN) + (GRID_MARGIN_RIGHT + ROOT_MARGIN)
        label_w = max(50, width - h_margin)

        self.lbl_title.ensurePolished()
        self.lbl_body.ensurePolished()

        title_h = self.lbl_title.fontMetrics().height()
        body_h = self.lbl_body.heightForWidth(label_w)
        if body_h <= 0:
            body_h = self.lbl_body.fontMetrics().height()

        nav_h = 22  # nav row fixed height
        return title_h + body_h + nav_h + BANNER_VERTICAL_MARGIN * 2 + 5 * 2 + 2  # 5 = spacing, 2 = top inset

    # ── Glass background capture ─────────────────────────────────────────────

    def _capture_glass_background(self, x: int, y: int, w: int, h: int):
        screen = QApplication.primaryScreen()
        if not screen or w <= 0 or h <= 0:
            return None
        pixmap = screen.grabWindow(0, x, y, w, h)
        if pixmap.isNull():
            return None
        blur_factor = 0.06
        small = pixmap.scaled(
            max(1, int(w * blur_factor)),
            max(1, int(h * blur_factor)),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return small.scaled(
            w,
            h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # ── Positioning & animation ───────────────────────────────────────────────

    def show_at(self, x: int, width: int, container_edge_y: int, above: bool):
        """Position and show the banner with a slide+fade animation."""
        self.ensurePolished()
        self.setFixedWidth(width)

        banner_h = self._compute_height(width)
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

        if self._glass_ui:
            vis_x = x + ROOT_MARGIN
            vis_w = width - ROOT_MARGIN * 2
            vis_y = y + 2
            vis_h = banner_h - 4
            self._glass_pixmap = self._capture_glass_background(vis_x, vis_y, vis_w, vis_h)

        self.move(x, slide_from_y)
        self.setWindowOpacity(0.0)
        self.show()

        self._pos_anim.setStartValue(QPoint(x, slide_from_y))
        self._pos_anim.setEndValue(QPoint(x, y))
        self._pos_anim.start()

        self._slide_anim.setStartValue(0.0)
        self._slide_anim.setEndValue(1.0)
        self._slide_anim.start()

        if self._border_effect and self._border_effect != "None":
            self.border_anim.stop()
            self.border_anim.setStartValue(0.0)
            self.border_anim.setEndValue(1.0)
            self.border_anim.start()

    def _animate_out(self, callback):
        # Only ever animate out once: a second click while fading would connect
        # the callback again and fire confirmed/finished twice.
        if getattr(self, '_leaving', False):
            return
        self._leaving = True
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

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(ROOT_MARGIN, 2, -ROOT_MARGIN, -2)

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
            painter,
            rect,
            is_light=self._is_light,
            corner_radius=8,
        )

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

    # ── Step navigation ───────────────────────────────────────────────────────

    def _on_next(self):
        if self._step < len(self._steps) - 1:
            self._step += 1
            self._update_step_ui()
        else:
            self._animate_out(self.finished.emit)

    def _on_skip(self):
        self._animate_out(self.finished.emit)

    def _update_step_ui(self):
        title, body = self._steps[self._step]
        self.lbl_title.setText(title)
        self.lbl_body.setText(body)
        self.dot_widget.set_step(self._step)
        self.btn_next.setText(t("welcome.btn_done") if self._step == len(self._steps) - 1 else t("welcome.btn_next"))

        old_h = self.height()
        new_h = self._compute_height(self.width())
        if new_h != old_h:
            self.setFixedHeight(new_h)
            new_y = self._target_y + (old_h - new_h) if self._above else self._target_y
            self._target_y = new_y
            self.move(self._banner_x, new_y)
