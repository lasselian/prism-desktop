"""
Prism - Home Assistant Tray Application
Main entry point and application controller.
"""

import sys
import json
import time
from pathlib import Path
from typing import Optional
import logging

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)



from core.utils import get_config_path
import keyring
import copy

SERVICE_NAME = "PrismDesktop"
KEY_TOKEN = "ha_token"

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, pyqtSlot, QThread, QThreadPool, QRunnable
from PyQt6.QtGui import QPixmap

from ui.theme_manager import ThemeManager
from core.ha_client import HAClient
from core.ha_websocket import HAWebSocket, WebSocketThread
from ui.dashboard import Dashboard
from core.worker_threads import EntityFetchThread
from ui.tray_manager import TrayManager
from services.notifications import NotificationManager
from services.input_manager import InputManager
from ui.icons import load_mdi_font

VERSION = "1.2"

class ServiceCallSignals(QObject):
    """Signals for ServiceCallRunnable."""
    call_complete = pyqtSignal(bool)


class ServiceCallRunnable(QRunnable):
    """Executes Home Assistant service calls in a background thread."""
    
    def __init__(self, client: HAClient, domain: str, service: str, entity_id: str, data: dict = None):
        super().__init__()
        self.client = client
        self.domain = domain
        self.service = service
        self.entity_id = entity_id
        self.data = data
        self.signals = ServiceCallSignals()
    
    def run(self):
        """Call the service in background."""
        try:
            # Use the shared client which is thread-safe for requests
            result = self.client.call_service(
                self.domain, self.service, self.entity_id, self.data
            )
            if not result:
                 # If call_service returned False (e.g. 404 or 500), we should know
                 raise Exception("Service call failed (HTTP Error)")
            
            self.signals.call_complete.emit(True)
        except Exception as e:
            print(f"Service call error: {e}")
            self.signals.call_complete.emit(False)


class StateFetchThread(QThread):
    """Fetches entity states in a background thread."""
    
    state_fetched = pyqtSignal(str, dict)
    
    def __init__(self, client: HAClient, entity_ids: list):
        super().__init__()
        self.client = client
        self.entity_ids = entity_ids
    
    def run(self):
        """Fetch states in background."""
        try:
            for entity_id in self.entity_ids:
                try:
                    state = self.client.get_state(entity_id)
                    if state:
                        self.state_fetched.emit(entity_id, state)
                except Exception as e:
                    print(f"Error fetching {entity_id}: {e}")
        except Exception as e:
            print(f"State fetch error: {e}")


class CameraFetchThread(QThread):
    """Fetches camera images in a background thread."""
    
    image_fetched = pyqtSignal(str, bytes)  # entity_id, image_bytes
    
    def __init__(self, client: HAClient, entity_ids: list):
        super().__init__()
        self.client = client
        self.entity_ids = entity_ids
    
    def run(self):
        """Fetch camera images."""
        for entity_id in self.entity_ids:
            try:
                # Use get_camera_image from client
                image_bytes = self.client.get_camera_image(entity_id)
                if image_bytes:
                    print(f"Fetched camera image for {entity_id} ({len(image_bytes)} bytes)")
                    self.image_fetched.emit(entity_id, image_bytes)
            except Exception as e:
                print(f"Error fetching camera {entity_id}: {e}")

