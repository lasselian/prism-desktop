"""
Workarounds for dependencies that break on Python 3.15.

Each patch first checks whether the problem is present and does nothing
otherwise, so they switch themselves off once the dependency ships a fix.
On Python 3.14 and earlier the qasync wrapper still goes in, but only
forwards to the original hook. Call apply() before the app imports
dbus_next and before the first socket is opened on the qasync loop.
"""

import functools
import inspect
import typing

import qasync


def _patch_qasync_hook(name: str) -> bool:
    """Make qasync's _add_reader/_add_writer accept ``context``.

    Python 3.15's selector transports pass ``context=`` to these loop hooks.
    qasync 0.28.0 (its latest release) overrides both without that parameter,
    so the first socket read raises a TypeError inside a loop callback and
    every aiohttp request - REST and the WebSocket - hangs instead of failing.

    On 3.14 and earlier qasync never receives a context there, so its
    callbacks already run in whatever context Qt's socket notifier delivers
    them in. Accepting the argument and dropping it keeps that behaviour.
    """
    loop_cls = qasync._QEventLoop
    original = getattr(loop_cls, name, None)
    if original is None:
        return False
    try:
        if "context" in inspect.signature(original).parameters:
            return False
    except (TypeError, ValueError):
        return False

    def hook(self, fd, callback, *args, context=None):
        return original(self, fd, callback, *args)

    hook.__name__ = hook.__qualname__ = name
    hook.__doc__ = original.__doc__
    setattr(loop_cls, name, hook)
    return True


def _patch_no_type_check_decorator() -> bool:
    """Restore typing.no_type_check_decorator, removed in Python 3.15.

    dbus-next 0.2.3 (its last release) imports it, so on 3.15 dbus_next
    fails to import and the D-Bus notifier, the Wayland global shortcut
    portal and GeoClue location all switch themselves off without a word.
    This is the 3.14 implementation, minus the deprecation warning.
    """
    if hasattr(typing, "no_type_check_decorator"):
        return False

    def no_type_check_decorator(decorator):
        @functools.wraps(decorator)
        def wrapped_decorator(*args, **kwds):
            return typing.no_type_check(decorator(*args, **kwds))

        return wrapped_decorator

    typing.no_type_check_decorator = no_type_check_decorator
    return True


def apply() -> list:
    """Apply every workaround that is needed. Returns the ones applied."""
    applied = [name for name in ("_add_reader", "_add_writer") if _patch_qasync_hook(name)]
    if _patch_no_type_check_decorator():
        applied.append("no_type_check_decorator")
    return applied
