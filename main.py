"""
Prism - Home Assistant Tray Application
Main entry point and application controller.
"""

import sys
import os
import json
import time
import asyncio
from pathlib import Path
from typing import Optional
import logging
import copy
import platform

# Force XWayland on Wayland sessions — Qt's QWidget.move() is silently ignored
# under native Wayland, causing the dashboard to appear centered instead of
# anchored to the system tray corner. Must be set before QApplication is created.
if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
    os.environ['QT_QPA_PLATFORM'] = 'xcb'

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

import qasync
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSlot, QTimer, QRect
from PyQt6.QtGui import QPixmap

from core.config_manager import ConfigManager
from core.service_dispatcher import ServiceDispatcher
from ui.theme_manager import ThemeManager
from core.ha_client import HAClient
from core.ha_websocket import HAWebSocket
from ui.dashboard import Dashboard
 
from ui.tray_manager import TrayManager
from services.notifications import NotificationManager
from services.input_manager import InputManager
from services.mobile_app import register_mobile_app, send_location_update
from services.location_manager import get_location
from ui.icons import load_mdi_font
from services.update_checker import UpdateCheckerThread
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

VERSION = "1.4.3"

def _create_task_safe(coro):
    """Schedule an async task safely from synchronous Qt context.
    
    Defers asyncio.create_task() to the next event-loop iteration via
    QTimer.singleShot(0, ...) to avoid re-entrant qasync timer-callback
    assertions (affects distro-packaged qasync on Raspbian/Pi 5).
    """
    QTimer.singleShot(0, lambda: asyncio.create_task(coro))

