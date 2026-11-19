"""
Camera picture-in-picture: one floating window per camera, each fed by Home
Assistant's MJPEG stream.

The stream reconnects on its own. HA's still stream only sends a frame when
the picture changes, so a static camera goes quiet for long stretches; a read
timeout after frames have arrived is therefore a quiet reconnect, not an
error. Real failures (HTTP errors, no route to HA, a stream that ends before
any frame) show a status line over the last frame and back off.

JPEG decoding runs on a worker thread and only the newest frame is decoded,
so a fast native-MJPEG camera can't flood the GUI thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import aiohttp
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QTimer
from PyQt6.QtGui import QImage, QImageReader, QPixmap

from core.camera_stream import MjpegError, MjpegParser
from core.ha_client import CAMERA_ENTITY_RE, HAClient
from core.localization_manager import t
from ui.widgets.camera_pip import CameraPipWindow

# Translation key of the status line shown while a feed can't be read
STATUS_UNAVAILABLE = 'camera_pip.unavailable'

logger = logging.getLogger(__name__)

CONFIG_KEY = "camera_pip"
READ_TIMEOUT = 45  # seconds of silence before a quiet reconnect
BACKOFF = (2, 5, 10, 30, 60)


class _HttpStatus(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


# Failures a stream attempt recovers from by reconnecting; anything else stops the feed.
_STREAM_FAILURES = (_HttpStatus, aiohttp.ClientError, OSError, ValueError, MjpegError)


class _StreamAttempt:
    """One connection attempt; still readable after the stream fails."""

    __slots__ = ("got_frame",)

    def __init__(self):
        self.got_frame = False


class _Latest:
    """Single-slot mailbox: a new item replaces one nobody has taken yet."""

    def __init__(self):
        self._item: Optional[bytes] = None
        self._event = asyncio.Event()

    def put(self, item: bytes):
        self._item = item
        self._event.set()

    async def get(self) -> bytes:
        await self._event.wait()
        self._event.clear()
        item, self._item = self._item, None
        return item


def decode_frame(data: bytes, target: QSize) -> Optional[QImage]:
    """Decode a JPEG (or PNG) frame, shrinking it while decoding when it is
    much bigger than `target`. Safe to call from a worker thread."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    reader = QImageReader(buf)
    size = reader.size()
    if size.isValid() and target.isValid() and size.width() > target.width() and size.height() > target.height():
        # libjpeg decodes straight to 1/2, 1/4 or 1/8 size, which is far
        # cheaper than decoding a 4K frame and scaling it afterwards.
        reader.setScaledSize(size.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding))
    image = reader.read()
    return None if image.isNull() else image


