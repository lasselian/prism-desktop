"""
Compositor-side background blur (Glass UI) on native Wayland.

GNOME 51+ blurs whatever lies behind a surface when the client asks for it
through ext-background-effect-v1. Qt doesn't speak that protocol, so this
module does, over ctypes: libwayland-client for the requests, and Qt's native
interfaces for the wl_display, wl_compositor and wl_surface Qt owns.

Mutter offers no blur to XWayland clients, so main.py has to know whether to run
on native Wayland before QApplication exists. PyQt6 is therefore only imported
inside the functions that need a running application.

KDE Plasma is handled in kwin_blur.py instead: KWin blurs XWayland and X11
windows through a window property, so Prism stays on xcb there.

Like floating_window.py, nothing here may take the app down: a missing library
or an API that moved only loses the blur. The one thing that can't be caught is
a protocol error, which ends the Wayland connection, so the surface lifecycle in
WaylandBlurHandle is deliberately strict.
"""

from __future__ import annotations

import ctypes
import functools
import json
import logging
import math
import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path

from core import utils

logger = logging.getLogger(__name__)

_MANAGER_NAME = b"ext_background_effect_manager_v1"
_EFFECT_NAME = b"ext_background_effect_surface_v1"
_CAPABILITY_BLUR = 1
_MARSHAL_FLAG_DESTROY = 1

# Request opcodes, in the order the protocol XML declares them.
_DISPLAY_GET_REGISTRY = 1
_REGISTRY_BIND = 0
_COMPOSITOR_CREATE_REGION = 1
_REGION_DESTROY = 0
_REGION_ADD = 1
_MANAGER_DESTROY = 0
_MANAGER_GET_BACKGROUND_EFFECT = 1
_EFFECT_DESTROY = 0
_EFFECT_SET_BLUR_REGION = 1
_XDG_TOPLEVEL_SET_APP_ID = 3

# Virtual table slots of Qt's native interfaces (two destructors come first;
# QWaylandWindow is a QObject, whose twelve virtuals come first).
_SLOT_DISPLAY = 2
_SLOT_COMPOSITOR = 3
_SLOT_SURFACE = 12

_HANDLE_ATTR = "_prism_wayland_blur"
_APP_ID_WINDOW_ATTR = "_prism_wayland_app_id_window"

Strip = tuple[int, int, int, int]


# -- Desktop and capability detection ------------------------------------------


def is_gnome_wayland_session() -> bool:
    if not sys.platform.startswith("linux") or os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return any("GNOME" in desktop for desktop in desktops)


@functools.lru_cache(maxsize=1)
def probe_wayland_blur_support() -> bool:
    """True if the compositor offers background blur.

    Asks over a connection of its own, so the answer is the same on xcb and
    before QApplication exists.
    """
    if not sys.platform.startswith("linux") or os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        wl = _wayland()
        display = wl.lib.wl_display_connect(None)
        if not display:
            return False
        try:
            manager = _BlurManager(wl, display)
            supported = manager.has_blur
            manager.close()
            return supported
        finally:
            wl.lib.wl_display_disconnect(display)
    except Exception:
        logger.debug("Wayland blur probe failed", exc_info=True)
        return False


def clear_probe_cache() -> None:
    probe_wayland_blur_support.cache_clear()


def is_gnome_glass_supported() -> bool:
    return is_gnome_wayland_session() and probe_wayland_blur_support()


def is_glass_ui_supported() -> bool:
    """True if Glass UI can be offered: Windows, macOS, a blur-capable GNOME, or KWin with blur."""
    if sys.platform.startswith("linux"):
        return is_gnome_glass_supported() or _is_kwin_blur_available()
    return True


def _is_kwin_blur_available() -> bool:
    from ui.utils.kwin_blur import is_kwin_blur_available

    return is_kwin_blur_available()


def is_compositor_blur_active() -> bool:
    """True if the compositor blurs behind our windows: GNOME on native Wayland, or KWin.

    Unlike is_native_wayland_blur_active(), this says nothing about whether the
    window is placed on a Wayland stage; KWin blurs Prism's xcb window directly.
    """
    if not sys.platform.startswith("linux"):
        return False
    return is_native_wayland_blur_active() or _is_kwin_blur_available()


