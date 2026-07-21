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


class HAWebSocket(QObject):
    """WebSocket client for Home Assistant real-time updates."""
    
    # Signals
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    state_changed = pyqtSignal(str, dict)  # entity_id, new_state
    notification_received = pyqtSignal(dict)  # full notification payload dict
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
        self._push_channel_id: int = 0   # WS message ID for push_notification_channel
        self.logger = logging.getLogger(__name__)
    
    def configure(self, url: str, token: str):
        """Update connection settings thread-safely."""
        with self._config_lock:
            self.url = url.rstrip('/')
            self.token = token

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
        
        ws_url = current_url.replace('http://', 'ws://').replace('https://', 'wss://')
        ws_url = f"{ws_url}/api/websocket"
        
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(ws_url)
            self._running = True
            
            # Wait for auth_required (bounded — a proxy can accept the socket
            # while HA itself never answers, which would hang the reconnect loop)
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
            if msg.get('type') != 'auth_required':
                raise Exception("Unexpected message type")

            # Send auth
            await self._send({
                "type": "auth",
                "access_token": current_token
            })

            # Wait for auth_ok
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
            if msg.get('type') != 'auth_ok':
                raise Exception(f"Authentication failed: {msg.get('message', 'Unknown error')}")
            
            self.connected.emit()
            
            # Subscribe to state changes
            await self._send({
                "id": self._next_id(),
                "type": "subscribe_events",
                "event_type": "state_changed"
            })
            
            # Subscribe to call_service events (for catching notification creates)
            await self._send({
                "id": self._next_id(),
                "type": "subscribe_events",
                "event_type": "call_service"
            })

            # Subscribe to Mobile App push notifications via WebSocket channel
            # This delivers notify.mobile_app_* calls over the WebSocket instead of HTTP webhook
            if self._webhook_id:
                push_id = self._next_id()
                self._push_channel_id = push_id
                await self._send({
                    "id": push_id,
                    "type": "mobile_app/push_notification_channel",
                    "webhook_id": self._webhook_id,
                    "support_confirm": False,
                })
                self.logger.info(f"[HAWebSocket] Subscribed to push_notification_channel (id={push_id})")
            
            # Start message loop
            await self._message_loop()
            
        except asyncio.CancelledError:
            self._running = False
            raise
        except Exception as e:
            detail = str(e) or type(e).__name__  # TimeoutError stringifies to ""
            self.logger.error(f"WebSocket connection error: {detail}")
            self.error.emit(detail)
        finally:
            await self._cleanup()
    
    async def _message_loop(self):
        """Process incoming WebSocket messages."""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await asyncio.wait_for(
                    self._ws.receive(),
                    timeout=5  # Short timeout to check _running flag more often
                )
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
                    
            except asyncio.TimeoutError:
                # Check if we should stop
                if not self._running:
                    break
                # Send ping to keep connection alive
                if self._ws and not self._ws.closed:
                    try:
                        await self._send({"id": self._next_id(), "type": "ping"})
                    except:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    self.error.emit(str(e))
                break
    
    async def _handle_message(self, data: dict):
        """Handle incoming message."""
        msg_type = data.get('type', '')
        
        if msg_type == 'event':
            # Check if this is a push_notification_channel event
            if self._push_channel_id and data.get('id') == self._push_channel_id:
                event = data.get('event', {})
                title = event.get('title', 'Home Assistant')
                message_text = event.get('message', '')
                extra = event.get('data', {})
                if isinstance(extra, dict):
                    payload = {'title': title, 'message': message_text, **extra}
                else:
                    payload = {'title': title, 'message': message_text}
                if message_text:
                    self.notification_received.emit(payload)
                return

            event = data.get('event', {})
            event_type = event.get('event_type', '')
            event_data = event.get('data', {})
            
            if event_type == 'state_changed':
                entity_id = event_data.get('entity_id', '')
                new_state = event_data.get('new_state', {})
                
                # Check for persistent_notification entities
                if entity_id.startswith('persistent_notification.'):
                    # New notification created
                    if new_state:
                        attrs = new_state.get('attributes', {})
                        title = attrs.get('title', 'Home Assistant')
                        message = attrs.get('message', new_state.get('state', ''))
                        if message:
                            self.notification_received.emit({
                                'title': title,
                                'message': message,
                            })
                
                # Only emit state changes for subscribed entities or all if none specified
                if not self._subscribed_entities or entity_id in self._subscribed_entities:
                    self.state_changed.emit(entity_id, new_state)
            
            elif event_type == 'call_service':
                # Check for persistent_notification.create service calls
                domain = event_data.get('domain', '')
                service = event_data.get('service', '')
                service_data = event_data.get('service_data', {})
                
                if domain == 'persistent_notification' and service == 'create':
                    title = service_data.get('title', 'Home Assistant')
                    message = service_data.get('message', '')
                    if message:
                        self.notification_received.emit({
                            'title': title,
                            'message': message,
                        })
    
    async def _cleanup(self):
        """Clean up WebSocket connection."""
        self._running = False
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except:
            pass
        try:
            if self._session and not self._session.closed:
                await self._session.close()
        except:
            pass
        # Only emit if object still exists
        try:
            self.disconnected.emit()
        except RuntimeError:
            # Object was deleted
            pass
    
    async def disconnect(self):
        """Disconnect from WebSocket."""
        self._running = False
        try:
            await self._cleanup()
        except RuntimeError:
            # Object was deleted
            pass

    async def run_reconnect_loop(self):
        """Run the WebSocket client with auto-reconnection in the main loop."""
        self._should_stop_loop = False
        backoff = 1
        max_backoff = 30
        
        while not self._should_stop_loop:
            try:
                # Reset backoff on successful run
                start_time = time.time()
                
                # Run client connection
                await self.connect()
                
                # If we are here, connect() returned
                if self._should_stop_loop:
                    break
                    
                if time.time() - start_time > 10:
                    backoff = 1  # Reset backoff if we were connected for >10s
                
                self.logger.warning(f"WebSocket disconnected. Reconnecting in {backoff}s...")
                self.error.emit(f"Disconnected. Reconnecting in {backoff}s...")
                
                # Wait for backoff
                for _ in range(backoff * 10):
                    if self._should_stop_loop:
                        break
                    await asyncio.sleep(0.1)
                
                backoff = min(backoff * 2, max_backoff)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"WebSocket Loop Error: {e}")
                if self._should_stop_loop:
                    break
                await asyncio.sleep(1)
