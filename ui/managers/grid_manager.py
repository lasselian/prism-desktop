from ui.grid_layout_engine import GridLayoutEngine
from ui.constants import BUTTON_HEIGHT, BUTTON_SPACING, GRID_MARGIN_TOP, GRID_MARGIN_BOTTOM, FOOTER_HEIGHT, FOOTER_MARGIN_BOTTOM
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
        # 1. Clear Grid
        if not preview_mode:
            for btn in self.dashboard.buttons:
                btn.hide()
        
        while self.dashboard.grid.count():
            self.dashboard.grid.takeAt(0)
            
        # 2. Calculate Layout
        all_buttons = list(self.dashboard.buttons)
        if hasattr(self.dashboard, '_virtual_buttons') and self.dashboard._virtual_buttons:
            all_buttons.extend(self.dashboard._virtual_buttons)
            
        placements = self.layout_engine.calculate_layout(all_buttons, self.dashboard._rows)
        
        max_row = 0
        
        # 3. Apply Placements
        for btn, r, c, span_y, span_x in placements:
            is_local = btn.config and btn.config.get('type') == 'pin_window'
            if not (btn.config and (btn.config.get('entity_id') or is_local)):
                 btn.config = {}
                 # Invalidate content fingerprint so the empty view updater
                 # always runs its label show() calls, even if a prior path
                 # (set_camera_image, forbidden view, etc.) hid the labels.
                 btn._content_fp = None
                 btn.update_content()
                 btn.update_style()

            self.dashboard.grid.addWidget(btn, r, c, span_y, span_x)
            btn.setVisible(True)
            
            if not getattr(btn, '_is_resizing', False):
                btn.resize_handle_opacity = 0.0
                if hasattr(btn, 'resize_anim'):
                    btn.resize_anim.stop()
            
            new_slot = r * self.dashboard._cols + c
            btn.slot = new_slot
            
            if not preview_mode and btn.config:
                btn.config['row'] = r
                btn.config['col'] = c
                
            max_row = max(max_row, r + span_y)
            
        forbidden_cells = self.layout_engine.get_forbidden_cells()
        if forbidden_cells:
            for btn, r, c, span_y, span_x in placements:
                is_local = btn.config and btn.config.get('type') == 'pin_window'
                if (r, c) in forbidden_cells and not (btn.config and (btn.config.get('entity_id') or is_local)):
                    btn.config = {'type': 'forbidden'}
                    btn._content_fp = None
                    btn.update_content()
                    btn.update_style()

        for btn, r, c, span_y, span_x in placements:
            if btn.config and (btn.config.get('entity_id') or btn.config.get('type') == 'pin_window'):
                btn.raise_()
            
        placed_buttons = set(p[0] for p in placements)
        for btn in self.dashboard.buttons:
            if btn not in placed_buttons:
                btn.setVisible(False)
        
        # 5. Update Height
        if update_height:
            grid_h = (self.dashboard._rows * BUTTON_HEIGHT) + ((self.dashboard._rows - 1) * BUTTON_SPACING)
            extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + 20
            new_height = grid_h + extras
            
            start_h = self.dashboard.height()
            if start_h != new_height and self.dashboard._current_view == 'grid':
                if preview_mode:
                    self.dashboard.setFixedSize(self.dashboard.width(), new_height)
                    if (hasattr(self.dashboard, '_resize_anchor_y')
                            and not self.dashboard._is_top_anchored()):
                        new_y = self.dashboard._resize_anchor_y - new_height
                        self.dashboard.move(self.dashboard.x(), new_y)
                else:
                    if self.dashboard.height_anim.state() == QPropertyAnimation.State.Running and \
                       abs(self.dashboard.height_anim.endValue() - new_height) < 1.0:
                        return

                    self.dashboard._resize_anchor_y = self.dashboard.y() + self.dashboard.height()
                    self.dashboard.height_anim.stop()
                    self.dashboard.height_anim.setStartValue(float(start_h))
                    self.dashboard.height_anim.setEndValue(float(new_height))
                    self.dashboard.height_anim.start()

    def set_buttons(self, configs: list[dict], appearance_config: dict = None, update_height=True):
        """Set button configurations using (row, col) based positioning."""
        self.dashboard._all_button_configs = configs
        page = getattr(self.dashboard, '_current_page', 0)
        configs = [c for c in configs if c.get('page', 0) == page]

        self.dashboard._button_configs = configs
        if appearance_config:
            self.dashboard._live_dimming = True
            self.dashboard._border_effect = appearance_config.get('border_effect', 'Rainbow')
            self.dashboard._show_dimming = appearance_config.get('show_dimming', False)
            self.dashboard._glass_ui = appearance_config.get('glass_ui', False)
            self.dashboard._button_style = appearance_config.get('button_style', 'Gradient')
            self.dashboard._temperature_unit = appearance_config.get('temperature_unit', 'celsius')
            self.dashboard.overlay_manager.set_temperature_unit_preference(self.dashboard._temperature_unit)
        
        target_slots = self.dashboard._rows * self.dashboard._cols
        while len(self.dashboard.buttons) < target_slots:
            button = self.dashboard._get_button_from_pool(len(self.dashboard.buttons))
            self.dashboard.buttons.append(button)
            
        self.dashboard._virtual_buttons = []
        
        for button in self.dashboard.buttons:
            button.config = {}
            button.set_spans(1, 1)
        
        config_idx = 0
        for cfg in configs:
            if not cfg.get('entity_id') and cfg.get('type') != 'pin_window':
                continue
            
            r = cfg.get('row', 0)
            c = cfg.get('col', 0)
            sx = cfg.get('span_x', 1)
            sy = cfg.get('span_y', 1)
            
            if c >= self.dashboard._cols or r >= self.dashboard._rows:
                 continue

            if c + sx > self.dashboard._cols or r + sy > self.dashboard._rows:
                if not hasattr(self.dashboard, '_virtual_buttons'): self.dashboard._virtual_buttons = []
                self.dashboard._virtual_buttons.append(VirtualButton(cfg))
                continue
            
            if config_idx < len(self.dashboard.buttons):
                button = self.dashboard.buttons[config_idx]
                config_idx += 1
                
                old_entity = button.config.get('entity_id')
                new_entity = cfg.get('entity_id')
                
                button.config = cfg
                if cfg.get('type') == 'pin_window':
                    button._app_config = self.dashboard.config

                if old_entity != new_entity:
                    button.reset_state()
                    if new_entity and new_entity in self.dashboard._entity_states:
                        button.apply_ha_state(self.dashboard._entity_states[new_entity])
                
                button.set_spans(sx, sy)
                button.update_content()
                button.button_style = getattr(self.dashboard, '_button_style', 'Gradient')
                button.set_temperature_unit_preference(getattr(self.dashboard, '_temperature_unit', 'celsius'))
                button._page_count = getattr(self.dashboard, '_page_count', 1)
                button._current_page = getattr(self.dashboard, '_current_page', 0)
                button.update_style()
                button.set_border_effect(self.dashboard._border_effect)
                button.show_dimming = self.dashboard._show_dimming
                
                try:
                    button.resize_requested.disconnect(self.dashboard.handle_button_resize)
                except TypeError:
                    pass
                button.resize_requested.connect(self.dashboard.handle_button_resize)
                
                try:
                    button.resize_finished.disconnect(self.dashboard.handle_button_resize_finished)
                except TypeError:
                    pass
                button.resize_finished.connect(self.dashboard.handle_button_resize_finished)
                
                try:
                    button.duplicate_requested.disconnect(self.dashboard.duplicate_button_requested)
                except TypeError:
                    pass
                button.duplicate_requested.connect(self.dashboard.duplicate_button_requested)
        
        for i in range(config_idx, len(self.dashboard.buttons)):
            self.dashboard.buttons[i].update_content()
            self.dashboard.buttons[i].button_style = getattr(self.dashboard, '_button_style', 'Gradient')
            self.dashboard.buttons[i].set_temperature_unit_preference(getattr(self.dashboard, '_temperature_unit', 'celsius'))
            self.dashboard.buttons[i].update_style()
            self.dashboard.buttons[i].set_border_effect(self.dashboard._border_effect)
        
        self.rebuild_grid(update_height=update_height)
