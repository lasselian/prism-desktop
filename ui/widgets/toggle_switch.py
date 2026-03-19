"""
Compact iOS-style toggle switch for the Settings panel.
Filled pill track, white thumb, no border strokes.
"""

from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtCore import Qt, QRectF, QSize, pyqtProperty, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFontMetrics


class ToggleSwitch(QAbstractButton):
    """
    Compact filled-track toggle.
    Same API as QCheckBox: isChecked(), setChecked(), toggled signal.
    """

    W = 34      # track width
    H = 20      # track height
    PAD = 2     # thumb padding inside track

    def __init__(self, label: str = "", accent_color: str = "#0078d4", parent=None):
        super().__init__(parent)
        self.setText(label)
        self.setCheckable(True)
        self._accent = QColor(accent_color)
        self._text_color = QColor("#e0e0e0")
        self._pos = 0.0  # 0.0 = off, 1.0 = on

        self._anim = QPropertyAnimation(self, b"_slide_pos")
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.toggled.connect(self._on_toggled)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Properties ───────────────────────────────────────────────────────────

    def get_slide_pos(self) -> float:
        return self._pos

    def set_slide_pos(self, val: float):
        self._pos = val
        self.update()

    _slide_pos = pyqtProperty(float, get_slide_pos, set_slide_pos)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_accent(self, color: str):
        self._accent = QColor(color)
        self.update()

    def set_text_color(self, color: str):
        self._text_color = QColor(color)
        self.update()

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self.update()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        lbl_w = fm.horizontalAdvance(self.text()) + 8 if self.text() else 0
        return QSize(self.W + lbl_w, max(self.H, fm.height()))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = self._pos
        W, H, PAD = self.W, self.H, self.PAD
        cy = (self.height() - H) / 2

        # ── Track color: gray → accent ────────────────────────────────────────
        off = QColor(120, 120, 128)  # neutral gray
        track = QColor(
            int(off.red()   + (self._accent.red()   - off.red())   * t),
            int(off.green() + (self._accent.green() - off.green()) * t),
            int(off.blue()  + (self._accent.blue()  - off.blue())  * t),
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track))
        p.drawRoundedRect(QRectF(0, cy, W, H), H / 2, H / 2)

        # ── Thumb: white circle ───────────────────────────────────────────────
        thumb_d = H - 2 * PAD
        thumb_x = PAD + t * (W - H)
        thumb_y = cy + PAD

        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(QRectF(thumb_x, thumb_y, thumb_d, thumb_d))

        # ── Label ─────────────────────────────────────────────────────────────
        if self.text():
            p.setPen(QPen(self._text_color))
            p.setFont(self.font())
            p.drawText(
                W + 8, 0, self.width() - W - 8, self.height(),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )

        p.end()
