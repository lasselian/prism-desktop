"""
In-dashboard notification pill.

A compact card shown in a drawer the dashboard grows for it (or, when the
screen has no room for that, floating over the bottom of the content). It
lives inside the dashboard window, so it moves, hides and re-themes with it
on every platform instead of being a second top-level window.

The pill only draws and reports what happened (`finished`); queueing and
placement live in ui.managers.notice_center, the drawer in notice_drawer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Callable

from PyQt6.QtCore import (
    Qt,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QVariantAnimation,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from core.localization_manager import t
from core.utils import SYSTEM_FONT
from ui.icons import get_icon, get_mdi_font

RADIUS = 10
SHADOW = 12  # px the shadow reaches past the pill
PAD_LEFT, PAD_RIGHT, PAD_V = 12, 8, 8
SPACING = 8
ICON_W = 20
CONTROL_H = 26
MIN_WIDTH = 240
GUIDE_GAP = 6  # between the rows of a guide
DOT = 6  # step dot diameter
DOT_PITCH = 10

KINDS = ("info", "success", "warning", "error")
_KIND_ICONS = {
    "info": "information-outline",
    "success": "check-circle-outline",
    "warning": "alert-outline",
    "error": "alert-circle-outline",
}
# (dark theme, light theme)
_KIND_COLORS = {
    "info": ("#62a8ff", "#1f6fd1"),
    "success": ("#5fd38a", "#1d8f4e"),
    "warning": ("#f2c14e", "#a86a0c"),
    "error": ("#ff7b72", "#c9372c"),
}


@dataclass
class Guide:
    """A short walkthrough that turns its pages inside one pill.

    The card shows the page's title and text, a dot per page, Skip, and Next
    (Done on the last page). `page` is where the reader is. It lives here, not
    in the pill, so a guide that goes back in the queue (the dashboard closed)
    carries on from that page.
    """

    pages: list[tuple[str, str]]  # (title, text)
    skip_text: str
    next_text: str
    done_text: str
    page: int = 0


@dataclass
class Notice:
    """One message for the pill. A notice with `confirm_text` is a question.

    A notice with a `guide` is a question too: Done is "yes" (`on_confirm`),
    and Skip, or anything else that ends it early, is "no" (`on_reject`).
    """

    message: str
    kind: str = "info"
    icon: str | None = None  # MDI name ("lan-connect") or glyph; None = the kind's icon
    duration_ms: int = 5000  # 0 = stays until the user answers or closes it
    key: str | None = None  # same key replaces in place instead of queueing
    urgent: bool = False  # caused by what the user just did: jumps the queue
    system_when_hidden: bool = False  # dashboard closed: post an OS notification instead
    action_text: str | None = None
    on_action: Callable[[], None] | None = None
    confirm_text: str | None = None
    cancel_text: str | None = None
    destructive: bool = False
    on_confirm: Callable[[], None] | None = None
    on_reject: Callable[[], None] | None = None
    guide: Guide | None = None
    shown_at: float = field(default=0.0, compare=False)

    @property
    def is_confirm(self) -> bool:
        return self.confirm_text is not None or self.guide is not None

    def seen_for(self) -> float:
        """Seconds this notice has been on screen in its current showing."""
        return monotonic() - self.shown_at if self.shown_at else 0.0


@dataclass
class PillStyle:
    dark: bool = True
    glass: bool = False
    text: str = "#ffffff"


class _PillShadow(QWidget):
    """Soft drop shadow drawn as a sibling below the pill.

    It lives outside the pill so the pill's own rect is exactly its clickable
    body: a shadow margin inside the pill would swallow clicks meant for the
    tiles and footer underneath.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._opacity = 0.0
        self._dark = True
        self.hide()

    def set_opacity(self, value: float):
        self._opacity = value
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark

    def paintEvent(self, event):
        if self._opacity <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        body = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW).translated(0, 3)
        # Stacked translucent rounded rects: dense at the edge, fading outward.
        step_alpha = (0.05 if self._dark else 0.03) * self._opacity
        color = QColor(0, 0, 0)
        color.setAlphaF(step_alpha)
        p.setBrush(color)
        for grow in range(SHADOW - 2, -1, -1):
            r = body.adjusted(-grow, -grow, grow, grow)
            p.drawRoundedRect(r, RADIUS + grow, RADIUS + grow)
        p.end()