def is_native_wayland_blur_active() -> bool:
    """True if this process can ask the compositor for blur right now."""
    if not sys.platform.startswith("linux"):
        return False
    from PyQt6.QtGui import QGuiApplication

    return QGuiApplication.platformName().startswith("wayland") and is_gnome_glass_supported()


def should_use_native_wayland(config_path: Path | None = None) -> bool:
    """True if Qt should run on Wayland instead of XWayland: Glass UI is on and can work."""
    if not is_gnome_glass_supported():
        return False
    try:
        data = json.loads(Path(config_path or utils.get_config_path()).read_text(encoding="utf-8"))
        return bool(data.get("appearance", {}).get("glass_ui", False))
    except (OSError, ValueError, AttributeError):
        return False


# -- Region geometry -----------------------------------------------------------


def rounded_rect_strips(x: int, y: int, width: int, height: int, radius: int) -> list[Strip]:
    """Rectangles covering a rounded rect, since a wl_region only takes rectangles.

    The corners are built from one-pixel rows (identical neighbours merged),
    rounded inwards so the blur doesn't show outside the painted corner.
    """
    if width <= 0 or height <= 0:
        return []
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        return [(x, y, width, height)]
    insets = [math.ceil(radius - math.sqrt(radius**2 - (radius - row - 0.5) ** 2)) for row in range(radius)]
    strips: list[Strip] = []
    _add_rows(strips, x, y, width, insets)
    if height > 2 * radius:
        strips.append((x, y + radius, width, height - 2 * radius))
    _add_rows(strips, x, y + height - radius, width, insets[::-1])
    return strips


def _add_rows(strips: list[Strip], x: int, top: int, width: int, insets: list[int]) -> None:
    for row, inset in enumerate(insets):
        rx, rw, ry = x + inset, width - 2 * inset, top + row
        if rw <= 0:
            continue
        if strips:
            px, py, pw, ph = strips[-1]
            if (px, pw, py + ph) == (rx, rw, ry):
                strips[-1] = (px, py, pw, ph + 1)
                continue
        strips.append((rx, ry, rw, 1))


# -- libwayland-client ---------------------------------------------------------


class _WlInterface(ctypes.Structure):
    pass


class _WlMessage(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("signature", ctypes.c_char_p),
        ("types", ctypes.POINTER(ctypes.POINTER(_WlInterface))),
    ]


_WlInterface._fields_ = [
    ("name", ctypes.c_char_p),
    ("version", ctypes.c_int),
    ("method_count", ctypes.c_int),
    ("methods", ctypes.POINTER(_WlMessage)),
    ("event_count", ctypes.c_int),
    ("events", ctypes.POINTER(_WlMessage)),
]


class _WlArgument(ctypes.Union):
    """One request argument. A new_id slot stays zero; libwayland fills it."""

    _fields_ = [("i", ctypes.c_int32), ("u", ctypes.c_uint32), ("s", ctypes.c_char_p), ("o", ctypes.c_void_p)]


def _int(value: int) -> _WlArgument:
    return _WlArgument(i=value)


def _uint(value: int) -> _WlArgument:
    return _WlArgument(u=value)


def _string(value: bytes) -> _WlArgument:
    return _WlArgument(s=value)


def _object(pointer: int | None) -> _WlArgument:
    return _WlArgument(o=pointer)


_GLOBAL_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32)
_GLOBAL_REMOVE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_CAPABILITIES_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


