"""
Local IPC helpers for controlling an existing Prism Desktop instance.
"""

from __future__ import annotations

import hashlib
import os
import socket as _socket
import sys
import tempfile

from core.utils import get_config_path


def prism_ipc_server_name() -> str:
    """Return a stable local server name for the current Prism config path."""
    config_path = str(get_config_path().resolve())
    # Only a name, not a security boundary; sha1 keeps it identical to older versions.
    digest = hashlib.sha1(config_path.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"prism-desktop-{digest}"


def send_local_command(command: str, timeout_ms: int = 1000) -> bool:
    """Send a command to the running Prism instance. Returns False if none is listening."""
    payload = command.strip().encode("utf-8")
    if sys.platform == "win32":
        return _send_over_named_pipe(payload)
    return _send_over_unix_socket(payload, timeout_ms)


def _send_over_unix_socket(payload: bytes, timeout_ms: int) -> bool:
    # QLocalServer on Unix listens on a socket file in the temp directory.
    socket_path = os.path.join(tempfile.gettempdir(), prism_ipc_server_name())
    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_ms / 1000.0)
            sock.connect(socket_path)
            sock.sendall(payload)
        return True
    except OSError:
        return False


def _send_over_named_pipe(payload: bytes) -> bool:
    # QLocalServer on Windows listens on the named pipe \\.\pipe\<server name>.
    try:
        with open(rf"\\.\pipe\{prism_ipc_server_name()}", "wb", buffering=0) as pipe:
            pipe.write(payload)
        return True
    except OSError:
        return False


# LocalCommandServer pulls in PyQt6, so we build the class on first use rather
# than at import time — that way the --toggle helper process stays lightweight.

_server_class = None


def _build_server_class():
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket

    class _LocalCommandServer(QObject):
        command_received = pyqtSignal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._server = QLocalServer(self)
            # Only this user may connect: owner-only socket file on Unix,
            # a current-user ACL on the Windows named pipe.
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self._server.newConnection.connect(self._on_new_connection)
            self._clients = set()

        def start(self) -> bool:
            name = prism_ipc_server_name()
            if self._server.listen(name):
                return True
            QLocalServer.removeServer(name)
            return self._server.listen(name)

        def close(self):
            self._server.close()
            QLocalServer.removeServer(prism_ipc_server_name())

        def _on_new_connection(self):
            while self._server.hasPendingConnections():
                socket = self._server.nextPendingConnection()
                if socket is None:
                    return
                self._clients.add(socket)
                socket.readyRead.connect(lambda s=socket: self._read_socket(s))
                socket.disconnected.connect(lambda s=socket: self._drop_socket(s))

        def _read_socket(self, socket: QLocalSocket):
            raw = bytes(socket.readAll()).decode("utf-8", errors="ignore").strip()
            if raw:
                self.command_received.emit(raw)
            socket.disconnectFromServer()

        def _drop_socket(self, socket: QLocalSocket):
            self._clients.discard(socket)
            socket.deleteLater()

    return _LocalCommandServer


class LocalCommandServer:
    """Thin wrapper — builds the real server class the first time it's needed."""

    def __new__(cls, parent=None):
        global _server_class
        if _server_class is None:
            _server_class = _build_server_class()
        return _server_class(parent)
