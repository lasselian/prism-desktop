"""
Native tweaks for a floating, non-activating window (the camera PiP).

Qt's flags (Tool | Frameless | StaysOnTop | DoesNotAcceptFocus) get most of
the way on every platform. What's left:

Linux (xcb, which Prism forces on Wayland):
  - Qt always advertises WM_TAKE_FOCUS, so GNOME and KWin still focus the
    window on click even with input=False in WM_HINTS. Removing the protocol
    stops that.
  - KWin, unlike mutter, doesn't keep utility windows off the taskbar, and
    Plasma's taskbar only honours _NET_WM_STATE_SKIP_TASKBAR.
macOS:
  - Qt makes a Tool window an NSPanel that hides when the app deactivates and
    moves to the active Space. We want it on every Space, over fullscreen
    apps, and not activating Prism when clicked.
Windows needs nothing: Qt already gives WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE
and HWND_TOPMOST.

Every function here swallows its own errors: a missing library or an API
that moved must never break the window, only lose the tweak.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# AppKit constants, numeric so nothing depends on pyobjc's names.
_NS_CAN_JOIN_ALL_SPACES = 1 << 0
_NS_MOVE_TO_ACTIVE_SPACE = 1 << 1
_NS_FULLSCREEN_AUXILIARY = 1 << 8
_NS_NONACTIVATING_PANEL = 1 << 7


def prepare_before_show(widget: QWidget) -> None:
    """Apply the tweaks that must land before the window is first mapped.

    Call after `widget.winId()` and before `show()`.
    """
    if not (sys.platform.startswith("linux") and QGuiApplication.platformName() == "xcb"):
        return
    try:
        xc = _x11()
        if xc is None:
            return
        wid = int(widget.winId())
        xc.add_initial_states(wid, [b"_NET_WM_STATE_SKIP_TASKBAR", b"_NET_WM_STATE_SKIP_PAGER"])
        xc.drop_take_focus(wid)
    except Exception:
        logger.debug("X11 window tweaks failed", exc_info=True)


def apply_after_show(widget: QWidget) -> None:
    """Apply the tweaks Qt would undo if set earlier. Call from showEvent."""
    if sys.platform != "darwin":
        return
    try:
        _macos_tweak(widget)
    except Exception:
        logger.debug("macOS window tweaks failed", exc_info=True)


# -- macOS ---------------------------------------------------------------------

def _macos_tweak(widget: QWidget) -> None:
    try:
        import objc
        from AppKit import NSPanel
    except ImportError:
        return
    view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
    win = view.window()
    if win is None:
        return
    behavior = int(win.collectionBehavior())
    # Qt gives panels MoveToActiveSpace; AppKit allows only one Spaces option.
    behavior &= ~_NS_MOVE_TO_ACTIVE_SPACE
    behavior |= _NS_CAN_JOIN_ALL_SPACES | _NS_FULLSCREEN_AUXILIARY
    win.setCollectionBehavior_(behavior)
    win.setHidesOnDeactivate_(False)
    if win.isKindOfClass_(NSPanel):
        # Clicking a non-activating panel doesn't bring Prism to the front.
        win.setStyleMask_(int(win.styleMask()) | _NS_NONACTIVATING_PANEL)


# -- X11 -----------------------------------------------------------------------

class _X11:
    """Just enough libX11 to edit WM_PROTOCOLS and _NET_WM_STATE."""

    def __init__(self):
        # find_library is unreliable in frozen builds, so try the soname first.
        try:
            x = ctypes.cdll.LoadLibrary("libX11.so.6")
        except OSError:
            path = ctypes.util.find_library("X11")
            if not path:
                raise OSError("libX11 not found") from None
            x = ctypes.cdll.LoadLibrary(path)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XGetWMProtocols.restype = ctypes.c_int
        x.XGetWMProtocols.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_int),
        ]
        x.XSetWMProtocols.restype = ctypes.c_int
        x.XSetWMProtocols.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int,
        ]
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
        ]
        self.x = x
        # A second connection next to Qt's: window ids are server-wide, and
        # XSync makes sure the change lands before Qt maps the window.
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("cannot open X display")

    def _protocols(self, wid: int) -> list[int]:
        ptr = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_int()
        if not self.x.XGetWMProtocols(self.dpy, wid, ctypes.byref(ptr), ctypes.byref(count)):
            return []
        atoms = [ptr[i] for i in range(count.value)]
        self.x.XFree(ptr)
        return atoms

    def drop_take_focus(self, wid: int) -> None:
        take_focus = self.x.XInternAtom(self.dpy, b"WM_TAKE_FOCUS", False)
        current = self._protocols(wid)
        if take_focus not in current:
            return
        kept = [a for a in current if a != take_focus]
        arr = (ctypes.c_ulong * len(kept))(*kept)
        self.x.XSetWMProtocols(self.dpy, wid, arr, len(kept))
        self.x.XSync(self.dpy, False)

    def add_initial_states(self, wid: int, names: list[bytes]) -> None:
        """Append _NET_WM_STATE atoms to a window that isn't mapped yet.

        Qt merges with, rather than overwrites, the states already there when
        it maps the window (QXcbWindow::setNetWmStateOnUnmappedWindow).
        """
        net_wm_state = self.x.XInternAtom(self.dpy, b"_NET_WM_STATE", False)
        atoms = [self.x.XInternAtom(self.dpy, n, False) for n in names]
        arr = (ctypes.c_ulong * len(atoms))(*atoms)
        xa_atom, prop_mode_append = 4, 2
        self.x.XChangeProperty(self.dpy, wid, net_wm_state, xa_atom, 32,
                               prop_mode_append, ctypes.cast(arr, ctypes.c_void_p), len(atoms))
        self.x.XSync(self.dpy, False)


_x11_conn: _X11 | None = None
_x11_failed = False


def _x11() -> _X11 | None:
    global _x11_conn, _x11_failed
    if _x11_conn is None and not _x11_failed:
        try:
            _x11_conn = _X11()
        except OSError as e:
            _x11_failed = True
            logger.info("Floating-window tweaks unavailable (%s)", e)
    return _x11_conn