class _StepDots(QWidget):
    """One dot per page of a guide, the current one filled."""

    def __init__(self, count: int, step: int, dark: bool):
        super().__init__()
        self._count = count
        self._step = step
        self._dark = dark
        self.setFixedSize(count * DOT_PITCH - (DOT_PITCH - DOT), 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() // 2
        for i in range(self._count):
            center = QPoint(i * DOT_PITCH + DOT // 2, cy)
            if i == self._step:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 220) if self._dark else QColor(0, 0, 0, 200))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 90) if self._dark else QColor(0, 0, 0, 80), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center, DOT // 2, DOT // 2)
        p.end()


class _PillButton(QPushButton):
    """Flat text button (`color`) or filled one (`fill`), sized for the pill."""

    def __init__(self, text: str, style: PillStyle, *, color: str = "", fill: str = "", padding: int = 10):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(CONTROL_H)
        self.setFont(QFont(SYSTEM_FONT, 10, QFont.Weight.Medium))
        if fill:
            hover = QColor(fill).lighter(112).name()
            css = (
                f"QPushButton {{ background: {fill}; color: #ffffff; border: none; border-radius: 6px;"
                f" padding: 0 {padding + 2}px; }}"
                f"QPushButton:hover {{ background: {hover}; }}"
            )
        else:
            hover = "rgba(255,255,255,0.10)" if style.dark else "rgba(0,0,0,0.07)"
            css = (
                f"QPushButton {{ background: transparent; color: {color or style.text}; border: none;"
                f" border-radius: 6px; padding: 0 {padding}px; }}"
                f"QPushButton:hover {{ background: {hover}; }}"
            )
        self.setStyleSheet(css)


