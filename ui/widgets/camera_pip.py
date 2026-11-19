"""
Camera picture-in-picture window.

A small always-on-top window that keeps showing a camera feed while the
dashboard is closed. It never takes focus: clicking or dragging it leaves the
keyboard with whatever app you were typing in. Move it by dragging anywhere,
resize it from a corner (the camera's aspect ratio is kept), close it with
the x that appears on hover.

The window is deliberately unparented, so it lives on when the dashboard
hides. Platform details are in ui/utils/floating_window.py.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import QWidget

from core.utils import SYSTEM_FONT
from ui.utils import floating_window

# KDE users match this in a window rule to float the PiP over fullscreen apps.
WINDOW_ROLE = "prism-camera-pip"


class CameraPipWindow(QWidget):
    close_requested = pyqtSignal()
    geometry_committed = pyqtSignal()  # after the user moved or resized it

    RADIUS = 12
    GRIP = 22          # corner area that resizes instead of moving
    SNAP = 16          # distance at which an edge snaps to the screen edge
    MARGIN = 24        # gap to the screen corner for the default position
    MIN_SHORT = 112    # shortest side, in logical pixels
    DEFAULT_LONG = 420
    CLOSE = 26

    def __init__(self, title: str, aspect: float = 16 / 9):
        super().__init__(None)
        self._title = title
        self.aspect = aspect if aspect > 0 else 16 / 9
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple | None = None
        self._status: str | None = None
        self._hover = False
        self._drag: tuple | None = None

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS: without this a Tool panel hides whenever Prism isn't active.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setMouseTracking(True)
        self.setWindowTitle(f"{title} - Prism")
        self.setWindowRole(WINDOW_ROLE)

        w, h = self._default_size()
        self.resize(w, h)
        # Create the native window now so the tweaks land before it's mapped.
        self.winId()
        floating_window.prepare_before_show(self)

    # -- content -----------------------------------------------------------------

    def set_title(self, title: str):
        self._title = title
        self.setWindowTitle(f"{title} - Prism")
        self.update()

    def set_status(self, text: str | None):
        if text != self._status:
            self._status = text
            self.update()

    def set_frame(self, image: QImage):
        if image.isNull():
            return
        aspect = image.width() / max(1, image.height())
        if abs(aspect - self.aspect) / self.aspect > 0.015:
            self.aspect = aspect
            self._refit_to_aspect()
        self._pixmap = QPixmap.fromImage(image)
        self._scaled = None
        self.update()

    def has_frame(self) -> bool:
        return self._pixmap is not None

    # -- placement ---------------------------------------------------------------

    def place_default(self, offset: int = 0):
        """Bottom-right corner of the screen under the mouse."""
        area = _screen_for(QCursor.pos()).availableGeometry()
        w, h = self._default_size(area)
        x = area.right() + 1 - w - self.MARGIN - offset
        y = area.bottom() + 1 - h - self.MARGIN - offset
        self.setGeometry(self._clamped(QRect(x, y, w, h), area))

    def restore(self, saved: dict) -> bool:
        """Put the window back where it was saved. False if that's off-screen."""
        try:
            x, y, w = int(saved["x"]), int(saved["y"]), int(saved["w"])
        except (KeyError, TypeError, ValueError):
            return False
        if w <= 0:
            return False
        rect = QRect(x, y, w, max(1, round(w / self.aspect)))
        # The screen holding most of it; none when that monitor is gone.
        best, best_area = None, 0
        for screen in QGuiApplication.screens():
            overlap = screen.availableGeometry().intersected(rect)
            if overlap.width() * overlap.height() > best_area:
                best, best_area = screen, overlap.width() * overlap.height()
        if best is None:
            return False
        area = best.availableGeometry()
        rect.setSize(self._fit_size(rect.width(), area))
        self.setGeometry(self._clamped(rect, area))
        return True

    def saved_geometry(self) -> dict:
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width()}

    # -- native ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        floating_window.apply_after_show(self)

    # -- mouse -------------------------------------------------------------------

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        if self._drag is None:
            self.unsetCursor()
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        if self._close_rect().contains(pos):
            self.close_requested.emit()
            return
        geo = self.geometry()
        corner = self._corner_at(pos)
        if corner:
            # The corner opposite the one being dragged stays put.
            anchor = {
                "tl": geo.bottomRight() + QPoint(1, 1),
                "tr": QPoint(geo.left(), geo.bottom() + 1),
                "bl": QPoint(geo.right() + 1, geo.top()),
                "br": geo.topLeft(),
            }[corner]
            self._drag = ("resize", corner, anchor)
        else:
            self._drag = ("move", event.globalPosition().toPoint() - geo.topLeft())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        gpos = event.globalPosition().toPoint()
        if self._drag is None:
            corner = self._corner_at(event.position().toPoint())
            if corner in ("tl", "br"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif corner in ("tr", "bl"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.unsetCursor()
            return
        if self._drag[0] == "move":
            self.move(self._snapped(gpos - self._drag[1], gpos))
        else:
            self._resize_to(gpos)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        dragged = self._drag is not None
        self._drag = None
        self.unsetCursor()
        if dragged:
            self.geometry_committed.emit()

    # -- geometry ----------------------------------------------------------------

    def _default_size(self, area: QRect | None = None) -> tuple[int, int]:
        long = self.DEFAULT_LONG
        w = long if self.aspect >= 1 else round(long * self.aspect)
        size = self._fit_size(w, area or _screen_for(QCursor.pos()).availableGeometry())
        return size.width(), size.height()

    def _fit_size(self, w: int, area: QRect) -> QSize:
        """Width clamped between the minimum and 90% of the screen, aspect kept."""
        min_w = self.MIN_SHORT * max(1.0, self.aspect)
        max_w = min(area.width() * 0.9, area.height() * 0.9 * self.aspect)
        w = round(max(min_w, min(w, max_w)))
        return QSize(w, max(1, round(w / self.aspect)))

    @staticmethod
    def _clamped(rect: QRect, area: QRect) -> QRect:
        x = min(max(rect.x(), area.left()), area.right() + 1 - rect.width())
        y = min(max(rect.y(), area.top()), area.bottom() + 1 - rect.height())
        return QRect(x, y, rect.width(), rect.height())

    def _refit_to_aspect(self):
        """New aspect from the camera: keep the width and the edges nearest the
        screen edges, so a window parked bottom-right stays parked there."""
        geo = self.geometry()
        area = _screen_for(geo.center()).availableGeometry()
        size = self._fit_size(geo.width(), area)
        x = geo.right() + 1 - size.width() if geo.center().x() > area.center().x() else geo.x()
        y = geo.bottom() + 1 - size.height() if geo.center().y() > area.center().y() else geo.y()
        self.setGeometry(self._clamped(QRect(QPoint(x, y), size), area))
        if self._drag is not None and self._drag[0] == "resize":
            self._drag = None  # anchor no longer matches; restart on next press

    def _snapped(self, top_left: QPoint, cursor: QPoint) -> QPoint:
        area = _screen_for(cursor).availableGeometry()
        x, y = top_left.x(), top_left.y()
        w, h = self.width(), self.height()
        if abs(x - area.left()) < self.SNAP:
            x = area.left()
        elif abs(area.right() + 1 - (x + w)) < self.SNAP:
            x = area.right() + 1 - w
        if abs(y - area.top()) < self.SNAP:
            y = area.top()
        elif abs(area.bottom() + 1 - (y + h)) < self.SNAP:
            y = area.bottom() + 1 - h
        return QPoint(x, y)

    def _resize_to(self, cursor: QPoint):
        _, corner, anchor = self._drag
        area = _screen_for(anchor).availableGeometry()
        dx = abs(cursor.x() - anchor.x())
        dy = abs(cursor.y() - anchor.y())
        size = self._fit_size(max(dx, round(dy * self.aspect)), area)
        x = anchor.x() - size.width() if corner in ("tl", "bl") else anchor.x()
        y = anchor.y() - size.height() if corner in ("tl", "tr") else anchor.y()
        self.setGeometry(x, y, size.width(), size.height())

    def _close_rect(self) -> QRect:
        return QRect(self.width() - self.CLOSE - 8, 8, self.CLOSE, self.CLOSE)

    def _corner_at(self, pos: QPoint) -> str | None:
        g, w, h = self.GRIP, self.width(), self.height()
        left, right = pos.x() < g, pos.x() > w - g
        top, bottom = pos.y() < g, pos.y() > h - g
        if top and left:
            return "tl"
        if top and right and not self._close_rect().contains(pos):
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        return None

    # -- painting ----------------------------------------------------------------

    def _scaled_frame(self) -> QPixmap | None:
        if self._pixmap is None:
            return None
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._scaled is None or self._scaled_key != key:
            scaled = self._pixmap.scaled(
                self.size() * dpr,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._scaled, self._scaled_key = scaled, key
        return self._scaled

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.setClipPath(path)
        p.fillRect(rect, QColor("#111"))

        frame = self._scaled_frame()
        if frame is not None:
            dpr = frame.devicePixelRatio()
            fw, fh = frame.width() / dpr, frame.height() / dpr
            p.drawPixmap(QPointF((self.width() - fw) / 2, (self.height() - fh) / 2), frame)

        if self._hover:
            self._paint_controls(p)
        if self._status:
            self._paint_status(p, centered=frame is None)

        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)

    def _paint_controls(self, p: QPainter):
        top = QLinearGradient(0, 0, 0, 52)
        top.setColorAt(0, QColor(0, 0, 0, 150))
        top.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, self.width(), 52), top)

        font = QFont(SYSTEM_FONT)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor("white"))
        text_rect = QRect(14, 8, self.width() - self.CLOSE - 30, self.CLOSE)
        title = QFontMetrics(font).elidedText(self._title, Qt.TextElideMode.ElideRight, text_rect.width())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

        cr = self._close_rect()
        p.setBrush(QColor(0, 0, 0, 140))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cr)
        p.setPen(QPen(QColor("white"), 2))
        m = 8
        p.drawLine(cr.left() + m, cr.top() + m, cr.right() - m, cr.bottom() - m)
        p.drawLine(cr.right() - m, cr.top() + m, cr.left() + m, cr.bottom() - m)

        # Resize grips in the bottom corners.
        p.setPen(QPen(QColor(255, 255, 255, 160), 2))
        g = 10
        w, h = self.width() - 5, self.height() - 5
        p.drawLine(w - g, h, w, h)
        p.drawLine(w, h - g, w, h)
        p.drawLine(5, h - g, 5, h)
        p.drawLine(5, h, 5 + g, h)

    def _paint_status(self, p: QPainter, centered: bool):
        font = QFont(SYSTEM_FONT)
        font.setPixelSize(12)
        p.setFont(font)
        fm = QFontMetrics(font)
        text = fm.elidedText(self._status, Qt.TextElideMode.ElideRight, self.width() - 40)
        tw, th = fm.horizontalAdvance(text) + 20, fm.height() + 10
        if centered:
            pill = QRectF((self.width() - tw) / 2, (self.height() - th) / 2, tw, th)
        else:
            pill = QRectF(10, self.height() - th - 10, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawRoundedRect(pill, th / 2, th / 2)
        p.setPen(QColor("#e8e8e8"))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


def _screen_for(point: QPoint):
    return QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
