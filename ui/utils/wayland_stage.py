"""
A see-through window that lets a widget be placed anywhere on native Wayland.

A Wayland client can't position its own top-level windows, but a maximized one
is given the monitor's work area, and inside it a child widget goes wherever it
is moved. This "stage" is such a window: invisible around its child, passing
clicks there through to whatever is behind, and blurring only behind the child.

Kept apart from wayland_blur.py, which main.py imports before choosing Qt's
platform and which therefore doesn't import PyQt6 at module level.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSize, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import QPainter, QPixmap, QPlatformSurfaceEvent, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

from ui.utils.wayland_blur import (
    clear_wayland_toplevel_app_id,
    set_attached_blur_enabled,
    sync_attached_blur,
)


class WaylandStageWindow(QWidget):
    """The top-level window of a widget that has to choose its own position.

    The child is shown, hidden, moved and resized as usual, in stage
    coordinates. Open the stage with present() (or present_fade_in() to fade
    cleanly in place without GNOME Shell's center-scale map/destroy sweep); it
    follows the child's hiding by itself. A child that defines
    on_stage_resized() is told when the compositor changes the room it has.

    Attributes the stage lacks are looked up on the child, so code that asks a
    descendant for its window() still reaches the child.
    """

    # GNOME Shell animates new and closed xdg_toplevel windows from/to screen
    # center for 150 ms (SHOW_WINDOW_ANIMATION_TIME / DESTROY_WINDOW_ANIMATION_TIME).
    # Keeping the stage empty during map and committing an empty zero-blur frame
    # before hide hides both compositor sweeps completely.
    MAP_SETTLE_MS = 160
    FADE_IN_MS = 180
    FADE_OUT_MS = 150
    HIDE_COMMIT_MS = 40

    def __init__(self, child: QWidget):
        super().__init__()
        self._dashboard = child
        self._work_area: tuple[str, QSize] | None = None  # (screen name, size) when last open
        self._fade_pixmap: QPixmap | None = None
        self._fade_pos = QPoint()
        self._fade_opacity = 0.0
        self._fade_phase: str | None = None  # opening_wait | fading_in | fading_out | hiding_wait
        self._blur_wanted = True
        self._blur_suppressed = False
        self._suppress_child_hide = False
        self._on_fade_in_done = None

        self._map_timer = QTimer(self)
        self._map_timer.setSingleShot(True)
        self._map_timer.timeout.connect(self._on_map_settled)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._finish_hide)

        self._fade_anim = QPropertyAnimation(self, b"fade_opacity", self)
        self._fade_anim.finished.connect(self._on_fade_finished)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        child.setParent(self)
        child.installEventFilter(self)
        # An empty stage must not stay behind, taking clicks where the child was.
        child.destroyed.connect(self.deleteLater)

    def __getattr__(self, name: str):
        # Only called when normal lookup fails. Read __dict__ so that a lookup
        # before _dashboard exists can't recurse.
        child = self.__dict__.get("_dashboard")
        if child is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(child, name)

    def _live_child(self) -> QWidget | None:
        """The child, unless it is gone: events still arrive while it is being deleted."""
        return None if sip.isdeleted(self._dashboard) else self._dashboard

    def get_fade_opacity(self) -> float:
        return self._fade_opacity

    def set_fade_opacity(self, value: float) -> None:
        self._fade_opacity = max(0.0, min(1.0, float(value)))
        if self._fade_phase == "fading_out" and self._fade_opacity <= 0.1 and not self._blur_suppressed:
            self._blur_suppressed = True
            set_attached_blur_enabled(self, False)
        self.update()

    fade_opacity = pyqtProperty(float, get_fade_opacity, set_fade_opacity)

    def is_showing(self) -> bool:
        """True when the child is visible or actively opening/fading in."""
        if self._fade_phase in ("opening_wait", "fading_in"):
            return True
        if self._fade_phase in ("fading_out", "hiding_wait"):
            return False
        child = self._live_child()
        return child is not None and child.isVisible()

    def bounds(self, screen=None) -> QSize:
        """The room there is to place the child in, even before the stage is open.

        While closed, the size the compositor gave last time beats the screen's
        availableGeometry(), which on Wayland ignores panels.
        """
        child = self._live_child()
        fits = child is not None and self.width() >= child.width() and self.height() >= child.height()
        if screen is None or (self.isVisible() and fits):
            return self.size()
        if self._work_area is not None and self._work_area[0] == screen.name():
            return self._work_area[1]
        return screen.availableGeometry().size()

    def present(self, screen=None):
        """Open the stage over `screen`'s work area."""
        self._cancel_transitions()
        self._fade_phase = None
        self._fade_pixmap = None
        self._blur_suppressed = False
        if not self.isVisible():
            if screen is not None:
                # So that even a frame drawn before the compositor answers has the child in place.
                self.resize(self.bounds(screen))
            # Ensure the QWindow exists so surfaceRoleCreated is hooked before
            # showMaximized() creates and commits the xdg_toplevel.
            self.winId()
            clear_wayland_toplevel_app_id(self)
            self.showMaximized()
        self.raise_()

    def present_fade_in(self, screen=None, blur_enabled: bool = True, on_finished=None):
        """Open the stage invisibly while GNOME finishes its map zoom, then fade the child in."""
        self._cancel_transitions()
        self._blur_wanted = bool(blur_enabled)
        self._on_fade_in_done = on_finished
        child = self._live_child()
        if child is None:
            return

        if not self.isVisible():
            self._fade_phase = "opening_wait"
            self._fade_pixmap = None
            self._fade_opacity = 0.0
            self._blur_suppressed = True
            self._hide_child_silently(child)
            set_attached_blur_enabled(self, False)
            self.clearMask()
            if screen is not None:
                self.resize(self.bounds(screen))
            self.winId()
            clear_wayland_toplevel_app_id(self)
            self.showMaximized()
            self.raise_()
            self._map_timer.start(self.MAP_SETTLE_MS)
            return

        if self._fade_phase is None and child.isVisible():
            self.raise_()
            self._invoke_fade_in_done()
            return

        self._start_fade_in(self._fade_opacity)

    def fade_out_and_hide(self):
        """Fade the child out in place, commit one transparent frame, then hide the stage."""
        self._cancel_transitions()
        self._on_fade_in_done = None
        child = self._live_child()
        if not self.isVisible() or self._fade_phase in ("opening_wait", "hiding_wait") or child is None:
            self._finish_hide()
            return

        if self._fade_phase == "fading_in" and self._fade_pixmap is not None:
            start_opacity = self._fade_opacity
        elif child.isHidden():
            self._finish_hide()
            return
        else:
            self._fade_pixmap = child.grab()
            self._fade_pos = child.pos()
            start_opacity = 1.0
            self._hide_child_silently(child)

        self.clearMask()
        self._fade_phase = "fading_out"
        self._fade_opacity = start_opacity
        self._fade_anim.setDuration(max(1, round(self.FADE_OUT_MS * start_opacity)))
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.setStartValue(start_opacity)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def hide_immediately(self):
        """Cancel any fade and hide both child and stage at once."""
        self._cancel_transitions()
        self._on_fade_in_done = None
        self._finish_hide()

    @property
    def blur_suppressed(self) -> bool:
        """True while the stage is empty (before map settles or after fade-out)."""
        return self._blur_suppressed

    def set_blur_wanted(self, enabled: bool):
        """Remember whether blur should be active once not suppressed by a fade."""
        self._blur_wanted = bool(enabled)
        if not self._blur_suppressed:
            set_attached_blur_enabled(self, self._blur_wanted)

    def _cancel_transitions(self):
        self._map_timer.stop()
        self._hide_timer.stop()
        if self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()

    def _hide_child_silently(self, child: QWidget):
        if child.isHidden():
            return
        self._suppress_child_hide = True
        try:
            child.hide()
        finally:
            self._suppress_child_hide = False

    def _on_map_settled(self):
        if self._fade_phase != "opening_wait":
            return
        self._start_fade_in(0.0)

    def _start_fade_in(self, start_opacity: float):
        child = self._live_child()
        if child is None:
            self._finish_hide()
            return
        on_resized = getattr(child, "on_stage_resized", None)
        if on_resized is not None:
            on_resized()
        child.ensurePolished()
        if child.layout():
            child.layout().activate()
        self._fade_pixmap = child.grab()
        self._fade_pos = child.pos()
        self._hide_child_silently(child)
        self._fade_phase = "fading_in"
        self._fade_opacity = start_opacity
        self._blur_suppressed = False
        self.setMask(QRegion(child.geometry()))
        set_attached_blur_enabled(self, self._blur_wanted)
        sync_attached_blur(self)
        self.update()
        self._fade_anim.setDuration(max(1, round(self.FADE_IN_MS * (1.0 - start_opacity))))
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.setStartValue(start_opacity)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def _on_fade_finished(self):
        if self._fade_phase == "fading_in":
            self._fade_phase = None
            self._fade_pixmap = None
            self._fade_opacity = 1.0
            child = self._live_child()
            if child is not None:
                child.show()
                self.sync_child_region()
            self.update()
            self._invoke_fade_in_done()
        elif self._fade_phase == "fading_out":
            self._fade_phase = "hiding_wait"
            self._fade_pixmap = None
            self._fade_opacity = 0.0
            self._blur_suppressed = True
            set_attached_blur_enabled(self, False)
            self.clearMask()
            self.update()
            self._hide_timer.start(self.HIDE_COMMIT_MS)

    def _invoke_fade_in_done(self):
        callback, self._on_fade_in_done = self._on_fade_in_done, None
        if callback is not None:
            callback()

    def _finish_hide(self):
        self._fade_phase = None
        self._fade_pixmap = None
        self._fade_opacity = 0.0
        self._blur_suppressed = True
        child = self._live_child()
        if child is not None:
            self._hide_child_silently(child)
        set_attached_blur_enabled(self, False)
        self.clearMask()
        self.hide()

    def event(self, event):
        if (
            event.type() == QEvent.Type.PlatformSurface
            and event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated
        ):
            clear_wayland_toplevel_app_id(self)
        return super().event(event)

    def paintEvent(self, event):
        if self._fade_pixmap is None or self._fade_opacity <= 0.0:
            return
        painter = QPainter(self)
        painter.setOpacity(self._fade_opacity)
        painter.drawPixmap(self._fade_pos, self._fade_pixmap)

    def sync_child_region(self):
        """Take input, and blur, only where the child is."""
        if self._fade_phase in ("opening_wait", "fading_out", "hiding_wait"):
            return
        child = self._live_child()
        if child is None or (child.isHidden() and self._fade_phase != "fading_in"):
            return
        self.setMask(QRegion(child.geometry()))
        sync_attached_blur(self)

    def eventFilter(self, obj, event):
        if obj is self._live_child():
            kind = event.type()
            if kind == QEvent.Type.Hide:
                # Not when it is only the stage hiding, or while we fade a snapshot.
                if obj.isHidden() and not self._suppress_child_hide:
                    self._cancel_transitions()
                    self._fade_phase = None
                    self._fade_pixmap = None
                    self.clearMask()
                    self.hide()
            elif kind in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
                self.sync_child_region()
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        child = self._live_child()
        on_resized = getattr(child, "on_stage_resized", None)
        if on_resized is not None:
            on_resized()
        if self._fade_phase == "fading_in" and child is not None:
            self._fade_pos = child.pos()
        self.sync_child_region()

    def hideEvent(self, event):
        super().hideEvent(event)
        screen = self.screen()
        if screen is not None:
            self._work_area = (screen.name(), self.size())

    def changeEvent(self, event):
        super().changeEvent(event)
        # Qt tells only the window that it was (de)activated, not the widgets in it.
        if event.type() != QEvent.Type.ActivationChange or self._fade_phase is not None:
            return
        child = self._live_child()
        if child is not None and not child.isHidden():
            QApplication.sendEvent(child, QEvent(QEvent.Type.ActivationChange))