class _Wayland:
    """libwayland-client, plus the descriptors of ext-background-effect-v1."""

    def __init__(self):
        lib = ctypes.CDLL("libwayland-client.so.0")
        ptr, num = ctypes.c_void_p, ctypes.c_int
        for name, restype, argtypes in (
            ("wl_display_connect", ptr, [ctypes.c_char_p]),
            ("wl_display_disconnect", None, [ptr]),
            ("wl_display_roundtrip", num, [ptr]),
            ("wl_display_roundtrip_queue", num, [ptr, ptr]),
            ("wl_display_create_queue", ptr, [ptr]),
            ("wl_event_queue_destroy", None, [ptr]),
            ("wl_display_flush", num, [ptr]),
            ("wl_display_get_fd", num, [ptr]),
            ("wl_display_get_error", num, [ptr]),
            ("wl_proxy_create_wrapper", ptr, [ptr]),
            ("wl_proxy_wrapper_destroy", None, [ptr]),
            ("wl_proxy_set_queue", None, [ptr, ptr]),
            ("wl_proxy_add_listener", num, [ptr, ptr, ptr]),
            ("wl_proxy_get_version", ctypes.c_uint32, [ptr]),
            ("wl_proxy_get_class", ctypes.c_char_p, [ptr]),
            ("wl_proxy_destroy", None, [ptr]),
            (
                "wl_proxy_marshal_array_flags",
                ptr,
                [ptr, ctypes.c_uint32, ptr, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_WlArgument)],
            ),
        ):
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes
        self.lib = lib
        self.registry_interface = _WlInterface.in_dll(lib, "wl_registry_interface")
        self.region_interface = _WlInterface.in_dll(lib, "wl_region_interface")
        self._define_protocol(_WlInterface.in_dll(lib, "wl_surface_interface"))

    def _define_protocol(self, surface_interface: _WlInterface) -> None:
        # Everything libwayland points at lives on self, so it is never freed.
        self.manager_interface = _WlInterface(name=_MANAGER_NAME, version=1)
        self.effect_interface = _WlInterface(name=_EFFECT_NAME, version=1)
        pointer_type = ctypes.POINTER(_WlInterface)

        def types(*interfaces):
            entries = [ctypes.pointer(i) if i is not None else pointer_type() for i in interfaces]
            return (pointer_type * len(entries))(*entries)

        no_types = types(None)
        get_effect_types = types(self.effect_interface, surface_interface)
        region_types = types(self.region_interface)
        self._manager_requests = (_WlMessage * 2)(
            _WlMessage(b"destroy", b"", no_types),
            _WlMessage(b"get_background_effect", b"no", get_effect_types),
        )
        self._manager_events = (_WlMessage * 1)(_WlMessage(b"capabilities", b"u", no_types))
        self._effect_requests = (_WlMessage * 2)(
            _WlMessage(b"destroy", b"", no_types),
            _WlMessage(b"set_blur_region", b"?o", region_types),
        )
        self._types = (no_types, get_effect_types, region_types)
        self.manager_interface.method_count = 2
        self.manager_interface.methods = self._manager_requests
        self.manager_interface.event_count = 1
        self.manager_interface.events = self._manager_events
        self.effect_interface.method_count = 2
        self.effect_interface.methods = self._effect_requests

    def request(self, proxy, opcode: int, *args, new=None, version: int | None = None, flags: int = 0):
        """Send a request; returns the new proxy when `new` names its interface."""
        array = (_WlArgument * max(1, len(args)))(*args)
        if version is None:
            version = self.lib.wl_proxy_get_version(proxy)
        interface = ctypes.addressof(new) if new is not None else None
        return self.lib.wl_proxy_marshal_array_flags(proxy, opcode, interface, version, flags, array)


@functools.lru_cache(maxsize=1)
def _wayland() -> _Wayland:
    return _Wayland()


