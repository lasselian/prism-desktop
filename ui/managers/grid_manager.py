from ui.grid_layout_engine import GridLayoutEngine, is_real_button
from ui.constants import (
    BUTTON_HEIGHT,
    BUTTON_SPACING,
    GRID_MARGIN_TOP,
    GRID_MARGIN_BOTTOM,
    FOOTER_HEIGHT,
    FOOTER_MARGIN_BOTTOM,
)
from PyQt6.QtCore import QPropertyAnimation


class VirtualButton:
    """Helper for layout engine to track out-of-bounds buttons without consuming a widget."""

    def __init__(self, config):
        self.config = config
        self.span_x = config.get('span_x', 1)
        self.span_y = config.get('span_y', 1)


class GridManager:
    """Manages the lifecycle, pooling, and layout of DashboardButtons."""

    def __init__(self, dashboard):
        self.dashboard = dashboard
        self.layout_engine = GridLayoutEngine(cols=dashboard._cols)

    def update_cols(self, cols):
        self.layout_engine.cols = cols

    def rebuild_grid(self, preview_mode=False, update_height=True):
        """Rebuild the grid using (row, col) based layout."""
        dash = self.dashboard
        if not preview_mode:
            for btn in dash.buttons:
                btn.hide()
        while dash.grid.count():
            dash.grid.takeAt(0)

        all_buttons = list(dash.buttons) + list(getattr(dash, '_virtual_buttons', None) or [])
        placements = self.layout_engine.calculate_layout(all_buttons, dash._rows)
        for btn, r, c, span_y, span_x in placements:
            self._place(btn, r, c, span_y, span_x, preview_mode)

        self._finish_placements(placements)
        if update_height:
            self._update_height(preview_mode)

    def _finish_placements(self, placements: list):
        """Mark blocked empty slots forbidden, keep real tiles on top, hide unplaced buttons."""
        forbidden_cells = self.layout_engine.get_forbidden_cells()
        for btn, r, c, _, _ in placements:
            if (r, c) in forbidden_cells and not _is_real(btn):
                _reset_button(btn, {'type': 'forbidden'})
        for btn, *_ in placements:
            if _is_real(btn):
                btn.raise_()
        placed_buttons = {p[0] for p in placements}
        for btn in self.dashboard.buttons:
            if btn not in placed_buttons:
                btn.setVisible(False)

    def _place(self, btn, r, c, span_y, span_x, preview_mode):
        dash = self.dashboard
        if not _is_real(btn):
            _reset_button(btn, {})
        dash.grid.addWidget(btn, r, c, span_y, span_x)
        btn.setVisible(True)
        if not getattr(btn, '_is_resizing', False):
            btn.resize_handle_opacity = 0.0
            if hasattr(btn, 'resize_anim'):
                btn.resize_anim.stop()
        btn.slot = r * dash._cols + c
        if not preview_mode and btn.config:
            btn.config['row'] = r
            btn.config['col'] = c

    def _update_height(self, preview_mode):
        """Grow or shrink the dashboard to fit the grid: at once in preview, else animated."""
        dash = self.dashboard
        grid_h = (dash._rows * BUTTON_HEIGHT) + ((dash._rows - 1) * BUTTON_SPACING)
        extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + 20
        new_height = grid_h + extras
        start_h = dash.height()
        if start_h == new_height or dash._current_view != 'grid':
            return

        if preview_mode:
            dash.setFixedSize(dash.width(), new_height)
            if hasattr(dash, '_resize_anchor_y') and not dash._is_top_anchored():
                dash.move(dash.x(), dash._resize_anchor_y - new_height)
            return

        anim = dash.height_anim
        if anim.state() == QPropertyAnimation.State.Running and abs(anim.endValue() - new_height) < 1.0:
            return  # already heading there
        dash._resize_anchor_y = dash.y() + dash.height()
        anim.stop()
        anim.setStartValue(float(start_h))
        anim.setEndValue(float(new_height))
        anim.start()

    def set_buttons(self, configs: list[dict], appearance_config: dict | None = None, update_height=True):
        """Set button configurations using (row, col) based positioning."""
        dash = self.dashboard
        dash._all_button_configs = configs
        page = getattr(dash, '_current_page', 0)
        configs = [c for c in configs if c.get('page', 0) == page]
        dash._button_configs = configs
        if appearance_config:
            self._apply_appearance(appearance_config)

        target_slots = dash._rows * dash._cols
        while len(dash.buttons) < target_slots:
            dash.buttons.append(dash._get_button_from_pool(len(dash.buttons)))

        dash._virtual_buttons = []
        for button in dash.buttons:
            # Stash the pre-clear entity_id: it's compared against the incoming
            # config below to tell "reassigned to a different entity" (which
            # should reset the button) from "same entity, just rebuilt" (which
            # shouldn't) -- reading button.config for that after clearing it
            # here would always see {}, making that comparison always true.
            button._prev_entity_id = button.config.get('entity_id')
            button.config = {}
            button.set_spans(1, 1)

        free_buttons = iter(dash.buttons)
        used = 0
        for cfg in configs:
            if not self._fits_grid(cfg):
                continue
            button = next(free_buttons, None)
            if button is None:
                continue
            used += 1
            self._bind_button(button, cfg)

        for button in dash.buttons[used:]:
            self._apply_dashboard_look(button)
        self.rebuild_grid(update_height=update_height)

    def _apply_appearance(self, appearance: dict):
        dash = self.dashboard
        dash._live_dimming = True
        dash._border_effect = appearance.get('border_effect', 'Rainbow')
        dash._show_dimming = appearance.get('show_dimming', False)
        dash._refresh_indicators_enabled = appearance.get('refresh_indicators', False)
        dash._glass_ui = appearance.get('glass_ui', False)
        dash._button_style = appearance.get('button_style', 'Gradient')
        dash._temperature_unit = appearance.get('temperature_unit', 'celsius')
        dash.overlay_manager.set_temperature_unit_preference(dash._temperature_unit)

    def _fits_grid(self, cfg: dict) -> bool:
        """True if the button gets a widget. One that starts inside the grid but
        runs off its edge becomes a VirtualButton, which keeps its cells blocked."""
        dash = self.dashboard
        if not is_real_button(cfg):
            return False
        r, c = cfg.get('row', 0), cfg.get('col', 0)
        if c >= dash._cols or r >= dash._rows:
            return False
        if c + cfg.get('span_x', 1) > dash._cols or r + cfg.get('span_y', 1) > dash._rows:
            dash._virtual_buttons.append(VirtualButton(cfg))
            return False
        return True

    def _bind_button(self, button, cfg: dict):
        dash = self.dashboard
        old_entity = getattr(button, '_prev_entity_id', None)
        new_entity = cfg.get('entity_id')
        button.config = cfg
        if cfg.get('type') == 'pin_window':
            button._app_config = dash.config

        known_state = dash._entity_states.get(new_entity) if new_entity else None
        if old_entity != new_entity:
            button.reset_state()
            if known_state is not None:
                button.apply_ha_state(known_state)
        elif cfg.get('type') == 'widget' and known_state is not None:
            # Same entity, but precision may have changed: temperature readings
            # are formatted when the state arrives, so format them again.
            button.apply_ha_state(known_state)

        button.set_spans(cfg.get('span_x', 1), cfg.get('span_y', 1))
        button._page_count = getattr(dash, '_page_count', 1)
        button._current_page = getattr(dash, '_current_page', 0)
        self._apply_dashboard_look(button)
        button.show_dimming = dash._show_dimming
        button.refresh_indicators_enabled = dash._refresh_indicators_enabled
        _reconnect(button.resize_requested, dash.handle_button_resize)
        _reconnect(button.resize_finished, dash.handle_button_resize_finished)
        _reconnect(button.duplicate_requested, dash.duplicate_button_requested)

    def _apply_dashboard_look(self, button):
        dash = self.dashboard
        button.update_content()
        button.button_style = getattr(dash, '_button_style', 'Gradient')
        button.set_temperature_unit_preference(getattr(dash, '_temperature_unit', 'celsius'))
        button.update_style()
        button.set_border_effect(dash._border_effect)


def _is_real(btn) -> bool:
    return bool(btn.config) and is_real_button(btn.config)


def _reset_button(btn, config: dict):
    """Turn a slot into an empty or forbidden placeholder.

    The content fingerprint is cleared so the view updater always runs its
    label show() calls, even if a prior path (set_camera_image, the forbidden
    view, ...) hid the labels.
    """
    btn.config = config
    btn._content_fp = None
    btn.update_content()
    btn.update_style()


def _reconnect(signal, slot):
    """Connect slot exactly once, whether or not it was connected before."""
    try:
        signal.disconnect(slot)
    except TypeError:
        pass
    signal.connect(slot)
