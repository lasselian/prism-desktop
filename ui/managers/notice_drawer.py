"""
The strip a notice slides out in.

A spacer in the dashboard's content layout whose height grows and shrinks
together with the window, so the dashboard visibly gets taller to make room
for a notice and shrinks back afterwards. Nothing else on screen moves: the
window grows away from the edge it is anchored to (down from a top tray,
up from a bottom one) and the spacer sits on that far side.

Only one thing may size the window at a time. While the dashboard is doing
its own resize (rows, columns, a view change, Assist, the open/close
animation, a drag) the drawer pauses where it is, and every window-height
target the dashboard computes adds the drawer's current height, so the two
never fight. When the dashboard is idle again the drawer re-reads the window
size and carries on.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEasingCurve, QObject, QRect, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

QWIDGETSIZE_MAX = 16777215


class NoticeDrawer(QObject):
    OPEN_MS = 260
    CLOSE_MS = 230
    RESIZE_MS = 200
    POLL_MS = 40  # while waiting for the dashboard to finish its own resize

    # Slot height after each step of an animation (or a jump).
    changed = pyqtSignal(int)

    def __init__(self, dashboard: QWidget):
        super().__init__(dashboard)
        self._dash = dashboard
        self._layout: QVBoxLayout | None = None
        self.slot: QWidget | None = None
        self._h = 0  # slot height == drawer's share of the window height
        self._target = 0
        self._on_done: Callable[[], None] | None = None
        self._pending: tuple[int, Callable[[], None] | None] | None = None
        self._base_h = 0
        self._edge = 0
        self._top = True

        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_frame)
        self._anim.finished.connect(self._on_finished)

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    # ── State ─────────────────────────────────────────────────────────

    @property
    def height(self) -> int:
        """The drawer's share of the window height right now."""
        return self._h

    @property
    def target(self) -> int:
        return self._target

    @property
    def anchored_top(self) -> bool:
        """True when the drawer is (or will be) below the content, growing downward.

        A closed drawer follows the tray setting as it is now; an open one keeps
        the side it opened on until it closes.
        """
        return self._top if self._h > 0 else self._dash._is_top_anchored()

    @property
    def moving(self) -> bool:
        return self._anim.state() != QVariantAnimation.State.Stopped or self._pending is not None

    def slot_rect(self) -> QRect:
        """Where the slot is in dashboard coordinates, worked out from the window
        itself so it is right even before the layout has caught up."""
        dash = self._dash
        container = getattr(dash, 'container', None)
        if container is None:
            return QRect()
        geo = container.geometry()
        if self.anchored_top:
            return QRect(geo.x(), geo.y() + geo.height() - self._h, geo.width(), self._h)
        return QRect(geo.x(), geo.y(), geo.width(), self._h)

    # ── Setup ─────────────────────────────────────────────────────────

    def attach(self, content_layout: QVBoxLayout):
        """Put a fresh, closed slot into the dashboard's (new) content layout."""
        self._stop()
        self._layout = content_layout
        self.slot = QWidget()
        self.slot.setObjectName("noticeDrawerSlot")
        self.slot.setFixedHeight(0)
        self._h = self._target = 0
        self._top = self._dash._is_top_anchored()
        self._dock_slot()

    def _dock_slot(self):
        """Below the content when the window grows down, above it when it grows up."""
        if self._layout is None or self.slot is None:
            return
        self._layout.removeWidget(self.slot)
        if self._top:
            self._layout.addWidget(self.slot)
        else:
            self._layout.insertWidget(0, self.slot)

    # ── Moving ────────────────────────────────────────────────────────

    def room_for(self, h: int) -> bool:
        """Whether the window can grow to fit a drawer of height `h` on its screen."""
        dash = self._dash
        try:
            screen = dash.screen() or QApplication.primaryScreen()
        except RuntimeError:
            screen = QApplication.primaryScreen()
        if screen is None:
            return True
        avail = screen.availableGeometry()
        grow = h - self._h
        if self.anchored_top:
            return dash.y() + dash.height() + grow <= avail.y() + avail.height()
        return dash.y() - grow >= avail.y()

    def animate_to(self, h: int, on_done: Callable[[], None] | None = None):
        """Grow or shrink to `h`, as soon as the dashboard isn't resizing itself."""
        h = max(0, int(h))
        # A new request replaces whatever was running; its callback no longer applies.
        self._stop()
        if h == self._h:
            self._target = h
            self._pending = None
            if on_done:
                on_done()
            return
        self._pending = (h, on_done)
        self._try_start()

    def snap_to(self, h: int, resize_window: bool):
        """Jump to `h` at once.

        resize_window=False leaves the window as it is, for when a dashboard
        resize is about to start from the current height and will absorb the
        change; True grows or shrinks the window by the difference (hidden
        dashboard, or Assist about to size it).
        """
        h = max(0, int(h))
        self._stop()
        delta = h - self._h
        self._target = h
        if delta == 0:
            return
        if resize_window:
            dash = self._dash
            new_h = max(1, dash.height() + delta)
            y = dash.y() if self._top else dash.y() - delta
            self._unlock_height()
            self._set_slot(h)
            dash.setGeometry(dash.x(), y, dash.width(), new_h)
            dash.setFixedSize(dash.width(), new_h)
        else:
            self._set_slot(h)
        self.changed.emit(h)

    def freeze(self):
        """Stop where it is (dashboard closing): the window keeps this height until it hides."""
        self._stop()
        self._target = self._h

    # ── Internals ─────────────────────────────────────────────────────

    def _busy(self) -> bool:
        dash = self._dash
        busy = getattr(dash, '_window_busy', None)
        if busy is not None and busy():
            return True
        return not self.anchored_top and self.overlay_open()

    def overlay_open(self) -> bool:
        """A tile's control panel is open. When the drawer opens above the content,
        moving it would shift the content inside the window and leave the panel
        detached from its tile, so the drawer holds still until it closes."""
        overlays = getattr(self._dash, 'overlay_manager', None)
        return bool(overlays is not None and overlays.any_overlay_open())

    def _try_start(self):
        if self._pending is None:
            return
        if self._busy() or not self._dash.isVisible():
            self._poll.start()
            return
        h, on_done = self._pending
        self._pending = None
        self._poll.stop()
        if self._h == 0:
            # Re-dock: the tray side may have changed since the last notice.
            self._top = self._dash._is_top_anchored()
            self._dock_slot()
        self._target = h
        self._on_done = on_done
        self._rebase()
        if self._h == 0:
            duration = self.OPEN_MS
        elif h == 0:
            duration = self.CLOSE_MS
        else:
            duration = self.RESIZE_MS
        self._anim.setDuration(duration)
        self._anim.setStartValue(float(self._h))
        self._anim.setEndValue(float(h))
        self._anim.start()

    def _rebase(self):
        """Read the window's current size and anchored edge; the drawer adds to that."""
        dash = self._dash
        self._base_h = dash.height() - self._h
        self._edge = dash.y() if self._top else dash.y() + dash.height()
        self._unlock_height()

    def _unlock_height(self):
        # Height only: releasing the width too would let the layout widen the
        # window to its widest page's minimum (the settings panel).
        self._dash.setMinimumHeight(0)
        self._dash.setMaximumHeight(QWIDGETSIZE_MAX)

    def _on_frame(self, value):
        if self._anim.state() == QVariantAnimation.State.Stopped:
            return
        if self._busy() or not self._dash.isVisible():
            # The dashboard is resizing itself: hold still and let it finish.
            self._anim.pause()
            self._poll.start()
            return
        self._apply(int(round(float(value))))

    def _apply(self, h: int):
        dash = self._dash
        self._set_slot(h)
        win_h = self._base_h + h
        y = self._edge if self._top else self._edge - win_h
        dash.setGeometry(dash.x(), y, dash.width(), win_h)
        self.changed.emit(h)

    def _on_poll(self):
        if self._busy() or not self._dash.isVisible():
            return
        self._poll.stop()
        if self._anim.state() == QVariantAnimation.State.Paused:
            self._rebase()
            self._anim.resume()
        else:
            self._try_start()

    def _on_finished(self):
        self._poll.stop()
        self._apply(self._target)
        dash = self._dash
        dash.setFixedSize(dash.width(), dash.height())
        self._fire_done()

    def _fire_done(self):
        done, self._on_done = self._on_done, None
        if done:
            done()

    def _stop(self):
        self._pending = None
        self._poll.stop()
        if self._anim.state() != QVariantAnimation.State.Stopped:
            self._anim.stop()
        self._on_done = None

    def _set_slot(self, h: int):
        self._h = h
        if self.slot is not None:
            try:
                self.slot.setFixedHeight(h)
            except RuntimeError:  # the container (and slot) were rebuilt
                self.slot = None
