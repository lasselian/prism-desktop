"""
Compositor-side background blur (Glass UI) on KDE Plasma.

KWin's blur effect blurs what lies behind an X11 window carrying the
_KDE_NET_WM_BLUR_BEHIND_REGION property, a CARDINAL list of x, y, width,
height rectangles in device pixels. On a Plasma Wayland session that includes
XWayland clients, which is how Prism already runs there (main.py forces xcb so
the dashboard can position itself), so one path covers Plasma on X11 and on
Wayland without the stage window GNOME needs.

The property is written over a libxcb connection of our own (window ids are
server-wide). libxcb, not libX11: an X error from a write to a window that just
went away is an event nobody reads here, where Xlib's default handler would
exit the process. As in wayland_blur.py, nothing here may take the app down; a
missing library or a compositor without blur only loses the blur.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import logging
import math
import sys
from collections.abc import Callable

from ui.utils.wayland_blur import Strip, rounded_rect_strips

logger = logging.getLogger(__name__)

_BLUR_ATOM = b"_KDE_NET_WM_BLUR_BEHIND_REGION"
_ATOM_CARDINAL = 6
_PROP_MODE_REPLACE = 0
_HANDLE_ATTR = "_prism_kwin_blur"


class _Cookie(ctypes.Structure):
    _fields_ = [("sequence", ctypes.c_uint)]


class _ScreenIterator(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("rem", ctypes.c_int), ("index", ctypes.c_int)]


class _ReplyHeader(ctypes.Structure):
    """The fixed start of every reply, then the first CARD32 field of its body."""

    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("value", ctypes.c_uint32),  # atom (InternAtom) or owner (GetSelectionOwner)
    ]


class _Xcb:
    """Just enough libxcb to probe for KWin's blur and set the blur region."""

    def __init__(self):
        lib = self._load("libxcb.so.1", "xcb")
        libc = ctypes.CDLL(None)
        ptr, u32, cookie = ctypes.c_void_p, ctypes.c_uint32, _Cookie
        for name, restype, argtypes in (
            ("xcb_connect", ptr, [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]),
            ("xcb_connection_has_error", ctypes.c_int, [ptr]),
            ("xcb_disconnect", None, [ptr]),
            ("xcb_flush", ctypes.c_int, [ptr]),
            ("xcb_poll_for_event", ptr, [ptr]),
            ("xcb_get_setup", ptr, [ptr]),
            ("xcb_setup_roots_iterator", _ScreenIterator, [ptr]),
            ("xcb_screen_next", None, [ctypes.POINTER(_ScreenIterator)]),
            ("xcb_intern_atom", cookie, [ptr, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_char_p]),
            ("xcb_intern_atom_reply", ptr, [ptr, cookie, ptr]),
            ("xcb_get_selection_owner", cookie, [ptr, u32]),
            ("xcb_get_selection_owner_reply", ptr, [ptr, cookie, ptr]),
            ("xcb_list_properties", cookie, [ptr, u32]),
            ("xcb_list_properties_reply", ptr, [ptr, cookie, ptr]),
            ("xcb_list_properties_atoms", ctypes.POINTER(u32), [ptr]),
            ("xcb_list_properties_atoms_length", ctypes.c_int, [ptr]),
            ("xcb_change_property", cookie, [ptr, ctypes.c_uint8, u32, u32, u32, ctypes.c_uint8, u32, ptr]),
            ("xcb_delete_property", cookie, [ptr, u32, u32]),
        ):
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes
        libc.free.argtypes = [ptr]
        libc.free.restype = None
        self.lib, self._free = lib, libc.free

        screen = ctypes.c_int(0)
        self.conn = lib.xcb_connect(None, ctypes.byref(screen))
        if not self.conn or lib.xcb_connection_has_error(self.conn):
            if self.conn:
                lib.xcb_disconnect(self.conn)
            raise OSError("cannot open X display")
        self.screen = screen.value
        self.root = self._root_window(self.screen)
        self.blur_atom = self.atom(_BLUR_ATOM, only_if_exists=True)

    @staticmethod
    def _load(soname: str, name: str) -> ctypes.CDLL:
        # find_library is unreliable in frozen builds, so try the soname first.
        try:
            return ctypes.CDLL(soname)
        except OSError:
            path = ctypes.util.find_library(name)
            if not path:
                raise OSError(f"lib{name} not found") from None
            return ctypes.CDLL(path)

    def _root_window(self, screen: int) -> int:
        it = self.lib.xcb_setup_roots_iterator(self.lib.xcb_get_setup(self.conn))
        for _ in range(screen):
            if it.rem <= 0:
                break
            self.lib.xcb_screen_next(ctypes.byref(it))
        if not it.data:
            raise OSError("no X screen")
        return ctypes.c_uint32.from_address(it.data).value  # xcb_screen_t starts with root

    def _reply_value(self, reply: int | None) -> int:
        if not reply:
            return 0
        try:
            return _ReplyHeader.from_address(reply).value
        finally:
            self._free(reply)

    def atom(self, name: bytes, only_if_exists: bool = False) -> int:
        """The atom's id; 0 when only_if_exists and nobody has created it."""
        cookie = self.lib.xcb_intern_atom(self.conn, int(only_if_exists), len(name), name)
        return self._reply_value(self.lib.xcb_intern_atom_reply(self.conn, cookie, None))

    def compositing_active(self) -> bool:
        """A compositing manager owns _NET_WM_CM_S<screen> (EWMH)."""
        selection = self.atom(b"_NET_WM_CM_S%d" % self.screen)
        if not selection:
            return False
        cookie = self.lib.xcb_get_selection_owner(self.conn, selection)
        return self._reply_value(self.lib.xcb_get_selection_owner_reply(self.conn, cookie, None)) != 0

    def root_has_property(self, atom: int) -> bool:
        cookie = self.lib.xcb_list_properties(self.conn, self.root)
        reply = self.lib.xcb_list_properties_reply(self.conn, cookie, None)
        if not reply:
            return False
        try:
            atoms = self.lib.xcb_list_properties_atoms(reply)
            return any(atoms[i] == atom for i in range(self.lib.xcb_list_properties_atoms_length(reply)))
        finally:
            self._free(reply)

    def set_blur_region(self, window: int, strips: list[Strip]) -> None:
        data = [v for strip in strips for v in strip]
        array = (ctypes.c_uint32 * len(data))(*data)
        self.lib.xcb_change_property(
            self.conn, _PROP_MODE_REPLACE, window, self.blur_atom, _ATOM_CARDINAL, 32, len(data), array
        )

    def clear_blur_region(self, window: int) -> None:
        self.lib.xcb_delete_property(self.conn, window, self.blur_atom)

    def flush(self) -> None:
        self.lib.xcb_flush(self.conn)
        # Errors from writes to windows that are already gone arrive as events.
        while event := self.lib.xcb_poll_for_event(self.conn):
            self._free(event)


