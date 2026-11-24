"""
Queue and placement for the dashboard's notification pill.

Where it shows: in a drawer the dashboard grows for it, on the side away from
the tray (see notice_drawer.py). The pill rides the drawer's moving edge as
the window grows and shrinks, fading in and out with it. Back-to-back notices
keep the drawer open and swap in place. If the screen has no room for the
window to grow, the pill floats over the bottom of the content instead.

What shows when:
- One notice on screen at a time; the rest wait their turn.
- A notice with the same `key` as one showing or waiting replaces it in place
  (download progress, then the result); an identical message just restarts.
- `urgent` notices, the answer to something the user just did, go on screen
  at once and push whatever was showing back to the front of the queue.
- While the dashboard is closed nothing is shown. Notices wait for the next
  open, except `system_when_hidden` ones, which go to the OS notifier.
  Urgent notices are about a moment that has passed by then, so closing the
  dashboard drops them, and switching views drops urgent questions.
- While Assist has the dashboard, notices wait until it gives it back.
- A guide (a notice with pages, such as the first-run walkthrough) turns its
  pages in this same pill and stays until it is finished or skipped. It is a
  question, so closing the dashboard puts it back at the front of the queue,
  on the page it had reached.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.constants import GRID_MARGIN_BOTTOM, GRID_MARGIN_LEFT, ROOT_MARGIN
from ui.managers.notice_drawer import NoticeDrawer
from ui.widgets.notification_pill import Notice, NotificationPill, PillStyle

logger = logging.getLogger(__name__)


class NoticeCenter(QObject):
    MAX_QUEUE = 6
    GAP_ABOVE_FOOTER = 8  # floating fallback
    FLOAT_SLIDE = 8  # room under a floating pill for its slide in/out
    DRAWER_MARGIN = 12  # between the pill and the window edge, matching the footer's margin
    SEEN_SECONDS = 1.5  # a toast on screen this long counts as read if the dashboard closes
    OPEN_DELAY_MS = 320  # let the dashboard's entrance animation land first
    NEXT_DELAY_MS = 90
    SWAP_FADE_MS = 140
    DRAWER_WAIT_MS = 1500  # how long a notice waits for the dashboard to let the drawer open

    def __init__(self, dashboard: QWidget):
        super().__init__(dashboard)
        self._dash = dashboard
        self._queue: list[Notice] = []
        self._current: Notice | None = None
        self._open = dashboard.isVisible()
        self._container: QWidget | None = None
        self._footer: QWidget | None = None
        self._mode: str | None = None  # "drawer" | "float" while the pill is on screen
        self._ride: str | None = None  # "open" | "close": the pill's fade follows the drawer
        self._ride_h = 1
        # Posts the message as an OS notification; set by the app when available.
        self.system_notify: Callable[[str], None] | None = None

        # The pill sits in a transparent holder that clips it: the open part of the
        # drawer while the window grows or shrinks, else just the pill's own area.
        # (A mask can't do this: Qt ignores masks on a widget with a fade effect.)
        self._holder = QWidget(dashboard)
        self._holder.setObjectName("noticeHolder")
        self._holder.setGeometry(0, 0, 0, 0)
        self.pill = NotificationPill(self._holder, shadow_parent=dashboard)
        self.pill.installEventFilter(self)
        self.pill.finished.connect(self._on_finished)
        self.pill.gone.connect(self._on_pill_gone)
        self.pill.advanced.connect(self._on_guide_advanced)
        self.drawer = NoticeDrawer(dashboard)
        self.drawer.changed.connect(self._on_drawer_changed)

        self._pump_timer = QTimer(self)
        self._pump_timer.setSingleShot(True)
        self._pump_timer.timeout.connect(self._pump)
        self._place_timer = QTimer(self)
        self._place_timer.setSingleShot(True)
        self._place_timer.timeout.connect(self.reposition)
        self._drawer_wait = QTimer(self)
        self._drawer_wait.setSingleShot(True)
        self._drawer_wait.timeout.connect(self._drawer_never_opened)

    # ── Public API ────────────────────────────────────────────────────

    @property
    def current(self) -> Notice | None:
        return self._current

    @property
    def queued(self) -> list[Notice]:
        return list(self._queue)

    @property
    def drawer_h(self) -> int:
        """The drawer's share of the window height right now."""
        return self.drawer.height

    def post(self, notice: Notice):
        cur = self._current
        if cur is not None and self._same(cur, notice):
            self._replace_current(notice)
            return
        for i, waiting in enumerate(self._queue):
            if self._same(waiting, notice):
                self._queue[i] = notice
                if notice.urgent and i:
                    self._queue.insert(0, self._queue.pop(i))
                if self._can_show():
                    self._pump_soon(0)
                return

        if not self._open:
            if notice.system_when_hidden and self.system_notify is not None:
                self._run(lambda: self.system_notify(notice.message))
                return
            self._enqueue(notice)
            return

        if self._held:
            self._enqueue(notice)
            return

        if notice.urgent and cur is not None:
            self._shelve_current()
            self.pill.vanish()
            self._ride = None
            self._show(notice)
            return

        self._enqueue(notice)
        # A pending pump means the dashboard is still opening; background news waits for it.
        if notice.urgent or not self._pump_timer.isActive():
            self._pump()

    def attach(self, container: QWidget, footer: QWidget | None, content_layout: QVBoxLayout):
        """Follow this container and footer, and put the drawer in its layout (setup_ui builds new ones)."""
        for old in (self._container, self._footer):
            if old is not None:
                try:
                    old.removeEventFilter(self)
                except RuntimeError:  # already deleted by setup_ui
                    pass
        self._container = container
        self._footer = footer
        for w in (container, footer):
            if w is not None:
                w.installEventFilter(self)
        if self._current is not None:
            self._shelve_current()
        self.pill.vanish()
        self._mode = self._ride = None
        self.drawer.attach(content_layout)
        self.reposition_soon()

    def dismiss(self, key: str):
        """Take away the notice with `key`, whether it is on screen or waiting.

        A question counts as answered "no", as when it is discarded unseen.
        """
        self._discard(lambda n: n.key == key)
        if self._current is not None and self._current.key == key:
            self.pill.leave("dismiss")

    def dashboard_shown(self):
        self._open = True
        if self._current is None and self.pill.isVisible():
            # Reopened during the close animation: start the pill over.
            self.pill.vanish()
            self._mode = self._ride = None
        if self._current is None:
            self._pump_soon(self.OPEN_DELAY_MS)

    def dashboard_closing(self):
        """The close animation started: stop everything where it is; it fades out with the window."""
        self._open = False
        self._pump_timer.stop()
        self.drawer.freeze()
        self.pill.freeze()
        self._ride = None
        if self._current is not None:
            self._shelve_current()
        self._discard(lambda n: n.urgent)

    def dashboard_hidden(self):
        """Hidden: collapse the drawer at once so the next open starts at the normal size."""
        self.dashboard_closing()
        self.pill.vanish()
        self._mode = None
        self.drawer.snap_to(0, resize_window=True)

    def view_changed(self):
        """A view change is starting. A question about the view being left no longer applies."""
        if self._current is not None and self._current.urgent and self._current.is_confirm:
            self._current, dropped = None, self._current
            self.pill.vanish()
            self._mode = self._ride = None
            self._discard_one(dropped)
            # The view change resizes the window from its current height, which
            # absorbs the drawer closing; nothing jumps.
            self.drawer.snap_to(0, resize_window=False)
            self._pump_soon(self.NEXT_DELAY_MS)
        elif self._current is None and self._ride == "close":
            # Already on its way in: let the view change take it the rest of the way.
            self.pill.vanish()
            self._mode = self._ride = None
            self.drawer.snap_to(0, resize_window=False)
        self._discard(lambda n: n.urgent and n.is_confirm)

    def set_held(self, held: bool):
        """Assist is taking over the dashboard (True) or handing it back (False).

        Whether notices are held is read live from the dashboard's own Assist
        flag, so a missed hand-back can't leave the queue stuck; this only acts
        on the change.
        """
        if held:
            self._pump_timer.stop()
            if self._current is not None:
                self._shelve_current()
            self.pill.vanish()
            self._mode = self._ride = None
            self.drawer.snap_to(0, resize_window=True)
        else:
            self._pump_soon(self.OPEN_DELAY_MS)

    def reposition_soon(self):
        self._place_timer.start(0)

    def reposition(self):
        if not self.pill.isVisible():
            return
        if self._mode == "drawer":
            if self._current is not None and self._ride is None:
                # The width changed (Settings widens the window): the message may wrap differently.
                wanted = self.pill.height_for(self._drawer_pill_width()) + self.DRAWER_MARGIN
                if wanted != self.drawer.target:
                    self.drawer.animate_to(wanted)
            self._place_in_drawer()
        elif self._mode == "float" and self._current is not None and not self.pill.leaving:
            pos, size = self._float_geometry()
            self._hold_float(pos, size)
            self.pill.place(QPoint(0, 0), size)

    def raise_pill(self):
        if self.pill.isVisible():
            self.pill.raise_above_siblings()

    # ── Queue ─────────────────────────────────────────────────────────

    @staticmethod
    def _same(a: Notice, b: Notice) -> bool:
        if a.key or b.key:
            return a.key == b.key
        return a.message == b.message and a.kind == b.kind and a.is_confirm == b.is_confirm

    @property
    def _held(self) -> bool:
        return bool(getattr(self._dash, '_assist_active', False))

    def _can_show(self) -> bool:
        return self._open and not self._held

    def _enqueue(self, notice: Notice):
        if notice.urgent:
            self._queue.insert(0, notice)
        else:
            self._queue.append(notice)
        while len(self._queue) > self.MAX_QUEUE:
            self._discard_one(self._queue.pop(self._overflow_index()))

    def _overflow_index(self) -> int:
        """Oldest background toast, else oldest background question, else the newest notice."""
        for i, n in enumerate(self._queue):
            if not n.urgent and not n.is_confirm:
                return i
        for i, n in enumerate(self._queue):
            if not n.urgent:
                return i
        return len(self._queue) - 1

    def _discard(self, matches: Callable[[Notice], bool]):
        keep = []
        for n in self._queue:
            if matches(n):
                self._discard_one(n)
            else:
                keep.append(n)
        self._queue = keep

    def _discard_one(self, notice: Notice):
        """Drop a notice unshown; an unanswered question counts as "no"."""
        if notice.is_confirm and notice.on_reject:
            self._run(notice.on_reject)

    def _shelve_current(self):
        """Put the notice on screen back at the front of the queue, unless it's been read."""
        cur = self._current
        self._current = None
        if cur is None:
            return
        if cur.is_confirm or cur.seen_for() < self.SEEN_SECONDS:
            cur.shown_at = 0.0
            self._queue.insert(0, cur)

    def _pump_soon(self, delay_ms: int):
        if self._can_show():
            self._pump_timer.start(delay_ms)

    def _pump(self):
        if not self._can_show() or self._current is not None:
            return
        if self.pill.isVisible() and self._ride != "close":
            return  # the last one is still leaving; its exit pumps again
        # If the dashboard is busy resizing itself, the drawer waits for it to finish.
        if self._queue:
            if self._ride == "close":
                # A notice arrived while the drawer was closing: turn it around.
                self.pill.vanish()
                self._ride = None
            self._show(self._queue.pop(0))
        elif self.drawer.target > 0 and self._ride != "close":
            self._close_drawer()

    # ── Showing and leaving ───────────────────────────────────────────

    def _show(self, notice: Notice):
        self._current = notice
        self.pill.apply(notice, self._style())
        width = self._drawer_pill_width()
        height = self.pill.height_for(width)
        drawer_h = height + self.DRAWER_MARGIN
        overlay_blocks = not self.drawer.anchored_top and self.drawer.overlay_open()
        if self._container is not None and self.drawer.room_for(drawer_h) and not overlay_blocks:
            self._mode = "drawer"
            self.pill.set_shadow(False)
            self.pill.prepare(QSize(width, height))
            if self.drawer.height == 0:
                self._ride = "open"
                self._ride_h = drawer_h
                self._place_in_drawer()
                self.drawer.animate_to(drawer_h, on_done=self._revealed)
                if self.drawer.height == 0:
                    self._drawer_wait.start(self.DRAWER_WAIT_MS)
            else:
                # The drawer is already out (a notice just left): resize if needed and fade in place.
                self._ride = None
                self.drawer.animate_to(drawer_h)
                self._place_in_drawer()
                self.pill.fade_to(1.0, self.SWAP_FADE_MS, then=self._revealed)
        else:
            self._mode = "float"
            self._ride = None
            if self.drawer.target > 0:
                self.drawer.animate_to(0)
            self.pill.set_shadow(True)
            pos, size = self._float_geometry()
            self._hold_float(pos, size)
            self.pill.present(QPoint(0, 0), size)
            self._revealed()

    def _revealed(self):
        """The pill is fully on screen: start its clock."""
        self._drawer_wait.stop()
        self._ride = None
        if self._current is not None and self.pill.notice is self._current:
            self._current.shown_at = monotonic()
            self.pill.start_countdown()
        self.reposition()

    def _drawer_never_opened(self):
        """The dashboard kept resizing (a long drag, say): show the notice floating instead."""
        if self._current is None or self._mode != "drawer" or self._ride != "open" or self.drawer.height > 0:
            return
        self.drawer.animate_to(0)  # drops the waiting open
        self._mode = "float"
        self._ride = None
        self.pill.set_shadow(True)
        pos, size = self._float_geometry()
        self._hold_float(pos, size)
        self.pill.present(QPoint(0, 0), size)
        self._revealed()

    def _replace_current(self, notice: Notice):
        notice.shown_at = self._current.shown_at if self._current else 0.0
        self._current = notice
        self.pill.apply(notice, self._style())
        if self._ride != "open":
            notice.shown_at = notice.shown_at or monotonic()
            self.pill.start_countdown()
        if self._mode == "drawer":
            drawer_h = self.pill.height_for(self._drawer_pill_width()) + self.DRAWER_MARGIN
            if drawer_h != self.drawer.target:
                if self._ride == "open":
                    self._ride_h = drawer_h
                    self.drawer.animate_to(drawer_h, on_done=self._revealed)
                else:
                    self.drawer.animate_to(drawer_h)
            self._place_in_drawer()
        else:
            self.reposition()

    def _on_finished(self, outcome: str):
        notice = self._current
        self._current = None
        if notice is None:
            return
        if outcome == "confirm":
            callback = notice.on_confirm
        elif outcome == "action":
            callback = notice.on_action
        elif notice.is_confirm:  # "reject", or a question left unanswered
            callback = notice.on_reject
        else:
            callback = None
        if callback is not None:
            self._run(callback)
        self._exit_pill()

    def _exit_pill(self):
        """Take the pill that just finished off screen."""
        if self._current is not None or not self.pill.isVisible():
            return  # a callback already put something else up
        if self._mode != "drawer":
            return  # floating pill: leave() slides it away and `gone` pumps
        if self._queue and self._can_show():
            # More to come: keep the drawer out and swap in place.
            self.pill.fade_to(0.0, self.SWAP_FADE_MS, then=self._swap_out_done)
        else:
            self.pill.claim_exit()
            self._close_drawer()

    def _close_drawer(self):
        self._drawer_wait.stop()
        h = self.drawer.height
        # Pick the ride height so the pill's fade carries on from where it is now
        # (dismissed mid-open, it must not flash to full first).
        reveal = self.pill.get_reveal() if self.pill.isVisible() else 0.0
        progress = 0.3 + 0.7 * max(0.0, min(1.0, reveal))
        self._ride = "close"
        self._ride_h = max(1, round(h / progress)) if h > 0 else 1
        self.drawer.animate_to(0, on_done=self._closed)

    def _swap_out_done(self):
        self.pill.vanish()
        self._mode = None
        self._pump()
        if self._current is None and self.drawer.target > 0 and not self._queue:
            self._close_drawer()

    def _closed(self):
        self._ride = None
        self.pill.vanish()
        self._mode = None
        if self._queue:
            self._pump_soon(self.NEXT_DELAY_MS)

    def _on_pill_gone(self):
        self._mode = None
        self._pump_soon(self.NEXT_DELAY_MS)

    def _on_guide_advanced(self):
        """A guide turned a page: show its new text, and grow or shrink the drawer to fit."""
        if self._current is not None and self._current is self.pill.notice:
            self._replace_current(self._current)

    def _on_drawer_changed(self, h: int):
        if self._mode != "drawer" or not self.pill.isVisible():
            return
        self._place_in_drawer()
        if self._ride in ("open", "close"):
            progress = max(0.0, min(1.0, h / self._ride_h))
            self.pill.set_reveal(max(0.0, min(1.0, (progress - 0.3) / 0.7)))

    # ── Geometry ──────────────────────────────────────────────────────

    def _drawer_pill_width(self) -> int:
        if self._container is None:
            return 300
        return max(160, self._container.width() - 2 * GRID_MARGIN_LEFT)

    def _hold_float(self, pos: QPoint, size: QSize):
        self._holder.setGeometry(QRect(pos, QSize(size.width(), size.height() + self.FLOAT_SLIDE)))

    def _place_in_drawer(self):
        """Put the pill on the drawer's far edge, clipped to the open part of the drawer."""
        container = self._container
        if container is None:
            return
        area = container.geometry()
        width = max(160, area.width() - 2 * GRID_MARGIN_LEFT)
        height = self.pill.height_for(width)
        if self.pill.size() != QSize(width, height):
            self.pill.setFixedSize(width, height)
        slot = self.drawer.slot_rect()
        if self.drawer.anchored_top:
            y = slot.y() + slot.height() - self.DRAWER_MARGIN - height
        else:
            y = slot.y() + self.DRAWER_MARGIN
        x = area.x() + GRID_MARGIN_LEFT
        self._holder.setGeometry(slot)
        self.pill.move(x - slot.x(), y - slot.y())

    def _float_geometry(self) -> tuple[QPoint, QSize]:
        dash = self._dash
        container = self._container
        if container is not None:
            area = container.geometry()
        else:
            area = dash.rect().adjusted(ROOT_MARGIN, ROOT_MARGIN, -ROOT_MARGIN, -ROOT_MARGIN)
        size = self.pill.size_for(max(160, area.width() - 2 * GRID_MARGIN_LEFT))
        x = area.x() + (area.width() - size.width()) // 2
        footer = self._footer
        if footer is not None and footer.isVisible():
            bottom = footer.mapTo(dash, QPoint(0, 0)).y() - self.GAP_ABOVE_FOOTER
        else:
            bottom = area.bottom() - GRID_MARGIN_BOTTOM - self.GAP_ABOVE_FOOTER
        y = max(area.y() + GRID_MARGIN_BOTTOM, bottom - size.height())
        return QPoint(x, y), size

    # ── Plumbing ──────────────────────────────────────────────────────

    @staticmethod
    def _run(callback: Callable[[], object]):
        try:
            callback()
        except Exception:
            logger.exception("Notification callback failed")

    def _style(self) -> PillStyle:
        tm = getattr(self._dash, 'theme_manager', None)
        dark = not (tm and tm.get_effective_theme() == 'light')
        text = tm.get_colors().get('text', '#ffffff') if tm else '#ffffff'
        return PillStyle(dark=dark, glass=bool(getattr(self._dash, '_glass_ui', False)), text=text)

    def eventFilter(self, obj, event):
        if obj is self.pill:
            if event.type() == QEvent.Type.Hide:
                # An empty holder must not catch clicks meant for what's under it.
                self._holder.setGeometry(0, 0, 0, 0)
            return False
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide):
            self.reposition_soon()
        return False
