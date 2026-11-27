"""
Home Assistant WebSocket Client
Handles real-time state updates and notifications.
"""

import asyncio
import time
import json
import aiohttp
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal
import threading
import logging

DEFAULT_NOTIFICATION_TITLE = 'Home Assistant'


class AuthenticationError(Exception):
    """Home Assistant rejected the access token."""


_WS_SCHEMES = {"http": "ws", "https": "wss"}


def websocket_url(base_url: str) -> str:
    """Return Home Assistant's WebSocket endpoint for a base URL such as http://ha.local:8123.

    The socket is encrypted exactly when the configured URL is: an HTTPS
    install gets wss, a plain-HTTP install on the LAN gets ws.
    """
    scheme, sep, rest = base_url.partition("://")
    ws_scheme = _WS_SCHEMES.get(scheme.lower(), scheme)
    return f"{ws_scheme}{sep}{rest.rstrip('/')}/api/websocket"


class HAWebSocket(QObject):
    """WebSocket client for Home Assistant real-time updates."""

    # Signals
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    state_changed = pyqtSignal(str, dict)  # entity_id, new_state
    notification_received = pyqtSignal(dict)  # full notification payload dict
    command_received = pyqtSignal(str, dict)  # mobile_app command_* message, extra data dict (plus any push title)
    error = pyqtSignal(str)

    def __init__(self, url: str = "", token: str = ""):
        super().__init__()
        self.url = url.rstrip('/')
        self.token = token
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._message_id = 0
        self._subscribed_entities: set[str] = set()
        self._config_lock = threading.Lock()
        self._webhook_id: str = ""  # Set after Mobile App registration
        self._push_channel_id: int = 0  # WS message ID for push_notification_channel
        self._pending_results: dict[int, asyncio.Future] = {}
        self._pending_pipeline_runs: dict[int, asyncio.Queue] = {}
        self._auth_failed = False  # token rejected: retrying can't help (and risks an IP ban)
        self.logger = logging.getLogger(__name__)

    def set_webhook_id(self, webhook_id: str):
        """Set the Mobile App webhook_id for push notification delivery."""
        self._webhook_id = webhook_id

    def subscribe_entity(self, entity_id: str):
        """Add entity to subscription list."""
        self._subscribed_entities.add(entity_id)

    def _next_id(self) -> int:
        """Get next message ID."""
        self._message_id += 1
        return self._message_id

    def request_stop(self):
        """Thread-safe stop request."""
        self._running = False
        self._should_stop_loop = True  # Signal the reconnect loop to stop

    async def _send(self, data: dict):
        """Send message to WebSocket."""
        if self._ws and not self._ws.closed:
            await self._ws.send_json(data)

    async def connect(self):
        """Connect to Home Assistant WebSocket."""
        # Capture config under lock
        with self._config_lock:
            current_url = self.url
            current_token = self.token

        if not current_url or not current_token:
            self.error.emit("URL and token are required")
            return

        ws_url = websocket_url(current_url)

        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(ws_url)
            self._running = True

            # Wait for auth_required (bounded — a proxy can accept the socket
            # while HA itself never answers, which would hang the reconnect loop)
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
            if msg.get('type') != 'auth_required':
                raise ConnectionError("Unexpected message type")

            await self._send({"type": "auth", "access_token": current_token})

            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
            if msg.get('type') != 'auth_ok':
                raise AuthenticationError(f"Authentication failed: {msg.get('message', 'Unknown error')}")

            self.connected.emit()

            await self._send({"id": self._next_id(), "type": "subscribe_events", "event_type": "state_changed"})

            # Subscribe to call_service events (for catching notification creates)
            await self._send({"id": self._next_id(), "type": "subscribe_events", "event_type": "call_service"})

            # Subscribe to Mobile App push notifications via WebSocket channel
            # This delivers notify.mobile_app_* calls over the WebSocket instead of HTTP webhook
            if self._webhook_id:
                push_id = self._next_id()
                self._push_channel_id = push_id
                await self._send(
                    {
                        "id": push_id,
                        "type": "mobile_app/push_notification_channel",
                        "webhook_id": self._webhook_id,
                        "support_confirm": False,
                    }
                )
                self.logger.info(f"[HAWebSocket] Subscribed to push_notification_channel (id={push_id})")

            await self._message_loop()

        except asyncio.CancelledError:
            self._running = False
            raise
        except Exception as e:
            if isinstance(e, AuthenticationError):
                self._auth_failed = True
            detail = str(e) or type(e).__name__  # TimeoutError stringifies to ""
            self.logger.error(f"WebSocket connection error: {detail}")
            self.error.emit(detail)
        finally:
            await self._cleanup()

    async def _message_loop(self):
        """Process incoming WebSocket messages until the socket closes or a stop is requested."""
        while self._running and self._ws and not self._ws.closed:
            try:
                keep_going = await self._receive_one()
            except Exception as e:
                if self._running:
                    self.logger.warning(f"WebSocket message loop error: {e}", exc_info=True)
                    self.error.emit(str(e))
                break
            if not keep_going:
                break

    async def _receive_one(self) -> bool:
        """Handle one message, or ping after 5 s of silence. False ends the loop."""
        try:
            # Short timeout so a stop request is noticed quickly.
            msg = await asyncio.wait_for(self._ws.receive(), timeout=5)
        except asyncio.TimeoutError:
            return self._running and await self._ping()
        if msg.type == aiohttp.WSMsgType.TEXT:
            await self._handle_message(json.loads(msg.data))
            return True
        return msg.type not in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)

    async def _ping(self) -> bool:
        """Keep the connection alive; False if the ping could not be sent."""
        if self._ws and not self._ws.closed:
            try:
                await self._send({"id": self._next_id(), "type": "ping"})
            except Exception:
                return False
        return True

    async def _handle_message(self, data: dict):
        """Route an incoming message by type; anything but results and events is ignored."""
        msg_type = data.get('type', '')
        if msg_type == 'result':
            await self._handle_result(data)
        elif msg_type == 'event':
            await self._handle_event(data)

    async def _handle_result(self, data: dict):
        msg_id = data.get('id')
        future = self._pending_results.pop(msg_id, None)
        if future and not future.done():
            future.set_result(data)
            return
        # Ack for a streaming command — only surface it if HA rejected the command outright.
        if msg_id in self._pending_pipeline_runs and not data.get('success', True):
            error = data.get('error', {}) or {}
            await self._pending_pipeline_runs[msg_id].put(
                {
                    'type': 'error',
                    'data': {
                        'code': error.get('code', 'unknown'),
                        'message': error.get('message', 'assist_pipeline/run failed'),
                    },
                }
            )

    async def _handle_event(self, data: dict):
        msg_id = data.get('id')
        if msg_id in self._pending_pipeline_runs:
            await self._pending_pipeline_runs[msg_id].put(data.get('event', {}))
            return

        event = data.get('event', {})
        if self._push_channel_id and msg_id == self._push_channel_id:
            self._handle_push_notification(event)
            return

        event_type = event.get('event_type', '')
        event_data = event.get('data', {})
        if event_type == 'state_changed':
            self._handle_state_changed(event_data)
        elif event_type == 'call_service':
            self._handle_service_call(event_data)

    def _handle_push_notification(self, event: dict):
        """A message pushed to this device through the mobile_app notify service."""
        message_text = event.get('message', '')
        if not message_text:
            return
        extra = event.get('data', {})
        extra_dict = extra if isinstance(extra, dict) else {}
        if isinstance(message_text, str):
            normalized = message_text.strip().lower()
            if normalized.startswith('command_'):
                title = event.get('title')
                if isinstance(title, str) and title.strip() and 'title' not in extra_dict:
                    self.command_received.emit(normalized, {'title': title, **extra_dict})
                else:
                    self.command_received.emit(normalized, extra_dict)
                return
        payload = {'title': event.get('title', DEFAULT_NOTIFICATION_TITLE), 'message': message_text}
        if extra_dict:
            payload = {**payload, **extra_dict}
        self.notification_received.emit(payload)

    def _handle_state_changed(self, event_data: dict):
        entity_id = event_data.get('entity_id', '')
        # HA sends new_state: null (not a missing key) when an entity is removed;
        # `.get(key, {})` only substitutes the default for a missing key, so an
        # explicit null would reach `state_changed.emit()` and fail its `dict` type.
        new_state = event_data.get('new_state') or {}

        if entity_id.startswith('persistent_notification.') and new_state:
            # New notification created
            attrs = new_state.get('attributes', {})
            self._emit_notification(
                attrs.get('title', DEFAULT_NOTIFICATION_TITLE), attrs.get('message', new_state.get('state', ''))
            )

        # Only emit state changes for subscribed entities or all if none specified
        if not self._subscribed_entities or entity_id in self._subscribed_entities:
            self.state_changed.emit(entity_id, new_state)

    def _handle_service_call(self, event_data: dict):
        # persistent_notification.create calls show up as notifications too.
        if event_data.get('domain', '') == 'persistent_notification' and event_data.get('service', '') == 'create':
            service_data = event_data.get('service_data', {})
            self._emit_notification(
                service_data.get('title', DEFAULT_NOTIFICATION_TITLE), service_data.get('message', '')
            )

    def _emit_notification(self, title: str, message: str):
        if message:
            self.notification_received.emit({'title': title, 'message': message})

    async def _cleanup(self):
        """Clean up WebSocket connection."""
        self._running = False
        for future in self._pending_results.values():
            if not future.done():
                future.set_exception(ConnectionError("WebSocket disconnected"))
        self._pending_results.clear()
        for queue in self._pending_pipeline_runs.values():
            await queue.put({'type': 'error', 'data': {'code': 'disconnected', 'message': 'WebSocket disconnected'}})
        self._pending_pipeline_runs.clear()
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self._session and not self._session.closed:
                await self._session.close()
        except Exception:
            pass
        # Only emit if object still exists
        try:
            self.disconnected.emit()
        except RuntimeError:
            # Object was deleted
            pass

    async def conversation_process(
        self,
        text: str,
        conversation_id: Optional[str] = None,
        language: Optional[str] = None,
        timeout: float = 15.0,
    ) -> dict:
        """Send text to HA's conversation agent. Returns the 'result' payload
        (contains 'response' and 'conversation_id')."""
        if not self._ws or self._ws.closed:
            raise ConnectionError("WebSocket is not connected")

        msg_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self._pending_results[msg_id] = future

        payload = {"id": msg_id, "type": "conversation/process", "text": text}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if language:
            payload["language"] = language

        try:
            await self._send(payload)
            data = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_results.pop(msg_id, None)

        if not data.get('success', False):
            error = data.get('error', {}) or {}
            raise RuntimeError(error.get('message', 'conversation/process failed'))

        return data.get('result', {})

    # No pipeline event for this long means HA (or the connection) has
    # stalled; HA's own run timeout (default 300s) is far too long to leave
    # the Assist popup stuck on "Thinking…".
    PIPELINE_IDLE_TIMEOUT = 90.0

    async def run_assist_pipeline(
        self,
        conversation_id: Optional[str] = None,
        sample_rate: int = 16000,
        *,
        text: Optional[str] = None,
        end_stage: str = "tts",
        idle_timeout: Optional[float] = None,
    ):
        """Start an Assist pipeline and yield its events as they arrive
        (run-start, stt-*, intent-start, intent-progress, intent-end, tts-*,
        run-end, error).

        With `text`, the run starts at the intent stage, so typed messages go
        to the same (preferred) pipeline and conversation agent as voice.
        Otherwise it starts at STT and `sample_rate` must match the rate of
        the audio actually sent via send_stt_audio.

        Stops after 'run-end' or 'error'. If no event arrives within
        `idle_timeout` seconds, yields a synthetic error with code 'timeout'."""
        if not self._ws or self._ws.closed:
            raise ConnectionError("WebSocket is not connected")

        msg_id = self._next_id()
        queue: asyncio.Queue = asyncio.Queue()
        self._pending_pipeline_runs[msg_id] = queue

        payload = {
            "id": msg_id,
            "type": "assist_pipeline/run",
            "start_stage": "intent" if text is not None else "stt",
            "end_stage": end_stage,
            "input": {"text": text} if text is not None else {"sample_rate": sample_rate},
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        idle = self.PIPELINE_IDLE_TIMEOUT if idle_timeout is None else idle_timeout
        try:
            await self._send(payload)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=idle)
                except asyncio.TimeoutError:
                    yield {'type': 'error', 'data': {'code': 'timeout', 'message': ''}}
                    break
                yield event
                if event.get('type') in ('run-end', 'error'):
                    break
        finally:
            self._pending_pipeline_runs.pop(msg_id, None)

    async def send_stt_audio(self, handler_id: int, chunk: bytes):
        """Send a chunk of raw PCM16LE mono 16kHz audio for a pipeline's STT stage."""
        if self._ws and not self._ws.closed:
            await self._ws.send_bytes(bytes([handler_id]) + chunk)

    async def end_stt_audio(self, handler_id: int):
        """Signal end of audio input for a pipeline's STT stage."""
        if self._ws and not self._ws.closed:
            await self._ws.send_bytes(bytes([handler_id]))

    async def run_reconnect_loop(self):
        """Run the WebSocket client with auto-reconnection in the main loop."""
        self._should_stop_loop = False
        backoff = 1
        max_backoff = 30

        while not self._should_stop_loop:
            try:
                started = time.time()
                await self.connect()
                if not self._may_reconnect():
                    break
                if time.time() - started > 10:
                    backoff = 1  # it was a real session, not a failed attempt

                self.logger.warning(f"WebSocket disconnected. Reconnecting in {backoff}s...")
                self.error.emit(f"Disconnected. Reconnecting in {backoff}s...")
                await self._sleep_unless_stopped(backoff)
                backoff = min(backoff * 2, max_backoff)

            except Exception as e:
                self.logger.error(f"WebSocket Loop Error: {e}", exc_info=True)
                if self._should_stop_loop:
                    break
                await asyncio.sleep(1)

    def _may_reconnect(self) -> bool:
        """After connect() returns: False when stopped or when the token was rejected."""
        if self._should_stop_loop:
            return False
        if self._auth_failed:
            # HA keeps rejecting the same token, and with ip_ban_enabled a few
            # failed logins get this machine banned. Wait for new credentials
            # (saving settings builds a fresh client).
            self.logger.error("WebSocket authentication failed; not retrying with the same token")
            return False
        return True

    async def _sleep_unless_stopped(self, seconds: int):
        for _ in range(seconds * 10):
            if self._should_stop_loop:
                return
            await asyncio.sleep(0.1)