class CameraPipManager:
    def __init__(
        self,
        ha_client: HAClient,
        get_config: Callable[[], dict],
        save_config: Callable[[], None],
        cached_pixmap: Callable[[str], Optional[QPixmap]] = lambda _e: None,
    ):
        self._ha = ha_client
        self._get_config = get_config
        self._save_config = save_config
        self._cached_pixmap = cached_pixmap
        self._windows: dict[str, CameraPipWindow] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # -- public ------------------------------------------------------------------

    def is_open(self, entity_id: str) -> bool:
        return entity_id in self._windows

    def open(self, entity_id: str, title: str = "") -> Optional[CameraPipWindow]:
        """Open a PiP window for a camera, or bring back the one already open."""
        if not isinstance(entity_id, str) or not CAMERA_ENTITY_RE.fullmatch(entity_id):
            logger.warning("Camera PiP: not a camera entity")
            return None
        win = self._windows.get(entity_id)
        if win is not None:
            win.show()
            win.raise_()
            return win

        seed = self._cached_pixmap(entity_id)
        if seed is not None and seed.isNull():
            seed = None
        aspect = seed.width() / max(1, seed.height()) if seed is not None else 16 / 9
        win = CameraPipWindow(title or entity_id, aspect)
        if seed is not None:
            win.set_frame(seed.toImage())
        else:
            win.set_status(t("camera_pip.connecting"))

        saved = self._positions().get(entity_id)
        if not (isinstance(saved, dict) and win.restore(saved)):
            win.place_default(offset=32 * len(self._windows))

        win.close_requested.connect(lambda e=entity_id: self.close(e))
        win.geometry_committed.connect(lambda e=entity_id: self._remember_geometry(e))
        self._windows[entity_id] = win
        win.show()
        self._start(entity_id, win)
        return win

    def reconnect_all(self):
        """Restart every stream. Call after the HA URL or token changed:
        closing the HTTP session does not wake a reader in the middle of a
        streaming response, so the old streams would hang on their last frame."""
        for entity_id, win in list(self._windows.items()):
            task = self._tasks.pop(entity_id, None)
            if task is not None:
                task.cancel()
            self._start(entity_id, win)

    def close(self, entity_id: str):
        task = self._tasks.pop(entity_id, None)
        if task is not None:
            task.cancel()
        win = self._windows.pop(entity_id, None)
        if win is not None:
            win.hide()
            win.deleteLater()

    def close_all(self):
        for entity_id in list(self._windows):
            self.close(entity_id)

    # -- geometry ----------------------------------------------------------------

    def _positions(self) -> dict:
        section = self._get_config().get(CONFIG_KEY)
        positions = section.get("positions") if isinstance(section, dict) else None
        return positions if isinstance(positions, dict) else {}

    def _remember_geometry(self, entity_id: str):
        win = self._windows.get(entity_id)
        if win is None:
            return
        # Read the config at save time: a settings save swaps the dict out.
        config = self._get_config()
        section = config.get(CONFIG_KEY)
        if not isinstance(section, dict):
            section = config[CONFIG_KEY] = {}
        positions = section.get("positions")
        if not isinstance(positions, dict):
            positions = section["positions"] = {}
        positions[entity_id] = win.saved_geometry()
        self._save_config()

    # -- feed --------------------------------------------------------------------

    def _start(self, entity_id: str, win: CameraPipWindow):
        # Started on the next loop turn: opens come from Qt slots, and
        # creating tasks from inside a qasync callback can re-enter it.
        def start():
            if self._windows.get(entity_id) is win and entity_id not in self._tasks:
                self._tasks[entity_id] = asyncio.ensure_future(self._run(entity_id, win))

        QTimer.singleShot(0, start)

    async def _run(self, entity_id: str, win: CameraPipWindow):
        box = _Latest()
        decoder = asyncio.ensure_future(self._decode_loop(entity_id, win, box))
        try:
            await self._read_loop(entity_id, win, box)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Camera PiP feed stopped")
            if self._alive(entity_id, win):
                win.set_status(t(STATUS_UNAVAILABLE))
        finally:
            decoder.cancel()

    async def _read_loop(self, entity_id: str, win: CameraPipWindow, box: _Latest):
        failures = 0
        while True:
            attempt = _StreamAttempt()
            try:
                await self._pump_stream(entity_id, win, box, attempt)
            except _STREAM_FAILURES as exc:
                status, delay = self._failure_status(exc, attempt.got_frame)
            else:
                # HA ended the stream (it does when the camera returns no image).
                status, delay = (None, 1) if attempt.got_frame else (t(STATUS_UNAVAILABLE), None)

            if attempt.got_frame:
                failures = 0
            if delay is None:
                delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
            if status is not None:
                failures += 1
                if self._alive(entity_id, win):
                    win.set_status(status)
            await asyncio.sleep(delay)

    async def _pump_stream(self, entity_id: str, win: CameraPipWindow, box: _Latest, attempt: _StreamAttempt):
        """Read one stream until it ends, handing the newest frame to the decoder."""
        async with self._ha.camera_stream(entity_id, read_timeout=READ_TIMEOUT) as resp:
            if resp.status != 200:
                raise _HttpStatus(resp.status)
            parser = MjpegParser()
            async for chunk in resp.content.iter_any():
                frames = parser.feed(chunk)
                if not frames:
                    continue
                box.put(frames[-1])
                if not attempt.got_frame:
                    attempt.got_frame = True
                    if self._alive(entity_id, win):
                        win.set_status(None)

    @staticmethod
    def _failure_status(exc: Exception, got_frame: bool) -> tuple[Optional[str], Optional[float]]:
        """Status line and retry delay for a failed attempt (None delay: use the backoff)."""
        if isinstance(exc, _HttpStatus):
            logger.info("Camera PiP: HTTP %s", exc.status)
            if exc.status in (401, 403):
                return t("camera_pip.unauthorized"), 30
            if exc.status == 404:
                return t("camera_pip.not_found"), 60
            return t(STATUS_UNAVAILABLE), None
        if isinstance(exc, aiohttp.ConnectionTimeoutError):
            return t("camera_pip.no_connection"), None
        if isinstance(exc, TimeoutError):
            # A read timeout (aiohttp's ServerTimeoutError). After frames
            # this is a static picture, not a fault: reconnect at once.
            return (None, 0) if got_frame else (t("camera_pip.not_responding"), None)
        if isinstance(exc, ValueError):
            return t("camera_pip.not_configured"), 30
        if isinstance(exc, MjpegError):
            logger.info("Camera PiP: %s", exc)
            return t(STATUS_UNAVAILABLE), None
        # aiohttp.ClientError or OSError. Never log the URL; only the kind of failure.
        logger.info("Camera PiP: connection failed (%s)", type(exc).__name__)
        return t("camera_pip.no_connection"), None

    async def _decode_loop(self, entity_id: str, win: CameraPipWindow, box: _Latest):
        loop = asyncio.get_running_loop()
        while True:
            data = await box.get()
            if not self._alive(entity_id, win):
                return
            dpr = win.devicePixelRatioF()
            # Some headroom so a window that grows a little stays sharp.
            target = QSize(round(win.width() * dpr * 1.5), round(win.height() * dpr * 1.5))
            try:
                image = await loop.run_in_executor(None, decode_frame, data, target)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Camera PiP: frame decode failed", exc_info=True)
                continue
            # The window may have been closed while the frame was decoding.
            if image is not None and self._alive(entity_id, win):
                win.set_frame(image)

    def _alive(self, entity_id: str, win: CameraPipWindow) -> bool:
        return self._windows.get(entity_id) is win
