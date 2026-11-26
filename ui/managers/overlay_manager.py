from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QRect, QPoint, QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

from ui.widgets.overlays import (
    DimmerOverlay,
    ClimateOverlay,
    PrinterOverlay,
    WeatherOverlay,
    CameraOverlay,
    MowerOverlay,
    VacuumOverlay,
    AlarmOverlay,
)
from ui.constants import BUTTON_HEIGHT, BUTTON_SPACING, BUTTON_WIDTH
from core.temperature_utils import (
    convert_temperature,
    convert_temperature_delta,
    normalize_temperature_unit,
    preference_to_unit,
)

TWO_ROW_MIN_HEIGHT = (BUTTON_HEIGHT * 2) + BUTTON_SPACING


class OverlayManager(QObject):
    """
    Manages overlay widgets (Dimmer, Climate) and their interactions.
    Decouples logic from Dashboard.
    """

    # Signals
    service_request = pyqtSignal(dict)  # Emit service calls (to be connected to HAClient or Dashboard)
    morph_changed = pyqtSignal(float)  # Optional: if dashboard needs to know global animation progress
    camera_pip_requested = pyqtSignal(str, str)  # camera entity_id, title

    def __init__(self, parent: QWidget, theme_manager=None):
        super().__init__(parent)
        self.parent_widget = parent
        self.theme_manager = theme_manager

        self.buttons = []  # Reference to dashboard buttons
        self._entity_states = {}  # Reference or copy of states

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
        self.camera_overlay.pip_requested.connect(self.on_camera_pip_requested)

        self.mower_overlay = MowerOverlay(parent)
        self.mower_overlay.action_requested.connect(self.on_mower_action)
        self.mower_overlay.finished.connect(self.on_mower_finished)
        self.mower_overlay.morph_changed.connect(self.on_morph_changed)

        self.vacuum_overlay = VacuumOverlay(parent)
        self.vacuum_overlay.action_requested.connect(self.on_vacuum_action)
        self.vacuum_overlay.finished.connect(self.on_vacuum_finished)
        self.vacuum_overlay.morph_changed.connect(self.on_morph_changed)

        self.alarm_overlay = AlarmOverlay(parent)
        self.alarm_overlay.action_requested.connect(self.on_alarm_action)
        self.alarm_overlay.finished.connect(self.on_alarm_finished)
        self.alarm_overlay.morph_changed.connect(self.on_morph_changed)
        self.alarm_overlay.keypad_toggled.connect(self.on_alarm_keypad_toggled)

        # Per-overlay state: siblings faded by the overlay, and the source button it morphed from
        # Every overlay, with the name of the handler that cleans up after it closes.
        self._overlay_finishers = {
            self.dimmer_overlay: 'on_dimmer_finished',
            self.climate_overlay: 'on_climate_finished',
            self.printer_overlay: 'on_printer_finished',
            self.weather_overlay: 'on_weather_finished',
            self.camera_overlay: 'on_camera_finished',
            self.mower_overlay: 'on_mower_finished',
            self.vacuum_overlay: 'on_vacuum_finished',
            self.alarm_overlay: 'on_alarm_finished',
        }
        self._overlay_state = {overlay: {'siblings': [], 'source': None} for overlay in self._overlay_finishers}

        # State Tracking
        self._active_dimmer_entity = None
        self._active_dimmer_type = None
        self._pending_dimmer_val = None
        self._final_dimmer_val = None

        self._active_climate_entity = None
        self._pending_climate_val = None

        self._active_printer_entity = None
        self._active_printer_config = None

        self._active_weather_entity = None
        self._active_camera_entity = None
        self._active_mower_entity = None
        self._active_vacuum_entity = None
        self._active_alarm_entity = None

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

        parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.parent_widget and event.type() == QEvent.Type.Resize:
            self.close_all_overlays()
        return False

    def _emit_service(self, request: dict):
        """Forward an overlay's service call to the app.

        These are deliberate, distinct actions (a temperature step, then a mode,
        then a fan speed...), not accidental double-clicks, so they bypass the
        per-entity click debounce that would otherwise swallow the later ones.
        """
        request.setdefault("skip_debounce", True)
        self.service_request.emit(request)

    def any_overlay_open(self) -> bool:
        """Return True if any overlay is currently visible."""
        return any(overlay.isVisible() for overlay in self._overlay_finishers)

    def close_all_overlays_animated(self):
        """Trigger close_morph on all visible overlays instead of instant hide."""
        for overlay in self._overlay_finishers:
            if overlay.isVisible() and not getattr(overlay, '_is_closing', False):
                overlay.close_morph()

    def _queue_or_start_overlay(self, method, slot, *args):
        """
        Check if any overlay is open. If so, animate them closed and queue this method.
        Returns True if the method was queued or discarded (caller should return), False if safe to run now.
        """
        if self.any_overlay_open():
            active_btn = None
            for overlay, state in self._overlay_state.items():
                if overlay.isVisible() and not getattr(overlay, '_is_closing', False):
                    active_btn = state['source']
                    break

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
        for overlay, finisher in self._overlay_finishers.items():
            if overlay.isVisible():
                overlay.hide()
                getattr(self, finisher)()

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
                cfg.get('printer_progress_entity'),
            ]
            if entity_id in relevant_entities:
                self._push_printer_state()

        if self.climate_overlay.isVisible() and self._active_climate_entity == entity_id:
            self.climate_overlay.update_state(state)

        if self.weather_overlay.isVisible() and self._active_weather_entity == entity_id:
            self.weather_overlay.update_state(state)

        if self.mower_overlay.isVisible() and self._active_mower_entity == entity_id:
            self.mower_overlay.update_state(state)

        if self.vacuum_overlay.isVisible() and self._active_vacuum_entity == entity_id:
            self.vacuum_overlay.update_state(state)

        if self.alarm_overlay.isVisible() and self._active_alarm_entity == entity_id:
            self.alarm_overlay.update_state(state)

    def update_camera_image(self, entity_id: str, pixmap):
        """Update active overlays with new camera image."""
        if self.printer_overlay.isVisible() and self._active_printer_config:
            if self._active_printer_config.get('printer_camera_entity') == entity_id:
                self.printer_overlay.set_camera_pixmap(pixmap)
        if self.camera_overlay.isVisible() and self._active_camera_entity == entity_id:
            self.camera_overlay.set_camera_pixmap(pixmap)

    def _push_printer_state(self):
        """Consolidate entities into a virtual state for the printer overlay."""
        if not self._active_printer_config:
            return None

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
            attrs['hotend_target'] = noz_data.get('attributes', {}).get(
                'target_temperature', noz_data.get('attributes', {}).get('temperature', 0.0)
            )

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
            attrs['bed_target'] = bed_data.get('attributes', {}).get(
                'target_temperature', bed_data.get('attributes', {}).get('temperature', 0.0)
            )

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

        virtual_state = {'state': primary_state, 'attributes': attrs}
        self.printer_overlay.update_state(virtual_state)
        return virtual_state

    def set_border_effect(self, effect: str):
        self._border_effect = effect
        self.dimmer_overlay.set_border_effect(effect)
        self.climate_overlay.set_border_effect(effect)
        self.printer_overlay.set_border_effect(effect)
        self.weather_overlay.set_border_effect(effect)
        self.mower_overlay.set_border_effect(effect)
        self.vacuum_overlay.set_border_effect(effect)
        self.alarm_overlay.set_border_effect(effect)

    def set_temperature_unit_preference(self, preference: str):
        self._temperature_unit_preference = preference
        self.weather_overlay.set_temperature_unit_preference(preference)
        self.printer_overlay.set_temperature_unit_preference(preference)

    # ==========================
    # Dimmer / Volume Logic
    # ==========================

    # ── Shared helpers for the start_* methods ──

    def _button_at(self, slot: int):
        return next((b for b in self.buttons if b.slot == slot), None)

    def _base_color(self) -> QColor:
        colors = self.theme_manager.get_colors() if self.theme_manager else {}
        return QColor(colors.get('base', '#2d2d2d'))

    def _overlay_colors(self, config: dict, default_accent: str) -> tuple[QColor, QColor]:
        """(base, accent): the theme's base colour, and the tile's colour, else the theme accent."""
        colors = self.theme_manager.get_colors() if self.theme_manager else {}
        button_color = config.get('color')
        accent = QColor(button_color) if button_color else QColor(colors.get('accent', default_accent))
        return QColor(colors.get('base', '#2d2d2d')), accent

    def _local_rect(self, global_rect: QRect) -> QRect:
        """A global rect in the dashboard's coordinates."""
        return QRect(self.parent_widget.mapFromGlobal(global_rect.topLeft()), global_rect.size())

    def _grow_to_min_height(self, target_rect: QRect, source_btn, min_height: int) -> bool:
        """Make target_rect at least min_height tall; True if it had to grow.

        If the taller rect would run into the footer (about 40px), it grows
        upwards instead, its bottom aligned with the source button's.
        """
        if target_rect.height() >= min_height:
            return False
        target_rect.setHeight(min_height)
        safe_bottom = self.parent_widget.height() - 40
        if target_rect.bottom() > safe_bottom:
            if source_btn:
                src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                target_rect.moveBottom(src_pos.y() + source_btn.height())
            else:
                target_rect.moveTop(target_rect.top() - (target_rect.bottom() - safe_bottom))
        return True

    def start_dimmer(self, slot: int, global_rect: QRect, config: dict):
        """Start the dimmer morph sequence."""
        if self._queue_or_start_overlay(self.start_dimmer, slot, global_rect, config):
            return

        if not config:
            return

        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_dimmer_entity = entity_id
        self._active_dimmer_type = config.get('type', 'switch')

        source_btn = self._button_at(slot)
        current_val = self._dimmer_start_value(self._active_dimmer_type, entity_id, source_btn)

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#FFD700")

        # Geometries
        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='dimmer')

        # Start
        self.dimmer_overlay.set_border_effect(self._border_effect)

        label = config.get('label', 'Dimmer')

        self.dimmer_overlay.start_morph(
            start_rect, target_rect, current_val, label, color=accent_color, base_color=base_color
        )
        self.dimmer_timer.start()

    def _dimmer_start_value(self, dimmer_type: str, entity_id: str, source_btn) -> int:
        """0-100: a cover's position or a light's brightness, else the tile's on/off state."""
        attrs = self._entity_states.get(entity_id, {}).get('attributes', {})
        if dimmer_type == 'curtain':
            pos = attrs.get('current_position')
            if pos is not None:
                return int(pos)
            on_state, has_state = "open", bool(source_btn)
        else:
            bri = attrs.get('brightness')
            if bri is not None:
                return int((bri / 255.0) * 100)
            on_state, has_state = "on", bool(source_btn) and hasattr(source_btn, '_state')
        return 100 if has_state and source_btn._state == on_state else 0

    def start_volume(self, slot: int, global_rect: QRect, config: dict):
        """Start volume slider using dimmer overlay."""
        if self._queue_or_start_overlay(self.start_volume, slot, global_rect, config):
            return

        if not config:
            return
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        source_btn = self._button_at(slot)
        if not source_btn:
            return

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
            start_rect,
            target_rect,
            start_pct,
            "Volume",
            color=color,
            base_color=QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
            if self.theme_manager
            else QColor("#2d2d2d"),
        )
        if not self.dimmer_timer.isActive():
            self.dimmer_timer.start()

    def _add_covered_siblings(self, overlay, source_btn, target_rect):
        """Append any buttons covered by target_rect to the overlay's sibling list and fade them.

        Coverage is decided purely by actual rect intersection (_fade_opacity_for_coverage),
        so this catches buttons in *any* direction — the overlay's target_rect can be shifted
        up or left to fit the grid, covering buttons above/beside the source button's row.
        """
        state = self._overlay_state[overlay]
        for btn in self.buttons:
            if not btn.isVisible() or btn == source_btn:
                continue
            if btn in state['siblings']:
                continue
            btn_pos = btn.mapTo(self.parent_widget, QPoint(0, 0))
            btn_rect = QRect(btn_pos, btn.size())
            opacity = self._fade_opacity_for_coverage(target_rect, btn_rect)
            if opacity < 1.0:
                state['siblings'].append(btn)
                btn.set_opacity(opacity)

    def _restore_overlay_state(self, overlay):
        """Restore opacity for all buttons faded by an overlay, then clear its state."""
        state = self._overlay_state[overlay]
        for btn in state['siblings']:
            btn.set_opacity(1.0)
        if state['source']:
            state['source'].set_opacity(1.0)
        state['siblings'] = []
        state['source'] = None

    @staticmethod
    def _fade_opacity_for_coverage(overlay_rect: QRect, btn_rect: QRect) -> float:
        """Graded opacity: 0.0 if >=50% covered, 0.4 if >=15%, 1.0 otherwise."""
        inter = overlay_rect.intersected(btn_rect)
        if inter.isEmpty():
            return 1.0
        btn_area = btn_rect.width() * btn_rect.height()
        if btn_area == 0:
            return 1.0
        coverage = (inter.width() * inter.height()) / btn_area
        if coverage >= 0.5:
            return 0.0
        if coverage >= 0.15:
            return 0.4
        return 1.0

    def _calculate_target_rect_and_siblings(self, source_btn, slot, overlay_type='dimmer'):
        """Rect an overlay opens into: the source button's row (at most six columns
        wide), one button tall. Buttons it covers are faded and remembered."""
        if not source_btn:
            return QRect()
        src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
        row_buttons = self._row_buttons(source_btn)

        state = self._overlay_state[self._overlay_for(overlay_type)]
        state['source'] = source_btn
        state['siblings'] = []
        source_btn.set_opacity(0.0)
        if not row_buttons:
            return QRect(src_pos, source_btn.size())

        target_x, target_width = self._row_extent(row_buttons, source_btn, src_pos)
        # Single-row height — overlays that need more vertical space expand via their own loops
        final_rect = QRect(int(target_x), src_pos.y(), int(target_width), BUTTON_HEIGHT)
        state['siblings'] = self._fade_row_siblings(row_buttons, source_btn, final_rect)
        return final_rect

    def _overlay_for(self, overlay_type: str):
        overlays = {
            'dimmer': self.dimmer_overlay,
            'climate': self.climate_overlay,
            'printer': self.printer_overlay,
            'weather': self.weather_overlay,
            'camera': self.camera_overlay,
            'mower': self.mower_overlay,
            'vacuum': self.vacuum_overlay,
            'alarm': self.alarm_overlay,
        }
        return overlays.get(overlay_type, self.dimmer_overlay)

    def _row_buttons(self, source_btn) -> list:
        """Visible buttons sharing a grid row with source_btn, with their positions, left to right.

        Rows come from slots, not pixels, so taller (span_y > 1) buttons don't
        produce false overlaps.
        """
        cols = getattr(self.parent_widget, '_cols', 4)
        src_row = source_btn.slot // cols
        row_buttons = []
        for btn in self.buttons:
            btn_row = btn.slot // cols
            if btn.isVisible() and btn_row <= src_row < btn_row + getattr(btn, 'span_y', 1):
                row_buttons.append((btn, btn.mapTo(self.parent_widget, QPoint(0, 0))))
        row_buttons.sort(key=lambda x: x[1].x())
        return row_buttons

    def _row_extent(self, row_buttons: list, source_btn, src_pos: QPoint) -> tuple:
        """(x, width) across the row, capped at six columns kept on the source's side, and on screen."""
        _, first_pos = row_buttons[0]
        last_btn, last_pos = row_buttons[-1]
        target_x = first_pos.x()
        target_width = (last_pos.x() + last_btn.width()) - first_pos.x()

        max_6_col_width = (6 * BUTTON_WIDTH) + (5 * BUTTON_SPACING)
        if target_width > max_6_col_width:
            src_center = src_pos.x() + (source_btn.width() / 2)
            row_center = first_pos.x() + (target_width / 2)
            if src_center > row_center:
                target_x = target_x + target_width - max_6_col_width  # keep the right edge
            target_width = max_6_col_width

        target_width = min(target_width, self.parent_widget.width() - target_x)
        return max(target_x, 0), target_width

    def _fade_row_siblings(self, row_buttons: list, source_btn, final_rect: QRect) -> list:
        """Fade the row's buttons by how much final_rect covers them; return the covered ones."""
        covered = []
        for btn, btn_pos in row_buttons:
            if btn == source_btn:
                continue
            opacity = self._fade_opacity_for_coverage(final_rect, QRect(btn_pos, btn.size()))
            btn.set_opacity(opacity if opacity < 1.0 else 1.0)
            if opacity < 1.0:
                covered.append(btn)
        return covered

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
        self._emit_service(
            {
                "service": "light.turn_on",
                "entity_id": self._active_dimmer_entity,
                "service_data": {"brightness_pct": val},
                "skip_debounce": True,
            }
        )

    def on_dimmer_finished(self):
        self.dimmer_timer.stop()

        final_val = getattr(self, '_final_dimmer_val', None)
        dimmer_type = getattr(self, '_active_dimmer_type', 'switch')
        entity_id = self._active_dimmer_entity

        if final_val is not None and entity_id:
            if dimmer_type == 'curtain':
                self._emit_service(
                    {
                        "service": "cover.set_cover_position",
                        "entity_id": entity_id,
                        "service_data": {"position": final_val},
                    }
                )
            elif dimmer_type == 'media_player':
                self._emit_service(
                    {
                        "service": "media_player.volume_set",
                        "entity_id": entity_id,
                        "service_data": {"volume_level": final_val / 100.0},
                        "skip_debounce": True,
                    }
                )
            # Lights are usually handled by live update, but send one final to be sure?
            # Or if live dimming was off.
            elif dimmer_type != 'curtain' and (not self._live_dimming or final_val is not None):
                self._emit_service(
                    {
                        "service": "light.turn_on",
                        "entity_id": entity_id,
                        "service_data": {"brightness_pct": final_val},
                        "skip_debounce": True,
                    }
                )

        self._reset_dimmer_state()
        self.parent_widget.activateWindow()

    def _reset_dimmer_state(self):
        self._active_dimmer_entity = None
        self._active_dimmer_type = None
        self._pending_dimmer_val = None
        self._final_dimmer_val = None

        self._restore_overlay_state(self.dimmer_overlay)

        self._check_pending_actions()

    # ==========================
    # Climate Logic
    # ==========================

    def start_climate(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_climate, slot, global_rect, config):
            return

        if not config:
            return
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_climate_entity = entity_id

        source_btn = self._button_at(slot)

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#EA4335")

        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='climate')

        if self._grow_to_min_height(target_rect, source_btn, TWO_ROW_MIN_HEIGHT):
            self._add_covered_siblings(self.climate_overlay, source_btn, target_rect)

        current_state = self._entity_states.get(entity_id, {})
        attrs = current_state.get('attributes', {})
        source_unit = attrs.get('temperature_unit')
        display_unit = preference_to_unit(self._temperature_unit_preference, fallback=source_unit)
        tile_temp = _tile_temperature(source_btn)
        converted_temp = convert_temperature(attrs.get('temperature', tile_temp), source_unit, display_unit)
        curr_temp = converted_temp if converted_temp is not None else tile_temp
        self._configure_climate_range(attrs, source_unit, display_unit)

        self.climate_overlay.set_border_effect(self._border_effect)
        self.climate_overlay.start_morph(
            start_rect,
            target_rect,
            curr_temp,
            config.get('label', 'Climate'),
            color=accent_color,
            base_color=base_color,
            current_state=current_state,
        )
        self.climate_timer.start()

    def _configure_climate_range(self, attrs: dict, source_unit, display_unit):
        """Min, max and step of the target temperature, in the unit shown."""

        def or_default(value, default):
            return value if value is not None else default

        self.climate_overlay.configure_temperature_range(
            or_default(convert_temperature(attrs.get('min_temp', 5.0), source_unit, display_unit), 5.0),
            or_default(convert_temperature(attrs.get('max_temp', 35.0), source_unit, display_unit), 35.0),
            or_default(convert_temperature_delta(attrs.get('target_temp_step', 0.5), source_unit, display_unit), 0.5),
            normalize_temperature_unit(display_unit),
        )

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

        self._emit_service(
            {
                "service": "climate.set_temperature",
                "entity_id": self._active_climate_entity,
                "service_data": {"temperature": service_temp},
            }
        )

    def on_climate_mode_changed(self, mode):
        if self._active_climate_entity:
            self._emit_service(
                {
                    "service": "climate.set_hvac_mode",
                    "entity_id": self._active_climate_entity,
                    "service_data": {"hvac_mode": mode},
                }
            )

    def on_climate_fan_changed(self, mode):
        if self._active_climate_entity:
            self._emit_service(
                {
                    "service": "climate.set_fan_mode",
                    "entity_id": self._active_climate_entity,
                    "service_data": {"fan_mode": mode},
                }
            )

    def on_climate_finished(self):
        self.climate_timer.stop()
        self._pending_climate_val = None
        self._active_climate_entity = None

        self._restore_overlay_state(self.climate_overlay)

        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Shared
    # ==========================

    def on_morph_changed(self, progress: float):
        """Update sibling opacity for the overlay that fired this signal."""
        overlay = self.sender()
        state = self._overlay_state.get(overlay)
        if state is None:
            self.morph_changed.emit(progress)
            return

        opacity = 1.0 - (progress * 0.8)
        for btn in state['siblings']:
            btn.set_opacity(opacity)

        if state['source'] and overlay._is_closing:
            state['source'].set_opacity(1.0 - progress)

        self.morph_changed.emit(progress)

    # ==========================
    # 3D Printer Logic
    # ==========================

    def start_printer(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_printer, slot, global_rect, config):
            return

        if not config:
            return
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_printer_entity = entity_id
        self._active_printer_config = config

        source_btn = self._button_at(slot)
        self._overlay_state[self.printer_overlay]['source'] = source_btn

        # Push initial camera if available on button
        if source_btn and hasattr(source_btn, '_last_camera_pixmap') and source_btn._last_camera_pixmap:
            self.printer_overlay.set_camera_pixmap(source_btn._last_camera_pixmap)

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#FF6D00")

        start_rect = self._local_rect(global_rect)

        # Use existing shared logic but with 'printer' overlay_type
        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='printer')

        if self._grow_to_min_height(target_rect, source_btn, TWO_ROW_MIN_HEIGHT):
            self._add_covered_siblings(self.printer_overlay, source_btn, target_rect)

        # Consolidate entities into a virtual state for the printer overlay
        virtual_state = self._push_printer_state()

        self.printer_overlay.set_border_effect(self._border_effect)
        self.printer_overlay.start_morph(
            start_rect,
            target_rect,
            config.get('label', '3D Printer'),
            color=accent_color,
            base_color=base_color,
            current_state=virtual_state,
        )

    def on_printer_action(self, action: str):
        if not self._active_printer_config:
            return

        if action in ('pause', 'resume'):
            entity = self._active_printer_config.get('printer_pause_entity')
            if entity:
                self._emit_service({"service": "button.press", "entity_id": entity})
        elif action == 'stop':
            entity = self._active_printer_config.get('printer_stop_entity')
            if entity:
                self._emit_service({"service": "button.press", "entity_id": entity})

    def on_printer_finished(self):
        self._active_printer_entity = None
        self._active_printer_config = None
        self._restore_overlay_state(self.printer_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Lawn Mower Logic
    # ==========================

    def start_mower(self, slot: int, global_rect: QRect):
        if self._queue_or_start_overlay(self.start_mower, slot, global_rect):
            return

        source_btn = self._button_at(slot)
        if not source_btn or not source_btn.config:
            return
        config = source_btn.config
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_mower_entity = entity_id

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#4CAF50")

        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='mower')

        if self._grow_to_min_height(target_rect, source_btn, TWO_ROW_MIN_HEIGHT):
            self._add_covered_siblings(self.mower_overlay, source_btn, target_rect)

        current_state = self._entity_states.get(entity_id, {})

        self.mower_overlay.set_border_effect(self._border_effect)
        self.mower_overlay.start_morph(
            start_rect,
            target_rect,
            config.get('label', 'Mower'),
            color=accent_color,
            base_color=base_color,
            current_state=current_state,
        )

    def on_mower_action(self, action: str):
        if not self._active_mower_entity:
            return
        self._emit_service({"service": f"lawn_mower.{action}", "entity_id": self._active_mower_entity})

    def on_mower_finished(self):
        self._active_mower_entity = None
        self._restore_overlay_state(self.mower_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Vacuum Logic
    # ==========================

    def start_vacuum(self, slot: int, global_rect: QRect):
        if self._queue_or_start_overlay(self.start_vacuum, slot, global_rect):
            return

        source_btn = self._button_at(slot)
        if not source_btn or not source_btn.config:
            return
        config = source_btn.config
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_vacuum_entity = entity_id

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#4CAF50")

        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='vacuum')

        if self._grow_to_min_height(target_rect, source_btn, TWO_ROW_MIN_HEIGHT):
            self._add_covered_siblings(self.vacuum_overlay, source_btn, target_rect)

        current_state = self._entity_states.get(entity_id, {})

        self.vacuum_overlay.set_border_effect(self._border_effect)
        self.vacuum_overlay.start_morph(
            start_rect,
            target_rect,
            config.get('label', 'Vacuum'),
            color=accent_color,
            base_color=base_color,
            current_state=current_state,
        )

    def on_vacuum_action(self, action: str):
        if not self._active_vacuum_entity:
            return
        self._emit_service({"service": f"vacuum.{action}", "entity_id": self._active_vacuum_entity})

    def on_vacuum_finished(self):
        self._active_vacuum_entity = None
        self._restore_overlay_state(self.vacuum_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Alarm Control Panel Logic
    # ==========================

    # alarm_control_panel supported_features bit -> arm action
    ALARM_FEATURE_BITS = {
        1: 'arm_home',
        2: 'arm_away',
        4: 'arm_night',
        16: 'arm_custom_bypass',
        32: 'arm_vacation',
    }

    def start_alarm(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_alarm, slot, global_rect, config):
            return

        if not config:
            return
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_alarm_entity = entity_id
        source_btn = self._button_at(slot)

        # Colors
        base_color, accent_color = self._overlay_colors(config, "#4285F4")

        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='alarm')

        current_state = self._entity_states.get(entity_id, {})
        attrs = current_state.get('attributes', {})

        supported = int(attrs.get('supported_features', 0) or 0)
        modes = [action for bit, action in self.ALARM_FEATURE_BITS.items() if supported & bit]
        self.alarm_overlay.configure_modes(modes)

        code_required = attrs.get('code_format') is not None
        code_arm_required = attrs.get('code_arm_required', True)
        self.alarm_overlay.configure_code(
            code_for_arm=code_required and code_arm_required,
            code_for_disarm=code_required,
        )

        # Open compact like every other overlay — grows on demand if a keypad is needed
        # (see on_alarm_keypad_toggled), so the common "just tap Home/Away" case stays small.
        target_rect = self._size_alarm_rect(source_btn, target_rect, rows=2)

        self.alarm_overlay.set_border_effect(self._border_effect)
        self.alarm_overlay.start_morph(
            start_rect,
            target_rect,
            config.get('label', 'Alarm'),
            color=accent_color,
            base_color=base_color,
            current_state=current_state,
        )

    # Alarm never needs a full grid-row width — a handful of pill buttons or a 3-column
    # keypad both fit comfortably in 3 columns, so cap it instead of stretching edge to edge.
    ALARM_MAX_WIDTH = (3 * BUTTON_WIDTH) + (2 * BUTTON_SPACING)

    def _size_alarm_rect(self, source_btn, target_rect: QRect, rows: int) -> QRect:
        """Cap target_rect to a sane width and grow it to fit `rows` of button height,
        shifting on-screen and (re)computing sibling fade against the final rect."""
        state = self._overlay_state[self.alarm_overlay]
        for btn in state['siblings']:
            btn.set_opacity(1.0)
        state['siblings'] = []
        if source_btn:
            source_btn.set_opacity(0.0)

        if target_rect.width() > self.ALARM_MAX_WIDTH:
            row_center = target_rect.x() + target_rect.width() / 2
            if source_btn:
                src_pos = source_btn.mapTo(self.parent_widget, QPoint(0, 0))
                src_center = src_pos.x() + source_btn.width() / 2
            else:
                src_center = row_center
            new_x = target_rect.right() - self.ALARM_MAX_WIDTH + 1 if src_center > row_center else target_rect.x()
            target_rect = QRect(int(new_x), target_rect.y(), int(self.ALARM_MAX_WIDTH), target_rect.height())

        self._grow_to_min_height(target_rect, source_btn, (BUTTON_HEIGHT * rows) + (BUTTON_SPACING * (rows - 1)))
        self._add_covered_siblings(self.alarm_overlay, source_btn, target_rect)
        return target_rect

    def on_alarm_keypad_toggled(self, show_keypad: bool):
        """Alarm panel needs to grow (keypad appearing) or shrink back (keypad dismissed)."""
        if not self.alarm_overlay.isVisible():
            return
        state = self._overlay_state[self.alarm_overlay]
        source_btn = state['source']
        slot = source_btn.slot if source_btn else None

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='alarm')
        target_rect = self._size_alarm_rect(source_btn, target_rect, rows=3 if show_keypad else 2)

        self.alarm_overlay.resize_to(target_rect)

    def on_alarm_action(self, action: str, code: str):
        if not self._active_alarm_entity:
            return
        service_data = {'code': code} if code else {}
        self._emit_service(
            {
                "service": f"alarm_control_panel.alarm_{action}",
                "entity_id": self._active_alarm_entity,
                "service_data": service_data,
            }
        )

    def on_alarm_finished(self):
        self._active_alarm_entity = None
        self._restore_overlay_state(self.alarm_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Weather Logic
    # ==========================

    def start_weather(self, slot: int, global_rect: QRect, config: dict, forecasts: list):
        if self._queue_or_start_overlay(self.start_weather, slot, global_rect, config, forecasts):
            return

        if not config:
            return
        entity_id = config.get('entity_id')
        if not entity_id:
            return

        self._active_weather_entity = entity_id
        source_btn = self._button_at(slot)

        base_color, accent_color = self._overlay_colors(config, "#4285F4")

        start_rect = self._local_rect(global_rect)

        target_rect = self._calculate_target_rect_and_siblings(source_btn, slot, overlay_type='weather')

        if self._grow_to_min_height(target_rect, source_btn, TWO_ROW_MIN_HEIGHT):
            self._add_covered_siblings(self.weather_overlay, source_btn, target_rect)

        current_state = self._entity_states.get(entity_id, {})

        self.weather_overlay.set_border_effect(self._border_effect)
        self.weather_overlay.start_morph(
            start_rect,
            target_rect,
            current_state,
            forecasts,
            config.get('label', 'Weather'),
            color=accent_color,
            base_color=base_color,
        )

    def on_weather_finished(self):
        self._active_weather_entity = None
        self._restore_overlay_state(self.weather_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()

    # ==========================
    # Camera Logic
    # ==========================

    def start_camera(self, slot: int, global_rect: QRect, config: dict):
        if self._queue_or_start_overlay(self.start_camera, slot, global_rect, config):
            return
        entity_id = config.get('entity_id') if config else None
        if not entity_id:
            return

        self._active_camera_entity = entity_id
        source_btn = self._button_at(slot)
        self._overlay_state[self.camera_overlay]['source'] = source_btn
        if source_btn and getattr(source_btn, '_last_camera_pixmap', None):
            self.camera_overlay.set_camera_pixmap(source_btn._last_camera_pixmap)

        start_rect = self._local_rect(global_rect)
        target_rect = self._camera_target_rect(start_rect)
        self._add_covered_siblings(self.camera_overlay, source_btn, target_rect)

        self.camera_overlay.set_border_effect(self._border_effect)
        self.camera_overlay.start_morph(
            start_rect,
            target_rect,
            config.get('label', 'Camera'),
            base_color=self._base_color(),
            pip_available=entity_id.startswith('camera.'),
        )

    def _grid_bounds(self, fallback: QRect) -> QRect:
        """Bounding rect of the visible buttons, or fallback when none are laid out."""
        bounds = QRect()
        for btn in self.buttons:
            if btn.isVisible():
                bounds = bounds.united(QRect(btn.mapTo(self.parent_widget, QPoint(0, 0)), btn.size()))
        return bounds if bounds.isValid() else fallback

    def _camera_target_rect(self, start_rect: QRect) -> QRect:
        """Up to 4x4 buttons from the source's top-left, shifted back inside the grid."""
        grid = self._grid_bounds(start_rect)
        ideal_w = (BUTTON_WIDTH * 4) + (BUTTON_SPACING * 3)
        ideal_h = (BUTTON_HEIGHT * 4) + (BUTTON_SPACING * 3)
        target_w = min(ideal_w, grid.width())
        target_h = min(ideal_h, grid.height())
        # Start at the source's top-left; shift left/up where it would hang over the grid.
        target_x = max(min(start_rect.left(), grid.right() - target_w + 1), grid.left())
        target_y = max(min(start_rect.top(), grid.bottom() - target_h + 1), grid.top())
        return QRect(target_x, target_y, target_w, target_h)

    def on_camera_pip_requested(self):
        """Pop the open camera out into picture-in-picture and close the overlay."""
        entity_id = self._active_camera_entity
        if self.camera_overlay._is_closing or not entity_id:
            return
        self.camera_pip_requested.emit(entity_id, self.camera_overlay._text)
        self.camera_overlay.close_morph()

    def on_camera_finished(self):
        self._active_camera_entity = None
        self._restore_overlay_state(self.camera_overlay)
        self.parent_widget.activateWindow()
        self._check_pending_actions()


def _tile_temperature(source_btn) -> float:
    """The temperature a climate tile shows (e.g. "21.5°"), or 20 when it shows none."""
    try:
        return float(str(source_btn._value).replace('°', '').strip())
    except (AttributeError, TypeError, ValueError):
        return 20.0