class CameraStreamThread(QThread):
    """
    Streams MJPEG from a camera in a background thread.
    Parses the multipart stream to extract JPEG frames.
    """
    
    image_fetched = pyqtSignal(str, bytes)
    
    def __init__(self, client: HAClient, entity_id: str):
        super().__init__()
        self.client = client
        self.entity_id = entity_id
        self._running = True
    
    def stop(self):
        self._running = False
        self.wait()
    
    def run(self):
        """Connect to stream and parse MJPEG; fallback to polling if stream fails."""
        print(f"Starting Live Thread for {self.entity_id}")
        
        while self._running:
            # 1. Attempt Streaming
            try:
                response = self.client.stream_camera(self.entity_id)
                if response and response.status_code == 200:
                    print(f"Stream connected for {self.entity_id}")
                    chunk_count = 0
                    buffer = b""
                    
                    for chunk in response.iter_content(chunk_size=4096):
                        if not self._running:
                            break
                        
                        chunk_count += 1
                        buffer += chunk
                        
                        while True:
                            start = buffer.find(b'\xff\xd8')
                            end = buffer.find(b'\xff\xd9')
                            
                            if start != -1 and end != -1 and end > start:
                                jpg_data = buffer[start:end+2]
                                self.image_fetched.emit(self.entity_id, jpg_data)
                                buffer = buffer[end+2:]
                            else:
                                break
                                
                        # Safety limit
                        if len(buffer) > 5 * 1024 * 1024:
                             buffer = b""

                    # Stream ended (expected if connection closed)
                    print(f"Stream ended for {self.entity_id} after {chunk_count} chunks")
                    
                    if chunk_count > 10:
                        # Logic: If successfully streamed > 10 chunks, retry streaming immediately.
                        # It might just be network blip.
                        time.sleep(1)
                        continue
                    else:
                        # < 10 chunks = immediate failure. Switching to Fallback Polling.
                        print(f"Stream unstable/unsupported for {self.entity_id}, switching to Polling Fallback")
                else:
                    print(f"Stream connection failed for {self.entity_id}, switching to Polling Fallback")
            
            except Exception as e:
                print(f"Stream error for {self.entity_id}: {e}")
                
            # 2. Fallback Polling (Run this loop until stopped or retry interval passed)
            # Actually, let's just use polling if streaming failed immediately.
            # We'll retry streaming every 60s maybe? Or just stick to polling.
            # Let's stick to polling for now as it's robust.
            
            poll_start = time.time()
            while self._running:
                try:
                    # Fetch Snapshot
                    img = self.client.get_camera_image(self.entity_id)
                    if img:
                        self.image_fetched.emit(self.entity_id, img)
                    
                    # Sleep consistent with desired FPS (e.g. 0.5 FPS = 2.0s)
                    # Reduced from 10 FPS to save resources as per audit
                    time.sleep(2.0) 
                    
                except Exception as e:
                    print(f"Polling error for {self.entity_id}: {e}")
                    time.sleep(2.0) # Error backoff
                
                # Retry streaming after 60s of polling?
                # Uncomment to retry:
                # if time.time() - poll_start > 60:
                #     break 
            
            # If polling loop breaks (e.g. for retry), outer loop continues to Try Streaming again.
            if not self._running:
                break
                
        print(f"Live Thread stopped for {self.entity_id}")