class PrismDesktopApp(QObject):
    """Main application controller."""
    
    def __init__(self):
        super().__init__()
        
        # Configuration
        self.config_manager = ConfigManager()
        self.config = self.config_manager.config
        
        # Components
        self.theme_manager = ThemeManager()
        self.ha_client = HAClient()
        self.notification_manager = NotificationManager(ha_client=self.ha_client)
        self.input_manager = InputManager()
        
        # UI Components
        self.dashboard: Optional[Dashboard] = None
        self.tray_manager: Optional[TrayManager] = None
        
        # WebSocket
        self._ha_websocket: Optional[HAWebSocket] = None
        self._ws_task: Optional[asyncio.Task] = None

        # Location reporting (Windows only)
        self._location_task: Optional[asyncio.Task] = None
        
        # Helper threads (legacy/transition)
        
        
        # Cache for entity list (for editor)
        self._available_entities: list[dict] = []
        
        # Service Dispatcher
        self.service_dispatcher = ServiceDispatcher(self.ha_client)
        
        # Camera refresh integration
        self._camera_refresh_interval = 1  # seconds
        
        # Media player album art cache (entity_id -> last entity_picture URL)
        self._media_art_cache = {}
        # Camera image cache (entity_id -> (timestamp, QPixmap))
        self._camera_cache = {}
        
        # Initialize
        self.init_theme()
        self.init_ha_client()
        self.init_ui()
        
        # Initialize shortcuts in background
        QTimer.singleShot(100, self.init_shortcuts)
        
        # Defer task execution to the event loop so it occurs after QApplication begins
        def start_background_tasks():
            self.start_websocket()
            asyncio.create_task(self._camera_refresh_loop())
            
        QTimer.singleShot(0, start_background_tasks)
        
        # Helper for update check
        self._update_thread = None

        # Show Dashboard on Startup after GNOME/XWayland has settled monitor state
        if self.dashboard:
            QTimer.singleShot(1500, self.dashboard.show_near_tray)
            
        # Check for updates
        QTimer.singleShot(2000, self.check_for_updates)
    
    def init_shortcuts(self):
        """Initialize global shortcuts."""
        shortcut_config = self.config.get('shortcut', {'type': 'keyboard', 'value': '<ctrl>+<alt>+h'})
        self.input_manager.update_shortcut(shortcut_config)
        self.input_manager.triggered.connect(self._toggle_dashboard)
    
    def save_config(self):
        """Save configuration to file via ConfigManager."""
        self.config_manager.save_config()
    
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
        rows = self.config.get('appearance', {}).get('rows', 4)
        cols = self.config.get('appearance', {}).get('cols', 6)
        self.dashboard = Dashboard(config=self.config, theme_manager=self.theme_manager, input_manager=self.input_manager, version=VERSION, rows=rows, cols=cols)
        self.dashboard.set_buttons(self.config.get('buttons', []), self.config.get('appearance', {}))
        
        # Connect signals
        self.dashboard.button_clicked.connect(self.on_button_clicked)
        self.dashboard.add_button_clicked.connect(self.on_edit_button_requested)
        self.dashboard.edit_button_requested.connect(self.on_edit_button_requested)
        self.dashboard.duplicate_button_requested.connect(self.on_duplicate_button_requested)
        self.dashboard.clear_button_requested.connect(self.on_clear_button_requested)
        self.dashboard.buttons_reordered.connect(self.on_buttons_reordered)

        self.dashboard.settings_saved.connect(self._on_embedded_settings_saved)
        self.dashboard.rows_changed.connect(self.fetch_initial_states)
        self.dashboard.cols_changed.connect(self.fetch_initial_states)
        self.dashboard.edit_button_saved.connect(self.on_edit_button_saved)
        self.dashboard.save_config_requested.connect(self.save_config)
        self.dashboard.volume_scroll_requested.connect(self.on_volume_scroll)
        self.dashboard.media_command_requested.connect(self.on_media_command)
        self.dashboard.weather_forecast_requested.connect(self.on_weather_forecast_requested)
        
        self.dashboard._init_settings_widget(self.config, self.input_manager)
        
        self.tray_manager = TrayManager(
            on_left_click=self._toggle_dashboard,
            on_settings=self._show_settings,
            on_quit=self._quit,
            theme=self.theme_manager.get_effective_theme()
        )
        self.tray_manager.start()
        
        self.theme_manager.theme_changed.connect(self.tray_manager.set_theme)
    
    def start_websocket(self):
        """Start a new WebSocket connection."""
        ha_config = self.config.get('home_assistant', {})
        if not ha_config.get('url') or not ha_config.get('token'):
            return
        
        # Create fresh WebSocket client
        self._ha_websocket = HAWebSocket(
            url=ha_config.get('url', ''),
            token=ha_config.get('token', '')
        )
        
        # Subscribe to configured entities
        for btn in self.config.get('buttons', []):
            if btn.get('type') == '3d_printer':
                for key in ['printer_state_entity', 'printer_progress_entity', 'printer_camera_entity', 'printer_nozzle_entity', 'printer_bed_entity', 'printer_nozzle_target_entity', 'printer_bed_target_entity', 'printer_pause_entity', 'printer_stop_entity', 'entity_id']:
                    eid = btn.get(key)
                    if eid:
                        self._ha_websocket.subscribe_entity(eid)
            else:
                entity_id = btn.get('entity_id')
                if entity_id:
                    self._ha_websocket.subscribe_entity(entity_id)
        
        self._ha_websocket.state_changed.connect(self.on_state_changed)
        self._ha_websocket.notification_received.connect(self.on_notification)
        self._ha_websocket.connected.connect(self.on_ws_connected)
        self._ha_websocket.disconnected.connect(self.on_ws_disconnected)
        self._ha_websocket.error.connect(self.on_ws_error)
        
        # Apply any saved webhook_id so it subscribes to push_notification_channel on connect
        saved_webhook_id = self.config.get('mobile_app', {}).get('webhook_id', '')
        if saved_webhook_id:
            self._ha_websocket.set_webhook_id(saved_webhook_id)
        
        # Keep WS isolated in thread for now
        self._ws_task = asyncio.create_task(self._ha_websocket.run_reconnect_loop())
    
    def stop_websocket(self, on_finished=None):
        """Stop the WebSocket connection."""
        # Clean signals
        if self._ha_websocket:
            try:
                self._ha_websocket.state_changed.disconnect()
                self._ha_websocket.notification_received.disconnect()
                self._ha_websocket.connected.disconnect()
                self._ha_websocket.disconnected.disconnect()
                self._ha_websocket.error.disconnect()
            except: pass
            
        def delete_ws_obj():
            if self._ha_websocket:
                self._ha_websocket.deleteLater()
                self._ha_websocket = None
            if on_finished:
                on_finished()
        
        if self._ha_websocket:
            self._ha_websocket.request_stop()
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
            
        delete_ws_obj()



    def stop_all_threads(self):
        """Stop all background threads."""
        self._stop_location_loop()
        self.stop_websocket()

        if self.tray_manager:
            self.tray_manager.stop()
        
        # Async cleanup
        if self.ha_client:
            _create_task_safe(self.ha_client.close())
            
    async def _camera_refresh_loop(self):
        """Background task to refresh camera images."""
        while True:
            try:
                if self.dashboard and self.dashboard.isVisible():
                    # Identify visible camera buttons and 3D printers
                    camera_buttons = []
                    for btn in self.dashboard.buttons:
                        if btn.isVisible():
                            if btn.config.get('type') == 'camera':
                                entity_id = btn.config.get('entity_id')
                                if entity_id:
                                    camera_buttons.append((btn, entity_id))
                            elif btn.config.get('type') == '3d_printer':
                                cam_entity_id = btn.config.get('printer_camera_entity')
                                # Only pull the camera feed if the button is large enough to display it (2x2+)
                                # OR if the 3D printer overlay is currently active and belongs to this button
                                is_active_overlay = (
                                    self.dashboard.overlay_manager.printer_overlay.isVisible() and
                                    self.dashboard.overlay_manager._active_printer_config and
                                    self.dashboard.overlay_manager._active_printer_config.get('printer_camera_entity') == cam_entity_id
                                )
                                
                                if cam_entity_id and (is_active_overlay or (btn.span_x >= 2 and btn.span_y >= 2)):
                                    camera_buttons.append((btn, cam_entity_id))
                    
                    # Create tasks for concurrent fetching
                    tasks = []
                    for btn, entity_id in camera_buttons:
                        tasks.append(self._fetch_camera_image(entity_id))
                    
                    if tasks:
                        await asyncio.gather(*tasks)
                        
                await asyncio.sleep(self._camera_refresh_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Camera loop error: {e}")
                await asyncio.sleep(10)

    async def _fetch_camera_image(self, entity_id: str):
        """Fetch and update single camera image with caching."""
        # Check cache validity (immediate feedback)
        if entity_id in self._camera_cache:
            _, pixmap = self._camera_cache[entity_id]
            if self.dashboard:
                self.dashboard.update_camera_image(entity_id, pixmap)

        # Fetch fresh data
        data = await self.ha_client.get_camera_image(entity_id)
        if data:
             pixmap = QPixmap()
             if pixmap.loadFromData(data):
                 # Update Cache
                 self._camera_cache[entity_id] = (time.time(), pixmap)
                 # Update UI
                 if self.dashboard:
                     self.dashboard.update_camera_image(entity_id, pixmap)

    @pyqtSlot()
    def _toggle_dashboard(self):
        if self.dashboard:
            if not self.dashboard.isVisible():
                # Apply cached images immediately before showing to prevent black flash
                self.dashboard.apply_camera_cache(self._camera_cache)
            self.dashboard.toggle()
    
    @pyqtSlot()
    def _show_settings(self):
        if self.dashboard:
            if not self.dashboard.isVisible():
                self.dashboard.show_near_tray()
                QTimer.singleShot(0, self.dashboard.show_settings)
                return
            self.dashboard.show_settings()
    
    @pyqtSlot()
    def _quit(self):
        """Quit the application."""
        self.stop_all_threads()
        QApplication.instance().quit()
    
    @pyqtSlot(dict)
    def on_settings_saved(self, new_config: dict):
        """Handle settings saved. Re-initialize if necessary."""
        # Use asyncio task to handle re-init which might involve network operations
        _create_task_safe(self._process_settings_change(new_config))

    async def _process_settings_change(self, new_config):
        print("Settings saved, reinitializing...")
        
        new_ha_config = new_config.get('home_assistant', {})
        new_url = new_ha_config.get('url', '').rstrip('/')
        new_token = new_ha_config.get('token', '')
        
        ha_changed = (self.ha_client.url != new_url or self.ha_client.token != new_token)
        
        self.config_manager.config = new_config
        self.config = self.config_manager.config
        self.save_config()
        
        # Re-apply UI
        rows = self.config.get('appearance', {}).get('rows', 2)
        cols = self.config.get('appearance', {}).get('cols', 4)
        if self.dashboard:
            self.dashboard.set_rows(rows)
            self.dashboard.set_cols(cols)
            self.dashboard.set_buttons(self.config.get('buttons', []), self.config.get('appearance', {}))
            # Re-apply camera images after rebuild
            self.dashboard.apply_camera_cache(self._camera_cache)
            if self.dashboard.isVisible():
                self.dashboard.refresh_tray_anchor(move_now=True)
        
        if self.input_manager:
             self.input_manager.update_shortcut(self.config.get('shortcut', {}))
             
        if ha_changed:
            print("HA config changed, restarting connections...")
            # Clear mobile_app registration so we re-register with the new HA instance
            self.config.setdefault("mobile_app", {}).pop("webhook_id", None)
            self._stop_location_loop()
            self.stop_websocket()
            await self.ha_client.close()

            self.init_ha_client()
            self.start_websocket()
            self.fetch_initial_states()
        else:
            self.theme_manager.set_theme(self.config.get('appearance', {}).get('theme', 'system'))
            # Sync location loop with new setting
            location_enabled = self.config.get('mobile_app', {}).get('location_enabled', False)
            if location_enabled:
                self._start_location_loop()
            else:
                self._stop_location_loop()

    @pyqtSlot(dict)
    def _on_embedded_settings_saved(self, new_config: dict):
        # Redirect to main handler
        self.on_settings_saved(new_config)

    @pyqtSlot(int)
    def on_edit_button_requested(self, slot: int):
        # Async fetch entities
        _create_task_safe(self._async_open_editor(slot))
        
    async def _async_open_editor(self, slot: int):
        print(f"Fetching entities for slot {slot}...")
        # Since we are async now, we can await directly!
        entities = await self.ha_client.get_entities()
        if entities:
            self._available_entities = entities
            self._open_button_editor(slot)
        else:
            print("Failed to fetch entities")
            
    def _open_button_editor(self, slot: int):
        if not self.dashboard: return
        if not self.dashboard.isVisible(): self.dashboard.show()
        
        # Convert runtime slot to (row, col) for lookup
        row = slot // self.dashboard._cols
        col = slot % self.dashboard._cols
        
        buttons = self.config.get('buttons', [])
        existing_config = next((b for b in buttons if b.get('row') == row and b.get('col') == col), None)
        self.dashboard.show_edit_button(slot, existing_config, self._available_entities)

    # @pyqtSlot() - Removed to allow flexible arguments
    def on_edit_button_saved(self, slot, new_config):
        """Propagate button edit to config."""
        buttons = self.config.get('buttons', [])
        
        # Convert runtime slot to (row, col)
        row = slot // self.dashboard._cols
        col = slot % self.dashboard._cols
        
        # Remove old config at this (row, col)
        buttons = [b for b in buttons if not (b.get('row') == row and b.get('col') == col)]
        
        # Add new with (row, col)
        new_config['row'] = row
        new_config['col'] = col
        buttons.append(new_config)
        
        self.config['buttons'] = buttons
        self.save_config()
        
        # Update Dashboard
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
            
        # Update subscriptions
        if new_config.get('type') == '3d_printer' and self._ha_websocket:
            for key in ['printer_state_entity', 'printer_progress_entity', 'printer_camera_entity', 'printer_nozzle_entity', 'printer_bed_entity', 'printer_nozzle_target_entity', 'printer_bed_target_entity', 'printer_pause_entity', 'printer_stop_entity', 'entity_id']:
                eid = new_config.get(key)
                if eid:
                    self._ha_websocket.subscribe_entity(eid)
                    _create_task_safe(self._fetch_single_state(eid))
        else:
            entity_id = new_config.get('entity_id')
            if entity_id and self._ha_websocket:
                self._ha_websocket.subscribe_entity(entity_id)
                # Fetch immediate state
                _create_task_safe(self._fetch_single_state(entity_id))

    @pyqtSlot(int)
    def on_duplicate_button_requested(self, slot: int):
        buttons = self.config.get('buttons', [])
        
        # Find source by (row, col)
        row = slot // self.dashboard._cols
        col = slot % self.dashboard._cols
        source_config = next((b for b in buttons if b.get('row') == row and b.get('col') == col), None)
        if not source_config: return
        
        # Find empty slot
        span_x = source_config.get('span_x', 1)
        span_y = source_config.get('span_y', 1)
        
        target_row, target_col = self.dashboard.get_first_empty_slot(span_x, span_y)
        if target_row < 0:
            print("No space to duplicate")
            return
            
        new_config = source_config.copy()
        new_config['row'] = target_row
        new_config['col'] = target_col
        buttons.append(new_config)
        self.config['buttons'] = buttons
        self.save_config()
        
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
        
        if self._ha_websocket:
            if new_config.get('type') == '3d_printer':
                for key in ['printer_state_entity', 'printer_progress_entity', 'printer_camera_entity', 'printer_nozzle_entity', 'printer_bed_entity', 'printer_nozzle_target_entity', 'printer_bed_target_entity', 'printer_pause_entity', 'printer_stop_entity', 'entity_id']:
                    eid = new_config.get(key)
                    if eid:
                        self._ha_websocket.subscribe_entity(eid)
            else:
                if new_config.get('entity_id'):
                    self._ha_websocket.subscribe_entity(new_config.get('entity_id'))

    @pyqtSlot(int, int)
    def on_buttons_reordered(self, source: int, target: int):
        # Dashboard handles visual reindexing (drag/drop) 
        # But we must persist the change to config
        buttons = self.config.get('buttons', [])
        cols = self.dashboard._cols
        
        # Convert runtime slots to (row, col)
        src_row, src_col = source // cols, source % cols
        tgt_row, tgt_col = target // cols, target % cols
        
        # Find configs by (row, col)
        source_btn = next((b for b in buttons if b.get('row') == src_row and b.get('col') == src_col), None)
        if not source_btn: return
        
        target_btn = next((b for b in buttons if b.get('row') == tgt_row and b.get('col') == tgt_col), None)
        
        if target_btn:
            # Swap (row, col)
            source_btn['row'], target_btn['row'] = tgt_row, src_row
            source_btn['col'], target_btn['col'] = tgt_col, src_col
        else:
            # Move to empty cell
            # Clamp to bounds to prevent disappearing
            sx = source_btn.get('span_x', 1)
            sy = source_btn.get('span_y', 1)
            
            valid_tgt_col = min(tgt_col, cols - sx)
            valid_tgt_row = min(tgt_row, self.dashboard._rows - sy) # Ensure we valid row check too if needed
            
            source_btn['row'] = valid_tgt_row
            source_btn['col'] = valid_tgt_col
            
        # Save
        self.config['buttons'] = buttons
        self.save_config()
        
        # Refresh Dashboard
        if self.dashboard:
            self.dashboard.set_buttons(buttons, self.config.get('appearance', {}))
            
            # Restore Album Art from Cache
            for btn in buttons:
                eid = btn.get('entity_id')
                if eid and eid in self._media_art_cache:
                    path, pixmap = self._media_art_cache[eid]
                    if pixmap and not pixmap.isNull():
                         self.dashboard.update_media_art(eid, pixmap)
            
            # Force full state refresh
            self.fetch_initial_states()

    @pyqtSlot(int)
    def on_clear_button_requested(self, slot):
         buttons = self.config.get('buttons', [])
         row = slot // self.dashboard._cols
         col = slot % self.dashboard._cols
         self.config['buttons'] = [b for b in buttons if not (b.get('row') == row and b.get('col') == col)]
         self.save_config()
         if self.dashboard:
             self.dashboard.set_buttons(self.config['buttons'], self.config.get('appearance', {}))
    
    @pyqtSlot(dict)
    def on_button_clicked(self, config):
        _create_task_safe(self.service_dispatcher.handle_button_click(config))

    @pyqtSlot(str, float)
    def on_volume_scroll(self, entity_id, volume):
        _create_task_safe(self.service_dispatcher.handle_volume_scroll(entity_id, volume))

    @pyqtSlot(int, str)
    def on_media_command(self, slot, command):
        # Find entity by (row, col)
        buttons = self.config.get('buttons', [])
        cols = self.dashboard._cols if self.dashboard else 4
        row = slot // cols
        col = slot % cols
        btn = next((b for b in buttons if b.get('row') == row and b.get('col') == col), None)
        if btn and btn.get('entity_id'):
            _create_task_safe(self.service_dispatcher.handle_media_command(
                btn['entity_id'], command
            ))

    @pyqtSlot(int, QRect, dict)
    def on_weather_forecast_requested(self, slot: int, rect: QRect, config: dict):
        _create_task_safe(self._async_fetch_and_show_weather(slot, rect, config))
        
    async def _async_fetch_and_show_weather(self, slot: int, rect: QRect, config: dict):
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        # 1. Fetch current state to ensure ui has it
        state = await self.ha_client.get_state(entity_id)
        if state:
            self.on_state_changed(entity_id, state)
        
        # 2. Fetch forecast
        forecast_response = await self.ha_client.get_weather_forecast(entity_id, "daily")
        
        # 3. Present overlay
        if self.dashboard and self.dashboard.overlay_manager:
            # HAClient already returns the forecast list directly
            forecasts = forecast_response if isinstance(forecast_response, list) else []
            self.dashboard.overlay_manager.start_weather(slot, rect, config, forecasts)

    @pyqtSlot(str, dict)
    def on_state_changed(self, entity_id, new_state):
        if self.dashboard:
            self.dashboard.update_entity_state(entity_id, new_state)
            
            # Check for camera image
            if entity_id.startswith('camera.'):
                _create_task_safe(self._fetch_camera_image(entity_id))
            
            # Check for album art
            pic_path = new_state.get('attributes', {}).get('entity_picture')
            if pic_path:
                _create_task_safe(self._fetch_album_art(entity_id, new_state))
            elif new_state.get('attributes', {}).get('media_content_type'):
                # Is media player but no picture -> Clear it
                # We only clear if it's actually a media player (has content type or state)
                # to avoid clearing on random sensor updates if we reuse this logic
                self.dashboard.update_media_art(entity_id, None)
                
    @pyqtSlot(dict)
    def on_notification(self, payload: dict):
        self.notification_manager.show_ha_notification(payload)
    
    @pyqtSlot()
    def on_ws_connected(self):
        print("WS Connected")
        self.fetch_initial_states()
        # Register as a Mobile App so HA exposes notify.mobile_app_prism_desktop
        _create_task_safe(self._register_mobile_app())

    async def _register_mobile_app(self):
        ha_config = self.config.get('home_assistant', {})
        webhook_id = await register_mobile_app(
            ha_url=ha_config.get('url', ''),
            ha_token=ha_config.get('token', ''),
            config=self.config,
            save_config_fn=self.save_config,
        )
        # If this was a brand-new registration, update the running WS client
        # and reconnect so it subscribes to the push_notification_channel
        if webhook_id and self._ha_websocket:
            was_already_subscribed = bool(self._ha_websocket._webhook_id)
            self._ha_websocket.set_webhook_id(webhook_id)
            if not was_already_subscribed:
                # Force a reconnect so the connect() flow re-runs with the webhook_id set
                print("[MobileApp] New registration — reconnecting WS for push channel subscription")
                self.stop_websocket()
                self.start_websocket()

        # Start location loop if enabled
        if self.config.get('mobile_app', {}).get('location_enabled', False):
            self._start_location_loop()
        
    def _start_location_loop(self):
        """Start (or restart) the periodic location update task."""
        if self._location_task and not self._location_task.done():
            self._location_task.cancel()
        self._location_task = asyncio.create_task(self._location_update_loop())

    def _stop_location_loop(self):
        """Cancel the location update task if running."""
        if self._location_task and not self._location_task.done():
            self._location_task.cancel()
            self._location_task = None

    async def _location_update_loop(self):
        """Periodically fetch device location and send it to Home Assistant."""
        ha_config = self.config.get('home_assistant', {})
        ha_url = ha_config.get('url', '')
        webhook_id = self.config.get('mobile_app', {}).get('webhook_id', '')
        if not ha_url or not webhook_id:
            return
        try:
            while True:
                location = await get_location()
                if location:
                    await send_location_update(ha_url, webhook_id, location)
                await asyncio.sleep(900)  # 15 minutes
        except asyncio.CancelledError:
            pass

    @pyqtSlot()
    def on_ws_disconnected(self):
        print("WS Disconnected")
        
    @pyqtSlot(str)
    def on_ws_error(self, error):
        print(f"WS Error: {error}")

    def fetch_initial_states(self):
        _create_task_safe(self._async_fetch_initial_states())

    async def _async_fetch_initial_states(self):
        entity_ids = []
        for b in self.config.get('buttons', []):
            if b.get('type') == '3d_printer':
                for key in ['printer_state_entity', 'printer_progress_entity', 'printer_camera_entity', 'printer_nozzle_entity', 'printer_bed_entity', 'printer_nozzle_target_entity', 'printer_bed_target_entity', 'printer_pause_entity', 'printer_stop_entity', 'entity_id']:
                    if b.get(key):
                        entity_ids.append(b.get(key))
            else:
                if b.get('entity_id'):
                    entity_ids.append(b.get('entity_id'))
                    
        entity_ids = list(set(entity_ids)) # Remove duplicates
        if not entity_ids: return
        
        # Sync via API
        states = await self.ha_client.get_entities()
        state_map = {s['entity_id']: s for s in states}
        
        for eid in entity_ids:
            if eid in state_map:
                self.on_state_changed(eid, state_map[eid])
                
    async def _fetch_single_state(self, entity_id):
        state = await self.ha_client.get_state(entity_id)
        if state:
            self.on_state_changed(entity_id, state)

    async def _fetch_album_art(self, entity_id, state):
        pic_path = state.get('attributes', {}).get('entity_picture')
        if not pic_path: return
        
        # Check cache: (path, QPixmap)
        cached = self._media_art_cache.get(entity_id)
        if cached:
            cached_path, cached_pixmap = cached
            if cached_path == pic_path and cached_pixmap and not cached_pixmap.isNull():
                # Even if cached, we MUST push it to dashboard because
                # dashboard buttons might have been rebuilt (re-layout)
                if self.dashboard:
                    self.dashboard.update_media_art(entity_id, cached_pixmap)
                return

        # Not cached or path changed -> Fetch
        data = await self.ha_client.get_media_image(pic_path)
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._media_art_cache[entity_id] = (pic_path, pixmap)
                if self.dashboard:
                    self.dashboard.update_media_art(entity_id, pixmap)



    def check_for_updates(self):
        """Check for updates in background."""
        print("Checking for updates...")
        self._update_thread = UpdateCheckerThread(VERSION)
        self._update_thread.update_available.connect(self.on_update_available)
        self._update_thread.start()

    @pyqtSlot(str)
    def on_update_available(self, new_version):
        """Handle update available."""
        print(f"Update available: {new_version}")
        
        # Create message box
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Update Available")
        msg.setText(f"A new version of Prism Desktop is available ({new_version}).")
        msg.setInformativeText("Would you like to download it now?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        # Show (blocking, but we are in main thread so it's fine for a modal)
        ret = msg.exec()
        
        if ret == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl("https://github.com/lasselian/Prism-Desktop/releases/latest"))


if __name__ == '__main__':
    # Bootstrap
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # Use qasync Event Loop
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    with loop:
        load_mdi_font()
        controller = PrismDesktopApp()
        loop.run_forever()
