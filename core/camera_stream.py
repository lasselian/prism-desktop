"""
Split Home Assistant's MJPEG camera stream into frames.

`/api/camera_proxy_stream/<entity>` answers with multipart/x-mixed-replace.
The declared boundary can't be trusted: HA's own still stream declares
`boundary=--frameboundary` but writes `--frameboundary` lines (a strict parser
looks for `----frameboundary`), and native MJPEG cameras that HA passes
through get it wrong in their own ways. So this parser ignores the header and
learns the delimiter from the stream. Each part is cut by its Content-Length
when it has one, else at the next delimiter line, else at the JPEG end marker.
"""

from __future__ import annotations

SOI = b"\xff\xd8"  # JPEG start-of-image marker
EOI = b"\xff\xd9"  # JPEG end-of-image marker


class MjpegError(Exception):
    """The stream broke the limits or can't be parsed; reconnect."""


class MjpegParser:
    # Matches HAClient.MAX_MEDIA_BYTES: a frame bigger than this isn't a camera.
    MAX_FRAME = 16 * 1024 * 1024
    MAX_HEADER = 16 * 1024

    def __init__(self, max_frame: int = MAX_FRAME, max_header: int = MAX_HEADER):
        self.max_frame = max_frame
        self.max_header = max_header
        self._buf = bytearray()
        self._delim: bytes | None = None  # e.g. b"--frameboundary"
        self._in_body = False
        self._length: int | None = None

    def feed(self, data: bytes) -> list[bytes]:
        """Add stream bytes; return the frames completed by them."""
        self._buf += data
        frames: list[bytes] = []
        while True:
            if not self._in_body:
                if not self._read_headers():
                    break
            else:
                frame = self._read_body()
                if frame is None:
                    break
                if frame:
                    frames.append(frame)
        return frames

    # -- headers ---------------------------------------------------------------

    def _read_headers(self) -> bool:
        self._skip_part_separator()
        buf = self._buf
        if not buf:
            return False

        if buf.startswith(SOI):
            # A part with no headers at all: bare JPEGs back to back.
            self._in_body, self._length = True, None
            return True

        if not self._resync_to_delimiter():
            return False

        end, sep = _find_header_end(buf)
        if end < 0:
            if len(buf) > self.max_header:
                raise MjpegError("part headers too long")
            return False
        if end > self.max_header:
            raise MjpegError("part headers too long")

        length = self._parse_headers(bytes(buf[:end]))
        del buf[: end + sep]
        self._in_body, self._length = True, length
        return True

    def _skip_part_separator(self) -> None:
        """Drop the CRLF (and stray whitespace) that ends the previous part."""
        buf = self._buf
        start = 0
        while start < len(buf) and buf[start] in b"\r\n \t":
            start += 1
        if start:
            del buf[:start]

    def _resync_to_delimiter(self) -> bool:
        """Skip to the next delimiter when out of step (a wrong Content-Length, say).

        Returns False when more data is needed first.
        """
        buf, delim = self._buf, self._delim
        if not delim or buf.startswith(delim):
            return True
        idx = buf.find(delim)
        if idx < 0:
            # Keep a tail that might be the delimiter's first bytes.
            keep = len(delim) - 1
            if len(buf) > keep:
                del buf[: len(buf) - keep]
            return False
        del buf[:idx]
        return True

    def _parse_headers(self, block: bytes) -> int | None:
        """Learn the delimiter from the first part; return the part's Content-Length, if any."""
        length = None
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith(b"--"):
                self._learn_delimiter(line)
            elif line:
                found = self._content_length(line)
                if found is not None:
                    length = found
        return length

    def _learn_delimiter(self, line: bytes) -> None:
        if self._delim is None and len(line) > 2:
            # A closing delimiter ends in an extra "--".
            self._delim = line[:-2] if line.endswith(b"--") and len(line) > 4 else line

    def _content_length(self, line: bytes) -> int | None:
        name, colon, value = line.partition(b":")
        if not colon or name.strip().lower() != b"content-length":
            return None
        value = value.strip()
        if not value.isdigit():
            raise MjpegError("bad Content-Length")
        length = int(value)
        if length > self.max_frame:
            raise MjpegError("frame too large")
        return length

    # -- body ------------------------------------------------------------------

    def _read_body(self) -> bytes | None:
        buf = self._buf
        if self._length is not None:
            if len(buf) < self._length:
                return None
            frame = bytes(buf[: self._length])
            del buf[: self._length]
            self._in_body = False
            return frame

        if self._delim:
            idx = buf.find(self._delim)
            if idx >= 0:
                frame = bytes(buf[:idx]).rstrip(b"\r\n")
                del buf[:idx]
                self._in_body = False
                return frame
        else:
            # No delimiter known: cut at the end-of-image marker.
            idx = buf.find(EOI, 2)
            if idx >= 0:
                frame = bytes(buf[: idx + 2])
                del buf[: idx + 2]
                self._in_body = False
                return frame

        if len(buf) > self.max_frame:
            raise MjpegError("frame too large")
        return None


def _find_header_end(buf: bytearray) -> tuple[int, int]:
    """Index of the blank line ending a header block, and its length."""
    crlf = buf.find(b"\r\n\r\n")
    lf = buf.find(b"\n\n")
    if crlf >= 0 and (lf < 0 or crlf <= lf):
        return crlf, 4
    if lf >= 0:
        return lf, 2
    return -1, 0
