"""
Dashboard Widget for Prism Desktop
The main popup menu with 4x2 grid of buttons/widgets.
"""

import asyncio
import time
import platform
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QPushButton, QLabel, 
    QVBoxLayout, QHBoxLayout, QFrame, QApplication, QGraphicsDropShadowEffect, QMenu,
    QGraphicsOpacityEffect, QScrollArea
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, pyqtSignal, QPropertyAnimation, QEasingCurve, 
    QMimeData, QByteArray, QDataStream, QIODevice, pyqtProperty, QRectF, QTimer, QRect,
    pyqtSlot, QUrl, QSize

)
from PyQt6.QtGui import (
    QColor, QFont, QDrag, QPixmap, QPainter, QCursor,
    QPen, QBrush, QLinearGradient, QConicalGradient, QDesktopServices,
    QIcon, QPainterPath
)
from ui.icons import get_icon, get_mdi_font

# Cross-platform system font
from core.utils import SYSTEM_FONT
from ui.widgets.dashboard_button import DashboardButton, MIME_TYPE
from ui.widgets.footer_button import FooterButton
from ui.constants import (
    WINDOW_WIDTH, DEFAULT_COLS,
    BUTTON_HEIGHT, BUTTON_WIDTH, BUTTON_SPACING, 
    GRID_MARGIN_LEFT, GRID_MARGIN_RIGHT, GRID_MARGIN_TOP, GRID_MARGIN_BOTTOM,
    FOOTER_HEIGHT, FOOTER_MARGIN_BOTTOM,
    ANIM_DURATION_ENTRANCE, ANIM_DURATION_HEIGHT, ANIM_DURATION_WIDTH, ANIM_DURATION_BORDER,
    ROOT_MARGIN, RESIZE_MARGIN, calculate_width, calculate_footer_btn_width
)
from ui.managers.overlay_manager import OverlayManager

from ui.grid_layout_engine import GridLayoutEngine
from ui.settings_widget import SettingsWidget
from ui.button_edit_widget import ButtonEditWidget


class FrozenScrollArea(QScrollArea):
    """ScrollArea that disables wheel scrolling."""
    def wheelEvent(self, event):
        event.accept()

class VirtualButton:
    """Helper for layout engine to track out-of-bounds buttons without consuming a widget."""
    def __init__(self, config):
        self.config = config
        self.span_x = config.get('span_x', 1)
        self.span_y = config.get('span_y', 1)