class PrismDesktopApp(QObject):
    """Main application controller."""
    
    def __init__(self):
        super().__init__()
        
        # Configuration
        self.config_path = get_config_path("config.json")
        self.config = self.load_config()
        self.save_config() # Scrub sensitive data (tokens) from disk immediately
        
        # Components
        self.theme_manager = ThemeManager()
        self.ha_client = HAClient()
        self.notification_manager = NotificationManager()
        self.input_manager = InputManager()
        
        # Thread Pool
        self.thread_pool = QThreadPool()
        print(f"Thread Pool Max Threads: {self.thread_pool.maxThreadCount()}")
        
        # UI Components
        self.dashboard: Optional[Dashboard] = None
        self.tray_manager: Optional[TrayManager] = None
        
        # WebSocket - will be created fresh each time
        self._ha_websocket: Optional[HAWebSocket] = None
        self._ws_thread: Optional[WebSocketThread] = None
        
        # Background threads - referenced to prevent garbage collection
        self._fetch_thread: Optional[StateFetchThread] = None
        self._entity_list_thread: Optional[EntityFetchThread] = None # For editor
        
        # Cache for entity list (for editor)
        self._available_entities: list[dict] = []
        
        # Debounce tracking
        self._last_click_time: dict[str, float] = {}  # entity_id -> timestamp
        self._click_cooldown = 0.5
        
        # Camera refresh
        self._picture_thread: Optional[CameraFetchThread] = None
        # Map entity_id -> CameraStreamThread
        self._stream_threads: dict[str, CameraStreamThread] = {}
        
        self._camera_timer = QTimer()
        self._camera_timer.timeout.connect(self._refresh_picture_cameras)
        self._camera_refresh_interval = 10000  # 10 seconds for picture mode
        
        # Stream camera refresh (NO LONGER USED as streams are continuous)
        # We keep the timer object just in case cleaner removal is needed or for watchdog
        # But effectively we won't start it.
        # self._stream_camera_timer = QTimer() 
        # self._stream_camera_timer.timeout.connect(self._refresh_stream_cameras)
        # self._stream_refresh_interval = 1000  # 1 second for stream mode
        
        # Initialize
        self.init_theme()
        self.init_ha_client()
        self.init_ui()
        self.init_shortcuts()
        self.start_websocket()
    
    def init_shortcuts(self):
        """Initialize global shortcuts."""
        shortcut_config = self.config.get('shortcut', {'type': 'keyboard', 'value': '<ctrl>+<alt>+h'})
        self.input_manager.update_shortcut(shortcut_config)
        self.input_manager.triggered.connect(self._toggle_dashboard)
    
    def load_config(self) -> dict:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                    # --- Keyring Migration & Loading ---
                    ha_config = config.get('home_assistant', {})
                    token_in_file = ha_config.get('token', '')
                    
                    # 1. Try to load from keyring
                    token_from_keyring = None
                    try:
                        token_from_keyring = keyring.get_password(SERVICE_NAME, KEY_TOKEN)
                    except Exception as e:
                        print(f"Keyring read error: {e}")
                    
                    if token_from_keyring:
                        # Case A: Keyring has token -> Use it (ignoring file)
                        ha_config['token'] = token_from_keyring
                    elif token_in_file:
                        # Case B: Keyring Empty, File has token -> Migrate
                        print("Migrating token to keyring...")
                        try:
                            keyring.set_password(SERVICE_NAME, KEY_TOKEN, token_in_file)
                            # Verify it saved?
                            if keyring.get_password(SERVICE_NAME, KEY_TOKEN) == token_in_file:
                                print("Migration successful. Scrubbing token from file immediately.")
                                # Important: Remove from file immediately
                                ha_config['token'] = '' 
                                self.save_config()
                                # Restore to memory for use
                                ha_config['token'] = token_in_file
                        except Exception as e:
                            print(f"Migration failed: {e}")
                            # Keep token in config object so app works, but it remains in file for now.
                    
                    return config
            except Exception as e:
                print(f"Error loading config: {e}")
        
        return {
            "home_assistant": {"url": "", "token": ""},
            "appearance": {"theme": "system", "rows": 2},
            "shortcut": {"type": "keyboard", "value": "<ctrl>+<alt>+h", "modifier": "Alt"},
            "buttons": []
        }
    
    def save_config(self):
        """Save configuration to file."""
        try:
            # Create a deep copy to modify for saving
            config_to_save = copy.deepcopy(self.config)
            
            # Extract token
            ha_config = config_to_save.get('home_assistant', {})
            token = ha_config.get('token', '')
            
            if token:
                # Save to keyring
                try:
                    keyring.set_password(SERVICE_NAME, KEY_TOKEN, token)
                    # Successfully saved to keyring, so remove from file payload
                    ha_config['token'] = '' 
                except Exception as e:
                    print(f"Keyring write error: {e}")
                    # FAIL SECURE: Do NOT write token to file even if keyring fails.
                    # User will have to re-login if keyring is broken, but we don't leak credentials.
                    ha_config['token'] = '' 
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def init_theme(self):
        """Initialize theming."""
        theme = self.config.get('appearance', {}).get('theme', 'system')
        self.theme_manager.set_theme(theme)
    
    def init_ha_client(self):
        """Initialize Home Assistant client."""
        ha_config = self.config.get('home_assistant', {})
        self.ha_client.configure(
            url=ha_config.get('url', ''),
            token=ha_config.get('token', '')
        )
    
    def init_ui(self):
        """Initialize UI components."""
        # Create dashboard
        rows = self.config.get('appearance', {}).get('rows', 2)
        self.dashboard = Dashboard(config=self.config, theme_manager=self.theme_manager, input_manager=self.input_manager, version=VERSION, rows=rows)
        self.dashboard.set_buttons(self.config.get('buttons', []), self.config.get('appearance', {}))
        
        # Connect signals
        self.dashboard.button_clicked.connect(self.on_button_clicked)
        self.dashboard.add_button_clicked.connect(self.on_edit_button_requested) # Open editor on add
        self.dashboard.edit_button_requested.connect(self.on_edit_button_requested)
        self.dashboard.duplicate_button_requested.connect(self.on_duplicate_button_requested)
        self.dashboard.clear_button_requested.connect(self.on_clear_button_requested)
        self.dashboard.buttons_reordered.connect(self.on_buttons_reordered)
        self.dashboard.climate_value_changed.connect(self.on_climate_value_changed)  # Climate control
        self.dashboard.settings_saved.connect(self._on_embedded_settings_saved)  # Embedded settings
        self.dashboard.rows_changed.connect(self.fetch_initial_states)  # Refresh states after row change
        self.dashboard.edit_button_saved.connect(self.on_edit_button_saved) # Embedded button editor
        self.dashboard.save_config_requested.connect(self.save_config) # Save layout changes (resize)
        
        # Initialize embedded SettingsWidget
        self.dashboard._init_settings_widget(self.config, self.input_manager)
        
        # Create tray manager
        self.tray_manager = TrayManager(
            on_left_click=self._toggle_dashboard,
            on_settings=self._show_settings,
            on_quit=self._quit,
            theme=self.theme_manager.get_effective_theme()
        )
        self.tray_manager.start()
        
        # Connect theme changes to tray
        self.theme_manager.theme_changed.connect(self.tray_manager.set_theme)
    
    def start_websocket(self):
        """Start a new WebSocket connection."""
        ha_config = self.config.get('home_assistant', {})
        if not ha_config.get('url') or not ha_config.get('token'):
            print("No HA config, skipping WebSocket")
            return
        
        print("Starting WebSocket connection...")
        
        # Create fresh WebSocket client
        self._ha_websocket = HAWebSocket(
            url=ha_config.get('url', ''),
            token=ha_config.get('token', '')
        )
        
        # Subscribe to configured entities
        for btn in self.config.get('buttons', []):
            entity_id = btn.get('entity_id')
            if entity_id:
                self._ha_websocket.subscribe_entity(entity_id)
        
        # Connect signals
        self._ha_websocket.state_changed.connect(self.on_state_changed)
        self._ha_websocket.notification_received.connect(self.on_notification)
        self._ha_websocket.connected.connect(self.on_ws_connected)
        self._ha_websocket.disconnected.connect(self.on_ws_disconnected)
        self._ha_websocket.error.connect(self.on_ws_error)
        
        # Create and start thread
        self._ws_thread = WebSocketThread(self._ha_websocket)
        self._ws_thread.start()
    
    def stop_websocket(self, on_finished=None):
        """Stop the WebSocket connection gracefully (async)."""
        print("Stopping WebSocket...")
        
        # IMPORTANT: Disconnect signals FIRST to prevent ghost emissions
        if self._ha_websocket:
            try:
                self._ha_websocket.state_changed.disconnect(self.on_state_changed)
                self._ha_websocket.notification_received.disconnect(self.on_notification)
                self._ha_websocket.connected.disconnect(self.on_ws_connected)
                self._ha_websocket.disconnected.disconnect(self.on_ws_disconnected)
                self._ha_websocket.error.disconnect(self.on_ws_error)
            except TypeError:
                pass
        
        # Helper to cleanup
        def delete_ws_obj():
            if self._ha_websocket:
                self._ha_websocket.deleteLater()
                self._ha_websocket = None
            if on_finished:
                on_finished()
        
        if self._ws_thread:
            # Stop is now NON-BLOCKING
            self._ws_thread.stop()
            old_thread = self._ws_thread
            self._ws_thread = None
            
            if old_thread.isRunning():
                # Connect cleanup to signal
                old_thread.finished.connect(old_thread.deleteLater)
                old_thread.finished.connect(delete_ws_obj)
            else:
                old_thread.deleteLater()
                delete_ws_obj()
        else:
            delete_ws_obj()
        
        print("WebSocket stop requested")
    
    def stop_fetch_thread(self):
        """Stop the fetch thread and wait for it to finish."""
        if self._fetch_thread:
            if self._fetch_thread.isRunning():
                self._fetch_thread.quit()
                self._fetch_thread.wait(2000)
            old_thread = self._fetch_thread
            self._fetch_thread = None
            old_thread.deleteLater()

    def stop_entity_list_thread(self):
        """Stop the entity list fetch thread."""
        if self._entity_list_thread:
            if self._entity_list_thread.isRunning():
                self._entity_list_thread.quit()
                self._entity_list_thread.wait(2000)
            self._entity_list_thread.deleteLater()
            self._entity_list_thread = None
    
    def stop_all_threads(self):
        """Stop all background threads properly."""
        print("Stopping all threads...")
        self.stop_websocket()
        self.stop_fetch_thread()
        self.stop_entity_list_thread()
        
        # Stop camera threads
        if self._picture_thread:
            if self._picture_thread.isRunning():
                self._picture_thread.quit()
                self._picture_thread.wait(100)
            self._picture_thread.deleteLater()
            self._picture_thread = None
            
        # Stop all stream threads
        for entity_id, thread in list(self._stream_threads.items()):
            if thread.isRunning():
                thread.stop()
            thread.deleteLater()
        self._stream_threads.clear()
            
        # Thread pool handles its own cleanup on exit usually, or we can clear:
        self.thread_pool.clear()
        
        print("All threads stopped")
    
    @pyqtSlot()
    def _toggle_dashboard(self):
        """Toggle dashboard visibility."""
        if self.dashboard:
            self.dashboard.toggle()
    
    @pyqtSlot()
    def _show_settings(self):
        """Show settings in dashboard."""
        if self.dashboard:
            if not self.dashboard.isVisible():
                self.dashboard.show()
                # If hidden, show_near_tray logic might be better?
                # But show() is fine.
                
            self.dashboard.show_settings()
    
    @pyqtSlot()
    def _quit(self):
        """Quit the application."""
        self.stop_all_threads()
        if self.tray_manager:
            self.tray_manager.stop()
        QApplication.instance().quit()
    
    @pyqtSlot(dict)
    def on_settings_saved(self, new_config: dict):
        """Handle settings saved."""
        print("Settings saved, reinitializing...")
        self.config = new_config
        self.save_config()
        self.stop_all_threads()
        QApplication.processEvents()
        
        self.init_theme()
        self.init_ha_client()
        
        # Update dashboard grid and rows
        if self.dashboard:
            rows = self.config.get('appearance', {}).get('rows', 2)
            self.dashboard.set_rows(rows)
            self.dashboard.set_buttons(self.config.get('buttons', []), self.config.get('appearance', {}))
        
        # Update shortcut
        shortcut_config = self.config.get('shortcut', {'type': 'keyboard', 'value': '<ctrl>+<alt>+h'})
        self.input_manager.update_shortcut(shortcut_config)
        
        self.start_websocket()
        print("Reinitialization complete")
    
    @pyqtSlot(int)
    def on_edit_button_requested(self, slot: int):
        """Handle request to edit a button at slot."""
        print(f"Edit requested for slot {slot}")
        
        # Ensure dashboard stays open or we open a modal on top?
        # Modal on top is fine.
        
        # Fetch entities if we don't have them
        if not self._available_entities:
            self.fetch_all_entities(lambda: self._open_button_editor(slot))
        else:
            self._open_button_editor(slot)

    def fetch_all_entities(self, callback):
        """Fetch all entities then call callback."""
        ha_config = self.config.get('home_assistant', {})
        url = ha_config.get('url', '')
        token = ha_config.get('token', '')
        
        if not url or not token:
            print("Cannot fetch entities: missing config")
            return
            
        print("Fetching all entities for editor...")
        self.stop_entity_list_thread()
        
        self._entity_list_thread = EntityFetchThread(url, token)
        self._entity_list_thread.finished.connect(lambda entities: self._on_entities_ready(entities, callback))
        # Handle error?
        self._entity_list_thread.start()
        
    def _on_entities_ready(self, entities: list, callback):
        """Handle entities fetched."""
        print(f"Entities fetched: {len(entities)}")
        self._available_entities = entities
        callback()
        
    def _open_button_editor(self, slot: int):
        """Open the editor for a button slot."""
        if not self.dashboard:
            return
            
        # Ensure dashboard is visible
        if not self.dashboard.isVisible():
            self.dashboard.show()
        
        # Find existing config for this slot
        buttons = self.config.get('buttons', [])
        existing_config = next((b for b in buttons if b.get('slot') == slot), None)
        
        # Show embedded editor
        self.dashboard.show_edit_button(slot, existing_config, self._available_entities)

    @pyqtSlot(dict)
    def _on_embedded_settings_saved(self, new_config: dict):
        """Handle settings saved from embedded SettingsWidget."""
        print("Embedded settings saved")
        
        # Check what changed
        # Check what changed
        # compare against ACTIVE ha_client state, because self.config might be mutated in place
        new_ha_config = new_config.get('home_assistant', {})
        new_url = new_ha_config.get('url', '').rstrip('/')
        new_token = new_ha_config.get('token', '')
        
        # Check against current client state
        ha_changed = (self.ha_client.url != new_url or 
                      self.ha_client.token != new_token)
        
        # Update config
        self.config = new_config
        self.save_config()
        
        # Apply row changes
        rows = self.config.get('appearance', {}).get('rows', 2)
        if self.dashboard:
            self.dashboard.set_rows(rows)
            # Re-apply buttons (appearance might have changed)
            self.dashboard.set_buttons(self.config.get('buttons', []), self.config.get('appearance', {}))
        
        # Apply shortcut changes
        if self.input_manager:
            shortcut = self.config.get('shortcut', {})
            if shortcut.get('type') and shortcut.get('value'):
                self.input_manager.update_shortcut(shortcut)
        
        # Only restart networking if HA config changed
        if ha_changed:
            print("HA config changed, restarting connections...")
            self.init_ha_client()
            
            # Restart WebSocket (connection may have changed)
            def restart():
                self.start_websocket()
                self.fetch_initial_states()
                
            self.stop_websocket(on_finished=restart)
        else:
            print("HA config unchanged, skipping connection restart")
            # Just refresh button styles/states potentially
            self.theme_manager.set_theme(self.config.get('appearance', {}).get('theme', 'system'))

    @pyqtSlot(int)
    def on_clear_button_requested(self, slot: int):
        """Clear button at slot."""
        buttons = self.config.get('buttons', [])
        new_buttons = [b for b in buttons if b.get('slot') != slot]
        
        if len(new_buttons) != len(buttons):
            self.config['buttons'] = new_buttons
            self.save_config()
            
            if self.dashboard:
                self.dashboard.set_buttons(self.config['buttons'], self.config.get('appearance', {}))
                
            # Restart WS to cleanup subscriptions
            self.stop_websocket(on_finished=self.start_websocket)

    @pyqtSlot(dict)
    def on_button_clicked(self, config):
        """Handle button click from dashboard."""
        btn_type = config.get('type', 'switch')
        entity_id = config.get('entity_id', '')
        
        # Handle switch, curtain, script, scene, and fan types
        if btn_type in ('switch', 'curtain', 'script', 'scene', 'fan') and entity_id:
            # Debounce check - prevent rapid clicks
            current_time = time.time()
            last_time = self._last_click_time.get(entity_id, 0)
            
            # Allow skipping debounce for sliders/dimmers
            skip_debounce = config.get('skip_debounce', False)
            
            if not skip_debounce and (current_time - last_time < self._click_cooldown):
                print(f"Debounce: ignoring rapid click for {entity_id}")
                return
            
            self._last_click_time[entity_id] = current_time
            
            # Determine service to call
            if btn_type == 'curtain':
                # Curtains use cover.toggle
                domain = 'cover'
                action = 'toggle'
            elif btn_type == 'script':
                # Scripts use script.turn_on
                domain = 'script'
                action = 'turn_on'
            elif btn_type == 'scene':
                # Scenes always call turn_on
                domain = 'scene'
                action = 'turn_on'
            else:
                # Switches use configured service
                service = config.get('service', 'homeassistant.toggle')
                domain, action = service.split('.', 1) if '.' in service else ('homeassistant', 'toggle')
            
            print(f"Calling service: {domain}.{action} for {entity_id}")
            
            ha_config = self.config.get('home_assistant', {})
            service_data = config.get('service_data')
            
            # Use Runnable and ThreadPool
            runnable = ServiceCallRunnable(
                self.ha_client,
                domain, action, entity_id,
                service_data
            )
            self.thread_pool.start(runnable)
            
            # Connect for feedback
            def handle_service_result(success):
                if not success:
                    # Use existing notification manager if available, or just print
                    if hasattr(self, 'notification_manager'):
                        self.notification_manager.show_error(
                            "Action Failed", 
                            f"Failed to call {domain}.{action} for {entity_id}"
                        )
                    print(f"Service call failed for {entity_id}")

            runnable.signals.call_complete.connect(handle_service_result)
    
    @pyqtSlot(str, float)
    def on_climate_value_changed(self, entity_id: str, temperature: float):
        """Handle climate temperature change from dashboard."""
        print(f"Setting climate {entity_id} to {temperature}°C")
        
        ha_config = self.config.get('home_assistant', {})
        
        runnable = ServiceCallRunnable(
            self.ha_client,
            'climate', 'set_temperature', entity_id,
            {'temperature': temperature}
        )
        runnable.signals.call_complete.connect(lambda success: print(f"Climate service call result: {success}"))
        self.thread_pool.start(runnable)
    
    def _cleanup_service_thread(self, thread):
        """No longer used with QThreadPool."""
        pass
    
    @pyqtSlot(int)
    def on_add_button_clicked(self, slot: int):
        """Handle add button click on empty slot."""
        # This is now handled by on_edit_button_requested
        # We can re-route or keep separate if needed, but logic is same
        self.on_edit_button_requested(slot)
    

    @pyqtSlot(int)
    def on_edit_button_requested(self, slot: int):
        """Handle edit button request."""
        # Find config for this slot
        buttons = self.config.get('buttons', [])
        config = next((b for b in buttons if b.get('slot') == slot), None)
        
        # Fetch entities first
        ha_config = self.config.get('home_assistant', {})
        if not ha_config.get('url') or not ha_config.get('token'):
            # If no config, just open empty
            if self.dashboard:
                self.dashboard.show_edit_button(slot, config, [])
            return

        # Reuse Generic Entity Fetcher
        self._entity_fetcher = EntityFetchThread(
            ha_config['url'], 
            ha_config['token']
        )
        self._entity_fetcher.finished.connect(
            lambda entities: self._on_entities_ready(entities, slot, config)
        )
        self._entity_fetcher.start()

    def _on_entities_ready(self, entities, slot, config):
        if self.dashboard:
            self.dashboard.show_edit_button(slot, config, entities)
            
    @pyqtSlot(int)
    def on_duplicate_button_requested(self, slot: int):
        """Handle request to duplicate a button."""
        buttons = self.config.get('buttons', [])
        
        # Find source config
        source_config = next((b for b in buttons if b.get('slot') == slot), None)
        if not source_config:
            return
            
        # Find first empty slot (visual)
        if self.dashboard:
            span_x = source_config.get('span_x', 1)
            span_y = source_config.get('span_y', 1)
            target_slot = self.dashboard.get_first_empty_slot(span_x, span_y)
        else:
            # Fallback (shouldn't happen)
            target_slot = -1

        
        if target_slot == -1:
            print("No empty slots available to duplicate button.")
            return

        # Create copy of config
        new_config = source_config.copy()
        new_config['slot'] = target_slot
        
        # Add to buttons list
        buttons.append(new_config)
        self.config['buttons'] = buttons
        
        # Save and refresh
        self.save_config()
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
            self.fetch_initial_states()

    @pyqtSlot(dict)
    def on_edit_button_saved(self, new_config: dict):
        """Handle button config saved from embedded editor."""
        slot = new_config.get('slot')
        
        buttons = self.config.get('buttons', [])
        # Remove old config for this slot if exists
        buttons = [b for b in buttons if b.get('slot') != slot]
        
        buttons.append(new_config)
        self.config['buttons'] = buttons
        
        self.save_config()
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
            self.fetch_initial_states() # Refresh state for new item

    @pyqtSlot(int, int)
    def on_buttons_reordered(self, source: int, target: int):
        """Handle button reordering via drag and drop."""
        buttons = self.config.get('buttons', [])
        
        # Find buttons in source and target slots
        source_btn = next((b for b in buttons if b.get('slot') == source), None)
        target_btn = next((b for b in buttons if b.get('slot') == target), None)
        
        # Update slots (swap)
        if source_btn:
            source_btn['slot'] = target
        if target_btn:
            target_btn['slot'] = source
            
        # Save and update
        self.save_config()
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
        
        # Refresh states for new positions
        self.fetch_initial_states()

    @pyqtSlot(str, dict)
    def on_state_changed(self, entity_id: str, state: dict):
        """Handle entity state change from WebSocket."""
        print(f"State changed: {entity_id} -> {state.get('state', 'unknown')}")
        if self.dashboard:
            self.dashboard.update_entity_state(entity_id, state)
    
    @pyqtSlot(str, str)
    def on_notification(self, title: str, message: str):
        """Handle Home Assistant notification."""
        print(f"Notification: {title} - {message}")
        self.notification_manager.show_ha_notification(title, message)
    
    @pyqtSlot()
    def on_ws_connected(self):
        """Handle WebSocket connected."""
        print("WebSocket connected!")
        if self.tray_manager:
            self.tray_manager.update_title("Prism Desktop - Connected")
        
        # Fetch initial states
        self.fetch_initial_states()
    
    @pyqtSlot()
    def on_ws_disconnected(self):
        """Handle WebSocket disconnected."""
        print("WebSocket disconnected!")
        if self.tray_manager:
            self.tray_manager.update_title("Prism Desktop - Disconnected")
    
    @pyqtSlot(str)
    def on_ws_error(self, error: str):
        """Handle WebSocket error."""
        print(f"WebSocket error: {error}")
    
    def fetch_initial_states(self):
        """Fetch states for all configured entities."""
        # Stop any existing fetch thread first
        self.stop_fetch_thread()
        
        entity_ids = []
        for btn in self.config.get('buttons', []):
            entity_id = btn.get('entity_id')
            if entity_id:
                entity_ids.append(entity_id)
        
        if not entity_ids:
            return
        
        print(f"Fetching initial states for: {entity_ids}")
        
        ha_config = self.config.get('home_assistant', {})
        
        self._fetch_thread = StateFetchThread(
            self.ha_client,
            entity_ids
        )
        self._fetch_thread.state_fetched.connect(self._on_state_fetched)
        self._fetch_thread.finished.connect(self._on_fetch_finished)
        self._fetch_thread.start()
    
    @pyqtSlot(str, dict)
    def _on_state_fetched(self, entity_id: str, state: dict):
        """Handle fetched state."""
        print(f"Fetched state: {entity_id} -> {state.get('state', 'unknown')}")
        if self.dashboard:
            self.dashboard.update_entity_state(entity_id, state)
    
    @pyqtSlot()
    def _on_fetch_finished(self):
        """Handle fetch thread completion."""
        print("Initial state fetch complete")
        # Start camera refresh after initial states are loaded
        self._start_camera_refresh()
    
    def _start_camera_refresh(self):
        """Start the camera refresh timers."""
        # Picture mode cameras (10s refresh)
        picture_ids = self._get_camera_entity_ids('picture')
        if picture_ids:
            print(f"Starting picture refresh for: {picture_ids}")
            self._refresh_picture_cameras()
            self._camera_timer.start(self._camera_refresh_interval)
        else:
            self._camera_timer.stop()
        
        # Stream mode cameras (Start continuous threads)
        stream_ids = self._get_camera_entity_ids('stream')
        
        # 1. Start new streams
        for entity_id in stream_ids:
            if entity_id not in self._stream_threads:
                print(f"Starting stream thread for {entity_id}")
                thread = CameraStreamThread(self.ha_client, entity_id)
                thread.image_fetched.connect(self._on_camera_image_fetched)
                thread.start()
                self._stream_threads[entity_id] = thread
        
        # 2. Stop removed streams
        for entity_id in list(self._stream_threads.keys()):
            if entity_id not in stream_ids:
                print(f"Stopping stream thread for {entity_id}")
                thread = self._stream_threads.pop(entity_id)
                thread.stop()
                thread.deleteLater()
    
    def _get_camera_entity_ids(self, mode: str = 'picture') -> list:
        """Get list of camera entity IDs from button config by mode."""
        camera_ids = []
        for btn in self.config.get('buttons', []):
            if btn.get('type') == 'camera':
                btn_mode = btn.get('camera_mode', 'picture')
                if btn_mode == mode:
                    entity_id = btn.get('entity_id')
                    if entity_id:
                        camera_ids.append(entity_id)
        return camera_ids
    
    def _refresh_picture_cameras(self):
        """Fetch picture mode camera images (10s interval)."""
        camera_ids = self._get_camera_entity_ids('picture')
        if not camera_ids:
            return
            
        # Cleanup previous picture thread
        if self._picture_thread:
            if self._picture_thread.isRunning():
                # If it's still running, we skip this cycle to avoid buildup
                # or we could try to stop it, but skipping is safer for 10s interval
                return 
            self._picture_thread.deleteLater()
            self._picture_thread = None
        
        self._picture_thread = CameraFetchThread(self.ha_client, camera_ids)
        self._picture_thread.image_fetched.connect(self._on_camera_image_fetched)
        self._picture_thread.start()
    
    def _remove_stream_refresh_method(self):
        # Placeholder to remove the old method cleanly via replacement
        pass
    
    @pyqtSlot(str, bytes)
    def _on_camera_image_fetched(self, entity_id: str, image_bytes: bytes):
        """Handle fetched camera image."""
        if self.dashboard:
            pixmap = QPixmap()
            if pixmap.loadFromData(image_bytes):
                self.dashboard.update_camera_image(entity_id, pixmap)
    
    def check_first_run(self):
        """Show settings if no configuration exists."""
        ha_config = self.config.get('home_assistant', {})
        if not ha_config.get('url') or not ha_config.get('token'):
            QTimer.singleShot(500, self._show_settings)

    def shutdown(self):
        """Clean shutdown of threads and connections."""
        print("Shutting down...")
        if self._ws_thread:
            self._ws_thread.stop()
            self._ws_thread.wait(1000)
        if hasattr(self, 'update_checker') and self.update_checker.isRunning():
            self.update_checker.wait()

def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Prism Desktop")
    app.setApplicationDisplayName("Prism Desktop - Home Assistant")
    
    # Load MDI icon font
    load_mdi_font()
    
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray not available")
        sys.exit(1)
    
    prism = PrismDesktopApp()
    
    # Ensure clean shutdown
    app.aboutToQuit.connect(prism.shutdown)
    
    prism.check_first_run()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