class NotificationPill(QWidget):
    """The visible card. Emits `finished` once with how it ended."""

    # "confirm" | "reject" | "action" | "dismiss" | "timeout", sent as the pill starts to leave
    finished = pyqtSignal(str)
    # The leave animation is over and the pill is hidden
    gone = pyqtSignal()
    # A guide turned a page: the card has new text and may need a new height
    advanced = pyqtSignal()

    def __init__(self, parent: QWidget, shadow_parent: QWidget | None = None):
        """`parent` may be a clipping holder; the shadow goes on `shadow_parent` (default: `parent`)."""
        super().__init__(parent)
        self.notice: Notice | None = None
        self._style = PillStyle()
        self._leaving = False
        self._progress = 0.0
        self._reveal = 0.0
        self._shadow_on = True
        self._exit_claimed = False
        self._fade_then = None

        self._shadow = _PillShadow(shadow_parent or parent)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

        self._reveal_anim = QPropertyAnimation(self, b"reveal", self)
        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._reveal_anim.finished.connect(self._on_reveal_finished)

        self._countdown = QVariantAnimation(self)
        self._countdown.setStartValue(1.0)
        self._countdown.setEndValue(0.0)
        self._countdown.valueChanged.connect(self._on_countdown)
        self._countdown.finished.connect(self._on_timeout)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(PAD_LEFT, PAD_V, PAD_RIGHT, PAD_V)
        self._layout.setSpacing(SPACING)

        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(ICON_W)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setFont(QFont(SYSTEM_FONT, 10))
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)

        # A guide uses this instead of the icon/label row: icon and title on top,
        # the page's text under the title, dots and buttons along the bottom.
        self._guide_box = QWidget()
        grid = QGridLayout(self._guide_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACING)
        grid.setVerticalSpacing(GUIDE_GAP)
        self.guide_icon = QLabel()
        self.guide_icon.setFixedWidth(ICON_W)
        self.guide_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.guide_title = QLabel()
        self.guide_title.setTextFormat(Qt.TextFormat.PlainText)
        self.guide_title.setWordWrap(True)
        self.guide_title.setFont(QFont(SYSTEM_FONT, 11, QFont.Weight.DemiBold))
        self.guide_body = QLabel()
        self.guide_body.setTextFormat(Qt.TextFormat.PlainText)
        self.guide_body.setWordWrap(True)
        self.guide_body.setFont(QFont(SYSTEM_FONT, 10))
        self._guide_foot = QHBoxLayout()
        self._guide_foot.setContentsMargins(0, 0, 0, 0)
        self._guide_foot.setSpacing(SPACING)
        grid.addWidget(self.guide_icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.guide_title, 0, 1)
        grid.addWidget(self.guide_body, 1, 1)
        grid.addLayout(self._guide_foot, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        self._layout.addWidget(self._guide_box, 1)
        self._guide_box.hide()

        self._controls: list[QWidget] = []
        self.close_button: QPushButton | None = None
        self.confirm_button: QPushButton | None = None
        self.cancel_button: QPushButton | None = None
        self.action_button: QPushButton | None = None
        self.hide()

    # ── Content ────────────────────────────────────────────────────────

    def apply(self, notice: Notice, style: PillStyle):
        """Show `notice` in this pill, rebuilding its buttons. The countdown waits for start_countdown()."""
        self.notice = notice
        self._style = style
        self._shadow.set_dark(style.dark)
        kind = notice.kind if notice.kind in KINDS else "info"
        accent = _KIND_COLORS[kind][0 if style.dark else 1]

        glyph = notice.icon or _KIND_ICONS[kind]
        if len(glyph) > 2:  # an MDI name rather than a glyph
            glyph = get_icon(glyph)
        mdi = QFont(get_mdi_font())
        mdi.setPixelSize(18)

        for w in self._controls:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._controls = []
        self.close_button = self.confirm_button = self.cancel_button = self.action_button = None

        secondary = QColor(style.text)
        secondary.setAlphaF(0.72)
        secondary_css = secondary.name(QColor.NameFormat.HexArgb)

        guide = notice.guide
        self._guide_box.setVisible(guide is not None)
        self.icon_label.setVisible(guide is None)
        self.label.setVisible(guide is None)
        if guide is not None:
            self._apply_guide(guide, style, accent, glyph, mdi, secondary_css)
            self._leaving = False
            self._countdown.stop()
            self._progress = 0.0
            self.update()
            return

        self.icon_label.setFont(mdi)
        self.icon_label.setText(glyph)
        self.icon_label.setStyleSheet(f"color: {accent}; background: transparent;")

        self.label.setText(notice.message)
        self.label.setStyleSheet(f"color: {style.text}; background: transparent;")

        if notice.is_confirm:
            cancel = notice.cancel_text or t("notification_banner.cancel")
            self.cancel_button = _PillButton(cancel, style, color=secondary_css)
            self.cancel_button.clicked.connect(lambda: self.leave("reject"))
            fill = _KIND_COLORS["error"][1] if notice.destructive else ("#2f7de1" if style.dark else "#1f6fd1")
            self.confirm_button = _PillButton(notice.confirm_text, style, fill=fill)
            self.confirm_button.clicked.connect(lambda: self.leave("confirm"))
            self._controls = [self.cancel_button, self.confirm_button]
        else:
            if notice.action_text:
                self.action_button = _PillButton(notice.action_text, style, color=accent)
                self.action_button.clicked.connect(lambda: self.leave("action"))
                self._controls.append(self.action_button)
            self.close_button = _PillButton(get_icon("close"), style, color=secondary_css, padding=0)
            close_font = QFont(get_mdi_font())
            close_font.setPixelSize(15)
            self.close_button.setFont(close_font)
            self.close_button.setFixedWidth(CONTROL_H)
            self.close_button.setToolTip(t("notification_banner.dismiss"))
            self.close_button.setAccessibleName(t("notification_banner.dismiss"))
            self.close_button.clicked.connect(lambda: self.leave("dismiss"))
            self._controls.append(self.close_button)
        for w in self._controls:
            self._layout.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

        self._leaving = False
        self._countdown.stop()
        self._progress = 0.0
        self.update()

    def _apply_guide(self, guide: Guide, style: PillStyle, accent: str, glyph: str, mdi: QFont, secondary_css: str):
        title, text = guide.pages[guide.page]
        self.guide_icon.setFont(mdi)
        self.guide_icon.setText(glyph)
        self.guide_icon.setStyleSheet(f"color: {accent}; background: transparent;")
        self.guide_title.setText(title)
        self.guide_title.setStyleSheet(f"color: {style.text}; background: transparent;")
        self.guide_body.setText(text)
        self.guide_body.setStyleSheet(f"color: {style.text}; background: transparent;")

        while self._guide_foot.count():
            widget = self._guide_foot.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        last = guide.page == len(guide.pages) - 1
        self._guide_foot.addWidget(
            _StepDots(len(guide.pages), guide.page, style.dark), 0, Qt.AlignmentFlag.AlignVCenter
        )
        self._guide_foot.addStretch(1)
        self.cancel_button = _PillButton(guide.skip_text, style, color=secondary_css)
        self.cancel_button.clicked.connect(lambda: self.leave("reject"))
        self.confirm_button = _PillButton(
            guide.done_text if last else guide.next_text, style, fill="#2f7de1" if style.dark else "#1f6fd1"
        )
        self.confirm_button.clicked.connect(self._guide_next)
        self._guide_foot.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._guide_foot.addWidget(self.confirm_button, 0, Qt.AlignmentFlag.AlignVCenter)

    def _guide_next(self):
        guide = self.notice.guide if self.notice else None
        if guide is None or self._leaving:
            return
        if guide.page < len(guide.pages) - 1:
            guide.page += 1
            self.advanced.emit()
        else:
            self.leave("confirm")

    def _guide_height(self, width: int) -> int:
        text_w = max(40, width - PAD_LEFT - PAD_RIGHT - ICON_W - SPACING)
        for label in (self.guide_title, self.guide_body):
            label.ensurePolished()
        title_h = max(self.guide_title.heightForWidth(text_w), ICON_W)
        body_h = self.guide_body.heightForWidth(text_w)
        return PAD_V * 2 + title_h + GUIDE_GAP + body_h + GUIDE_GAP + CONTROL_H

    def _chrome_width(self) -> int:
        controls_w = 0
        for w in self._controls:
            w.ensurePolished()
            controls_w += max(w.sizeHint().width(), w.minimumWidth()) + SPACING
        return PAD_LEFT + ICON_W + SPACING + controls_w + PAD_RIGHT

    def height_for(self, width: int) -> int:
        """Height of the pill at exactly `width` (the message wraps to fit)."""
        if self.notice is not None and self.notice.guide is not None:
            return self._guide_height(width)
        label_w = max(40, width - self._chrome_width())
        self.label.ensurePolished()
        label_h = self.label.heightForWidth(label_w)
        if label_h <= 0:
            label_h = self.label.fontMetrics().height()
        return max(label_h, CONTROL_H) + PAD_V * 2

    def size_for(self, max_width: int) -> QSize:
        """Width that fits the message on one line if it can, else `max_width` with wrapping."""
        if self.notice is not None and self.notice.guide is not None:
            return QSize(max_width, self._guide_height(max_width))
        self.label.ensurePolished()
        text_w = self.label.fontMetrics().horizontalAdvance(self.label.text()) + 4
        width = min(max_width, max(MIN_WIDTH, self._chrome_width() + text_w))
        return QSize(width, self.height_for(width))

    # ── Countdown ─────────────────────────────────────────────────────

    def start_countdown(self):
        self._countdown.stop()
        duration = self.notice.duration_ms if self.notice else 0
        if duration > 0:
            self._countdown.setDuration(duration)
            self._countdown.start()
        else:
            self._progress = 0.0
        if self.underMouse():
            self._countdown.pause()

    def _on_countdown(self, value):
        self._progress = float(value)
        self.update()

    def _on_timeout(self):
        if self.notice and self.notice.duration_ms > 0 and not self._leaving:
            self.leave("timeout")

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._countdown.state() == QVariantAnimation.State.Running:
            self._countdown.pause()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._countdown.state() == QVariantAnimation.State.Paused:
            # Give the reader a moment after moving away, then carry on.
            floor = max(0, self._countdown.duration() - 1500)
            if self._countdown.currentTime() > floor:
                self._countdown.setCurrentTime(floor)
            self._countdown.resume()

    # ── Show / leave ──────────────────────────────────────────────────

    def get_reveal(self) -> float:
        return self._reveal

    def set_reveal(self, value: float):
        self._reveal = value
        self._effect.setOpacity(value)
        self._shadow.set_opacity(value)

    reveal = pyqtProperty(float, get_reveal, set_reveal)

    def set_shadow(self, on: bool):
        """Shadow for when the pill floats over content; none when it sits in the drawer."""
        self._shadow_on = on
        if not on:
            self._shadow.hide()
        elif self.isVisible():
            self._sync_shadow()
            self._shadow.show()

    def prepare(self, size: QSize):
        """Show the pill invisible at `size`, for the caller to reveal (drawer)."""
        self._reveal_anim.stop()
        self._slide_anim.stop()
        self._leaving = False
        self._fade_then = None
        self.setFixedSize(size)
        self.set_reveal(0.0)
        self.show()
        self.raise_above_siblings()

    def fade_to(self, value: float, ms: int, then=None):
        """Fade in or out in place. Fading out claims the exit (no default slide-away)."""
        if value <= 0.0:
            self._exit_claimed = True
        self._slide_anim.stop()
        self._reveal_anim.stop()
        self._fade_then = then
        self._reveal_anim.setDuration(ms)
        self._reveal_anim.setEasingCurve(QEasingCurve.Type.OutCubic if value > 0 else QEasingCurve.Type.InCubic)
        self._reveal_anim.setStartValue(self._reveal)
        self._reveal_anim.setEndValue(value)
        self._reveal_anim.start()

    def claim_exit(self):
        """Someone else drives how this pill leaves (the drawer closing)."""
        self._exit_claimed = True

    def freeze(self):
        """Stop the countdown and any motion, leaving the pill as it looks now."""
        self._countdown.stop()
        self._reveal_anim.stop()
        self._slide_anim.stop()
        self._fade_then = None

    def present(self, pos: QPoint, size: QSize):
        """Slide up into `pos` and fade in (floating over content)."""
        self._leaving = False
        self._fade_then = None
        self.setFixedSize(size)
        start = QPoint(pos.x(), pos.y() + 8)
        self.move(start)
        self.set_reveal(0.0)
        self.show()
        self.raise_above_siblings()
        self._animate(start, pos, 1.0, 240, QEasingCurve.Type.OutCubic)
        if self.notice:
            self.notice.shown_at = monotonic()

    def place(self, pos: QPoint, size: QSize):
        """Jump to a new geometry (window resized, content changed) without animating."""
        if self._slide_anim.state() == QPropertyAnimation.State.Running and not self._leaving:
            self._slide_anim.stop()
        self.setFixedSize(size)
        self.move(pos)

    def leave(self, outcome: str):
        """Report `outcome` and go. Only the first call counts.

        Whoever handles `finished` may take over the exit (fade_to/claim_exit);
        otherwise the pill fades and slides away by itself.
        """
        if self._leaving or not self.isVisible():
            return
        self._leaving = True
        self._countdown.stop()
        self._exit_claimed = False
        self.finished.emit(outcome)
        if not self._exit_claimed:
            self.animate_away()

    def animate_away(self):
        """Fade out with a small drop, then hide and emit `gone`."""
        self._exit_claimed = True
        self._leaving = True
        self._fade_then = None
        end = QPoint(self.x(), self.y() + 6)
        self._animate(self.pos(), end, 0.0, 170, QEasingCurve.Type.InCubic)

    def vanish(self):
        """Hide at once, without reporting anything."""
        self._reveal_anim.stop()
        self._slide_anim.stop()
        self._countdown.stop()
        self._leaving = False
        self._fade_then = None
        self.set_reveal(0.0)
        self.hide()

    @property
    def leaving(self) -> bool:
        return self._leaving

    def raise_above_siblings(self):
        self._shadow.raise_()
        host = self.parentWidget()
        if host is not None and host is not self._shadow.parentWidget():
            host.raise_()
        self.raise_()

    def _animate(self, start: QPoint, end: QPoint, reveal_to: float, ms: int, curve):
        self._slide_anim.stop()
        self._slide_anim.setDuration(ms)
        self._slide_anim.setEasingCurve(curve)
        self._slide_anim.setStartValue(start)
        self._slide_anim.setEndValue(end)
        self._reveal_anim.stop()
        self._reveal_anim.setDuration(ms)
        self._reveal_anim.setEasingCurve(curve)
        self._reveal_anim.setStartValue(self._reveal)
        self._reveal_anim.setEndValue(reveal_to)
        self._slide_anim.start()
        self._reveal_anim.start()

    def _on_reveal_finished(self):
        then, self._fade_then = self._fade_then, None
        if then is not None:
            then()
            return
        if self._leaving and self._reveal <= 0.0:
            self._leaving = False
            self.hide()
            self.gone.emit()

    # ── Qt plumbing ───────────────────────────────────────────────────

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_shadow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_shadow()

    def showEvent(self, event):
        super().showEvent(event)
        if self._shadow_on:
            self._sync_shadow()
            self._shadow.show()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._shadow.hide()

    def _sync_shadow(self):
        if not self._shadow_on:
            return
        shadow_parent = self._shadow.parentWidget()
        host = self.parentWidget()
        top_left = self.mapTo(shadow_parent, QPoint(0, 0)) if host is not shadow_parent else self.pos()
        self._shadow.setGeometry(QRect(top_left, self.size()).adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW))
        self._shadow.stackUnder(host if host is not shadow_parent else self)

    def mousePressEvent(self, event):
        event.accept()  # keep clicks on the card from reaching the dashboard's drag/resize

    def mouseReleaseEvent(self, event):
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)

        if self._style.dark:
            fill = QColor(30, 30, 32, 238 if self._style.glass else 250)
            border = QColor(255, 255, 255, 30)
        else:
            fill = QColor(252, 252, 252, 240 if self._style.glass else 252)
            border = QColor(0, 0, 0, 32)
        p.fillPath(path, fill)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        if self._progress > 0.0 and self.notice:
            kind = self.notice.kind if self.notice.kind in KINDS else "info"
            line = QColor(_KIND_COLORS[kind][0 if self._style.dark else 1])
            line.setAlpha(190)
            p.setClipPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(line)
            width = body.width() * self._progress
            x = body.right() - width if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else body.left()
            p.drawRect(QRectF(x, body.bottom() - 2, width, 2))
        p.end()