class Dashboard(QWidget):
    """Main dashboard popup widget with dynamic grid."""
    
    button_clicked = pyqtSignal(dict)  # Button config
    add_button_clicked = pyqtSignal(int)  # Slot index
    buttons_reordered = pyqtSignal(int, int) # (source, target)
    edit_button_requested = pyqtSignal(int)
    edit_button_saved = pyqtSignal(int, dict) # NEW: Signal for save completion
    save_config_requested = pyqtSignal() # New signal to request save from parent 
    duplicate_button_requested = pyqtSignal(int)
    clear_button_requested = pyqtSignal(int)
    rows_changed = pyqtSignal()  # Emitted after row count changes and UI rebuilds
    cols_changed = pyqtSignal()  # Emitted after column count changes and UI rebuilds
    settings_clicked = pyqtSignal()
    volume_scroll_requested = pyqtSignal(str, float)  # entity_id, new_volume (for scroll wheel)
    media_command_requested = pyqtSignal(int, str)    # slot, command
    weather_forecast_requested = pyqtSignal(int, QRect, dict) # slot, geometry, config
    
    def __init__(self, config: dict, theme_manager=None, input_manager=None, version: str = "Unknown", rows: int = 2, cols: int = DEFAULT_COLS, parent=None):
        super().__init__(parent)
        self.config = config
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        self.version = version
        self._rows = rows
        self._cols = cols
        self.buttons: list[DashboardButton] = []
        self._button_pool: list[DashboardButton] = []  # Pool for recycled buttons
        self._button_configs: list[dict] = []
        self._entity_states: dict = {} # Map entity_id -> full state dict
        
        # Entrance Animation
        self._anim_progress = 0.0
        self.anim = QPropertyAnimation(self, b"anim_progress")
        self.anim.setDuration(ANIM_DURATION_ENTRANCE)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.finished.connect(self._on_anim_finished)
        
        # Border Animation (Decoupled from entrance)
        self._border_progress = 0.0
        self.border_anim = QPropertyAnimation(self, b"glow_progress")
        self.border_anim.setDuration(ANIM_DURATION_BORDER) # Slower, elegant spin
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        # Overlay Manager
        self.overlay_manager = OverlayManager(self, self.theme_manager)
        self.overlay_manager.update_buttons(self.buttons)
        self.overlay_manager.update_states(self._entity_states)
        self.overlay_manager.service_request.connect(self.button_clicked.emit)
        
        # Throttling

        
        
        # Load theme settings
        app_config = self.config.get('appearance', {})
        self._border_effect = app_config.get('border_effect', 'Rainbow')
        self._show_dimming = app_config.get('show_dimming', False)
        self._glass_ui = app_config.get('glass_ui', False)
        self._button_style = app_config.get('button_style', 'Gradient')
        
        # Propagate border effect to overlay manager
        self.overlay_manager.set_border_effect(self._border_effect)
        
        self.setup_ui()
        
        # View switching (Grid vs Settings)
        self._current_view = 'grid'  # 'grid' or 'settings'
        self._grid_height = None  # Will be set after first show
        self._fixed_width = calculate_width(self._cols)  # Dynamic width based on cols
        
        # Window Resize Logic
        self.setMouseTracking(True) # Enable hover events for cursor change
        self._is_resizing_window = False
        self._resize_mode = None # 'top', 'left', 'top-left'
        self._resize_start_pos = None # Global pos
        self._resize_start_geo = None # (x, y, w, h)
        self._resize_start_rows = rows
        self._resize_start_cols = cols
        
        self._ignore_focus_loss = False  # Guard for resize release outside window
        
        # Drag & Drop
        self.setAcceptDrops(True)
        
        # Height animation (Custom Timer Loop for smooth sync)
        self._anim_start_height = 0
        self._anim_target_height = 0
        self._anim_start_time = 0
        self._anim_duration = 0.25

        self._height_anim_anchor_bottom = None # Anchor Y for height resize
        
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(16) # ~60 FPS
        self._animation_timer.timeout.connect(self._on_animation_frame)
        
        # SettingsWidget (created lazily to avoid circular import at module load)
        self.settings_widget = None
        
        if self.theme_manager:
            theme_manager.theme_changed.connect(self.on_theme_changed)

        # Window Height Animation
        self._anim_height = 0
        self.height_anim = QPropertyAnimation(self, b"anim_height")
        self.height_anim.setDuration(ANIM_DURATION_HEIGHT)
        self.height_anim.setEasingCurve(QEasingCurve.Type.OutBack) # Slight bounce
        # Serialize: Check for pending width changes after height finishes
        self._pending_resize_cols = None
        self._pending_rows_update = None # Store pending rows change (for phased animation)
        self.height_anim.finished.connect(self._on_height_anim_finished)
        
        # Window Width Animation
        self._anim_width = 0
        self.width_anim = QPropertyAnimation(self, b"anim_width")
        self.width_anim.setDuration(ANIM_DURATION_WIDTH)
        self.width_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.width_anim.finished.connect(self._on_width_anim_finished)
            
    def dragEnterEvent(self, event):
        """Accept dragging buttons."""
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        """Track drag movement."""
        if not event.mimeData().hasFormat(MIME_TYPE):
             return
             
        # Decode source slot to get span
        data = event.mimeData().data(MIME_TYPE)
        stream = QDataStream(data, QIODevice.OpenModeFlag.ReadOnly)
        source_slot = stream.readInt32()
        
        source_btn = next((b for b in self.buttons if b.slot == source_slot), None)
        if not source_btn:
            event.ignore()
            return

        # Decode source slot to get span
        data = event.mimeData().data(MIME_TYPE)
        stream = QDataStream(data, QIODevice.OpenModeFlag.ReadOnly)
        source_slot = stream.readInt32()
        
        source_btn = next((b for b in self.buttons if b.slot == source_slot), None)
        if not source_btn:
            event.ignore()
            return

        # Check target
        drop_pos = event.position().toPoint()
        target_slot = -1
        
        # 1. Check if over a valid button (occupied or empty)
        for btn in self.buttons:
             if not btn.isVisible(): continue
             if btn.geometry().contains(drop_pos):
                 target_slot = btn.slot
                 target_btn = btn
                 break
        
        # 2. If not over a button, check if within grid area (e.g. gap) and map to closest slot?
        # For now, require being over a slot.
        
        if target_slot != -1:
             # Check bounds: Does source button fit at target position?
             # Target slot -> (row, col)
             target_row = target_slot // self._cols
             target_col = target_slot % self._cols
             
             if target_row + source_btn.span_y > self._rows:
                 event.ignore()
                 return
             if target_col + source_btn.span_x > self._cols:
                 event.ignore()
                 return

             # Block if target is forbidden
             target_btn = next((b for b in self.buttons if b.slot == target_slot), None)
             if target_btn and target_btn.config and target_btn.config.get('type') == 'forbidden':
                  event.ignore()
                  return

        event.acceptProposedAction()
            
    def dropEvent(self, event):
        """Handle button drop."""
        if not event.mimeData().hasFormat(MIME_TYPE):
            return
            
        source_slot = event.source().slot
        
        # Determine target slot based on drop position
        drop_pos = event.position().toPoint()
        
        # Check if dropped on another button
        target_slot = -1
        
        # Find button under mouse
        for btn in self.buttons:
            if not btn.isVisible(): continue
            if btn.geometry().contains(drop_pos):
                target_slot = btn.slot
                break
        
        if target_slot != -1 and target_slot != source_slot:
            # 1. Bounds Check
            # Get source button to check dimensions
            source_btn = next((b for b in self.buttons if b.slot == source_slot), None)
            
            if source_btn:
                target_row = target_slot // self._cols
                target_col = target_slot % self._cols
                
                if target_row + source_btn.span_y > self._rows:
                    return
                if target_col + source_btn.span_x > self._cols:
                    return

            # 2. Forbidden Check
            target_btn = next((b for b in self.buttons if b.slot == target_slot), None)
            if target_btn and target_btn.config and target_btn.config.get('type') == 'forbidden':
                return
                
            self.on_button_dropped(source_slot, target_slot)
            event.acceptProposedAction()
            
    def get_anim_height(self):
        return self.height()
        
    def set_anim_height(self, h):
        h = int(h)
        # Anchor to BOTTOM (Grow Up)
        if self._height_anim_anchor_bottom is None:
             self._height_anim_anchor_bottom = self.y() + self.height()
             
        # Calculate new Y so bottom stays fixed
        new_y = self._height_anim_anchor_bottom - h
        self.setGeometry(self.x(), new_y, self.width(), h)
        
    anim_height = pyqtProperty(float, get_anim_height, set_anim_height)

    def get_anim_width(self):
        return self.width()
    
    def set_anim_width(self, w):
        w = int(w)
        # Anchor to RIGHT (Grow Left)
        # new_x = anchor_right - new_width
        anchor_right = getattr(self, '_width_anim_anchor_right', self.x() + self.width())
        new_x = anchor_right - w
        
        # Use setGeometry for atomic move+resize (smoother)
        self.setGeometry(new_x, self.y(), w, self.height())
    
    anim_width = pyqtProperty(float, get_anim_width, set_anim_width)

    def set_rows(self, rows: int):
        """Set number of rows and rebuild grid."""
        if self._rows != rows:
            # FIX: If we're currently showing settings, defer the rebuild until
            # the hide_settings animation completes.
            if self._current_view == 'settings':
                self._pending_rows = rows
                return
            
            self._do_set_rows(rows)
    
    def handle_button_resize(self, slot_idx, span_x, span_y):
        """Handle resize request from a button."""
        
        # Find the button and its config by runtime slot
        source_btn = next((b for b in self.buttons if b.slot == slot_idx), None)
        if not source_btn or not source_btn.config:
            return
        
        # Get (row, col) from config
        row = source_btn.config.get('row', 0)
        col = source_btn.config.get('col', 0)
        
        # Clamp span to available space
        max_span_x = self._cols - col
        
        # Check for dynamic row expansion (only for 3D Printers)
        if source_btn.config.get('type') == '3d_printer':
            required_rows = row + span_y
            if required_rows > self._rows and span_y <= 3 and required_rows <= 4:
                # Dynamically expand rows immediately
                self._rows = required_rows
                
                # Fill button pool to match new grid size
                target_slots = self._rows * self._cols
                while len(self.buttons) < target_slots:
                    b = self._get_button_from_pool(len(self.buttons))
                    self.buttons.append(b)
                
                # Update config so the new row count persists across restarts
                if 'appearance' not in self.config: self.config['appearance'] = {}
                self.config['appearance']['rows'] = self._rows
                
        max_span_y = self._rows - row
        
        valid_span_x = min(span_x, max_span_x)
        valid_span_y = min(span_y, max_span_y)
        
        # Auto-Relocation: Check if expanding would overlap other configured buttons
        if not hasattr(self, 'layout_engine'):
            self.layout_engine = GridLayoutEngine(cols=self._cols)
        
        relocations = self.layout_engine.find_relocations(
            source_btn, valid_span_x, valid_span_y, self.buttons, self._rows
        )
        
        if relocations is None:
            # No room to relocate displaced buttons — block the resize
            return
        
        # Apply relocations: update displaced buttons' config positions
        for btn, new_r, new_c in relocations:
            btn.config['row'] = new_r
            btn.config['col'] = new_c
            # Also update the master config list so persistence works
            for cfg in self._button_configs:
                if cfg.get('entity_id') == btn.config.get('entity_id'):
                    cfg['row'] = new_r
                    cfg['col'] = new_c
                    break
        
        source_btn.config['span_x'] = valid_span_x
        source_btn.config['span_y'] = valid_span_y
        
        # Also update master config list for the resized button
        for cfg in self._button_configs:
            if cfg.get('entity_id') == source_btn.config.get('entity_id'):
                cfg['span_x'] = valid_span_x
                cfg['span_y'] = valid_span_y
                break
        
        # Update button instance size (but DON'T rebuild grid during drag)
        source_btn.set_spans(valid_span_x, valid_span_y)

        # Live preview: rebuild grid without disrupting mouse events
        self.rebuild_grid(preview_mode=True)

    def handle_button_resize_finished(self):
        """Handle completion of resize drag."""
        # Finalize the grid (hide unused buttons, persist slots)
        self.rebuild_grid(preview_mode=False)
        self.save_config_requested.emit()

    def rebuild_grid(self, preview_mode=False, update_height=True):
        """Rebuild the grid using (row, col) based layout."""
        # 0. Lazy Import / Init Engine
        if not hasattr(self, 'layout_engine'):
            self.layout_engine = GridLayoutEngine(cols=self._cols)

        # 1. Clear Grid
        if not preview_mode:
            for btn in self.buttons:
                btn.hide()
        
        while self.grid.count():
            self.grid.takeAt(0)
            
        # 2. Calculate Layout
        # Include virtual (out-of-bounds) buttons so forbidden cells are marked
        all_buttons = list(self.buttons)
        if hasattr(self, '_virtual_buttons') and self._virtual_buttons:
            all_buttons.extend(self._virtual_buttons)
            
        placements = self.layout_engine.calculate_layout(all_buttons, self._rows)
        
        max_row = 0
        
        # 3. Apply Placements
        for btn, r, c, span_y, span_x in placements:
            # RESET transient states for empty buttons (fixes stuck "forbidden" icons)
            if not (btn.config and btn.config.get('entity_id')):
                 btn.config = {}
                 btn.update_content()
                 btn.update_style()

            # Add to grid FIRST to ensure parentage
            self.grid.addWidget(btn, r, c, span_y, span_x)
            
            # Then show
            btn.setVisible(True)
            
            # Reset resize styling if not resizing
            if not getattr(btn, '_is_resizing', False):
                btn.resize_handle_opacity = 0.0
                if hasattr(btn, 'resize_anim'):
                    btn.resize_anim.stop()
            
            # Compute runtime slot for signal/overlay compat
            new_slot = r * self._cols + c
            btn.slot = new_slot
            
            # Update Config with (row, col) — NOT slot
            if not preview_mode and btn.config:
                btn.config['row'] = r
                btn.config['col'] = c
                
            max_row = max(max_row, r + span_y)
            
        # 3b. Mark forbidden cells: buttons that are in cells blocked by out-of-bounds configured buttons
        forbidden_cells = self.layout_engine.get_forbidden_cells()
        if forbidden_cells:
            for btn, r, c, span_y, span_x in placements:
                if (r, c) in forbidden_cells and not (btn.config and btn.config.get('entity_id')):
                    # This is an empty/add button sitting in a forbidden cell
                    btn.config = {'type': 'forbidden'}
                    btn.update_content()
                    btn.update_style()
        
        # 3c. Fix z-order: raise configured buttons above empty/add buttons
        # Without this, empty buttons in adjacent cells can visually cover
        # multi-span configured buttons after a resize.
        for btn, r, c, span_y, span_x in placements:
            if btn.config and btn.config.get('entity_id'):
                btn.raise_()
            
        # 4. Hide buttons not placed (outside visible grid or conflicting)
        placed_buttons = set(p[0] for p in placements)
        for btn in self.buttons:
            if btn not in placed_buttons:
                btn.setVisible(False)
        
        # 5. Update Height (Only if requested and not previewing)
        if update_height:
            grid_h = (self._rows * BUTTON_HEIGHT) + ((self._rows - 1) * BUTTON_SPACING)
            extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + 20
            new_height = grid_h + extras
            
            start_h = self.height()
            if start_h != new_height and self._current_view == 'grid':
                if preview_mode:
                    self.setFixedSize(self.width(), new_height)
                    if hasattr(self, '_resize_anchor_y'):
                        new_y = self._resize_anchor_y - new_height
                        self.move(self.x(), new_y)
                else:
                    # Avoid restarting animation if already running towards target
                    if self.height_anim.state() == QPropertyAnimation.State.Running and \
                       abs(self.height_anim.endValue() - new_height) < 1.0:
                        return

                    self._resize_anchor_y = self.y() + self.height()
                    self.height_anim.stop()
                    self.height_anim.setStartValue(float(start_h))
                    self.height_anim.setEndValue(float(new_height))
                    self.height_anim.start()

    def get_first_empty_slot(self, span_x: int = 1, span_y: int = 1) -> tuple:
        """Find the first visible (row, col) that is completely empty and fits the span."""
        if not hasattr(self, 'layout_engine'):
            self.layout_engine = GridLayoutEngine(cols=self._cols)
            
        return self.layout_engine.find_first_empty_slot(self.buttons, self._rows, span_x, span_y)
            
    def _do_set_rows(self, rows: int):
        """Update grid rows dynamically (Animate First, Rebuild Later)."""
        
        # Calculate target height for the NEW row count
        # grid_h calculation moved here (Phase 1)
        grid_h = (rows * BUTTON_HEIGHT) + ((rows - 1) * BUTTON_SPACING)
        extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + (2 * ROOT_MARGIN)
        target_h = grid_h + extras
        
        # Store pending update
        self._pending_rows_update = rows
        
        # Start Animation (Phase 1)
        if self.height_anim.state() == QPropertyAnimation.State.Running:
             self.height_anim.stop()
             
        # Reset anchor to ensure we capture current position correctly
        self._height_anim_anchor_bottom = None
             
        # Unlock size constraints for animation
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        
        self._anim_target_height = target_h
        self.height_anim.setStartValue(self.height())
        self.height_anim.setEndValue(target_h)
        self.height_anim.start()

    def _on_height_anim_finished(self):
        """Called when height animation finishes. Apply grid changes (Phase 2)."""
        # Apply pending row update if any
        if self._pending_rows_update is not None:
            new_rows = self._pending_rows_update
            self._rows = new_rows
            
            # Ensure button pool matches grid size
            target_slots = new_rows * self._cols
            current_slots = len(self.buttons)
            
            new_buttons = []
            
            if target_slots > current_slots:
                for i in range(current_slots, target_slots):
                    button = self._get_button_from_pool(i)
                    self.buttons.append(button)
                    new_buttons.append(button)
                        
            elif target_slots < current_slots:
                for i in range(current_slots - 1, target_slots - 1, -1):
                    btn = self.buttons.pop()
                    self._recycle_button(btn)
            
            # Re-apply all configs using (row, col) logic
            # set_buttons calls rebuild_grid, so we must be careful.
            # But set_buttons is general purpose. 
            # Ideally we call set_buttons with a flag or just do it manually here.
            # set_buttons calls rebuild_grid() -> which might restart animation.
            
            # Let's modify set_buttons to NOT rebuild if we do it here? 
            # Or better: call set_buttons which calls rebuild_grid. 
            # We need rebuild_grid to NOT start animation if called from here.
            
            # Actually set_buttons() calls rebuild_grid(). 
            # If we call set_buttons below, we need to suppress its height animation.
            # But set_buttons doesn't take args.
            
            # FIX: We can set a temporary flag or just call the logic directly.
            
            # Applying config
            self._button_configs = self._button_configs # No change to config list
            
            # We need to refresh the button assignments (like set_buttons does)
            # but without triggering another height animation.
            
            # Reusing set_buttons logic but manually to control rebuild_grid
            self.set_buttons(self._button_configs, self.config.get('appearance', {}), update_height=False)
            
            self.update_style()
            
            # FIX: Ensure ALL buttons are fully visible after set_buttons
            for button in self.buttons:
                button.set_faded(1.0)
            
            # Fade in only genuinely new empty (Add) buttons
            new_empty_indices = []
            if new_buttons:
                for button in new_buttons:
                    if not (button.config and button.config.get('entity_id')):
                        button.set_faded(0.0)
                        new_empty_indices.append(button.slot)
                if new_empty_indices:
                    self._fade_in_buttons(new_empty_indices)
                
            self.rows_changed.emit()
            self._pending_rows_update = None
            
            # Lock size to new calculated height
            grid_h = (self._rows * BUTTON_HEIGHT) + ((self._rows - 1) * BUTTON_SPACING)
            extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + (2 * ROOT_MARGIN)
            self.setFixedSize(self._fixed_width, int(grid_h + extras))
            
            if self._current_view == 'grid':
                self._fade_in_footer()

        # Update Config & Persist (Rows)
        if 'appearance' not in self.config: self.config['appearance'] = {}
        self.config['appearance']['rows'] = self._rows
        print(f"DASHBOARD: Saving Rows={self._rows}")
        self.save_config_requested.emit()

        # Chain to width resize check
        self._check_pending_resize()

    def _get_button_from_pool(self, slot: int) -> DashboardButton:
        """Get a button from the pool or create a new one."""
        if self._button_pool:
            btn = self._button_pool.pop()
            btn.slot = slot
            btn.show()
            # Signals are already connected, but slot is updated on instance
            return btn
        
        # Create new with implicit parent to prevent top-level window spawning
        # Use grid_widget if available, else container, else self
        parent = getattr(self, 'grid_widget', getattr(self, 'container', self))
        
        button = DashboardButton(slot=slot, theme_manager=self.theme_manager, parent=parent)
        button.clicked.connect(lambda cfg, btn=button: self._on_button_clicked(btn.slot, cfg))
        button.dropped.connect(self.on_button_dropped)
        button.edit_requested.connect(self.edit_button_requested)
        button.duplicate_requested.connect(self.duplicate_button_requested)
        button.clear_requested.connect(self.clear_button_requested)
        button.duplicate_requested.connect(self.duplicate_button_requested)
        button.clear_requested.connect(self.clear_button_requested)
        button.dimmer_requested.connect(self._on_dimmer_requested)
        button.climate_requested.connect(self._on_climate_requested)
        button.weather_requested.connect(self._on_weather_requested)
        button.printer_requested.connect(self._on_printer_requested)
        button.volume_requested.connect(self._on_volume_requested)
        button.media_command_requested.connect(self.media_command_requested.emit)
        button.resize_requested.connect(self.handle_button_resize)
        return button

    def _recycle_button(self, btn: DashboardButton):
        """Hide and recycle a button."""
        btn.hide()
        btn.reset_state()
        btn.config = {} # Clear config
        self._button_pool.append(btn)

    def _fade_in_buttons(self, slot_indices: list):
        """Animate opacity for specific buttons."""
        for btn in self.buttons:
            if btn.slot in slot_indices:
                # Ensure effect is enabled
                btn._opacity_eff.setEnabled(True)
                
                # Create animation
                anim = QPropertyAnimation(btn._opacity_eff, b"opacity", btn)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setDuration(600)
                anim.setEasingCurve(QEasingCurve.Type.OutQuad)
                
                # Disable effect when done
                anim.finished.connect(lambda b=btn: b._opacity_eff.setEnabled(False))
                
                anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def _check_pending_resize(self):
        """Called when height animation finishes. Process pending width change if any."""
        # Lock size after height animation
        self.setFixedSize(self.width(), self.height())
        
        if getattr(self, '_pending_resize_cols', None) is not None:
             cols = self._pending_resize_cols
             self._pending_resize_cols = None
             # Use do_set_cols directly as self._cols is already updated
             self._do_set_cols(cols)

    def set_cols(self, cols: int):
        """Set number of columns and rebuild grid."""
        if self._cols != cols:
            # Always update _cols and _fixed_width immediately so setup_ui uses the right values
            self._cols = cols
            self._fixed_width = calculate_width(cols)
            if hasattr(self, 'layout_engine'):
                self.layout_engine.cols = cols
            
            if self._current_view == 'settings':
                self._pending_cols = cols
                return
            self._do_set_cols(cols)
    
    def _do_set_cols(self, cols: int):
        """Phase 1: Animate window width (defer grid rebuild)."""
        # Serialize: If height animation is running, wait for it to finish.
        if self.height_anim.state() == QPropertyAnimation.State.Running:
            self._pending_resize_cols = cols
            return
            
        # _cols and layout_engine already updated by set_cols logic
        
        # Calculate new width
        new_width = calculate_width(self._cols)
        self._fixed_width = new_width
        start_w = self.width()
        
        # Start Animation
        if start_w != new_width:
            if self.width_anim.state() == QPropertyAnimation.State.Running:
                self.width_anim.stop()
                
            # Capture RIGHT anchor for animation (if not already?)
            # Actually anchor is captured in set_anim_width dynamically or init?
            # Existing code captured it here. Let's keep it safe.
            self._width_anim_anchor_right = self.x() + self.width()

            # Unlock size constraints for animation (Window)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        
            # Unlock Footer Buttons too (otherwise they block shrinking)
            if hasattr(self, 'btn_left'):
                self.btn_left.setMinimumWidth(0)
                self.btn_left.setMaximumWidth(16777215)
            if hasattr(self, 'btn_settings'):
                self.btn_settings.setMinimumWidth(0)
                self.btn_settings.setMaximumWidth(16777215)
            
            self.width_anim.setStartValue(float(start_w))
            self.width_anim.setEndValue(float(new_width))
            self.width_anim.start()
        else:
            # If no width change needed (e.g. init or redundant call), force finish
            self._on_width_anim_finished()

    def _on_width_anim_finished(self):
        """Phase 2: Rebuild grid and fade in new buttons."""
        # Suppress repaints during bulk UI changes
        self.setUpdatesEnabled(False)
        
        # Update button pool size
        current_slots = len(self.buttons)
        target_slots = self._rows * self._cols
        new_buttons = []
        
        if target_slots > current_slots:
            for i in range(current_slots, target_slots):
                button = self._get_button_from_pool(i)
                button.set_faded(0.0)
                self.buttons.append(button)
                new_buttons.append(button)
        elif target_slots < current_slots:
            for i in range(current_slots - 1, target_slots - 1, -1):
                btn = self.buttons.pop()
                self._recycle_button(btn)
        
        # Re-apply all configs using (row, col) logic
        self.set_buttons(self._button_configs, self.config.get('appearance', {}))
        
        # FIX: Ensure ALL buttons are fully visible after set_buttons
        # set_buttons reassigns configs to button widgets sequentially,
        # so a "new" widget (faded to 0.0) might now hold a configured entity.
        # We must make everything visible first, then selectively fade-in empty slots.
        for button in self.buttons:
            button.set_faded(1.0)
        
        # Identify which slots are genuinely new empty (Add) buttons for fade-in
        new_empty_indices = []
        for button in new_buttons:
            if not (button.config and button.config.get('entity_id')):
                button.set_faded(0.0)  # Pre-fade only empty new slots
                new_empty_indices.append(button.slot)
        
        # Update footer button widths
        if hasattr(self, 'btn_left'):
            btn_w = calculate_footer_btn_width(self._cols)
            self.btn_left.setFixedWidth(btn_w)
            self.btn_settings.setFixedWidth(btn_w)
        
        self.update_style()
        
        # Re-enable updates
        self.setUpdatesEnabled(True)
        self.repaint()
        
        # Trigger fade-in for new empty slots
        if new_empty_indices:
            self._fade_in_buttons(new_empty_indices)
        
        # Update Config & Persist (Cols)
        if 'appearance' not in self.config: self.config['appearance'] = {}
        self.config['appearance']['cols'] = self._cols
        print(f"DASHBOARD: Saving Cols={self._cols}")
        self.save_config_requested.emit()
        
        # Store grid height
        grid_h = (self._rows * BUTTON_HEIGHT) + ((self._rows - 1) * BUTTON_SPACING)
        extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + 20
        self._grid_height = grid_h + extras
        # Lock only at the VERY end
        if self.height_anim.state() != QPropertyAnimation.State.Running:
             self.setFixedSize(self.width(), self.height())
             
        # Emit signal
        self.cols_changed.emit()

    def setup_ui(self):
        """Setup the dashboard UI."""
        # Reset layout if exists (not clean, but works for refresh)
        if self.layout():
             QWidget().setLayout(self.layout())

        # Frameless window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Clear existing buttons
        self.buttons.clear()
        
        # Container
        existing_container = self.findChild(QFrame, "dashboardContainer")
        if existing_container:
            existing_container.deleteLater()
            
        self.container = QFrame(self)
        self.container.setObjectName("dashboardContainer")
        
        # Root layout for Window
        if not self.layout():
            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(10, 10, 10, 10)
        else:
            root_layout = self.layout()
            while root_layout.count():
                child = root_layout.takeAt(0)
                if child.widget(): child.widget().deleteLater()
        
        root_layout.addWidget(self.container)
        
        # Container Layout (Stack + Footer)
        content_layout = QVBoxLayout(self.container)
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked Widget for switching views (Grid / Settings)
        from PyQt6.QtWidgets import QStackedWidget
        self.stack_widget = QStackedWidget()
        content_layout.addWidget(self.stack_widget)
        
        # 1. Main Grid
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(BUTTON_SPACING)
        self.grid.setContentsMargins(GRID_MARGIN_LEFT, GRID_MARGIN_TOP, GRID_MARGIN_RIGHT, GRID_MARGIN_BOTTOM)
        # Keep grid right-aligned (User requested Right Anchor)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        
        # FIX: Wrap Grid in ScrollArea for smooth animation
        self.grid_scroll = FrozenScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setStyleSheet("background: transparent;")
        self.grid_scroll.setWidget(self.grid_widget)
        
        self.stack_widget.addWidget(self.grid_scroll)
        
        # Create grid buttons
        total_slots = self._rows * self._cols
        for i in range(total_slots):
            row = i // self._cols
            col = i % self._cols
            button = DashboardButton(slot=i, theme_manager=self.theme_manager)
            button.clicked.connect(lambda cfg, btn=button: self._on_button_clicked(btn.slot, cfg))
            button.dropped.connect(self.on_button_dropped)
            button.edit_requested.connect(self.edit_button_requested)
            button.clear_requested.connect(self.clear_button_requested)
            button.dimmer_requested.connect(self._on_dimmer_requested)
            button.climate_requested.connect(self._on_climate_requested)
            button.weather_requested.connect(self._on_weather_requested)
            button.printer_requested.connect(self._on_printer_requested)
            button.volume_requested.connect(self._on_volume_requested)
            button.volume_scroll.connect(self.volume_scroll_requested.emit)
            self.grid.addWidget(button, row, col)
            self.buttons.append(button)
            
        # 2. Footer
        self.footer_widget = QWidget()
        
        # Note: Footer fade-in animation logic is handled dynamically in 
        # _fade_in_footer() to avoid "wrapped C/C++ object deleted" crashes.
        
        footer_layout = QHBoxLayout(self.footer_widget)
        footer_layout.setSpacing(BUTTON_SPACING)
        footer_layout.setContentsMargins(GRID_MARGIN_LEFT, 0, GRID_MARGIN_RIGHT, FOOTER_MARGIN_BOTTOM)
        
        # Calc standard button width (approx)
        # Layout: 428 total width. Container inner: 408.
        # Grid margins: 12 left, 12 right -> 384 for buttons.
        # 4 buttons + 3 spaces (8px) -> 384 - 24 = 360. 360/4 = 90px per button.
        # Footer buttons: 2 buttons. Width should cover 2 grid buttons + spacing.
        # Width = 90 + 8 + 90 = 188px.
        # Height = 1/3 of 80px = ~26px.
        
        btn_width = calculate_footer_btn_width(self._cols)
        btn_height = FOOTER_HEIGHT
        
        # Left Button (Home Assistant)
        self.btn_left = FooterButton("  HOME ASSISTANT") # Add space for spacing
        self.btn_left.setFixedSize(btn_width, btn_height)
        self.btn_left.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_left.clicked.connect(self.open_ha)
        
        # Create Custom HA Icon
        # Official Blue: #41bdf5
        # White Glyph
        ha_icon_char = get_icon("home-assistant")
        ha_pixmap = QPixmap(32, 32)
        ha_pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(ha_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Blue Rounded Rect
        painter.setBrush(QColor("#41BDF5"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 32, 32, 6, 6) # 6px radius for 32px is nice
        
        # 2. White Glyph
        painter.setFont(get_mdi_font(20))
        painter.setPen(QColor("white"))
        painter.drawText(ha_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ha_icon_char)
        painter.end()
        
        self.btn_left.setIcon(QIcon(ha_pixmap))
        self.btn_left.setIconSize(QSize(15, 15)) # Slightly smaller than button height (26)
        
        self.btn_left.setStyleSheet("background: rgba(255,255,255,0.1); border: none; border-radius: 4px; color: #888;")
        footer_layout.addWidget(self.btn_left)
        
        # Right Button (Settings) - now calls show_settings directly
        self.btn_settings = FooterButton("SETTINGS")
        self.btn_settings.setFixedSize(btn_width, btn_height)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self.show_settings)
        # Style handled in update_style or inline for now
        self.btn_settings.setStyleSheet("background: rgba(255,255,255,0.1); border: none; border-radius: 4px; color: #888;")
        footer_layout.addWidget(self.btn_settings)
        
        content_layout.addWidget(self.footer_widget)
        
        # FIX: Force visibility and repaint on startup/rebuild
        self.footer_widget.show()
        self.repaint()
        QTimer.singleShot(50, self.update)
        
        # Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)
        


        self.update_style()
        
        # Size Calculation
        width = calculate_width(self._cols)
        # Height: Grid rows*80 + (rows-1)*8 + Grid top(12) + Grid bot(8) + Footer(26) + Footer bot(12) + Root margins(20)
        # = (rows*80) + (rows-1)*8 + 12 + 8 + 26 + 12 + 20
        # = (rows*80) + (rows*8) - 8 + 78
        grid_h = (self._rows * BUTTON_HEIGHT) + ((self._rows - 1) * BUTTON_SPACING)
        extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + 20   # 78
        height = grid_h + extras
        self.setFixedSize(width, height)
    def open_ha(self):
        """Open Home Assistant in default browser."""
        ha_cfg = self.config.get('home_assistant', {})
        url = ha_cfg.get('url', '').strip()
        if url:
             QDesktopServices.openUrl(QUrl(url))
             self.hide()

    def update_style(self):
        """Update dashboard style based on theme."""
        # Ensure overlay manager has latest border effect
        if hasattr(self, 'overlay_manager'):
            self.overlay_manager.set_border_effect(self._border_effect)

        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {
                'window': '#1e1e1e',
                'border': '#555555',
            }
        
        # Glass UI: use semi-transparent tint over blurred desktop
        if self._glass_ui:
            is_light = (self.theme_manager and self.theme_manager.get_effective_theme() == 'light')
            if is_light:
                bg_color = 'rgba(240, 240, 240, 120)'
                border_color = 'rgba(0, 0, 0, 0.10)'
            else:
                bg_color = 'rgba(20, 20, 20, 100)'
                border_color = 'rgba(255, 255, 255, 0.12)'
        else:
            bg_color = colors['window']
            border_color = colors['border']
        
        self.container.setStyleSheet(f"""
            QFrame#dashboardContainer {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 12px;
            }}
            QMenu {{
                background-color: {colors.get('alternate_base', '#2b2b2b')};
                border: 1px solid {colors.get('border', '#3d3d3d')};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                background: transparent;
                padding: 6px 24px 6px 12px;
                color: {colors.get('text', '#e0e0e0')};
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {colors.get('accent', '#007aff')};
                color: white;
            }}
        """)
        
        for button in self.buttons:
            button.update_style()
            
        # Style Footer Buttons
        if hasattr(self, 'btn_left'):
            # Use safe defaults if keys missing
            bg = colors.get('alternate_base', '#353535')
            text = colors.get('text', '#aaaaaa')
            accent = colors.get('accent', '#4285F4')
            
            btn_style = f"""
                QPushButton {{
                    background-color: {bg};
                    border: none;
                    border-radius: 4px;
                    color: {text};
                    font-family: "{SYSTEM_FONT}";
                    font-size: 11px;
                    font-weight: 600;
                    text-transform: uppercase;
                }}
                QPushButton:hover {{
                    background-color: {accent};
                    color: white;
                }}
            """
            self.btn_left.setStyleSheet(btn_style)
            self.btn_settings.setStyleSheet(btn_style)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        
        # 1. Custom Shortcuts (Highest Priority)
        for button in self.buttons:
            sc = button.config.get('custom_shortcut', {})
            if sc.get('enabled') and sc.get('value'):
                if self.matches_pynput_shortcut(event, sc.get('value')):
                    # print(f"DEBUG: Triggering custom shortcut match for button {button.slot}")
                    button.simulate_click()
                    event.accept()
                    return



        super().keyPressEvent(event)
        
    def matches_pynput_shortcut(self, event, shortcut_str: str) -> bool:
        """Check if QKeyEvent matches pynput shortcut string."""
        if not shortcut_str: return False
        
        parts = shortcut_str.split('+')
        
        # Check modifiers
        has_ctrl = '<ctrl>' in parts
        has_alt = '<alt>' in parts
        has_shift = '<shift>' in parts
        # has_cmd/win ignored for simplicity or added if needed
        
        modifiers = event.modifiers()
        
        if has_ctrl != bool(modifiers & Qt.KeyboardModifier.ControlModifier): return False
        if has_alt != bool(modifiers & Qt.KeyboardModifier.AltModifier): return False
        if has_shift != bool(modifiers & Qt.KeyboardModifier.ShiftModifier): return False
        
        # Check key
        # Extract the non-modifier part
        target_key = None
        for p in parts:
            if p not in ['<ctrl>', '<alt>', '<shift>', '<cmd>']:
                target_key = p
                break
        
        if not target_key: return False # Modifier only?
        
        # Normalize target_key (pynput format) vs event
        # pynput: 'a', '1', '<esc>', '<space>', '<f1>'
        
        # Handle special keys
        key = event.key()
        text = event.text().lower()
        
        # 1. Single character match (letters, numbers)
        if len(target_key) == 1:
            # Prefer text() match for characters to handle layouts, 
            # BUT text() might be empty if modifiers are held (e.g. Ctrl+A might give \x01)
            # So fallback to Key code mapping if needed.
            
            # Simple check:
            if text and text == target_key: return True
            
            # Fallback: Check key code for letters/digits if text is control char
            if key >= 32 and key <= 126: # Ascii range roughly
                try:
                    # Qt Key to char
                    if chr(key).lower() == target_key: return True
                except: pass
                
            return False

        # 2. Special keys (<esc>, <f1>, etc)
        # Strip <>
        if target_key.startswith('<') and target_key.endswith('>'):
            clean_key = target_key[1:-1].lower()
            
            # Map common keys
            map_special = {
                'esc': Qt.Key.Key_Escape,
                'space': Qt.Key.Key_Space,
                'enter': Qt.Key.Key_Return,
                'backspace': Qt.Key.Key_Backspace,
                'tab': Qt.Key.Key_Tab,
                'up': Qt.Key.Key_Up,
                'down': Qt.Key.Key_Down,
                'left': Qt.Key.Key_Left,
                'right': Qt.Key.Key_Right,
                'f1': Qt.Key.Key_F1, 'f2': Qt.Key.Key_F2, 'f3': Qt.Key.Key_F3, 'f4': Qt.Key.Key_F4,
                'f5': Qt.Key.Key_F5, 'f6': Qt.Key.Key_F6, 'f7': Qt.Key.Key_F7, 'f8': Qt.Key.Key_F8,
                'f9': Qt.Key.Key_F9, 'f10': Qt.Key.Key_F10, 'f11': Qt.Key.Key_F11, 'f12': Qt.Key.Key_F12,
                'delete': Qt.Key.Key_Delete,
                'home': Qt.Key.Key_Home,
                'end': Qt.Key.Key_End,
                'page_up': Qt.Key.Key_PageUp,
                'page_down': Qt.Key.Key_PageDown
            }
            
            if map_special.get(clean_key) == key:
                return True
                
        return False
    
    def set_buttons(self, configs: list[dict], appearance_config: dict = None, update_height=True):
        """Set button configurations using (row, col) based positioning."""
        self._button_configs = configs
        if appearance_config:
            self._live_dimming = True
            self._border_effect = appearance_config.get('border_effect', 'Rainbow')
            self._show_dimming = appearance_config.get('show_dimming', False)
            self._glass_ui = appearance_config.get('glass_ui', False)
            self._button_style = appearance_config.get('button_style', 'Gradient')
        
        # Ensure we have enough button widgets for the visible grid
        target_slots = self._rows * self._cols
        while len(self.buttons) < target_slots:
            button = self._get_button_from_pool(len(self.buttons))
            self.buttons.append(button)
            
        # Clear virtual buttons (prevent stale collisions)
        self._virtual_buttons = []
        
        # Reset all buttons to empty first
        for button in self.buttons:
            button.config = {}
            button.set_spans(1, 1)
        
        # Assign configs to buttons
        # Each config with (row, col) gets assigned to a button widget
        config_idx = 0
        for cfg in configs:
            if not cfg.get('entity_id'):
                continue
            
            # Skip if config is strictly out of bounds
            # This prevents hidden buttons from consuming widgets, which would starve visible buttons
            r = cfg.get('row', 0)
            c = cfg.get('col', 0)
            sx = cfg.get('span_x', 1)
            sy = cfg.get('span_y', 1)
            
            # Check for partial overlap vs complete miss
            if c >= self._cols or r >= self._rows:
                 # Starting position is outside grid -> Completely hidden
                 continue

            if c + sx > self._cols or r + sy > self._rows:
                # Partially overlaps visible area -> Track as VirtualButton
                # This ensures the layout engine knows about it and marks intersecting cells as forbidden
                if not hasattr(self, '_virtual_buttons'): self._virtual_buttons = []
                self._virtual_buttons.append(VirtualButton(cfg))
                continue
            
            # Find a free button widget to use for this config
            if config_idx < len(self.buttons):
                button = self.buttons[config_idx]
                config_idx += 1
                
                # Reset state if entity changed
                old_entity = button.config.get('entity_id')
                new_entity = cfg.get('entity_id')
                
                button.config = cfg
                
                if old_entity != new_entity:
                    button.reset_state()
                    if new_entity and new_entity in self._entity_states:
                        button.apply_ha_state(self._entity_states[new_entity])
                
                button.set_spans(sx, sy)
                button.update_content()
                button.button_style = getattr(self, '_button_style', 'Gradient')
                button.update_style()
                button.set_border_effect(self._border_effect)
                button.show_dimming = self._show_dimming
                
                # ENSURE connection (fix for missing signal)
                try:
                    button.resize_requested.disconnect(self.handle_button_resize)
                except TypeError:
                    pass
                button.resize_requested.connect(self.handle_button_resize)
                
                try:
                    button.resize_finished.disconnect(self.handle_button_resize_finished)
                except TypeError:
                    pass
                button.resize_finished.connect(self.handle_button_resize_finished)
                
                try:
                    button.duplicate_requested.disconnect(self.duplicate_button_requested)
                except TypeError:
                    pass
                button.duplicate_requested.connect(self.duplicate_button_requested)
        
        # Remaining buttons are empty "Add" buttons — update their style too
        for i in range(config_idx, len(self.buttons)):
            self.buttons[i].update_content()
            self.buttons[i].button_style = getattr(self, '_button_style', 'Gradient')
            self.buttons[i].update_style()
            self.buttons[i].set_border_effect(self._border_effect)
        
        # Rebuild grid to properly layout
        self.rebuild_grid(update_height=update_height)





    def set_effect(self, effect_name: str):
        """Set the active border effect."""
        self._effect = effect_name
        if hasattr(self, 'overlay_manager'):
            self.overlay_manager.set_border_effect(effect_name)
        self.update()

    def paintEvent(self, event):
        """Paint overlay and effects."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Glass UI: Draw frosted desktop blur behind the container
        if self._glass_ui and hasattr(self, '_glass_bg_pixmap') and self._glass_bg_pixmap:
            container_geo = self.container.geometry()
            
            # Clip to container's rounded rect
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(container_geo), 12, 12)
            painter.setClipPath(clip_path)
            
            # Calculate offset to keep background fixed relative to screen (parallax/static effect)
            # If window is lower than capture (expanding/shrinking), we shift drawing up
            # Global Y of drawing area = self.y() + container_geo.y()
            # Global Y of pixmap = self._glass_capture_pos.y()
            
            current_global_y = self.y() + container_geo.y()
            diff_y = current_global_y - self._glass_capture_pos.y()
            
            # Draw at (container_x, container_y - diff_y) to align
            draw_pos = QPoint(int(container_geo.x()), int(container_geo.y() - diff_y))
            
            # Draw the blurred desktop
            painter.drawPixmap(draw_pos, self._glass_bg_pixmap)
            
            painter.setClipPath(QPainterPath())  # Reset clip
        
        # Ensure window receives mouse events (transparent windows pass events through on Windows)
        painter.setBrush(QColor(0, 0, 0, 1))  # Alpha 1/255
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        painter.end()

        # Only draw if animating and effect is active
        # Use border_anim state to control drawing duration
        if self.border_anim.state() == QPropertyAnimation.State.Running:
            if self._border_effect == 'Rainbow':
                self._draw_rainbow_border()
            elif self._border_effect == 'Aurora Borealis':
                self._draw_aurora_border()
            elif self._border_effect == 'Prism Shard':
                self._draw_prism_shard_border()
            elif self._border_effect == 'Liquid Mercury':
                self._draw_liquid_mercury_border()

    def _draw_aurora_border(self):
        """Draw the Aurora Borealis border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Angle (Slower spin for aurora?)
        angle = self._border_progress * 360.0 * 1.0 
        
        # Fade out
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Aurora Colors: Green -> Blue -> Purple -> Blue -> Green
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)

    def _draw_rainbow_border(self):
        """Draw the rainbow border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Angle
        angle = self._border_progress * 360.0 * 1.5
        
        # Fade out
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        # Use container geometry to ensure tight fit
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Colors - Google Brand Colors
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)

    def _draw_prism_shard_border(self):
        """Draw the Prism Shard border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        angle = self._border_progress * 360.0 * 0.9
        
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Muted jewel tones: Muted Cyan -> Muted Magenta -> Muted Yellow -> Blue-Gray
        colors = ["#26C6DA", "#EC407A", "#FFCA28", "#CFD8DC", "#26C6DA"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)

    def _draw_liquid_mercury_border(self):
        """Draw the Liquid Mercury border effect."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        angle = self._border_progress * 360.0 * 1.2
        
        opacity = 1.0
        if self._border_progress > 0.8:
            opacity = (1.0 - self._border_progress) / 0.2
        painter.setOpacity(opacity)
        
        rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
        
        # Gunmetal Chrome palette
        colors = ["#37474F", "#78909C", "#CFD8DC", "#ECEFF1", "#CFD8DC", "#78909C", "#37474F"]
        
        gradient = QConicalGradient(rect.center(), angle)
        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(3)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        painter.drawRoundedRect(rect, 12, 12)
            
    def _on_dimmer_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_dimmer(slot, rect, config)
        
    def _on_climate_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_climate(slot, rect, config)
        
    def _on_weather_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        
        # Emit a special signal to request the main app to fetch forecast
        # and then call self.overlay_manager.start_weather
        # So we need a signal for it.
        # Wait, if we use a pyqtSignal, we can just emit an action dict to main app
        # and have a callback passed in.
        
        # Actually I need a way to fetch the forecast. Let's emit a signal.
        self.weather_forecast_requested.emit(slot, rect, config)
        
    def _on_volume_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_volume(slot, rect, config)

    def _on_printer_requested(self, slot: int, rect: QRect, config: dict):
        self.overlay_manager.start_printer(slot, rect, config)

    def _get_button_config(self, slot: int):
        row = slot // self._cols
        col = slot % self._cols
        return next((c for c in self._button_configs if c.get('row') == row and c.get('col') == col), {})


    def update_media_art(self, entity_id: str, pixmap: QPixmap):
        """Update media art for a specific entity."""
        # Update internal state cache? Or just push to button?
        # Find buttons
        for btn in self.buttons:
             if btn.config.get('entity_id') == entity_id and btn.config.get('type') == 'media_player':
                 btn.set_album_art(pixmap)
                 


    def update_entity_state(self, entity_id: str, state: dict):
        """Update a button/widget when entity state changes."""
        self._entity_states[entity_id] = state
        
        for button in self.buttons:
            cfg = button.config
            if not cfg: continue
            
            # Standard entity match
            if cfg.get('entity_id') == entity_id:
                button.apply_ha_state(state)
            
            # 3D Printer handles multiple entities
            elif cfg.get('type') == '3d_printer':
                if entity_id == cfg.get('printer_state_entity'):
                    button.apply_ha_state(state) # Primary state
                elif entity_id in (cfg.get('printer_camera_entity'), cfg.get('printer_nozzle_entity'), cfg.get('printer_bed_entity')):
                    button.update_content() # Just trigger a redraw, dashboard_button_painter will fetch the latest state from _entity_states
        
        # Forward to overlay manager
        self.overlay_manager.update_entity_state(entity_id, state)
    
    def update_camera_image(self, entity_id: str, pixmap):
        """Update a camera button with a new image."""
        for button in self.buttons:
            cfg = button.config
            if not cfg: continue
            
            if cfg.get('entity_id') == entity_id and cfg.get('type') == 'camera':
                button.set_camera_image(pixmap)
            elif cfg.get('type') == '3d_printer' and cfg.get('printer_camera_entity') == entity_id:
                button.set_camera_image(pixmap)
                
        # Forward to overlay manager
        self.overlay_manager.update_camera_image(entity_id, pixmap)
                
    def apply_camera_cache(self, cache: dict):
        """Apply cached camera images to matching buttons."""
        for entity_id, cache_data in cache.items():
            if isinstance(cache_data, tuple) and len(cache_data) == 2:
                _, pixmap = cache_data
                self.update_camera_image(entity_id, pixmap)

    def _on_button_clicked(self, slot: int, config: dict):
        """Handle button click."""
        if config and config.get('type') == 'forbidden':
            return  # Forbidden slots are not interactive
        if not config:
            self.add_button_clicked.emit(slot)
        else:
            self.button_clicked.emit(config)


    def on_button_dropped(self, source: int, target: int):
        self.buttons_reordered.emit(source, target)
    
    def on_theme_changed(self, theme: str):
        self.update_style()
    
    def _capture_glass_background(self, target_height: int = None):
        """Capture and blur the desktop area behind the window for frosted glass."""
        if not self._glass_ui:
            self._glass_bg_pixmap = None
            return
        
        screen = QApplication.primaryScreen()
        if not screen:
            self._glass_bg_pixmap = None
            return
        
        # Geometry logic
        c_x = self.container.x()
        c_w = self.container.width()
        
        screen_geo = screen.geometry()
        
        # Capture the entire vertical column for this window to avoid needing
        # to recapture when animating height changes, which caused the UI itself to get captured.
        grab_x = self.x() + c_x
        grab_y = screen_geo.y()
        grab_w = c_w
        grab_h = screen_geo.height()
        
        # Safety: ensure valid dimensions
        if grab_w <= 0: grab_w = self.width() - 20
        if grab_w <= 0: grab_w = 100 # Fallback
        if grab_h <= 0: grab_h = 1080 # Fallback
        
        # Grab the screen region
        desktop_pixmap = screen.grabWindow(0, int(grab_x), int(grab_y), int(grab_w), int(grab_h))
        
        if desktop_pixmap.isNull():
            self._glass_bg_pixmap = None
            return
        
        # Apply downscale → upscale blur (same technique as label pill)
        blur_factor = 0.06  # Very heavy blur
        small = desktop_pixmap.scaled(
            max(1, int(grab_w * blur_factor)),
            max(1, int(grab_h * blur_factor)),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        blurred = small.scaled(
            int(grab_w), int(grab_h),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        self._glass_bg_pixmap = blurred
        self._glass_capture_pos = QPoint(int(grab_x), int(grab_y))
    
    def show_near_tray(self):
        """Position and show the dashboard near the system tray."""
        screen = QApplication.primaryScreen()
        if not screen:
            self.show()
            return
        
        screen_rect = screen.availableGeometry()
        
        # Calculate target position
        target_x = screen_rect.right() - self.width() - 10
        target_y = screen_rect.bottom() - self.height() - 10
        
        self._target_pos = QPoint(target_x, target_y)
        
        # Set initial state (Hidden & Positioned) BEFORE showing
        # This prevents the window from flashing in the center/wrong place
        self.set_anim_progress(0.0)
        
        # Capture frosted glass background BEFORE showing (so we grab the clean desktop)
        if self._glass_ui:
            # Position the window first so geometry is correct for capture
            self.move(target_x, target_y)
            self._capture_glass_background()
        
        # Ensure we are visible before animating
        super().show()
        self.activateWindow()
        
        # Start Entrance Animation
        self.anim.stop()
        self.anim.setDuration(250) # Fast, snappy
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        
        # Start Border Animation (Independent)
        self.border_anim.stop()
        self.border_anim.setStartValue(0.0)
        self.border_anim.setEndValue(1.0)
        self.border_anim.start()
    
    def toggle(self):
        if self.isVisible() and self.windowOpacity() > 0.1:
            self.close_animated()
        else:
            self.show_near_tray()
    
    def close_animated(self):
        """Fade out and slide down, then hide."""
        self.anim.stop()
        self.border_anim.stop() # Stop the glow too
        
        # Recalculate target position from current window position
        self._target_pos = QPoint(self.x(), self.y())
        
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self.anim.setStartValue(self._anim_progress)
        self.anim.setEndValue(0.0)
        self.anim.start()
        
    def _on_anim_finished(self):
        """Handle animation completion (hide if closing)."""
        # Robust check for near-zero
        if self._anim_progress < 0.01:
            super().hide()

    def focusOutEvent(self, event):
        # We rely on changeEvent for robust window-level focus loss
        # but focusOutEvent is still good for some edge cases
        super().focusOutEvent(event)
    
    def changeEvent(self, event):
        """Handle window activation changes."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                # Window lost focus? Close it.
                # Use small delay to allow for things like dialogs or transient windows
                QTimer.singleShot(100, self._check_hide)
        super().changeEvent(event)
    
    def _check_hide(self):
        # If we are not the active window, close.
        if self._ignore_focus_loss:
            return
            
        if not self.isActiveWindow():
            self.close_animated()
            
    def get_anim_progress(self):
        return self._anim_progress
        
    def set_anim_progress(self, val):
        self._anim_progress = val
        
        # 1. Opacity
        self.setWindowOpacity(val)
        
        # 2. Slide Up (Entrance) / Slide Down (Exit)
        # Offset: 0 at 1.0 (open), +20 at 0.0 (closed)
        if hasattr(self, '_target_pos'):
            offset = int((1.0 - val) * 20)
            self.move(self._target_pos.x(), self._target_pos.y() + offset)
            
        self.update() # Trigger repaint for border effects
        
    anim_progress = pyqtProperty(float, get_anim_progress, set_anim_progress)

    def get_glow_progress(self):
        return self._border_progress
        
    @pyqtSlot(float)
    def set_glow_progress(self, val):
        self._border_progress = val
        self.update() 
        
    glow_progress = pyqtProperty(float, get_glow_progress, set_glow_progress)

    def showEvent(self, event):
        """Standard show event."""
        super().showEvent(event)
        # We handle animation in show_near_tray usually, but for safety:
        self.activateWindow()
        self.setFocus()
    
    # ============ VIEW SWITCHING (Grid <-> Settings) ============
    
    def _init_settings_widget(self, config: dict, input_manager=None):
        """Initialize the SettingsWidget (call from main.py after Dashboard creation)."""
        # Store for re-initialization after set_rows() rebuilds UI
        self._settings_config = config
        self._settings_input_manager = input_manager
        
        # IMPORT Settings Widget
        self.settings_widget = SettingsWidget(config, self.theme_manager, input_manager, self.version, self)
        self.settings_widget.back_requested.connect(self.hide_settings)
        self.settings_widget.settings_saved.connect(self._on_settings_saved)
        
        # Wrap in ScrollArea for smooth animation (avoids squashing)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Transparent background
        self.settings_scroll.setStyleSheet("background: transparent;")
        # Disable wheel scrolling - content should fit
        self.settings_scroll.wheelEvent = lambda e: e.ignore()
        self.settings_scroll.setWidget(self.settings_widget)
        
        # Add ScrollArea to stack (index 1)
        self.stack_widget.addWidget(self.settings_scroll)
        # Ensure grid SCROLL is visible
        self.stack_widget.setCurrentWidget(self.grid_scroll)
        
        # Clear cached height so it re-calculates with new settings widget
        self._cached_settings_height = None

        # Init Button Editor (Embedded)
        # Create a placeholder instance to be ready
        self.edit_widget = ButtonEditWidget([], theme_manager=self.theme_manager, input_manager=self.input_manager, parent=self)
        self.edit_widget.saved.connect(self._on_edit_saved)
        self.edit_widget.cancelled.connect(self._on_edit_cancelled)
        self.edit_widget.size_changed.connect(self._on_edit_size_changed)
        
        self.edit_scroll = QScrollArea()
        self.edit_scroll.setWidget(self.edit_widget)
        self.edit_scroll.setWidgetResizable(True)
        self.edit_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_scroll.setStyleSheet("background: transparent; border: none;")
        self.edit_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Disable wheel scrolling - content should fit
        self.edit_scroll.wheelEvent = lambda e: e.ignore()
        
        self.stack_widget.addWidget(self.edit_scroll)

    def _on_settings_saved(self, config: dict):
        """Handle settings saved - emit signal and return to grid."""
        if self.settings_widget:
            self.settings_widget.set_opacity(1.0) # Reset in case
            
        # Update local config immediately
        app = config.get('appearance', {})
        self._border_effect = app.get('border_effect', 'Rainbow')
        self.overlay_manager.set_border_effect(self._border_effect)
        
        # Update custom colors
        self._show_dimming = app.get('show_dimming', False)
        self._glass_ui = app.get('glass_ui', False)
        
        self._live_dimming = True
        
        # Propagate to buttons
        for btn in self.buttons:
            btn.set_border_effect(self._border_effect)
            btn.show_dimming = self._show_dimming
        
        # Update display (handles height/width changes nicely)
        if 'rows' in app and app['rows'] != self._rows:
            self.set_rows(app['rows'])
            
        self.update_style()
        
        self.settings_saved.emit(config)
        self.hide_settings()
            
    def _on_edit_saved(self, config: dict):
        """Handle save from embedded editor."""
        # Find existing button config to update or append?
        # The main app handles actual saving, we just bubble up
        # BUT we need to close the view
        self.transition_to('grid')
        # Emit slot AND config
        self.edit_button_saved.emit(self.edit_widget.slot, config)
        
    def _on_edit_cancelled(self):
        self.transition_to('grid')

    def _on_edit_size_changed(self):
        """Handle dynamic height changes from the Edit Widget (debounced to prevent jitter)."""
        if getattr(self, '_current_view', '') != 'edit_button':
            return
            
        # Debounce: cancel any pending resize and reschedule
        if not hasattr(self, '_edit_resize_timer'):
            self._edit_resize_timer = QTimer(self)
            self._edit_resize_timer.setSingleShot(True)
            self._edit_resize_timer.timeout.connect(self._do_edit_resize)
        
        self._edit_resize_timer.start(80)  # Wait 80ms before actually animating

    def _do_edit_resize(self):
        """Perform the actual edit dialog resize after debounce."""
        if getattr(self, '_current_view', '') != 'edit_button':
            return
        
        target_height = self._calculate_view_height('edit_button')
        
        if abs(target_height - self.height()) <= 2:
            return
        
        # Stop any running animation first to prevent compounding
        self._animation_timer.stop()
        
        # MUST unlock the constraints so it can animate smoothly
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        
        self._anim_start_height = self.height()
        self._anim_target_height = target_height
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.18
        
        # Anchor exactly to the current bottom so sizing is perfectly stable,
        # _on_transition_done will run when finished to reset the anchor precisely
        self._anchor_bottom_y = self.geometry().y() + self.height()
        
        self._lock_view_sizes('edit_button', target_height)
        self._animation_timer.start()
        
    # edit_button_saved = pyqtSignal(dict) # REMOVED: Defined at top of class with (int, dict)
    
    def show_edit_button(self, slot: int, config: dict = None, entities: list = None):
        """Open the embedded button editor."""
        if self._current_view == 'edit_button': return
        
        # Update the widget content
        self.edit_widget.slot = slot
        self.edit_widget.config = config or {}
        self.edit_widget.entities = entities or []
        # IMPORTANT: Populate entities FIRST, then load config so entity_id can be selected
        self.edit_widget.populate_entities()
        self.edit_widget.load_config()
        
        # Transition
        self.transition_to('edit_button')

    settings_saved = pyqtSignal(dict)
    
    def _calculate_view_height(self, view_name: str) -> int:
        """Calculate target height for a given view."""
        if view_name == 'grid':
            # Use current height if available, or calculate from rows
            if self._grid_height:
                return self._grid_height
            # Fallback
            return (self._rows * 80) + ((self._rows - 1) * 8)
            
        elif view_name == 'settings':
            # Calculate dynamic settings height
            if self.settings_widget:
                # Always recalculate for accurate sizing
                content_h = self.settings_widget.get_content_height()
                settings_height = content_h + 30  # Small padding for container margins
                
                # Clamp against screen height
                screen = QApplication.primaryScreen()
                if screen:
                    max_h = screen.availableGeometry().height() * 0.9
                    settings_height = max(300, min(settings_height, int(max_h)))
                else:
                    settings_height = max(300, min(settings_height, 800))
                    
                return settings_height
            return 450
            
        elif view_name == 'edit_button':
            # Calculate dynamic editor height
            if hasattr(self, 'edit_widget'):
                content_h = self.edit_widget.get_content_height()
                # Add small padding for container margins
                h = content_h + 30
                
                # Clamp against screen height so it doesn't grow taller than monitor
                screen = QApplication.primaryScreen()
                if screen:
                    max_h = screen.availableGeometry().height() * 0.95
                    h = min(h, int(max_h))
                
                return max(300, min(h, 2000))
            return 400
            
        # Default fallback for unknown views
        return 400

    def _lock_view_sizes(self, target_view: str, target_height: int):
        """Lock widget sizes before animation to prevent jitter."""
        width = self.container.width() if self.container.width() > 0 else (self._fixed_width - 20)
        
        if target_view == 'settings':
            if self.settings_widget:
                self.settings_widget.setFixedSize(width, target_height)
        
        elif target_view == 'edit_button':
            if hasattr(self, 'edit_widget'):
                self.edit_widget.setFixedSize(width, target_height)
                
        elif target_view == 'grid':
            # Lock Grid Widget size to true grid height
            true_grid_h = getattr(self, '_captured_grid_widget_h', None)
            if not true_grid_h:
                true_grid_h = (self._rows * 80) + ((self._rows - 1) * 8)
            self.grid_widget.setFixedSize(width, true_grid_h)

    def transition_to(self, view_name: str):
        """
        Generic method to transition between views with smooth animation.
        view_name: 'grid', 'settings', 'edit_button', etc.
        """
        if self._current_view == view_name:
            return

        # 1. Capture state before transition
        if self._current_view == 'grid':
            self._grid_height = self.height()
            self._captured_grid_widget_h = self.grid_widget.height()
            
        # 2. Update view state
        self._current_view = view_name
        
        # 3. Calculate heights (Moved up so we can use it for capture)
        start_height = self.height()
        target_height = self._calculate_view_height(view_name)

        # No longer re-capturing glass background here because capturing while visible
        # will capture the dashboard UI itself. show_near_tray() captures the full column up front.
        
        # 4. Prepare Animation
        self._anim_start_height = start_height
        self._anim_target_height = target_height
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.25
        self._anchor_bottom_y = self.geometry().y() + self.height()
        
        # 5. Handle Footer Visibility
        if view_name == 'grid':
            # Footer will be shown/faded-in after animation in _on_transition_done
            pass
        else:
            self.footer_widget.hide()
            
        # 6. Button Opacity (if leaving grid)
        if view_name != 'grid':
             # Optional: fade out buttons
             pass 
        else:
            # Returning to grid: restore opacity
            for btn in self.buttons:
                btn.set_opacity(1.0)

        # 7. Unlock Window Constraints
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        
        # 8. Switch Stack & Lock Content
        self._lock_view_sizes(view_name, target_height)
        
        if view_name == 'settings':
            self.stack_widget.setCurrentWidget(self.settings_scroll)
        elif view_name == 'grid':
            self.stack_widget.setCurrentWidget(self.grid_scroll)
        elif view_name == 'edit_button':
            if hasattr(self, 'edit_scroll'):
                self.stack_widget.setCurrentWidget(self.edit_scroll)
            
        # 9. Start Animation
        self._animation_timer.start()

    def show_settings(self):
        """Morph from Grid view to Settings view."""
        if self.overlay_manager:
            self.overlay_manager.close_all_overlays()
        self.transition_to('settings')
    
    def hide_settings(self):
        """Morph from Settings view back to Grid view."""
        self.transition_to('grid')

    def _on_animation_frame(self):
        """Custom high-precision animation loop."""
        now = time.perf_counter()
        elapsed = now - self._anim_start_time
        progress = min(1.0, elapsed / self._anim_duration)
        
        # Cubic Ease Out: 1 - pow(1 - x, 3)
        t = 1.0 - pow(1.0 - progress, 3)
        
        # Calculate current height
        current_h = int(self._anim_start_height + (self._anim_target_height - self._anim_start_height) * t)
        
        # Update Geometry
        # Anchor to bottom: new_y = bottom - new_height
        new_y = self._anchor_bottom_y - current_h
        current_x = self.x()
        
        # Single atomic update
        self.setGeometry(current_x, new_y, self._fixed_width, current_h)
        
        if progress >= 1.0:
            self._animation_timer.stop()
            if self._current_view == 'grid':
                # Special handling for returning to grid
                pass
            
            # Lock the view to its final state (stops drift!)
            self._on_transition_done()

    def _fade_in_footer(self):
        """Fade in footer with dynamic effect creation to prevent crashes."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        
        # Create FRESH effect and animation each time
        effect = QGraphicsOpacityEffect(self.footer_widget)
        effect.setOpacity(0.0)
        self.footer_widget.setGraphicsEffect(effect)
        
        # Store refs to prevent garbage collection during anim
        self._current_footer_effect = effect
        self._current_footer_anim = QPropertyAnimation(effect, b"opacity")
        self._current_footer_anim.setDuration(300)
        self._current_footer_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._current_footer_anim.setStartValue(0.0)
        self._current_footer_anim.setEndValue(1.0)
        
        # Cleanup on finish
        self._current_footer_anim.finished.connect(self._on_footer_fade_finished)
        
        self.footer_widget.show()
        self._current_footer_anim.start()

    def _on_footer_fade_finished(self):
        """Remove opacity effect after fade-in to save resources/prevent bugs."""
        self.footer_widget.setGraphicsEffect(None)
        # Clear references
        self._current_footer_effect = None
        self._current_footer_anim = None
    
    def _on_transition_done(self):
        """After transition (morph), restore styles and cleanup."""
        try:
            # Not actually using QPropertyAnimation for window, so this might be old code
            # But just in case
            if hasattr(self, 'height_anim') and self.height_anim:
                 self.height_anim.finished.disconnect(self._on_transition_done)
        except:
            pass
            
        # Unlock grid size so it behaves normally (if we are in grid view)
        if self._current_view == 'grid':
            self.grid_widget.setMinimumSize(0, 0)
            self.grid_widget.setMaximumSize(16777215, 16777215)
        
            # FIX: Process pending row change if any (deferred from set_rows)
            pending = getattr(self, '_pending_rows', None)
            if pending is not None:
                self._pending_rows = None
                self._do_set_rows(pending)
                # Show footer after rebuild (with fade-in)
                self._fade_in_footer()
                
                # After rebuild, reposition
                self._reposition_after_morph()
            
            # FIX: Process pending col change if any (deferred from set_cols)
            pending_cols = getattr(self, '_pending_cols', None)
            if pending_cols is not None:
                self._pending_cols = None
                self._do_set_cols(pending_cols)
                self._fade_in_footer()
                self._reposition_after_morph()
                return
            
            if pending is not None:
                return
        
        # Re-lock the window to its final size
        # Use target height from animation vars or calculate fresh
        t_height = self._grid_height if self._current_view == 'grid' else self._anim_target_height
        
        # Safety fallback
        if not t_height: t_height = self.height()
            
        self.setFixedSize(self._fixed_width, int(t_height))
        
        # Show footer now that animation is complete (with fade-in) -- ONLY IF GRID
        if self._current_view == 'grid':
            self._fade_in_footer()
        
        # Reposition window to bottom-right corner
        self._reposition_after_morph()
    
    def _reposition_after_morph(self):
        """Reposition window to keep it anchored to bottom-right."""
        try:
            screen = self.screen()
        except:
            screen = QApplication.primaryScreen()
            
        if not screen:
            screen = QApplication.primaryScreen()
            
        if not screen:
            return
        screen_rect = screen.availableGeometry()
        x = screen_rect.right() - self.width() - 10
        y = screen_rect.bottom() - self.height() - 10
        self.move(x, y)

    # ============ DRAG TO RESIZE HANDLERS ============
    
    def mousePressEvent(self, event):
        """Handle window resize drag start."""
        # Only allow resizing in Grid View
        if self._current_view != 'grid':
            super().mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            y = event.position().y()
            
            # Identify resize zone
            mode = None
            # Disable Corner Drag (Top-Left) - Prioritize Top then Left
            if y < RESIZE_MARGIN:
                mode = 'top'
            elif x < RESIZE_MARGIN:
                mode = 'left'
                
            if mode:
                self._is_resizing_window = True
                self._resize_mode = mode
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = (self.x(), self.y(), self.width(), self.height())
                self._resize_start_rows = self._rows
                self._resize_start_cols = self._cols
                event.accept()
                return

        super().mousePressEvent(event)

    def leaveEvent(self, event):
        """Reset cursor when mouse leaves window."""
        # Only reset if not currently dragging
        if not self._is_resizing_window:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        """Handle resize drag and hover cursor."""
        # Only allow resizing in Grid View
        if self._current_view != 'grid':
            self.setCursor(Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)
            return

        x = event.position().x()
        y = event.position().y()
        
        if not self._is_resizing_window:
            # Hover Logic: Change Cursor (No Corner)
            if y < RESIZE_MARGIN:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif x < RESIZE_MARGIN:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)
            return
            
        # Drag Logic
        delta = event.globalPosition().toPoint() - self._resize_start_pos
        dx = delta.x()
        dy = delta.y()
        
        # Calculate Logic:
        # Top Drag (dy < 0 -> Grow Up -> Height Increase)
        # Left Drag (dx < 0 -> Grow Left -> Width Increase)
        
        target_rows = self._resize_start_rows
        target_cols = self._resize_start_cols
        
        if 'top' in self._resize_mode:
            # Current Height - dy (since dy is negative for Up drag)
            # Actually: new_h = start_h - dy
            start_h = self._resize_start_geo[3]
            new_h = start_h - dy
            target_rows = self._get_rows_at_height(new_h)
            
        if 'left' in self._resize_mode:
            # new_w = start_w - dx
            start_w = self._resize_start_geo[2]
            new_w = start_w - dx
            target_cols = self._get_cols_at_width(new_w)
            
        # Apply Changes (Snap)
        current_rows = getattr(self, '_pending_rows_update', None)
        if current_rows is None:
            current_rows = self._rows
            
        if target_rows != current_rows:
            self.set_rows(target_rows)
            
        if target_cols != self._cols:
            self.set_cols(target_cols)
            
        event.accept()

    def mouseReleaseEvent(self, event):
        """End resize drag."""
        if self._is_resizing_window:
            self._is_resizing_window = False
            self._resize_mode = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            
            # Prevent focus-loss close for a brief moment
            # (In case mouse release happened outside window)
            self._ignore_focus_loss = True
            QTimer.singleShot(500, lambda: setattr(self, '_ignore_focus_loss', False))
            
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _get_rows_at_height(self, target_h):
        """Find nearest row count for a target window height."""
        best_rows = self._rows
        min_diff = float('inf')
        
        # Calculate height for 2 to 6 rows
        for r in range(2, 7):
            # Calculate grid height
            grid_h = (r * BUTTON_HEIGHT) + ((r - 1) * BUTTON_SPACING)
            # Match calculation from _do_set_rows:
            # grid_h + GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + (2 * ROOT_MARGIN)
            extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + (2 * ROOT_MARGIN)
            calc_h = grid_h + extras
            
            diff = abs(calc_h - target_h)
            if diff < min_diff:
                min_diff = diff
                best_rows = r
                
        return best_rows

    def _get_cols_at_width(self, target_w):
        """Find nearest col count for a target window width."""
        best_cols = self._cols
        min_diff = float('inf')
        
        # 4 to 6 cols
        for c in range(4, 9): 
            calc_w = calculate_width(c)
            diff = abs(calc_w - target_w)
            if diff < min_diff:
                min_diff = diff
                best_cols = c
                
        return best_cols