@functools.lru_cache(maxsize=1)
def _xcb() -> _Xcb | None:
    try:
        return _Xcb()
    except Exception as e:  # ctypes raises OSError, AttributeError for missing symbols
        logger.info("KWin blur unavailable (%s)", e)
        return None


@functools.lru_cache(maxsize=1)
def _probe() -> bool:
    xcb = _xcb()
    if xcb is None or not xcb.blur_atom:
        return False
    try:
        return xcb.compositing_active() and xcb.root_has_property(xcb.blur_atom)
    except Exception:
        logger.debug("KWin blur probe failed", exc_info=True)
        return False


def clear_probe_cache() -> None:
    _probe.cache_clear()


def is_kwin_blur_available() -> bool:
    """True if Qt runs on X11 (or XWayland) and KWin's blur effect is enabled.

    The same check KWindowEffects makes: compositing is on and KWin announced
    the blur atom on the root window. Needs a running QGuiApplication.
    """
    if not sys.platform.startswith("linux"):
        return False
    from PyQt6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None or QGuiApplication.platformName() != "xcb":
        return False
    return _probe()


def device_strips(region: tuple[int, int, int, int, int], ratio: float) -> list[Strip]:
    """Rounded-rect strips for a logical region, in the device pixels X11 uses."""
    x, y, width, height, radius = region
    left, top = math.floor(x * ratio), math.floor(y * ratio)
    return rounded_rect_strips(left, top, math.ceil(width * ratio), math.ceil(height * ratio), round(radius * ratio))