class _BlurManager:
    """The compositor's blur global, bound on `display`.

    With `queue`, every object created here delivers its events to that queue,
    which keeps them away from the queue Qt dispatches on the same display.
    """

    def __init__(self, wl: _Wayland, display: int, queue: int | None = None):
        self.wl = wl
        self.proxy: int | None = None
        self.capabilities = 0
        self._global_name: int | None = None
        # libwayland keeps pointers to these for as long as the proxies live.
        self._callbacks = (
            _GLOBAL_CB(self._on_global),
            _GLOBAL_REMOVE_CB(lambda *_: None),
            _CAPABILITIES_CB(self._on_capabilities),
        )
        on_global, on_remove, on_capabilities = (ctypes.cast(cb, ctypes.c_void_p) for cb in self._callbacks)
        self._registry_listener = (ctypes.c_void_p * 2)(on_global, on_remove)
        self._manager_listener = (ctypes.c_void_p * 1)(on_capabilities)

        registry = self._get_registry(display, queue)
        if not registry:
            raise OSError("no Wayland registry")
        try:
            wl.lib.wl_proxy_add_listener(registry, self._registry_listener, None)
            self._roundtrip(display, queue)
            if self._global_name is not None:
                self._bind(registry)
                self._roundtrip(display, queue)
        except Exception:
            self.close()
            raise
        finally:
            wl.lib.wl_proxy_destroy(registry)

    @property
    def has_blur(self) -> bool:
        return self.proxy is not None and bool(self.capabilities & _CAPABILITY_BLUR)

    def _on_global(self, _data, _registry, name, interface, version):
        if interface == _MANAGER_NAME and version >= 1:
            self._global_name = name

    def _on_capabilities(self, _data, _manager, flags):
        self.capabilities = flags

    def _get_registry(self, display: int, queue: int | None):
        wl = self.wl
        if queue is None:
            return wl.request(display, _DISPLAY_GET_REGISTRY, _object(None), new=wl.registry_interface)
        wrapper = wl.lib.wl_proxy_create_wrapper(display)
        if not wrapper:
            return None
        try:
            wl.lib.wl_proxy_set_queue(wrapper, queue)
            return wl.request(wrapper, _DISPLAY_GET_REGISTRY, _object(None), new=wl.registry_interface)
        finally:
            wl.lib.wl_proxy_wrapper_destroy(wrapper)

    def _bind(self, registry: int) -> None:
        wl = self.wl
        self.proxy = wl.request(
            registry,
            _REGISTRY_BIND,
            _uint(self._global_name),
            _string(_MANAGER_NAME),
            _uint(1),
            _object(None),
            new=wl.manager_interface,
            version=1,
        )
        if self.proxy:
            wl.lib.wl_proxy_add_listener(self.proxy, self._manager_listener, None)

    def _roundtrip(self, display: int, queue: int | None) -> None:
        lib = self.wl.lib
        result = lib.wl_display_roundtrip(display) if queue is None else lib.wl_display_roundtrip_queue(display, queue)
        if result < 0:
            raise OSError("Wayland roundtrip failed")

    def close(self) -> None:
        if self.proxy:
            self.wl.request(self.proxy, _MANAGER_DESTROY, flags=_MARSHAL_FLAG_DESTROY)
            self.proxy = None


# -- Qt native handles ---------------------------------------------------------


def _load_mapped_library(name_fragment: str, symbol: str) -> ctypes.CDLL | None:
    """Open a shared library already mapped into this process that exports `symbol`."""
    try:
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as maps:
            for line in maps:
                parts = line.split(maxsplit=5)
                if len(parts) == 6:
                    path = parts[5].strip()
                    if name_fragment in path:
                        lib = ctypes.CDLL(path)
                        if hasattr(lib, symbol):
                            return lib
    except OSError:
        pass
    return None


def _load_qtgui() -> ctypes.CDLL:
    """The libQt6Gui this process already uses, wherever it was installed."""
    symbol = "_ZNK15QGuiApplication16resolveInterfaceEPKci"
    # Prefer the exact library PyQt6 already mapped (wheels/AppImages bundle
    # their own Qt, which may differ from a system libQt6Gui.so.6).
    mapped = _load_mapped_library("libQt6Gui.so", symbol)
    if mapped is not None:
        return mapped
    for name in (None, "libQt6Gui.so.6"):
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        if hasattr(lib, symbol):
            return lib
    raise OSError("libQt6Gui not found")


class _StdAny(ctypes.Structure):
    """libstdc++ std::any layout for small trivially-copyable pointer payloads."""

    _fields_ = [("manager", ctypes.c_void_p), ("ptr", ctypes.c_void_p)]


def _load_surface_role_fn():
    """Load QWaylandWindow::_surfaceRole() on x86_64 where std::any uses sret."""
    if platform.machine() not in ("x86_64", "AMD64"):
        return None
    symbol = "_ZNK15QtWaylandClient14QWaylandWindow12_surfaceRoleEv"
    lib = _load_mapped_library("libQt6WaylandClient.so", symbol)
    if lib is None:
        return None
    fn = getattr(lib, symbol)
    fn.restype = ctypes.c_void_p
    fn.argtypes = [ctypes.POINTER(_StdAny), ctypes.c_void_p]
    return fn


def _resolve_interface(function, obj_ptr: int, name: bytes, revisions: tuple[int, ...]) -> int | None:
    function.restype = ctypes.c_void_p
    function.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    for revision in revisions:
        interface = function(obj_ptr, name, revision)
        if interface:
            return interface
    return None


def _call_virtual(interface: int, slot: int) -> int | None:
    """Call a pointer-returning, argument-less virtual method on a C++ object."""
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtable[slot])(interface)


