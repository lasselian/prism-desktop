from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QRect, QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

from ui.widgets.overlays import DimmerOverlay, ClimateOverlay, PrinterOverlay, WeatherOverlay, CameraOverlay, MowerOverlay, VacuumOverlay
from ui.widgets.dashboard_button import DashboardButton
from ui.constants import BUTTON_HEIGHT, BUTTON_SPACING
from core.temperature_utils import convert_temperature, convert_temperature_delta, normalize_temperature_unit, preference_to_unit

class OverlayManager(QObject):
    """
    Manages overlay widgets (Dimmer, Climate) and their interactions.
    Decouples logic from Dashboard.
    """
    
    # Signals
    service_request = pyqtSignal(dict)  # Emit service calls (to be connected to HAClient or Dashboard)
    morph_changed = pyqtSignal(float) # Optional: if dashboard needs to know global animation progress
    
    def __init__(self, parent: QWidget, theme_manager=None):
        super().__init__(parent)
        self.parent_widget = parent
        self.theme_manager = theme_manager
        
        self.buttons = [] # Reference to dashboard buttons
        self._entity_states = {} # Reference or copy of states
        
        # Overlays
        self.dimmer_overlay = DimmerOverlay(parent)
        self.dimmer_overlay.value_changed.connect(self.on_dimmer_value_changed)
        self.dimmer_overlay.finished.connect(self.on_dimmer_finished)
        self.dimmer_overlay.morph_changed.connect(self.on_morph_changed)
        
        self.climate_overlay = ClimateOverlay(parent)
        self.climate_overlay.value_changed.connect(self.on_climate_value_changed)
        self.climate_overlay.mode_changed.connect(self.on_climate_mode_changed)
        self.climate_overlay.fan_changed.connect(self.on_climate_fan_changed)
        self.climate_overlay.finished.connect(self.on_climate_finished)
        self.climate_overlay.morph_changed.connect(self.on_morph_changed)
        
        self.printer_overlay = PrinterOverlay(parent)
        self.printer_overlay.action_requested.connect(self.on_printer_action)
        self.printer_overlay.finished.connect(self.on_printer_finished)
        self.printer_overlay.morph_changed.connect(self.on_morph_changed)
        
        self.weather_overlay = WeatherOverlay(parent)
        self.weather_overlay.finished.connect(self.on_weather_finished)
        self.weather_overlay.morph_changed.connect(self.on_morph_changed)
        
        self.camera_overlay = CameraOverlay(parent)
        self.camera_overlay.finished.connect(self.on_camera_finished)
        self.camera_overlay.morph_changed.connect(self.on_morph_changed)

        self.mower_overlay = MowerOverlay(parent)
        self.mower_overlay.action_requested.connect(self.on_mower_action)
        self.mower_overlay.finished.connect(self.on_mower_finished)
        self.mower_overlay.morph_changed.connect(self.on_morph_changed)

        self.vacuum_overlay = VacuumOverlay(parent)
        self.vacuum_overlay.action_requested.connect(self.on_vacuum_action)
        self.vacuum_overlay.finished.connect(self.on_vacuum_finished)
        self.vacuum_overlay.morph_changed.connect(self.on_morph_changed)

        # State Tracking
        self._active_dimmer_entity = None
        self._active_dimmer_type = None
        self._pending_dimmer_val = None
        self._final_dimmer_val = None
        self._dimmer_source_btn = None
        self._dimmer_siblings = []
        
        self._active_climate_entity = None
        self._pending_climate_val = None
        self._climate_source_btn = None
        self._climate_siblings = []
        
        self._active_printer_entity = None
        self._active_printer_config = None
        self._printer_source_btn = None
        self._printer_siblings = []
        
        self._active_weather_entity = None
        self._weather_source_btn = None
        self._weather_siblings = []
        
        self._active_camera_entity = None
        self._camera_source_btn = None
        self._camera_siblings = []

        self._active_mower_entity = None
        self._mower_source_btn = None
        self._mower_siblings = []

        self._active_vacuum_entity = None
        self._vacuum_source_btn = None
        self._vacuum_siblings = []

        # Throttling Timers
        self.dimmer_timer = QTimer(self)
        self.dimmer_timer.setInterval(100)
        self.dimmer_timer.timeout.connect(self.process_pending_dimmer)
        
        self.climate_timer = QTimer(self)
        self.climate_timer.setInterval(500)
        self.climate_timer.timeout.connect(self.process_pending_climate)
        
        self._border_effect = 'Rainbow'
        self._live_dimming = True
        self._pending_open_action = None
        self._temperature_unit_preference = "celsius"

    def close_all_overlays(self):
        """Instantly hide any active overlay. Called before navigating away from grid."""
        if self.dimmer_overlay.isVisible():
            self.dimmer_overlay.hide()
            self.on_dimmer_finished()
        if self.climate_overlay.isVisible():
            self.climate_overlay.hide()
            self.on_climate_finished()
        if self.printer_overlay.isVisible():
            self.printer_overlay.hide()
            self.on_printer_finished()
        if self.weather_overlay.isVisible():
            self.weather_overlay.hide()
            self.on_weather_finished()
    def any_overlay_open(self) -> bool:
        """Return True if any overlay is currently visible."""
        return (self.dimmer_overlay.isVisible() or
                self.climate_overlay.isVisible() or
                self.printer_overlay.isVisible() or
                self.weather_overlay.isVisible() or
                self.camera_overlay.isVisible() or
                self.mower_overlay.isVisible() or
                self.vacuum_overlay.isVisible())

    def close_all_overlays_animated(self):
        """Trigger close_morph on all visible overlays instead of instant hide."""
        if self.dimmer_overlay.isVisible() and not getattr(self.dimmer_overlay, '_is_closing', False):
            self.dimmer_overlay.close_morph()
        if self.climate_overlay.isVisible() and not getattr(self.climate_overlay, '_is_closing', False):
            self.climate_overlay.close_morph()
        if self.printer_overlay.isVisible() and not getattr(self.printer_overlay, '_is_closing', False):
            self.printer_overlay.close_morph()
        if self.weather_overlay.isVisible() and not getattr(self.weather_overlay, '_is_closing', False):
            self.weather_overlay.close_morph()
        if self.camera_overlay.isVisible() and not getattr(self.camera_overlay, '_is_closing', False):
            self.camera_overlay.close_morph()
        if self.mower_overlay.isVisible() and not getattr(self.mower_overlay, '_is_closing', False):
            self.mower_overlay.close_morph()
        if self.vacuum_overlay.isVisible() and not getattr(self.vacuum_overlay, '_is_closing', False):
            self.vacuum_overlay.close_morph()

    def _queue_or_start_overlay(self, method, slot, *args):
        """
        Check if any overlay is open. If so, animate them closed and queue this method.
        Returns True if the method was queued or discarded (caller should return), False if safe to run now.
        """
        if self.any_overlay_open():
            active_btn = None
            if self.dimmer_overlay.isVisible() and not getattr(self.dimmer_overlay, '_is_closing', False):
                active_btn = self._dimmer_source_btn
            elif self.climate_overlay.isVisible() and not getattr(self.climate_overlay, '_is_closing', False):
                active_btn = self._climate_source_btn
            elif self.printer_overlay.isVisible() and not getattr(self.printer_overlay, '_is_closing', False):
                active_btn = self._printer_source_btn
            elif self.weather_overlay.isVisible() and not getattr(self.weather_overlay, '_is_closing', False):
                active_btn = self._weather_source_btn
            elif self.camera_overlay.isVisible() and not getattr(self.camera_overlay, '_is_closing', False):
                active_btn = self._camera_source_btn
            elif self.mower_overlay.isVisible() and not getattr(self.mower_overlay, '_is_closing', False):
                active_btn = self._mower_source_btn

            if active_btn and active_btn.slot == slot:
                # Toggle off the current overlay, don't reopen
                self.close_all_overlays_animated()
                return True
                
            self._pending_open_action = lambda: method(slot, *args)
            self.close_all_overlays_animated()
            return True
            
        return False

    def _check_pending_actions(self):
        """Run pending open action after overlays finish closing."""
        if self._pending_open_action and not self.any_overlay_open():
            action = self._pending_open_action
            self._pending_open_action = None
            action()

    def close_all_overlays(self):
        """Instantly hide any active overlay. Called before navigating away from grid."""
        if self.dimmer_overlay.isVisible():
            self.dimmer_overlay.hide()
            self.on_dimmer_finished()
        if self.climate_overlay.isVisible():
            self.climate_overlay.hide()
            self.on_climate_finished()
        if self.printer_overlay.isVisible():
            self.printer_overlay.hide()
            self.on_printer_finished()
        if self.weather_overlay.isVisible():
            self.weather_overlay.hide()
            self.on_weather_finished()
        if self.camera_overlay.isVisible():
            self.camera_overlay.hide()
            self.on_camera_finished()
        if self.mower_overlay.isVisible():
            self.mower_overlay.hide()
            self.on_mower_finished()

    def update_buttons(self, buttons: list):
        """Update reference to buttons."""
        self.buttons = buttons

    def update_states(self, states: dict):
        """Update reference to entity states."""
        self._entity_states = states
        
    def update_entity_state(self, entity_id: str, state: dict):
        """Update a single entity's state and notify active overlays."""
        self._entity_states[entity_id] = state
        
        # Notify active printer overlay if any of its entities changed
        if self.printer_overlay.isVisible() and self._active_printer_config:
            cfg = self._active_printer_config
            relevant_entities = [
                cfg.get('printer_state_entity'),
                cfg.get('printer_nozzle_entity'),
                cfg.get('printer_nozzle_target_entity'),
                cfg.get('printer_bed_entity'),
                cfg.get('printer_bed_target_entity'),
                cfg.get('printer_progress_entity')
            ]
            if entity_id in relevant_entities:
                self._push_printer_state()
                
        # Notify active climate overlay
        if self.climate_overlay.isVisible() and self._active_climate_entity == entity_id:
            self.climate_overlay.update_state(state)
            
        # Notify active weather overlay
        if self.weather_overlay.isVisible() and self._active_weather_entity == entity_id:
            self.weather_overlay.update_state(state)

        # Notify active mower overlay
        if self.mower_overlay.isVisible() and self._active_mower_entity == entity_id:
            self.mower_overlay.update_state(state)

        # Notify active vacuum overlay
        if self.vacuum_overlay.isVisible() and self._active_vacuum_entity == entity_id:
            self.vacuum_overlay.update_state(state)

    def update_camera_image(self, entity_id: str, pixmap):
        """Update active overlays with new camera image."""
        if self.printer_overlay.isVisible() and self._active_printer_config:
            if self._active_printer_config.get('printer_camera_entity') == entity_id:
                self.printer_overlay.set_camera_pixmap(pixmap)
        if self.camera_overlay.isVisible() and self._active_camera_entity == entity_id:
            self.camera_overlay.set_camera_pixmap(pixmap)

    def _push_printer_state(self):
        """Consolidate entities into a virtual state for the printer overlay."""
        if not self._active_printer_config: return None
        
        cfg = self._active_printer_config
        
        state_data = self._entity_states.get(cfg.get('printer_state_entity'), {})
        primary_state = state_data.get('state', 'unknown')
        attrs = dict(state_data.get('attributes', {}))
        
        # Mix in Nozzle
        noz_data = self._entity_states.get(cfg.get('printer_nozzle_entity'), {})
        attrs['hotend_actual'] = noz_data.get('attributes', {}).get('actual_temperature', noz_data.get('state', 0.0))
        noz_unit = noz_data.get('attributes', {}).get('unit_of_measurement')
        if noz_unit:
            attrs['temperature_unit'] = noz_unit
        
        noz_target_ent = cfg.get('printer_nozzle_target_entity')
        if noz_target_ent:
            noz_tgt_data = self._entity_states.get(noz_target_ent, {})
            try:
                attrs['hotend_target'] = float(noz_tgt_data.get('state', 0.0))
            except (ValueError, TypeError):
                attrs['hotend_target'] = 0.0
        else:
            attrs['hotend_target'] = noz_data.get('attributes', {}).get('target_temperature', noz_data.get('attributes', {}).get('temperature', 0.0))
        
        # Mix in Bed
        bed_data = self._entity_states.get(cfg.get('printer_bed_entity'), {})
        attrs['bed_actual'] = bed_data.get('attributes', {}).get('actual_temperature', bed_data.get('state', 0.0))
        
        bed_target_ent = cfg.get('printer_bed_target_entity')
        if bed_target_ent:
            bed_tgt_data = self._entity_states.get(bed_target_ent, {})
            try:
                attrs['bed_target'] = float(bed_tgt_data.get('state', 0.0))
            except (ValueError, TypeError):
                attrs['bed_target'] = 0.0
        else:
            attrs['bed_target'] = bed_data.get('attributes', {}).get('target_temperature', bed_data.get('attributes', {}).get('temperature', 0.0))
        
        # Ensure progress is there
        prog_ent = cfg.get('printer_progress_entity')
        if prog_ent:
            prog_val = self._entity_states.get(prog_ent, {}).get('state', 0.0)
            try:
                attrs['progress'] = float(prog_val)
            except (ValueError, TypeError):
                attrs['progress'] = 0.0
        elif 'progress' not in attrs:
            attrs['progress'] = attrs.get('job_percentage', 0.0)
            
        virtual_state = {
            'state': primary_state,
            'attributes': attrs
        }
        self.printer_overlay.update_state(virtual_state)
        return virtual_state
        
    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.dimmer_overlay.set_border_effect(effect)
        self.climate_overlay.set_border_effect(effect)
        self.printer_overlay.set_border_effect(effect)
        self.weather_overlay.set_border_effect(effect)
        self.mower_overlay.set_border_effect(effect)

    def set_temperature_unit_preference(self, preference: str):
        self._temperature_unit_preference = preference
        self.weather_overlay.set_temperature_unit_preference(preference)
        self.printer_overlay.set_temperature_unit_preference(preference)

    # ==========================
    # Dimmer / Volume Logic
    # ==========================

    def start_dimmer(self, slot: int, global_rect: QRect, config: dict):
        """Start the dimmer morph sequence."""
        if self._queue_or_start_overlay(self.start_dimmer, slot, global_rect, config):
            return

        if not config: return
        
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_dimmer_entity = entity_id
        self._active_dimmer_type = config.get('type', 'switch')
        
        # Calculate Start Value
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        current_val = 0
        
        state_obj = self._entity_states.get(entity_id, {})
        attrs = state_obj.get('attributes', {})
        
        if self._active_dimmer_type == 'curtain':
            pos = attrs.get('current_position')
            if pos is not None:
                current_val = int(pos)
            elif source_btn:
                current_val = 100 if source_btn._state == "open" else 0
        else:
            bri = attrs.get('brightness')
            if bri is not None:
                current_val = int((bri / 255.0) * 100)
            elif source_btn and hasattr(source_btn, '_state'):
                current_val = 100 if source_btn._state == "on" else 0
        
        # Colors
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#FFD700")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#FFD700'))
            
        # Geometries
        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='dimmer')
        
        # Start
        self.dimmer_overlay.set_border_effect(self._border_effect)
        
        label = config.get('label', 'Dimmer')
        # If type is media_player (via Volume), label usually passed differently or inferred?
        # Standard dimmer uses config label.
        
        self.dimmer_overlay.start_morph(
            start_rect, target_rect, current_val, label,
            color=accent_color, base_color=base_color
        )
        self.dimmer_timer.start()

    def start_volume(self, slot: int, global_rect: QRect, config: dict):
        """Start volume slider using dimmer overlay."""
        if self._queue_or_start_overlay(self.start_volume, slot, global_rect, config):
            return

        if not config: return
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        if not source_btn: return
        
        # Get volume
        current_vol = source_btn._media_state.get('attributes', {}).get('volume_level', 0.5)
        start_pct = int(current_vol * 100)
        
        self._active_dimmer_entity = entity_id
        self._active_dimmer_type = 'media_player'
        self._pending_dimmer_val = None
        self._final_dimmer_val = None
        
        # Geometries
        local_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(local_rect, global_rect.size())
        
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='dimmer')
        
        # Color
        accent = config.get('color')
        color = QColor(accent) if accent else QColor("#4285F4")
        
        self.dimmer_overlay.set_border_effect(self._border_effect)
        self.dimmer_overlay.start_morph(
            start_rect, target_rect, start_pct, "Volume",
            color=color, base_color=QColor(self.theme_manager.get_colors().get('base', '#2d2d2d')) if self.theme_manager else QColor("#2d2d2d")
        )
        if not self.dimmer_timer.isActive():
            self.dimmer_timer.start()

    def _calculate_target_rect_and_siblings(self, source_btn, slot, overlay_type='dimmer'):
        """Shared logic to find row siblings and target rect."""
        if not source_btn: return QRect()
        
        src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
        src_top = src_pos.y()
        src_bottom = src_pos.y() + source_btn.height()
        
        siblings = []
        row_buttons = []
        
        for btn in self.buttons:
            if not btn.isVisible(): continue
            btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
            btn_top = btn_pos.y()
            btn_bottom = btn_pos.y() + btn.height()
            
            # Vertical overlap = same row
            if btn_top < src_bottom and btn_bottom > src_top:
                row_buttons.append((btn, btn_pos))
                if btn != source_btn:
                    siblings.append(btn)
        
        # Store source button, but defer sibling assignment until after Rect calculation
        if overlay_type == 'climate':
            self._climate_source_btn = source_btn  
            source_btn.set_opacity(0.0)
            self._climate_siblings = []
        elif overlay_type == 'printer':
            self._printer_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._printer_siblings = []
        elif overlay_type == 'weather':
            self._weather_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._weather_siblings = []
        elif overlay_type == 'camera':
            self._camera_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._camera_siblings = []
        elif overlay_type == 'mower':
            self._mower_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._mower_siblings = []
        elif overlay_type == 'vacuum':
            self._vacuum_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._vacuum_siblings = []
        else:
            self._dimmer_source_btn = source_btn
            source_btn.set_opacity(0.0)
            self._dimmer_siblings = []
            
        # Calculate Rect
        if row_buttons:
            row_buttons.sort(key=lambda x: x[1].x())
            first_btn, first_pos = row_buttons[0]
            last_btn, last_pos = row_buttons[-1]
            
            target_x = first_pos.x()
            target_width = (last_pos.x() + last_btn.width()) - first_pos.x()
            
            # Enforce Max Width of 6 Columns
            # Better to use constants: (6 * BUTTON_WIDTH) + (5 * BUTTON_SPACING)
            from ui.constants import BUTTON_WIDTH, BUTTON_SPACING
            max_6_col_width = (6 * BUTTON_WIDTH) + (5 * BUTTON_SPACING)
            
            if target_width > max_6_col_width:
                # If we need to clamp, decide alignment based on source button position
                # Center of source button relative to the full row width
                row_width = (last_pos.x() + last_btn.width()) - first_pos.x()
                src_center = src_pos.x() + (source_btn.width() / 2)
                row_center = first_pos.x() + (row_width / 2)
                
                # If source is in the right half, align right-to-left
                if src_center > row_center:
                    # Align Right: keep the right edge fixed
                    right_edge = target_x + target_width 
                    target_width = max_6_col_width
                    target_x = right_edge - target_width
                else:
                     # Align Left (Default): keep left edge fixed
                     target_width = max_6_col_width
            
            # Clamp width (fix for 3/4 col grid issue)
            # This is a safety check against window bounds
            max_w = self.parent_widget.width()
            if target_x + target_width > max_w:
                target_width = max_w - target_x
            if target_x < 0:
                target_x = 0
            
            final_rect = QRect(int(target_x), src_pos.y(), int(target_width), source_btn.height())
            
            # RE-FILTER SIBLINGS based on final intersection
            # We collected row_buttons purely for geometry calculation earlier.
            # Now we decide who gets faded.
            filtered_siblings = []
            for btn, btn_pos in row_buttons:
                if btn == source_btn: continue
                
                # Create button rect relative to parent
                btn_rect = QRect(btn_pos, btn.size())
                
                # If the overlay covers (intersects) this button, it should fade
                # We use a slight margin intersection to be safe
                if final_rect.intersects(btn_rect):
                    filtered_siblings.append(btn)
                else:
                    # Explicitly ensure it is NOT faded if it was previously
                    btn.set_opacity(1.0)
            
            if overlay_type == 'climate':
                self._climate_siblings = filtered_siblings
            elif overlay_type == 'printer':
                self._printer_siblings = filtered_siblings
            elif overlay_type == 'weather':
                self._weather_siblings = filtered_siblings
            elif overlay_type == 'camera':
                self._camera_siblings = filtered_siblings
            elif overlay_type == 'mower':
                self._mower_siblings = filtered_siblings
            else:
                self._dimmer_siblings = filtered_siblings
                
            return final_rect
        else:
            return QRect(src_pos, source_btn.size())

    def on_dimmer_value_changed(self, value):
        self._pending_dimmer_val = value

    def process_pending_dimmer(self):
        if self._pending_dimmer_val is None or not self._active_dimmer_entity:
            return
            
        val = self._pending_dimmer_val
        self._pending_dimmer_val = None
        self._final_dimmer_val = val
        
        dimmer_type = getattr(self, '_active_dimmer_type', 'switch')
        
        if dimmer_type in ['curtain', 'media_player']:
            # Send only on release
            return
            
        if not self._live_dimming:
            return
            
        # Live update for lights
        self.service_request.emit({
            "service": "light.turn_on",
            "entity_id": self._active_dimmer_entity,
            "service_data": {"brightness_pct": val},
            "skip_debounce": True
        })

    def on_dimmer_finished(self):
        self.dimmer_timer.stop()
        
        final_val = getattr(self, '_final_dimmer_val', None)
        dimmer_type = getattr(self, '_active_dimmer_type', 'switch')
        entity_id = self._active_dimmer_entity
        
        if final_val is not None and entity_id:
            if dimmer_type == 'curtain':
                self.service_request.emit({
                    "service": "cover.set_cover_position",
                    "entity_id": entity_id,
                    "service_data": {"position": final_val}
                })
            elif dimmer_type == 'media_player':
                self.service_request.emit({
                    "service": "media_player.volume_set",
                    "entity_id": entity_id,
                    "service_data": {"volume_level": final_val / 100.0},
                    "skip_debounce": True
                })
            # Lights are usually handled by live update, but send one final to be sure? 
            # Or if live dimming was off.
            elif dimmer_type != 'curtain' and (not self._live_dimming or final_val is not None):
                 self.service_request.emit({
                    "service": "light.turn_on",
                    "entity_id": entity_id,
                    "service_data": {"brightness_pct": final_val},
                    "skip_debounce": True
                })
        
        self._reset_dimmer_state()
        self.parent_widget.activateWindow()

    def _reset_dimmer_state(self):
        self._active_dimmer_entity = None
        self._active_dimmer_type = None
        self._pending_dimmer_val = None
        self._final_dimmer_val = None
        
        # Restore siblings
        for btn in self._dimmer_siblings:
            btn.set_opacity(1.0)
        self._dimmer_siblings = []
        
        if self._dimmer_source_btn:
            self._dimmer_source_btn.set_opacity(1.0)
            self._dimmer_source_btn = None
            
        self._check_pending_actions()

    # ==========================
    # Climate Logic
    # ==========================
    
    def start_climate(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_climate, slot, global_rect, config):
            return

        if not config: return
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_climate_entity = entity_id
        
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        curr_temp = 20.0
        if source_btn and hasattr(source_btn, '_value'):
             try:
                 temp_str = str(source_btn._value).replace('°', '').strip()
                 curr_temp = float(temp_str)
             except: pass
             
        # Colors
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#EA4335")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#EA4335'))
            
        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='climate')
        
        # Enforce Minimum Height (2 Rows) for Climate Overlay
        min_height = (BUTTON_HEIGHT * 2) + BUTTON_SPACING
        if target_rect.height() < min_height:
            target_rect.setHeight(min_height)
            
            # Check if it goes off-screen (bottom)
            # Use parent widget height minus footer area (approx 40px) as safe zone
            safe_bottom = self.parent_widget.height() - 40 
            
            if target_rect.bottom() > safe_bottom:
                 # Shift up so the BOTTOM of the overlay aligns with the BOTTOM of the source button
                 # This makes it expand UPWARDS from the button
                 if source_btn:
                     src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                     src_bottom = src_pos.y() + source_btn.height()
                     target_rect.moveBottom(src_bottom)
                 else:
                     # Fallback: Just shift it on screen
                     diff = target_rect.bottom() - safe_bottom
                     target_rect.moveTop(target_rect.top() - diff)

            # Identify additional buttons covered by the expanded/moved rect
            for btn in self.buttons:
                if not btn.isVisible() or btn == source_btn: continue
                if btn in self._climate_siblings: continue

                btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                btn_rect = QRect(btn_pos, btn.size())
                
                # Check for intersection with the new target geometry
                if target_rect.intersects(btn_rect):
                    self._climate_siblings.append(btn)
                    btn.set_opacity(0.0)

        
        current_state = self._entity_states.get(entity_id, {})
        attrs = current_state.get('attributes', {})
        source_unit = attrs.get('temperature_unit')
        display_unit = preference_to_unit(self._temperature_unit_preference, fallback=source_unit)
        current_temp = attrs.get('temperature', curr_temp)
        converted_temp = convert_temperature(current_temp, source_unit, display_unit)
        if converted_temp is not None:
            curr_temp = converted_temp

        min_temp = attrs.get('min_temp', 5.0)
        max_temp = attrs.get('max_temp', 35.0)
        temp_step = attrs.get('target_temp_step', 0.5)
        converted_min = convert_temperature(min_temp, source_unit, display_unit)
        converted_max = convert_temperature(max_temp, source_unit, display_unit)
        converted_step = convert_temperature_delta(temp_step, source_unit, display_unit)
        self.climate_overlay.configure_temperature_range(
            converted_min if converted_min is not None else 5.0,
            converted_max if converted_max is not None else 35.0,
            converted_step if converted_step is not None else 0.5,
            normalize_temperature_unit(display_unit),
        )
        
        self.climate_overlay.set_border_effect(self._border_effect)
        self.climate_overlay.start_morph(
            start_rect, target_rect, curr_temp, config.get('label', 'Climate'),
            color=accent_color, base_color=base_color,
            current_state=current_state
        )
        self.climate_timer.start()

    def on_climate_value_changed(self, value):
        self._pending_climate_val = value

    def process_pending_climate(self):
        if self._pending_climate_val is None or not self._active_climate_entity:
            return
        
        val = self._pending_climate_val
        self._pending_climate_val = None

        attrs = self._entity_states.get(self._active_climate_entity, {}).get('attributes', {})
        source_unit = attrs.get('temperature_unit')
        display_unit = preference_to_unit(self._temperature_unit_preference, fallback=source_unit)
        service_temp = convert_temperature(val, display_unit, source_unit)
        if service_temp is None:
            service_temp = val
        
        self.service_request.emit({
            "service": "climate.set_temperature",
            "entity_id": self._active_climate_entity,
            "service_data": {"temperature": service_temp}
        })

    def on_climate_mode_changed(self, mode):
        if self._active_climate_entity:
            self.service_request.emit({
                "service": "climate.set_hvac_mode",
                "entity_id": self._active_climate_entity,
                "service_data": {"hvac_mode": mode}
            })

    def on_climate_fan_changed(self, mode):
        if self._active_climate_entity:
             self.service_request.emit({
                "service": "climate.set_fan_mode",
                "entity_id": self._active_climate_entity,
                "service_data": {"fan_mode": mode}
            })

    def on_climate_finished(self):
        self.climate_timer.stop()
        self._pending_climate_val = None
        self._active_climate_entity = None
        
        for btn in self._climate_siblings:
            btn.set_opacity(1.0)
        self._climate_siblings = []
        
        if self._climate_source_btn:
            self._climate_source_btn.set_opacity(1.0)
            self._climate_source_btn = None
            
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Shared
    # ==========================
    
    def on_morph_changed(self, progress: float):
        """Update sibling opacity."""
        opacity = 1.0 - (progress * 0.8) # Results in 0.2 final opacity
        for btn in self._dimmer_siblings:
            btn.set_opacity(opacity)
        for btn in self._climate_siblings:
            btn.set_opacity(opacity)
        for btn in self._printer_siblings:
            btn.set_opacity(opacity)
        for btn in self._weather_siblings:
            btn.set_opacity(opacity)
        for btn in self._camera_siblings:
            btn.set_opacity(opacity)
        for btn in self._mower_siblings:
            btn.set_opacity(opacity)

        # Fade source button back in during close (overlay fades out, button fades in)
        source_opacity = 1.0 - progress  # 0→1 as progress goes 1→0
        if self._dimmer_source_btn and self.dimmer_overlay._is_closing:
            self._dimmer_source_btn.set_opacity(source_opacity)
        if self._climate_source_btn and self.climate_overlay._is_closing:
            self._climate_source_btn.set_opacity(source_opacity)
        if self._printer_source_btn and self.printer_overlay._is_closing:
            self._printer_source_btn.set_opacity(source_opacity)
        if self._weather_source_btn and self.weather_overlay._is_closing:
            self._weather_source_btn.set_opacity(source_opacity)
        if self._camera_source_btn and self.camera_overlay._is_closing:
            self._camera_source_btn.set_opacity(source_opacity)
        if self._mower_source_btn and self.mower_overlay._is_closing:
            self._mower_source_btn.set_opacity(source_opacity)

        self.morph_changed.emit(progress)

    # ==========================
    # 3D Printer Logic
    # ==========================

    def start_printer(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_printer, slot, global_rect, config):
            return

        if not config: return
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_printer_entity = entity_id
        self._active_printer_config = config
        
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        self._printer_source_btn = source_btn
        
        # Prepare initial data
        
        # Push initial camera if available on button
        if source_btn and hasattr(source_btn, '_last_camera_pixmap') and source_btn._last_camera_pixmap:
            self.printer_overlay.set_camera_pixmap(source_btn._last_camera_pixmap)
        
        # Colors
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#FF6D00")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#FF6D00'))
            
        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        # Use existing shared logic but with 'printer' overlay_type
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='printer')
        
        # We want the printer overlay to be as big as possible (at least 2x4 usually)
        from ui.constants import BUTTON_HEIGHT, BUTTON_SPACING, BUTTON_WIDTH
        min_height = (BUTTON_HEIGHT * 2) + BUTTON_SPACING
        if target_rect.height() < min_height:
            target_rect.setHeight(min_height)
            
            safe_bottom = self.parent_widget.height() - 40 
            if target_rect.bottom() > safe_bottom:
                 if source_btn:
                     src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                     src_bottom = src_pos.y() + source_btn.height()
                     target_rect.moveBottom(src_bottom)
                 else:
                     diff = target_rect.bottom() - safe_bottom
                     target_rect.moveTop(target_rect.top() - diff)

            for btn in self.buttons:
                if not btn.isVisible() or btn == source_btn: continue
                if btn in self._printer_siblings: continue
                btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                btn_rect = QRect(btn_pos, btn.size())
                if target_rect.intersects(btn_rect):
                    self._printer_siblings.append(btn)
                    btn.set_opacity(0.0)

        # Consolidate entities into a virtual state for the printer overlay
        virtual_state = self._push_printer_state()
        
        self.printer_overlay.set_border_effect(self._border_effect)
        self.printer_overlay.start_morph(
            start_rect, target_rect, config.get('label', '3D Printer'),
            color=accent_color, base_color=base_color,
            current_state=virtual_state
        )

    def on_printer_action(self, action: str):
        if not self._active_printer_config: return
        
        if action in ('pause', 'resume'):
            entity = self._active_printer_config.get('printer_pause_entity')
            if entity:
                self.service_request.emit({
                    "service": "button.press",
                    "entity_id": entity
                })
        elif action == 'stop':
            entity = self._active_printer_config.get('printer_stop_entity')
            if entity:
                self.service_request.emit({
                    "service": "button.press",
                    "entity_id": entity
                })

    def on_printer_finished(self):
        self._active_printer_entity = None
        self._active_printer_config = None
        for btn in self._printer_siblings:
            btn.set_opacity(1.0)
        self._printer_siblings = []
        if self._printer_source_btn:
            self._printer_source_btn.set_opacity(1.0)
            self._printer_source_btn = None
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Lawn Mower Logic
    # ==========================

    def start_mower(self, slot: int, global_rect: QRect):
        if self._queue_or_start_overlay(self.start_mower, slot, global_rect):
            return

        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        if not source_btn or not source_btn.config:
            return
        config = source_btn.config
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_mower_entity = entity_id

        # Colors
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#4CAF50")
        if self.theme_manager and not button_color:
            accent_color = QColor(self.theme_manager.get_colors().get('accent', '#4CAF50'))

        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='mower')

        # Enforce 2-row minimum height
        min_height = (BUTTON_HEIGHT * 2) + BUTTON_SPACING
        if target_rect.height() < min_height:
            target_rect.setHeight(min_height)

            safe_bottom = self.parent_widget.height() - 40
            if target_rect.bottom() > safe_bottom:
                if source_btn:
                    src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                    src_bottom = src_pos.y() + source_btn.height()
                    target_rect.moveBottom(src_bottom)
                else:
                    diff = target_rect.bottom() - safe_bottom
                    target_rect.moveTop(target_rect.top() - diff)

            for btn in self.buttons:
                if not btn.isVisible() or btn == source_btn:
                    continue
                if btn in self._mower_siblings:
                    continue
                btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                btn_rect = QRect(btn_pos, btn.size())
                if target_rect.intersects(btn_rect):
                    self._mower_siblings.append(btn)
                    btn.set_opacity(0.0)

        current_state = self._entity_states.get(entity_id, {})

        self.mower_overlay.set_border_effect(self._border_effect)
        self.mower_overlay.start_morph(
            start_rect, target_rect, config.get('label', 'Mower'),
            color=accent_color, base_color=base_color,
            current_state=current_state
        )

    def on_mower_action(self, action: str):
        if not self._active_mower_entity:
            return
        self.service_request.emit({
            "service": f"lawn_mower.{action}",
            "entity_id": self._active_mower_entity
        })

    def on_mower_finished(self):
        self._active_mower_entity = None
        for btn in self._mower_siblings:
            btn.set_opacity(1.0)
        self._mower_siblings = []
        if self._mower_source_btn:
            self._mower_source_btn.set_opacity(1.0)
            self._mower_source_btn = None
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Vacuum Logic
    # ==========================

    def start_vacuum(self, slot: int, global_rect: QRect):
        if self._queue_or_start_overlay(self.start_vacuum, slot, global_rect):
            return

        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        if not source_btn or not source_btn.config:
            return
        config = source_btn.config
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_vacuum_entity = entity_id

        # Colors
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#4CAF50")
        if self.theme_manager and not button_color:
            accent_color = QColor(self.theme_manager.get_colors().get('accent', '#4CAF50'))

        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='vacuum')

        # Enforce 2-row minimum height
        min_height = (BUTTON_HEIGHT * 2) + BUTTON_SPACING
        if target_rect.height() < min_height:
            target_rect.setHeight(min_height)

            safe_bottom = self.parent_widget.height() - 40
            if target_rect.bottom() > safe_bottom:
                if source_btn:
                    src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                    src_bottom = src_pos.y() + source_btn.height()
                    target_rect.moveBottom(src_bottom)
                else:
                    diff = target_rect.bottom() - safe_bottom
                    target_rect.moveTop(target_rect.top() - diff)

            for btn in self.buttons:
                if not btn.isVisible() or btn == source_btn:
                    continue
                if btn in self._vacuum_siblings:
                    continue
                btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                btn_rect = QRect(btn_pos, btn.size())
                if target_rect.intersects(btn_rect):
                    self._vacuum_siblings.append(btn)
                    btn.set_opacity(0.0)

        current_state = self._entity_states.get(entity_id, {})

        self.vacuum_overlay.set_border_effect(self._border_effect)
        self.vacuum_overlay.start_morph(
            start_rect, target_rect, config.get('label', 'Vacuum'),
            color=accent_color, base_color=base_color,
            current_state=current_state
        )

    def on_vacuum_action(self, action: str):
        if not self._active_vacuum_entity:
            return
        self.service_request.emit({
            "service": f"vacuum.{action}",
            "entity_id": self._active_vacuum_entity
        })

    def on_vacuum_finished(self):
        self._active_vacuum_entity = None
        for btn in self._vacuum_siblings:
            btn.set_opacity(1.0)
        self._vacuum_siblings = []
        if self._vacuum_source_btn:
            self._vacuum_source_btn.set_opacity(1.0)
            self._vacuum_source_btn = None
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Weather Logic
    # ==========================

    def start_weather(self, slot: int, global_rect: QRect, config: dict, forecasts: list):
        if self._queue_or_start_overlay(self.start_weather, slot, global_rect, config, forecasts):
            return

        if not config: return
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_weather_entity = entity_id
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
        button_color = config.get('color')
        accent_color = QColor(button_color) if button_color else QColor("#4285F4")
        
        if self.theme_manager and not button_color:
            cols = self.theme_manager.get_colors()
            accent_color = QColor(cols.get('accent', '#4285F4'))
            
        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='weather')
        
        # Enforce Minimum Height (2 Rows) for Weather Overlay
        min_height = (BUTTON_HEIGHT * 2) + BUTTON_SPACING
        if target_rect.height() < min_height:
            target_rect.setHeight(min_height)
            
            safe_bottom = self.parent_widget.height() - 40
            if target_rect.bottom() > safe_bottom:
                if source_btn:
                    src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                    src_bottom = src_pos.y() + source_btn.height()
                    target_rect.moveBottom(src_bottom)
                else:
                    diff = target_rect.bottom() - safe_bottom
                    target_rect.moveTop(target_rect.top() - diff)

            # Identify additional buttons covered by the expanded rect
            for btn in self.buttons:
                if not btn.isVisible() or btn == source_btn: continue
                if btn in self._weather_siblings: continue
                btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                btn_rect = QRect(btn_pos, btn.size())
                if target_rect.intersects(btn_rect):
                    self._weather_siblings.append(btn)
                    btn.set_opacity(0.0)

        current_state = self._entity_states.get(entity_id, {})
        
        self.weather_overlay.set_border_effect(self._border_effect)
        self.weather_overlay.start_morph(
            start_rect, target_rect, current_state, forecasts, config.get('label', 'Weather'),
            color=accent_color, base_color=base_color
        )

    def on_weather_finished(self):
        self._active_weather_entity = None
        for btn in self._weather_siblings:
            btn.set_opacity(1.0)
        self._weather_siblings = []
        if self._weather_source_btn:
            self._weather_source_btn.set_opacity(1.0)
            self._weather_source_btn = None
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Camera Logic
    # ==========================
    
    def start_camera(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_camera, slot, global_rect, config):
            return

        if not config: return
        entity_id = config.get('entity_id')
        if not entity_id: return
        
        self._active_camera_entity = entity_id
        
        source_btn = next((b for b in self.buttons if b.slot == slot), None)
        self._camera_source_btn = source_btn
        
        if source_btn and hasattr(source_btn, '_last_camera_pixmap') and source_btn._last_camera_pixmap:
            self.camera_overlay.set_camera_pixmap(source_btn._last_camera_pixmap)
            
        if self.theme_manager:
            base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
        else:
            base_color = QColor("#2d2d2d")
            
        start_rect = self.parent_widget.mapFromGlobal(global_rect.topLeft())
        start_rect = QRect(start_rect, global_rect.size())
        
        from ui.constants import BUTTON_HEIGHT, BUTTON_SPACING, BUTTON_WIDTH
        
        # Calculate max boundaries of the actual grid layout
        visible_rects = []
        for btn in self.buttons:
            if btn.isVisible():
                pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
                visible_rects.append(QRect(pos, btn.size()))
                
        if visible_rects:
            grid_min_x = min(r.left() for r in visible_rects)
            grid_max_x = max(r.right() for r in visible_rects)
            grid_min_y = min(r.top() for r in visible_rects)
            grid_max_y = max(r.bottom() for r in visible_rects)
        else:
            # Fallback if no layout calculated
            grid_min_x = start_rect.left()
            grid_max_x = start_rect.right()
            grid_min_y = start_rect.top()
            grid_max_y = start_rect.bottom()
            
        avail_w = grid_max_x - grid_min_x + 1
        avail_h = grid_max_y - grid_min_y + 1
        
        max_cols = 4
        max_rows = 4
        
        ideal_w = (BUTTON_WIDTH * max_cols) + (BUTTON_SPACING * (max_cols - 1))
        ideal_h = (BUTTON_HEIGHT * max_rows) + (BUTTON_SPACING * (max_rows - 1))
        
        target_w = min(ideal_w, avail_w)
        target_h = min(ideal_h, avail_h)
        
        # Start at top-left of source button
        target_x = start_rect.left()
        target_y = start_rect.top()
        
        # Shift left if hanging over the right edge
        if target_x + target_w > grid_max_x + 1:
            target_x = grid_max_x - target_w + 1
            if target_x < grid_min_x:
                target_x = grid_min_x
                
        # Shift up if hanging over the bottom edge
        if target_y + target_h > grid_max_y + 1:
            target_y = grid_max_y - target_h + 1
            if target_y < grid_min_y:
                target_y = grid_min_y

        target_rect = QRect(target_x, target_y, target_w, target_h)
        
        for btn in self.buttons:
            if not btn.isVisible() or btn == source_btn: continue
            if btn in self._camera_siblings: continue
            btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
            # Create a slight margin logic for intersection, or use the parent rect directly
            btn_rect = QRect(btn_pos, btn.size())
            # Use small margins to avoid floating point or off-by-one pixel overlaps causing unintended fade
            if target_rect.intersects(btn_rect.adjusted(2, 2, -2, -2)):
                self._camera_siblings.append(btn)
                btn.set_opacity(0.0)
                
        self.camera_overlay.set_border_effect(self._border_effect)
        label = config.get('label', 'Camera')
        self.camera_overlay.start_morph(
            start_rect, target_rect, label, base_color=base_color
        )

    def on_camera_finished(self):
        self._active_camera_entity = None
        for btn in self._camera_siblings:
            btn.set_opacity(1.0)
        self._camera_siblings = []
        if self._camera_source_btn:
            self._camera_source_btn.set_opacity(1.0)
            self._camera_source_btn = None
        self.parent_widget.activateWindow()
        self._check_pending_actions()