# -- Per-window blur -----------------------------------------------------------


class KWinBlurHandle:
    """Keeps the blur region on one top-level widget's X11 window up to date.

    Same interface as WaylandBlurHandle (region_fn, enabled, set_enabled, sync),
    but simpler: the property lives on the window, goes with it, and may be
    rewritten at any time, so there is no lifecycle to guard.
    """

    def __init__(self, xcb: _Xcb, widget, region_fn: Callable[[], tuple[int, int, int, int, int]], enabled=True):
        self._xcb = xcb
        self._widget = widget
        self.region_fn = region_fn
        self._enabled = enabled
        self._written: tuple[int, list[Strip]] | None = None  # (window id, strips) last sent
        self._window = None
        self._filter = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def install(self) -> None:
        self._filter = _make_event_filter(self, self._widget)
        self._widget.installEventFilter(self._filter)
        self._widget.destroyed.connect(lambda *_: self._on_widget_destroyed())
        self.sync()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.sync()

    def sync(self) -> None:
        """Bring the property up to date; cheap when nothing changed."""
        try:
            self._sync()
        except Exception:
            logger.debug("KWin blur update failed", exc_info=True)

    def window_gone(self) -> None:
        """The X11 window is being destroyed, and its property with it."""
        self._written = None

    def _on_widget_destroyed(self) -> None:
        self._written = None
        self._widget = None

    def _sync(self) -> None:
        if self._widget is None:
            return
        window = self._widget.windowHandle()
        if window is None:
            return
        if window != self._window:
            self._window = window
            if self._filter is not None:
                window.installEventFilter(self._filter)
        wid = int(window.winId())
        strips = device_strips(self.region_fn(), window.devicePixelRatio()) if self._enabled else []
        if (self._written is None and not strips) or self._written == (wid, strips):
            return
        if strips:
            self._xcb.set_blur_region(wid, strips)
            self._written = (wid, strips)
        else:
            # An empty property would blur the whole window, margin included.
            if self._written is not None and self._written[0] == wid:
                self._xcb.clear_blur_region(wid)
            self._written = None
        self._xcb.flush()


def _make_event_filter(handle: KWinBlurHandle, parent):
    """The QObject that feeds the widget's and its window's events to the handle."""
    from PyQt6.QtCore import QEvent, QObject, QTimer
    from PyQt6.QtGui import QPlatformSurfaceEvent

    class _KWinBlurEventFilter(QObject):
        def __init__(self):
            super().__init__(parent)
            # Show and Resize arrive before the window and layout are ready.
            self._later = QTimer(self)
            self._later.setSingleShot(True)
            self._later.setInterval(0)
            self._later.timeout.connect(handle.sync)

        def eventFilter(self, obj, event):
            kind = event.type()
            if kind == QEvent.Type.PlatformSurface:
                about_to_go = QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed
                if event.surfaceEventType() == about_to_go:
                    handle.window_gone()
                else:
                    self._later.start()
            elif kind in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WinIdChange):
                self._later.start()
            elif kind == QEvent.Type.UpdateRequest:
                # Right before Qt paints, so the region follows layout changes.
                handle.sync()
            return False

    return _KWinBlurEventFilter()


def attach_kwin_blur(widget, region_fn, enabled: bool = True) -> KWinBlurHandle | None:
    """Blur what's behind `widget`, a top-level window, through KWin.

    region_fn returns (x, y, width, height, corner_radius) in logical widget
    coordinates. Returns None when KWin's blur isn't available.
    """
    try:
        if not is_kwin_blur_available():
            return None
        handle = widget.__dict__.get(_HANDLE_ATTR)
        if handle is not None:
            handle.region_fn = region_fn
            handle.set_enabled(enabled)
            return handle
        xcb = _xcb()
        if xcb is None:
            return None
        handle = KWinBlurHandle(xcb, widget, region_fn, enabled)
        handle.install()
        widget.__dict__[_HANDLE_ATTR] = handle
        return handle
    except Exception:
        logger.debug("Cannot attach KWin blur", exc_info=True)
        return None