class _QtBlurSession:
    """Blur requests on Qt's own Wayland connection."""

    def __init__(self, app):
        from PyQt6 import sip

        self.wl = _wayland()
        self._app_ptr = sip.unwrapinstance(app)
        qtgui = _load_qtgui()
        self._window_resolve = qtgui._ZNK7QWindow16resolveInterfaceEPKci
        self._surface_role_fn = _load_surface_role_fn()
        interface = _resolve_interface(
            qtgui._ZNK15QGuiApplication16resolveInterfaceEPKci, self._app_ptr, b"QWaylandApplication", (2, 1)
        )
        if not interface:
            raise OSError("Qt exposes no Wayland application interface")
        lib = self.wl.lib
        self.display = _call_virtual(interface, _SLOT_DISPLAY)
        if not self.display or lib.wl_display_get_fd(self.display) < 0:
            raise OSError("unexpected wl_display from Qt")
        self.compositor = _call_virtual(interface, _SLOT_COMPOSITOR)
        if not self.compositor or lib.wl_proxy_get_class(self.compositor) != b"wl_compositor":
            raise OSError("unexpected wl_compositor from Qt")
        queue = lib.wl_display_create_queue(self.display)
        if not queue:
            raise OSError("cannot create a Wayland event queue")
        try:
            self.manager = _BlurManager(self.wl, self.display, queue)
            if not self.manager.has_blur:
                self.manager.close()
                raise OSError("the compositor offers no blur")
        except Exception:
            lib.wl_event_queue_destroy(queue)
            raise
        self._queue = queue  # lives as long as the manager

    def alive(self) -> bool:
        """False once the QApplication that owns the display is going or gone."""
        from PyQt6 import sip
        from PyQt6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        return app is not None and not QCoreApplication.closingDown() and sip.unwrapinstance(app) == self._app_ptr

    def _window_interface(self, window) -> int | None:
        from PyQt6 import sip

        return _resolve_interface(self._window_resolve, sip.unwrapinstance(window), b"QWaylandWindow", (1,))

    def surface_of(self, window) -> int | None:
        """The window's current wl_surface; Qt has none while the window is hidden."""
        interface = self._window_interface(window)
        if not interface:
            return None
        surface = _call_virtual(interface, _SLOT_SURFACE)
        if not surface or self.wl.lib.wl_proxy_get_class(surface) != b"wl_surface":
            return None
        return surface

    def watch_surface(self, window, on_destroyed: Callable[[], None]) -> bool:
        """Call on_destroyed as soon as Qt has destroyed the window's wl_surface."""
        from PyQt6 import sip
        from PyQt6.QtCore import QObject

        interface = self._window_interface(window)
        if not interface:
            return False
        sip.wrapinstance(interface, QObject).surfaceDestroyed.connect(on_destroyed)
        return True

    def watch_surface_role(self, window, on_created: Callable[[], None]) -> bool:
        """Call on_created whenever Qt creates the window's Wayland shell role."""
        from PyQt6 import sip
        from PyQt6.QtCore import QObject

        interface = self._window_interface(window)
        if not interface:
            return False
        qobj = sip.wrapinstance(interface, QObject)
        if not hasattr(qobj, "surfaceRoleCreated"):
            return False
        qobj.surfaceRoleCreated.connect(on_created)
        return True

    def clear_toplevel_app_id(self, window) -> bool:
        """Send xdg_toplevel.set_app_id('') on `window`'s current role, if any."""
        if self._surface_role_fn is None:
            return False
        interface = self._window_interface(window)
        if not interface:
            return False
        role = _StdAny()
        self._surface_role_fn(ctypes.byref(role), interface)
        if not role.manager or not role.ptr:
            return False
        if self.wl.lib.wl_proxy_get_class(role.ptr) != b"xdg_toplevel":
            return False
        self.wl.request(role.ptr, _XDG_TOPLEVEL_SET_APP_ID, _string(b""))
        self.flush()
        return True

    def create_effect(self, surface: int) -> int | None:
        return self.wl.request(
            self.manager.proxy,
            _MANAGER_GET_BACKGROUND_EFFECT,
            _object(None),
            _object(surface),
            new=self.wl.effect_interface,
        )

    def set_blur_region(self, effect: int, strips: list[Strip] | None) -> None:
        """Set the pending blur region (None removes it); applies on the next commit."""
        wl = self.wl
        if strips is None:
            wl.request(effect, _EFFECT_SET_BLUR_REGION, _object(None))
            return
        region = wl.request(self.compositor, _COMPOSITOR_CREATE_REGION, _object(None), new=wl.region_interface)
        if not region:
            return
        for strip in strips:
            wl.request(region, _REGION_ADD, *(_int(v) for v in strip))
        wl.request(effect, _EFFECT_SET_BLUR_REGION, _object(region))
        wl.request(region, _REGION_DESTROY, flags=_MARSHAL_FLAG_DESTROY)

    def destroy_effect(self, effect: int) -> None:
        self.wl.request(effect, _EFFECT_DESTROY, flags=_MARSHAL_FLAG_DESTROY)

    def flush(self) -> None:
        self.wl.lib.wl_display_flush(self.display)


