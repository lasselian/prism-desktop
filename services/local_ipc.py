"""
Local IPC helpers for controlling an existing Prism Desktop instance.
"""

from __future__ import annotations

import hashlib

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from core.utils import get_config_path


def prism_ipc_server_name() -> str:
    """Return a stable local server name for the current Prism config path."""
    config_path = str(get_config_path().resolve())
    digest = hashlib.sha1(config_path.encode("utf-8")).hexdigest()[:12]
    return f"prism-desktop-{digest}"


def send_local_command(command: str, timeout_ms: int = 1000) -> bool:
    """Send a command to an already-running Prism instance."""
    socket = QLocalSocket()
    socket.connectToServer(prism_ipc_server_name())
    if not socket.waitForConnected(timeout_ms):
        return False

    payload = command.strip().encode("utf-8")
    socket.write(payload)
    if not socket.waitForBytesWritten(timeout_ms):
        socket.disconnectFromServer()
        return False

    socket.flush()
    socket.disconnectFromServer()
    return True


class LocalCommandServer(QObject):
    """Listen for local commands from helper Prism invocations."""

    command_received = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._clients = set()

    def start(self) -> bool:
        """Start listening for local commands."""
        name = prism_ipc_server_name()
        if self._server.listen(name):
            return True

        QLocalServer.removeServer(name)
        return self._server.listen(name)

    def close(self):
        """Stop the local command server."""
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