_session: tuple[int, _QtBlurSession | None] | None = None


def _qt_session() -> _QtBlurSession | None:
    """One session per QApplication; a failure is remembered, not retried."""
    global _session
    from PyQt6 import sip
    from PyQt6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return None
    key = sip.unwrapinstance(app)
    if _session is None or _session[0] != key:
        try:
            _session = (key, _QtBlurSession(app))
        except Exception as e:
            logger.info("Glass UI blur unavailable (%s)", e)
            _session = (key, None)
    return _session[1]


# -- Per-window blur -----------------------------------------------------------


class WaylandBlurHandle:
    """Keeps the blur behind one top-level widget in step with its surface.

    The protocol allows one effect object per wl_surface and no use of it once
    that surface is gone (both are fatal errors), and Qt replaces the surface
    whenever the platform window is recreated (some Qt versions also on hide).
    So the effect is dropped on hide and before the surface goes, and created
    again once the widget is visible.
    """

    def __init__(self, session, widget, region_fn: Callable[[], tuple[int, int, int, int, int]], enabled=True):
        self._session = session
        self._widget = widget
        self.region_fn = region_fn
        self._enabled = enabled
        self._effect_ptr: int | None = None
        self._surface_ptr: int | None = None
        self._strips: list[Strip] | None = None
        self._window = None
        self._watching_surface = False
        self._filter = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def install(self) -> None:
        self._filter = _make_event_filter(self, self._widget)
        self._widget.installEventFilter(self._filter)
        # The last chance to free the effect of a widget deleted while shown.
        self._widget.destroyed.connect(lambda *_: self._on_widget_destroyed())
        self.sync()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.sync()

    def sync(self) -> None:
        """Bring the compositor up to date; cheap when nothing changed."""
        try:
            self._sync()
        except Exception:
            logger.debug("Wayland blur update failed", exc_info=True)

    def release(self) -> None:
        """Destroy the effect object, leaving nothing that refers to the surface."""
        effect, self._effect_ptr = self._effect_ptr, None
        self._surface_ptr = None
        self._strips = None
        if effect is None:
            return
        try:
            if self._session.alive():
                self._session.destroy_effect(effect)
                self._session.flush()
        except Exception:
            logger.debug("Wayland blur release failed", exc_info=True)

    def _on_widget_destroyed(self) -> None:
        self.release()
        self._widget = None

    def surface_gone(self) -> None:
        """The platform window is being destroyed: forget it along with the surface."""
        self.release()
        self._watching_surface = False

    def _sync(self) -> None:
        if self._widget is None or not self._session.alive():
            return
        window = self._watch_window()
        surface = self._session.surface_of(window) if window is not None else None
        if self._effect_ptr is not None and surface != self._surface_ptr:
            self.release()
        if not self._enabled:
            self._disable()
        elif surface is not None and self._widget.isVisible():
            self._apply(surface)

    def _watch_window(self):
        """The widget's QWindow once it exists, with the filter and signal hooked up."""
        window = self._widget.windowHandle()
        if window is None:
            return None
        if window != self._window:
            self._window = window
            self._watching_surface = False
            if self._filter is not None:
                window.installEventFilter(self._filter)
        if not self._watching_surface:
            self._watching_surface = self._session.watch_surface(window, self.release)
        return window

    def _apply(self, surface: int) -> None:
        strips = rounded_rect_strips(*self.region_fn())
        if self._effect_ptr is not None and strips == self._strips:
            return
        if self._effect_ptr is None:
            self._effect_ptr = self._session.create_effect(surface)
            if self._effect_ptr is None:
                return
            self._surface_ptr = surface
        self._session.set_blur_region(self._effect_ptr, strips)
        self._strips = strips
        self._commit()

    def _disable(self) -> None:
        if self._effect_ptr is None:
            return
        self._session.set_blur_region(self._effect_ptr, None)
        self.release()
        self._commit()

    def _commit(self) -> None:
        """Send the requests; Qt's next frame commits the surface and applies them.

        Committing here instead could run before Qt's first configure, which is
        a protocol error.
        """
        self._session.flush()
        self._widget.update()


def _make_event_filter(handle: WaylandBlurHandle, parent):
    """The QObject that feeds the widget's and its window's events to the handle."""
    from PyQt6.QtCore import QEvent, QObject, QTimer
    from PyQt6.QtGui import QPlatformSurfaceEvent, QWindow

    class _BlurEventFilter(QObject):
        def __init__(self):
            super().__init__(parent)
            # Show and Resize arrive before the surface and layout are ready.
            self._later = QTimer(self)
            self._later.setSingleShot(True)
            self._later.setInterval(0)
            self._later.timeout.connect(handle.sync)

        def eventFilter(self, obj, event):
            kind = event.type()
            if kind == QEvent.Type.PlatformSurface:
                about_to_go = QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed
                if event.surfaceEventType() == about_to_go:
                    handle.surface_gone()
                else:
                    self._later.start()
            elif kind == QEvent.Type.Hide:
                handle.release()
            elif kind in (QEvent.Type.Show, QEvent.Type.Resize):
                self._later.start()
            elif kind == QEvent.Type.UpdateRequest or (
                kind == QEvent.Type.Expose and isinstance(obj, QWindow) and obj.isExposed()
            ):
                # Right before Qt paints, so the region lands with that frame.
                handle.sync()
            return False

    return _BlurEventFilter()


def sync_attached_blur(widget) -> None:
    """Bring the blur attached to `widget`, if there is one, up to date."""
    # __dict__, not getattr: the stage window forwards unknown attributes to its child.
    handle = widget.__dict__.get(_HANDLE_ATTR)
    if handle is not None:
        handle.sync()


def set_attached_blur_enabled(widget, enabled: bool) -> None:
    """Enable or disable the blur attached to `widget`, if there is one."""
    handle = widget.__dict__.get(_HANDLE_ATTR)
    if handle is not None:
        handle.set_enabled(enabled)


def clear_wayland_toplevel_app_id(widget) -> bool:
    """Keep a transparent Wayland stage window's xdg_toplevel app_id empty.

    Extensions such as Blur my Shell apply a full-window blur and opacity layer
    to every xdg_toplevel with a non-empty wm_class. Clearing app_id on
    surfaceRoleCreated (before the initial commit) prevents the maximized stage
    from blurring the whole desktop while keeping our native blur region intact.
    """
    try:
        if not is_native_wayland_blur_active():
            return False
        window = widget.windowHandle()
        if window is None:
            return False
        session = _qt_session()
        if session is None:
            return False
        if widget.__dict__.get(_APP_ID_WINDOW_ATTR) is not window:

            def _clear(_w=window, _s=session):
                try:
                    if _s.alive():
                        _s.clear_toplevel_app_id(_w)
                except Exception:
                    logger.debug("Cannot clear Wayland stage app_id", exc_info=True)

            if session.watch_surface_role(window, _clear):
                widget.__dict__[_APP_ID_WINDOW_ATTR] = window
        return session.clear_toplevel_app_id(window)
    except Exception:
        logger.debug("Cannot clear Wayland stage app_id", exc_info=True)
        return False


def attach_wayland_blur(widget, region_fn, enabled: bool = True) -> WaylandBlurHandle | None:
    """Blur what's behind `widget`, a top-level window.

    region_fn returns (x, y, width, height, corner_radius) in widget
    coordinates. Returns None when native Wayland blur isn't available.
    """
    try:
        if not is_native_wayland_blur_active():
            return None
        handle = getattr(widget, _HANDLE_ATTR, None)
        if handle is not None:
            handle.region_fn = region_fn
            handle.set_enabled(enabled)
            return handle
        session = _qt_session()
        if session is None:
            return None
        handle = WaylandBlurHandle(session, widget, region_fn, enabled)
        handle.install()
        setattr(widget, _HANDLE_ATTR, handle)
        return handle
    except Exception:
        logger.debug("Cannot attach Wayland blur", exc_info=True)
        return None
