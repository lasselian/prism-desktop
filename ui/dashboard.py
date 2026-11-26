"""
Dashboard Widget for Prism Desktop
The main popup menu with 4x2 grid of buttons/widgets.
"""

import logging
import sys
import time
import ctypes
from PyQt6.QtWidgets import (
    QWidget,
    QGridLayout,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QApplication,
    QGraphicsDropShadowEffect,
    QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt,
    QPoint,
    pyqtSignal,
    QPropertyAnimation,
    QEasingCurve,
    QDataStream,
    QIODevice,
    pyqtProperty,
    QRectF,
    QTimer,
    QRect,
    pyqtSlot,
    QUrl,
    QSize,
)
from PyQt6.QtGui import QColor, QPixmap, QPainter, QIcon, QPainterPath, QDesktopServices
from ui.icons import get_icon, get_mdi_font

# Cross-platform system font
from core.utils import SYSTEM_FONT
from core.localization_manager import t
from ui.widgets.dashboard_button import DashboardButton, MIME_TYPE
from ui.widgets.footer_button import FooterButton
from ui.widgets.page_indicator_button import PageIndicatorButton
from ui.constants import (
    DEFAULT_COLS,
    BUTTON_HEIGHT,
    BUTTON_SPACING,
    GRID_MARGIN_LEFT,
    GRID_MARGIN_RIGHT,
    GRID_MARGIN_TOP,
    GRID_MARGIN_BOTTOM,
    FOOTER_HEIGHT,
    FOOTER_MARGIN_BOTTOM,
    ANIM_DURATION_ENTRANCE,
    ANIM_DURATION_HEIGHT,
    ANIM_DURATION_WIDTH,
    ANIM_DURATION_BORDER,
    ROOT_MARGIN,
    RESIZE_MARGIN,
    calculate_width,
    GLASS_MIN_INTERVAL_MS,
    GLASS_MAX_INTERVAL_MS,
    GLASS_WORK_BUDGET,
)
from ui.widgets.notification_pill import Notice
from ui.managers.notice_center import NoticeCenter
from ui.widgets.assist_overlay import AssistOverlay
from ui.managers.overlay_manager import OverlayManager
from ui.managers.grid_manager import GridManager
from ui.grid_layout_engine import plan_move
from ui.settings_widget import SettingsWidget
from ui.button_edit_widget import ButtonEditWidget
from ui.visuals.dashboard_effects import (
    draw_aurora_border,
    draw_rainbow_border,
    draw_prism_shard_border,
    draw_liquid_mercury_border,
    capture_glass_background,
)
from ui.utils.key_events import event_matches_shortcut
from ui.utils.wayland_blur import attach_wayland_blur, is_native_wayland_blur_active
from ui.utils.wayland_stage import WaylandStageWindow

FOOTER_BUTTON_STYLE = 'background: rgba(255,255,255,0.1); border: none; border-radius: 4px; color: #888;'

logger = logging.getLogger(__name__)

QWIDGETSIZE_MAX = 16777215


class FrozenScrollArea(QScrollArea):
    """ScrollArea that disables wheel scrolling."""

    def wheelEvent(self, event):
        event.accept()


class Dashboard(QWidget):
    """Main dashboard popup widget with dynamic grid."""

    button_clicked = pyqtSignal(dict)  # Button config
    add_button_clicked = pyqtSignal(int)  # Slot index
    buttons_reordered = pyqtSignal(int, int)  # (source, target)
    edit_button_requested = pyqtSignal(int)
    edit_button_saved = pyqtSignal(int, dict)  # slot, config
    save_config_requested = pyqtSignal()
    duplicate_button_requested = pyqtSignal(int)
    clear_button_requested = pyqtSignal(int)
    rows_changed = pyqtSignal()  # Emitted after row count changes and UI rebuilds
    cols_changed = pyqtSignal()  # Emitted after column count changes and UI rebuilds
    page_change_requested = pyqtSignal(int)  # Emitted when user navigates to a different page
    move_to_page_requested = pyqtSignal(int, int)  # slot, target_page
    settings_clicked = pyqtSignal()
    volume_scroll_requested = pyqtSignal(str, float)  # entity_id, new_volume (for scroll wheel)
    media_command_requested = pyqtSignal(int, str)  # slot, command
    weather_forecast_requested = pyqtSignal(int, QRect, dict)  # slot, geometry, config
    camera_pip_requested = pyqtSignal(str, str)  # camera entity_id, title
    settings_saved = pyqtSignal(dict)

    def __init__(
        self,
        config: dict,
        theme_manager=None,
        input_manager=None,
        version: str = "Unknown",
        rows: int = 2,
        cols: int = DEFAULT_COLS,
        parent=None,
    ):
        super().__init__(parent)
        # Native Wayland lets no window position itself, so there the dashboard is
        # placed inside a see-through maximized one. None everywhere else.
        self._wayland_stage = WaylandStageWindow(self) if is_native_wayland_blur_active() else None
        self.config = config
        self.theme_manager = theme_manager
        self.input_manager = input_manager
        self.version = version
        self._rows = rows
        self._cols = cols
        self.buttons: list[DashboardButton] = []
        self._button_pool: list[DashboardButton] = []  # Pool for recycled buttons
        self._button_configs: list[dict] = []
        self._all_button_configs: list[dict] = []
        self._current_page: int = 0
        self.notice_center = NoticeCenter(self)
        self._page_count: int = 1
        self._entity_states: dict = {}  # Map entity_id -> full state dict
        self._settings_editor_target: tuple | None = None  # (page, row, col) stashed for the editor

        # Entrance Animation
        self._anim_progress = 0.0
        self.anim = QPropertyAnimation(self, b"anim_progress")
        self.anim.setDuration(ANIM_DURATION_ENTRANCE)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.finished.connect(self._on_anim_finished)

        # Border Animation (Decoupled from entrance)
        self._border_progress = 0.0
        self.border_anim = QPropertyAnimation(self, b"glow_progress")
        self.border_anim.setDuration(ANIM_DURATION_BORDER)  # Slower, elegant spin
        self.border_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Overlay Manager
        self.overlay_manager = OverlayManager(self, self.theme_manager)
        self.overlay_manager.update_buttons(self.buttons)
        self.overlay_manager.update_states(self._entity_states)
        self.overlay_manager.service_request.connect(self.button_clicked.emit)
        self.overlay_manager.morph_changed.connect(self._on_overlay_morph)
        self.overlay_manager.camera_pip_requested.connect(self.camera_pip_requested)

        self.assist_overlay = AssistOverlay(self)
        self.assist_overlay.content_changed.connect(self._on_assist_content_changed)

        # Load theme settings
        app_config = self.config.get('appearance', {})
        self._border_effect = app_config.get('border_effect', 'Rainbow')
        self._show_dimming = app_config.get('show_dimming', False)
        self._refresh_indicators_enabled = app_config.get('refresh_indicators', False)
        self._glass_ui = bool(app_config.get('glass_ui', False)) and self._glass_ui_usable()
        self._wayland_blur = None  # compositor blur handle, attached in setup_ui()
        self._button_style = app_config.get('button_style', 'Gradient').capitalize()
        self._temperature_unit = app_config.get('temperature_unit', 'celsius')

        # Propagate border effect to overlay manager
        self.overlay_manager.set_border_effect(self._border_effect)
        self.overlay_manager.set_temperature_unit_preference(self._temperature_unit)

        # Grid Manager
        self.grid_manager = GridManager(self)

        self.setup_ui()

        # View switching (Grid vs Settings)
        self._current_view = 'grid'  # 'grid' or 'settings'
        self._grid_height = None  # Will be set after first show
        self._fixed_width = calculate_width(self._cols)  # Dynamic width based on cols

        # Window Resize Logic
        self.setMouseTracking(True)  # Enable hover events for cursor change
        QApplication.instance().installEventFilter(self)  # Track mouse globally for cursor reset
        self._is_resizing_window = False
        self._resize_mode = None  # 'top', 'left', 'top-left'
        self._resize_start_pos = None  # Global pos
        self._resize_start_geo = None  # (x, y, w, h)
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

        self._height_anim_anchor_bottom = None  # Anchor Y for height resize

        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(16)  # ~60 FPS
        self._animation_timer.timeout.connect(self._on_animation_frame)

        self._glass_refresh_timer = QTimer(self)
        self._glass_refresh_timer.setInterval(GLASS_MIN_INTERVAL_MS)  # ~30 FPS cap
        self._glass_refresh_timer.timeout.connect(self._refresh_glass_background)
        # Self-limiting frame rate: rolling estimate of how long one capture takes.
        # If captures get expensive (slow machine / huge screen), the interval grows
        # so the capture never hogs more than GLASS_WORK_BUDGET of the frame budget.
        self._glass_cost_ema = None

        # SettingsWidget (created lazily to avoid circular import at module load)
        self.settings_widget = None

        if self.theme_manager:
            theme_manager.theme_changed.connect(self.on_theme_changed)

        # Window Height Animation
        self.height_anim = QPropertyAnimation(self, b"anim_height")
        self.height_anim.setDuration(ANIM_DURATION_HEIGHT)
        self.height_anim.setEasingCurve(QEasingCurve.Type.OutBack)  # Slight bounce
        # Serialize: Check for pending width changes after height finishes
        self._pending_resize_cols = None
        self._pending_rows_update = None  # Store pending rows change (for phased animation)
        self.height_anim.finished.connect(self._on_height_anim_finished)

        # Window Width Animation
        self.width_anim = QPropertyAnimation(self, b"anim_width")
        self.width_anim.setDuration(ANIM_DURATION_WIDTH)
        self.width_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.width_anim.finished.connect(self._on_width_anim_finished)

        # Assist content-driven resize (separate from height_anim so its
        # finished handler doesn't persist rows / emit save_config_requested)
        self._assist_active = False
        self._assist_ceiling_height = None
        self._assist_resize_target = None
        self._assist_grid_fade_anim = None
        self.assist_resize_anim = QPropertyAnimation(self, b"anim_height")
        self.assist_resize_anim.setDuration(ANIM_DURATION_HEIGHT)
        self.assist_resize_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.assist_resize_anim.finished.connect(self._on_assist_resize_anim_finished)

        self._last_tray_geometry = QRect()

        # React to screen geometry changes (panel settling on startup, resolution, monitor changes)
        self._connect_screen_signals(self.screen() or QApplication.primaryScreen())
        app = QApplication.instance()
        app.primaryScreenChanged.connect(
            lambda s: (self._connect_screen_signals(s), self._on_screen_geometry_changed())
        )
        app.screenAdded.connect(lambda s: (self._connect_screen_signals(s), self._on_screen_geometry_changed()))

    def _connect_screen_signals(self, screen):
        """Connect per-screen geometry signals to the reposition slot."""
        if not screen:
            return
        screen.availableGeometryChanged.connect(self._on_screen_geometry_changed)
        screen.geometryChanged.connect(self._on_screen_geometry_changed)

    def _on_screen_geometry_changed(self, _rect=None):
        """Slot for any screen geometry change. Defers to next event loop tick
        so cascading X11 geometry updates have settled before we read them."""
        QTimer.singleShot(0, lambda: self.refresh_tray_anchor(move_now=True))

    def _screen_for_tray_geometry(self, tray_geometry: QRect | None = None):
        """Choose the screen containing the tray icon when Qt reports it."""
        if tray_geometry and not tray_geometry.isNull():
            self._last_tray_geometry = QRect(tray_geometry)
            screen = QApplication.screenAt(tray_geometry.center())
            if screen:
                return screen

        if not self._last_tray_geometry.isNull():
            screen = QApplication.screenAt(self._last_tray_geometry.center())
            if screen:
                return screen

        return QApplication.primaryScreen() or self.screen()

    def _drop_allowed(self, source_btn, target_slot: int) -> bool:
        """Whether moving/swapping `source_btn` onto `target_slot` leaves a valid layout."""
        if not source_btn or not source_btn.config:
            return False
        target_btn = self._button_at(target_slot)
        target_cfg = (
            target_btn.config
            if (
                target_btn
                and target_btn.config
                and (target_btn.config.get('entity_id') or target_btn.config.get('type') == 'pin_window')
            )
            else None
        )
        page_cfgs = [c for c in self._all_button_configs if c.get('page', 0) == self._current_page]
        moves = plan_move(
            page_cfgs,
            source_btn.config,
            target_cfg,
            target_slot // self._cols,
            target_slot % self._cols,
            self._cols,
            self._rows,
        )
        return moves is not None

    def dragEnterEvent(self, event):
        """Accept dragging buttons."""
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        """Track drag movement."""
        if not event.mimeData().hasFormat(MIME_TYPE):
            return
        # The source slot travels in the drag data; its button gives the spans.
        # Keep the QByteArray in a variable: QDataStream doesn't own it, and a
        # temporary would be freed before readInt32() reads from it.
        data = event.mimeData().data(MIME_TYPE)
        stream = QDataStream(data, QIODevice.OpenModeFlag.ReadOnly)
        source_slot = stream.readInt32()
        source_btn = self._button_at(source_slot)
        if not source_btn:
            event.ignore()
            return
        # Only a drop onto a slot is checked; the gaps between tiles accept for now.
        target_slot = self._slot_at(event.position().toPoint())
        if target_slot != -1 and self._drop_rejected(source_slot, source_btn, target_slot):
            event.ignore()
            return
        event.acceptProposedAction()

    def _button_at(self, slot: int):
        return next((b for b in self.buttons if b.slot == slot), None)

    def _slot_at(self, pos) -> int:
        """Slot of the visible button under pos, or -1."""
        return next((b.slot for b in self.buttons if b.isVisible() and b.geometry().contains(pos)), -1)

    def _drop_rejected(self, source_slot: int, source_btn, target_slot: int) -> bool:
        """True if the source can't land on target_slot: off the grid, on a
        forbidden slot, or (when it moves) on top of another tile."""
        if source_btn:
            target_row, target_col = divmod(target_slot, self._cols)
            if target_row + source_btn.span_y > self._rows or target_col + source_btn.span_x > self._cols:
                return True
        target_btn = self._button_at(target_slot)
        if target_btn and target_btn.config and target_btn.config.get('type') == 'forbidden':
            return True
        return target_slot != source_slot and not self._drop_allowed(source_btn, target_slot)

    def dropEvent(self, event):
        """Handle button drop."""
        if not event.mimeData().hasFormat(MIME_TYPE):
            return
        source_slot = event.source().slot
        target_slot = self._slot_at(event.position().toPoint())
        if target_slot in (-1, source_slot):
            return
        if self._drop_rejected(source_slot, self._button_at(source_slot), target_slot):
            return
        self.on_button_dropped(source_slot, target_slot)
        event.acceptProposedAction()

    def get_anim_height(self):
        return self.height()

    def _get_tray_position(self) -> str:
        """Return the configured tray anchor position."""
        default_pos = 'top' if sys.platform == 'darwin' else 'bottom'
        return self.config.get('appearance', {}).get('tray_position', default_pos)

    def _is_top_anchored(self) -> bool:
        """Whether the dashboard should stay pinned to the top edge."""
        return self._get_tray_position() == 'top'

    def _calculate_target_height(self, rows: int | None = None, include_drawer: bool = True) -> int:
        """Calculate total window height for a given row count (plus the notice drawer)."""
        if rows is None:
            rows = self._rows
        grid_h = (rows * BUTTON_HEIGHT) + ((rows - 1) * BUTTON_SPACING)
        extras = GRID_MARGIN_TOP + GRID_MARGIN_BOTTOM + FOOTER_HEIGHT + FOOTER_MARGIN_BOTTOM + (2 * ROOT_MARGIN)
        return grid_h + extras + (self._drawer_h() if include_drawer else 0)

    def _drawer_h(self) -> int:
        """The notice drawer's share of the window height. Every window-height target
        adds it, so a resize that starts while the drawer is out keeps its room; the
        drawer itself holds still until that resize is over (see _window_busy)."""
        center = getattr(self, 'notice_center', None)
        return center.drawer_h if center is not None else 0

    def _window_busy(self) -> bool:
        """True while something other than the notice drawer is resizing or moving the window,
        or is about to."""
        running = QPropertyAnimation.State.Running
        anims = (self.height_anim, self.width_anim, self.assist_resize_anim, self.anim)
        if any(a.state() == running for a in anims):
            return True
        if self._animation_timer.isActive() or self._is_resizing_window or self._assist_active:
            return True
        edit_timer = getattr(self, '_edit_resize_timer', None)
        if edit_timer is not None and edit_timer.isActive():
            return True
        # Rows/cols changed in Settings wait for the return to the grid; that resize
        # starts in the same callback that ends the view change, so it is covered above.
        return self._pending_resize_cols is not None

    # ── Notifications ───────────────────────────────────────────────────
    # Messages go through ui/notifications.py, which builds a Notice; the
    # NoticeCenter queues them and shows one at a time in a pill above the footer.

    TOAST_MS = 5000
    CONFIRM_MS = 20000  # an unanswered question counts as "no" after this

    def notify(self, notice: Notice):
        self.notice_center.post(notice)

    def dismiss_notice(self, key: str):
        """Take away the notice with this key, shown or waiting."""
        self.notice_center.dismiss(key)

    def show_toast(self, message: str, duration_ms: int | None = None, **notice_fields):
        """Show a self-dismissing message. Extra keyword arguments are Notice fields."""
        self.notify(Notice(message, duration_ms=duration_ms or self.TOAST_MS, **notice_fields))

    def show_confirm(self, message: str, on_confirm=None, on_reject=None, **notice_fields):
        """Ask a question. Extra keyword arguments are Notice fields (confirm_text, destructive…)."""
        notice_fields.setdefault('confirm_text', t("notification_banner.yes"))
        notice_fields.setdefault('cancel_text', t("notification_banner.no"))
        notice_fields.setdefault('duration_ms', self.CONFIRM_MS)
        self.notify(Notice(message, on_confirm=on_confirm, on_reject=on_reject, **notice_fields))

    def update_toast(self, message: str):
        """Progress text for the running update; each call replaces the last."""
        self.notify(Notice(message, icon="progress-download", duration_ms=60000, key="update"))

    def refresh_tray_anchor(self, move_now: bool = False, tray_geometry: QRect | None = None):
        """Refresh cached tray geometry after config changes."""
        screen = self._screen_for_tray_geometry(tray_geometry)
        if not screen:
            return

        if self._wayland_stage is None:
            self._target_pos = self._screen_anchor(screen)
        else:
            self._target_pos = self._stage_anchor(screen)

        if move_now and self.isVisible():
            self.move(self._target_pos)

    def _screen_anchor(self, screen) -> QPoint:
        """The window position, in screen coordinates, next to the tray."""
        screen_rect = screen.availableGeometry()
        target_x = screen_rect.right() - self.width() - 10
        if self._is_top_anchored():
            target_y = screen_rect.top() + 10
        else:
            target_y = screen_rect.bottom() - self.height() - 10

            if sys.platform == 'linux':
                # Guard against availableGeometry() ignoring the panel on some DEs.
                # Clamp to 60px above the physical screen bottom to clear any panel.
                full_rect = screen.geometry()
                safe_target_y = full_rect.bottom() - self.height() - 60
                target_y = min(target_y, safe_target_y)

        return QPoint(target_x, target_y)

    def _stage_anchor(self, screen) -> QPoint:
        """The same corner inside the Wayland stage, whose area already clears the panels."""
        area = self._wayland_stage.bounds(screen)
        x = max(0, area.width() - self.width() - 10)
        y = 10 if self._is_top_anchored() else max(0, area.height() - self.height() - 10)
        return QPoint(x, y)

    def on_stage_resized(self):
        """The compositor sized the Wayland stage: keep the dashboard in its corner."""
        if self._is_resizing_window:
            return
        self.refresh_tray_anchor()
        if self.anim.state() == QPropertyAnimation.State.Running:
            self.set_anim_progress(self._anim_progress)  # keeps the slide offset
        elif hasattr(self, '_target_pos'):
            self.move(self._target_pos)

    def setVisible(self, visible: bool):
        if visible and getattr(self, '_wayland_stage', None) is not None:
            self._present_stage()
        super().setVisible(visible)

    def _present_stage(self):
        """Open the stage, with the dashboard already in its corner."""
        if self._wayland_stage.isVisible():
            self._wayland_stage.present()
            return
        screen = self._screen_for_tray_geometry()
        self.refresh_tray_anchor()
        if hasattr(self, '_target_pos'):
            self.move(self._target_pos)
        self._wayland_stage.present(screen)

    def _raise_stage(self):
        """Bring the Wayland stage forward; raising the dashboard only restacks it inside."""
        if self._wayland_stage is not None:
            self._wayland_stage.raise_()
            self._wayland_stage.activateWindow()

    def set_anim_height(self, h):
        h = int(h)
        if self._is_top_anchored():
            if getattr(self, '_height_anim_anchor_top', None) is None:
                self._height_anim_anchor_top = self.y()
            new_y = self._height_anim_anchor_top
        else:
            if self._height_anim_anchor_bottom is None:
                self._height_anim_anchor_bottom = self.y() + self.height()
            new_y = self._height_anim_anchor_bottom - h
        self.setGeometry(self.x(), new_y, self.width(), h)
        self._sync_wayland_blur()

        # Keep the (opaque) Assist overlay locked to the window's rect on
        # every frame of an Assist-driven resize, so the grid underneath is
        # never exposed mid-animation.
        if self._assist_active and self.assist_overlay.isVisible():
            m = self.ASSIST_OVERLAY_MARGIN
            self.assist_overlay.retarget(self.rect().adjusted(m, m, -m, -m))

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
        self._sync_wayland_blur()

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
        source_btn = self._button_at(slot_idx)
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
                if 'appearance' not in self.config:
                    self.config['appearance'] = {}
                self.config['appearance']['rows'] = self._rows

        max_span_y = self._rows - row

        valid_span_x = min(span_x, max_span_x)
        valid_span_y = min(span_y, max_span_y)

        relocations = self.grid_manager.layout_engine.find_relocations(
            source_btn, valid_span_x, valid_span_y, self.buttons, self._rows
        )

        if relocations is None:
            # No room to relocate displaced buttons — block the resize
            return

        # Apply relocations: update displaced buttons' config positions
        for btn, new_r, new_c in relocations:
            # btn.config is the same dict as the entry in the master config list, so
            # this also persists. (Matching by entity_id here used to hit the wrong
            # tile whenever a tile had been duplicated.)
            btn.config['row'] = new_r
            btn.config['col'] = new_c

        source_btn.config['span_x'] = valid_span_x
        source_btn.config['span_y'] = valid_span_y

        # (source_btn.config is the master-list dict, see above)

        # Update button instance size (but DON'T rebuild grid during drag)
        source_btn.set_spans(valid_span_x, valid_span_y, animate=True)

        # Live preview: rebuild grid without disrupting mouse events
        self.rebuild_grid(preview_mode=True)

    def handle_button_resize_finished(self):
        """Handle completion of resize drag."""
        # Finalize the grid (hide unused buttons, persist slots)
        self.rebuild_grid(preview_mode=False)
        self.save_config_requested.emit()

    def rebuild_grid(self, preview_mode=False, update_height=True):
        """Rebuild the grid using (row, col) based layout."""
        self.grid_manager.rebuild_grid(preview_mode=preview_mode, update_height=update_height)

    def get_first_empty_slot(self, span_x: int = 1, span_y: int = 1) -> tuple:
        """Find the first visible (row, col) that is completely empty and fits the span."""
        return self.grid_manager.layout_engine.find_first_empty_slot(self.buttons, self._rows, span_x, span_y)

    def find_first_empty_slot_on_page(self, page: int, span_x: int = 1, span_y: int = 1) -> tuple:
        """Find first empty (row, col) on the given page. Returns (-1, -1) if no space."""
        engine = self.grid_manager.layout_engine
        occupied = set()
        for cfg in self._all_button_configs:
            if cfg.get('page', 0) != page:
                continue
            r = cfg.get('row', 0)
            c = cfg.get('col', 0)
            sx = cfg.get('span_x', 1)
            sy = cfg.get('span_y', 1)
            engine._mark_occupied(r, c, sx, sy, occupied)
        r, c = engine._find_first_available(span_x, span_y, self._rows, occupied)
        return (r, c) if r is not None else (-1, -1)

    def _do_set_rows(self, rows: int):
        """Update grid rows dynamically (Animate First, Rebuild Later)."""

        # Calculate target height for the NEW row count
        target_h = self._calculate_target_height(rows)

        # Store pending update
        self._pending_rows_update = rows

        # Start Animation (Phase 1)
        if self.height_anim.state() == QPropertyAnimation.State.Running:
            self.height_anim.stop()

        # Reset anchor to ensure we capture current position correctly
        self._height_anim_anchor_bottom = None
        self._height_anim_anchor_top = None

        # Unlock size constraints for animation
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)

        self._anim_target_height = target_h
        self.height_anim.setStartValue(self.height())
        self.height_anim.setEndValue(target_h)
        self.height_anim.start()

    def _on_height_anim_finished(self):
        """Called when height animation finishes. Apply grid changes (Phase 2)."""
        if self._pending_rows_update is not None:
            self._apply_pending_rows()

        self.config.setdefault('appearance', {})['rows'] = self._rows
        logger.info(f"Saving rows={self._rows}")
        self.save_config_requested.emit()

        # Chain to width resize check
        self._check_pending_resize()

    def _apply_pending_rows(self):
        self._rows = self._pending_rows_update
        new_buttons = self._resize_button_pool(self._rows * self._cols)
        self.set_buttons(self._all_button_configs, self.config.get('appearance', {}), update_height=False)
        self.update_style()

        for button in self.buttons:
            button.set_faded(1.0)
        # New empty slots fade in instead of popping in.
        new_empty = [b for b in new_buttons if not (b.config and b.config.get('entity_id'))]
        for button in new_empty:
            button.set_faded(0.0)
        if new_empty:
            self._fade_in_buttons([b.slot for b in new_empty])

        self.rows_changed.emit()
        self._pending_rows_update = None
        # Lock size to new calculated height
        self.setFixedSize(self._fixed_width, self._calculate_target_height())
        if self._current_view == 'grid':
            self._fade_in_footer()

    def _resize_button_pool(self, target_slots: int) -> list:
        """Grow or shrink self.buttons to target_slots; return the buttons added."""
        new_buttons = []
        while len(self.buttons) < target_slots:
            button = self._get_button_from_pool(len(self.buttons))
            self.buttons.append(button)
            new_buttons.append(button)
        while len(self.buttons) > target_slots:
            self._recycle_button(self.buttons.pop())
        return new_buttons

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
        button.dimmer_requested.connect(self._on_dimmer_requested)
        button.climate_requested.connect(self._on_climate_requested)
        button.weather_requested.connect(self._on_weather_requested)
        button.camera_requested.connect(self._on_camera_requested)
        button.pip_requested.connect(self.camera_pip_requested)
        button.printer_requested.connect(self._on_printer_requested)
        button.mower_requested.connect(self._on_mower_requested)
        button.vacuum_requested.connect(self._on_vacuum_requested)
        button.alarm_requested.connect(self._on_alarm_requested)
        button.volume_requested.connect(self._on_volume_requested)
        button.volume_scroll.connect(self.volume_scroll_requested.emit)
        button.media_command_requested.connect(self.media_command_requested.emit)
        button.resize_requested.connect(self.handle_button_resize)
        button.move_to_page_requested.connect(self._forward_move_to_page)
        return button

    def _recycle_button(self, btn: DashboardButton):
        """Hide and recycle a button."""
        btn.hide()
        btn.reset_state()
        btn.config = {}  # Clear config
        self._button_pool.append(btn)

    def _fade_in_buttons(self, slot_indices: list):
        """Animate opacity for specific buttons."""
        for btn in self.buttons:
            if btn.slot in slot_indices:
                eff = btn._morph_eff
                eff._opacity = 0.0
                eff.setEnabled(True)

                anim = QPropertyAnimation(eff, b"opacity_prop", btn)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setDuration(600)
                anim.setEasingCurve(QEasingCurve.Type.OutQuad)
                anim.finished.connect(lambda b=btn: b._morph_eff._refresh_enabled())
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
            self.grid_manager.update_cols(cols)

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

            # Capture the right edge so the window grows/shrinks leftward
            self._width_anim_anchor_right = self.x() + self.width()

            # Unlock size constraints for animation (Window)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)

            # Unlock Footer Buttons too (otherwise they block shrinking)
            if hasattr(self, 'btn_left'):
                self.btn_left.setMinimumWidth(0)
                self.btn_left.setMaximumWidth(QWIDGETSIZE_MAX)
            if hasattr(self, 'btn_settings'):
                self.btn_settings.setMinimumWidth(0)
                self.btn_settings.setMaximumWidth(QWIDGETSIZE_MAX)

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

        # Update button pool size; new buttons start faded out
        new_buttons = self._resize_button_pool(self._rows * self._cols)
        for button in new_buttons:
            button.set_faded(0.0)

        # Re-apply all configs using (row, col) logic
        self.set_buttons(self._all_button_configs, self.config.get('appearance', {}))

        for button in self.buttons:
            button.set_faded(1.0)

        new_empty_indices = []
        for button in new_buttons:
            if not (button.config and button.config.get('entity_id')):
                button.set_faded(0.0)
                new_empty_indices.append(button.slot)

        if hasattr(self, 'btn_left'):
            self._update_footer_widths()

        self.update_style()

        # Re-enable updates
        self.setUpdatesEnabled(True)
        self.repaint()

        # Trigger fade-in for new empty slots
        if new_empty_indices:
            self._fade_in_buttons(new_empty_indices)

        # Update Config & Persist (Cols)
        if 'appearance' not in self.config:
            self.config['appearance'] = {}
        self.config['appearance']['cols'] = self._cols
        logger.info(f"Saving cols={self._cols}")
        self.save_config_requested.emit()

        # Store grid height
        self._grid_height = self._calculate_target_height(include_drawer=False)
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

        # Frameless window (on the Wayland stage, the stage is the window)
        if self._wayland_stage is None:
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

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
                if child.widget():
                    child.widget().deleteLater()

        root_layout.addWidget(self.container)

        blur_window = self if self._wayland_stage is None else self._wayland_stage
        self._wayland_blur = attach_wayland_blur(blur_window, self._wayland_blur_region, enabled=self._glass_ui)

        # Container Layout (Stack + Footer)
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setSpacing(0)
        self.content_layout.setContentsMargins(0, 0, 0, 0)

        # Stacked Widget for switching views (Grid / Settings)
        from PyQt6.QtWidgets import QStackedWidget

        self.stack_widget = QStackedWidget()
        self.content_layout.addWidget(self.stack_widget)

        # 1. Main Grid
        self.grid_widget = QWidget()
        # The grid is a fixed-orientation spatial layout: button positions are
        # stored as absolute (row, col) and drag/resize logic assumes a stable
        # column order. Pin it to LTR so it never mirrors under an RTL locale.
        self.grid_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
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
            button = self._get_button_from_pool(i)
            self.grid.addWidget(button, row, col)
            self.buttons.append(button)

        # 2. Footer
        self.footer_widget = QWidget()
        # Keep the footer LTR so the Home Assistant / page-indicator / Settings
        # ordering and navigation stay consistent regardless of locale.
        self.footer_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        # Note: Footer fade-in animation logic is handled dynamically in
        # _fade_in_footer() to avoid "wrapped C/C++ object deleted" crashes.

        footer_layout = QHBoxLayout(self.footer_widget)
        footer_layout.setSpacing(BUTTON_SPACING)
        footer_layout.setContentsMargins(GRID_MARGIN_LEFT, 0, GRID_MARGIN_RIGHT, FOOTER_MARGIN_BOTTOM)

        btn_height = FOOTER_HEIGHT

        # Left Button (Home Assistant)
        self.btn_left = FooterButton(t("dashboard.footer.home_assistant"))
        self.btn_left.setFixedHeight(btn_height)
        self.btn_left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        painter.drawRoundedRect(0, 0, 32, 32, 6, 6)  # 6px radius for 32px is nice

        # 2. White Glyph
        painter.setFont(get_mdi_font(20))
        painter.setPen(QColor("white"))
        painter.drawText(ha_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, ha_icon_char)
        painter.end()

        self.btn_left.setIcon(QIcon(ha_pixmap))
        self.btn_left.setIconSize(QSize(15, 15))  # Slightly smaller than button height (26)

        self.btn_left.setStyleSheet(FOOTER_BUTTON_STYLE)
        footer_layout.addWidget(self.btn_left, 1)

        # Page Indicator (center)
        self.btn_page_indicator = PageIndicatorButton(page_count=self._page_count, current_page=0)
        self.btn_page_indicator.setStyleSheet("background: rgba(255,255,255,0.1); border: none; border-radius: 4px;")
        self.btn_page_indicator.setVisible(self._page_count > 1)
        self.btn_page_indicator.page_scrolled.connect(self.switch_to_page)
        self.btn_page_indicator.page_jumped.connect(self.jump_to_page)
        footer_layout.addWidget(self.btn_page_indicator)

        # Right Button (Settings) - now calls show_settings directly
        self.btn_settings = FooterButton(t("dashboard.footer.settings"))
        self.btn_settings.setFixedHeight(btn_height)
        self.btn_settings.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self.show_settings)
        # Style handled in update_style or inline for now
        self.btn_settings.setStyleSheet(FOOTER_BUTTON_STYLE)
        footer_layout.addWidget(self.btn_settings, 1)

        self.btn_assist = FooterButton("")
        self.btn_assist.setFixedSize(btn_height, btn_height)
        self.btn_assist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_assist.setFont(get_mdi_font(11))
        self.btn_assist.setText(get_icon("chat"))
        self.btn_assist.setToolTip(t("dashboard.footer.assist"))
        # lambda avoids clicked(bool) landing in open_assist_overlay's instant param
        self.btn_assist.clicked.connect(lambda: self.open_assist_overlay())
        self.btn_assist.setStyleSheet(FOOTER_BUTTON_STYLE)
        footer_layout.addWidget(self.btn_assist, 0)

        self.content_layout.addWidget(self.footer_widget)

        self.footer_widget.show()

        # Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        self.notice_center.attach(self.container, self.footer_widget, self.content_layout)

        self.update_style()

        # Size Calculation
        width = calculate_width(self._cols)
        height = self._calculate_target_height()
        self.setFixedSize(width, height)

    def open_ha(self):
        """Open Home Assistant in default browser."""
        ha_cfg = self.config.get('home_assistant', {})
        url = ha_cfg.get('url', '').strip()
        if not url:
            return
        try:
            if sys.platform == 'linux':
                # QDesktopServices.openUrl() can segfault on some KDE/D-Bus
                # configurations — use xdg-open in a detached subprocess instead.
                import subprocess

                subprocess.Popen(
                    ['xdg-open', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                QDesktopServices.openUrl(QUrl(url))
        except Exception:
            QDesktopServices.openUrl(QUrl(url))
        self.hide()

    ASSIST_OVERLAY_MARGIN = 10
    ASSIST_WIDTH_COLS = 4
    ASSIST_MIN_HEIGHT = 220
    ASSIST_FADE_DURATION = 160

    def open_assist_overlay(self, instant=False):
        """Open the Assist chat popup: fade the grid out, snap the window to
        the final Assist size, then fade the panel in at that size — never a
        grid resize while it's visible, never a grow-from-a-point morph.

        With instant=True (global hotkey while the dashboard was hidden)
        both steps are instant instead of faded — the dashboard was just
        shown quietly for this same open, so nothing should flash."""
        if not self.config.get('assist', {}).get('enabled', False):
            return
        if self.overlay_manager:
            self.overlay_manager.close_all_overlays()

        # Clear stale chat before measuring below, or the compact size is
        # based on the previous session's bubbles and a corrective resize
        # fires right as the panel appears.
        self.assist_overlay.clear_chat()

        # Close the notice drawer first: the ceiling below must not include it.
        self.notice_center.set_held(True)
        self._assist_ceiling_height = self.height()
        self._assist_active = True

        target_width = calculate_width(self.ASSIST_WIDTH_COLS)
        compact_height = max(
            self.ASSIST_MIN_HEIGHT,
            min(
                self.assist_overlay.measure_target_content_height(
                    calculate_width(self.ASSIST_WIDTH_COLS) - 2 * self.ASSIST_OVERLAY_MARGIN
                )
                + 2 * self.ASSIST_OVERLAY_MARGIN,
                self._assist_ceiling_height,
            ),
        )

        def _open_at_compact_size():
            self.setMinimumSize(0, 0)
            self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
            self.setFixedSize(target_width, compact_height)
            self.refresh_tray_anchor(move_now=True)

            m = self.ASSIST_OVERLAY_MARGIN
            target_rect = self.rect().adjusted(m, m, -m, -m)

            if self.theme_manager:
                base_color = QColor(self.theme_manager.get_colors().get('base', '#2d2d2d'))
                accent_color = QColor(self.theme_manager.get_colors().get('accent', '#7C4DFF'))
            else:
                base_color = QColor("#2d2d2d")
                accent_color = QColor("#7C4DFF")

            self.assist_overlay.set_border_effect(self._border_effect)
            self.assist_overlay.open_fade_in(
                target_rect,
                color=accent_color,
                base_color=base_color,
                instant_close=instant,
                instant=instant,
                glass=self._glass_ui,
            )

        if instant:
            self.stack_widget.hide()
            self.footer_widget.hide()
            _open_at_compact_size()
        else:
            self._fade_grid_for_assist(fade_in=False, then=_open_at_compact_size)

    def _ensure_assist_curtain(self):
        """Lazily create the solid-color 'curtain' used to fade the grid
        out/in for Assist. A bare QFrame with no children on purpose: every
        DashboardButton already has its own QGraphicsEffect (_morph_eff),
        and a second opacity effect on their ancestor (stack_widget) nests
        graphics effects, which Qt renders with a visible glitch. Fading
        this separate widget over the grid instead avoids that."""
        curtain = getattr(self, '_assist_curtain', None)
        if curtain is None:
            curtain = QFrame(self.container)
            curtain.setObjectName("assistFadeCurtain")
            self._assist_curtain = curtain
        bg_color, _ = self._get_container_colors()
        curtain.setStyleSheet(f"""
            QFrame#assistFadeCurtain {{
                background-color: {bg_color};
                border: none;
                border-radius: 11px;
            }}
        """)
        curtain.setGeometry(self.container.rect().adjusted(1, 1, -1, -1))
        return curtain

    def _fade_grid_for_assist(self, fade_in: bool, then=None):
        """Quickly fade the grid + footer in or out around an Assist
        open/close, so the window can jump straight to/from the compact
        Assist size without the grid ever being visibly resized while
        exposed. See _ensure_assist_curtain for why this animates a
        separate covering widget rather than the grid itself."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        curtain = self._ensure_assist_curtain()

        if fade_in:
            # Grid/footer are already showing underneath, untouched.
            self.stack_widget.show()
            self.footer_widget.show()
            start_val, end_val = 1.0, 0.0
        else:
            start_val, end_val = 0.0, 1.0

        curtain.show()
        curtain.raise_()

        effect = QGraphicsOpacityEffect(curtain)
        effect.setOpacity(start_val)
        curtain.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(self.ASSIST_FADE_DURATION)
        # Linear, not OutQuad — OutQuad front-loads the opacity change,
        # which reads as an abrupt snap rather than a smooth dissolve.
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.setStartValue(start_val)
        anim.setEndValue(end_val)
        self._assist_grid_fade_anim = anim  # keep alive until finished

        def _on_finished():
            curtain.setGraphicsEffect(None)
            if not fade_in:
                self.stack_widget.hide()
                self.footer_widget.hide()
            curtain.hide()
            self._assist_grid_fade_anim = None
            if then is not None:
                then()

        anim.finished.connect(_on_finished)
        anim.start()

    def _on_assist_content_changed(self):
        """Grow (or hold) the window height to fit the chat once per message,
        capped at the height captured when Assist was opened."""
        if not self._assist_active:
            return
        target_height = max(
            self.ASSIST_MIN_HEIGHT,
            min(
                self.assist_overlay.measure_target_content_height(
                    calculate_width(self.ASSIST_WIDTH_COLS) - 2 * self.ASSIST_OVERLAY_MARGIN
                )
                + 2 * self.ASSIST_OVERLAY_MARGIN,
                self._assist_ceiling_height,
            ),
        )
        if target_height == self.height():
            return

        if self.assist_resize_anim.state() == QPropertyAnimation.State.Running:
            self.assist_resize_anim.stop()

        self._height_anim_anchor_bottom = None
        self._height_anim_anchor_top = None
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self._assist_resize_target = (calculate_width(self.ASSIST_WIDTH_COLS), target_height)
        self.assist_resize_anim.setStartValue(float(self.height()))
        self.assist_resize_anim.setEndValue(float(target_height))
        self.assist_resize_anim.start()

    def _on_assist_resize_anim_finished(self):
        """Re-lock the window size after a content-driven Assist resize and
        keep the overlay's geometry in sync with the window's new rect."""
        if self._assist_resize_target is not None:
            self.setFixedSize(*self._assist_resize_target)
        self.refresh_tray_anchor(move_now=True)
        if self._assist_active and self.assist_overlay.isVisible():
            m = self.ASSIST_OVERLAY_MARGIN
            self.assist_overlay.retarget(self.rect().adjusted(m, m, -m, -m))

    def restore_grid_size(self):
        """Called once the Assist overlay has fully closed — snap the window
        back to grid size and fade the grid back in. Safe to call even if
        Assist was never opened (no-op)."""
        if not self._assist_active:
            return
        self._assist_ceiling_height = None
        self.assist_resize_anim.stop()

        target_w = calculate_width(self._cols)
        target_h = self._calculate_target_height()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self.setFixedSize(target_w, target_h)
        self.refresh_tray_anchor(move_now=True)

        if not self.isVisible():
            # About to hide anyway (quiet hotkey close) — nothing to animate.
            self.stack_widget.show()
            self.footer_widget.show()
            self._assist_active = False
            self.notice_center.set_held(False)
            return

        self._fade_grid_for_assist(fade_in=True, then=self._on_assist_grid_restored)

    def _on_assist_grid_restored(self):
        self._assist_active = False
        self.notice_center.set_held(False)

    def set_assist_enabled(self, enabled: bool):
        """Master on/off; force-closes any open Assist session when disabled."""
        if not enabled and self.assist_overlay.isVisible():
            self.assist_overlay.request_close()

    def set_assist_visible_in_footer(self, visible: bool):
        """Show/hide the footer trigger button independently of the master toggle."""
        self.btn_assist.setVisible(visible)

    def _get_container_colors(self, colors=None):
        """Background/border colors for self.container's own styling —
        factored out so the Assist fade curtain (see _ensure_assist_curtain)
        can match it exactly."""
        if colors is None:
            colors = (
                self.theme_manager.get_colors()
                if self.theme_manager
                else {
                    'window': '#1e1e1e',
                    'border': '#555555',
                }
            )

        # Glass UI: use semi-transparent tint over blurred desktop
        if self._glass_ui:
            is_light = self.theme_manager and self.theme_manager.get_effective_theme() == 'light'
            if is_light:
                return 'rgba(240, 240, 240, 120)', 'rgba(0, 0, 0, 0.10)'
            return 'rgba(20, 20, 20, 100)', 'rgba(255, 255, 255, 0.12)'
        return colors['window'], colors['border']

    def update_style(self):
        """Update dashboard style based on theme."""
        # Ensure overlay manager has latest border effect
        if hasattr(self, 'overlay_manager'):
            self.overlay_manager.set_border_effect(self._border_effect)
        if hasattr(self, 'assist_overlay'):
            self.assist_overlay.set_border_effect(self._border_effect)

        if self.theme_manager:
            colors = self.theme_manager.get_colors()
        else:
            colors = {
                'window': '#1e1e1e',
                'border': '#555555',
            }
        bg_color, border_color = self._get_container_colors(colors)

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
            self.btn_left.button_style = self._button_style
            self.btn_settings.button_style = self._button_style
            self.btn_left.update()
            self.btn_settings.update()

            if hasattr(self, 'btn_page_indicator'):
                is_light = self.theme_manager and self.theme_manager.get_effective_theme() == 'light'
                indicator_style = f"""
                    QPushButton {{
                        background-color: {bg};
                        border: none;
                        border-radius: 4px;
                    }}
                    QPushButton:hover {{
                        background-color: {accent};
                    }}
                """
                self.btn_page_indicator.setStyleSheet(indicator_style)
                self.btn_page_indicator.button_style = self._button_style
                self.btn_page_indicator.set_light_mode(is_light)
                self.btn_page_indicator.update()

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""

        # 1. Custom Shortcuts (Highest Priority)
        for button in self.buttons:
            sc = button.config.get('custom_shortcut', {})
            if sc.get('enabled') and sc.get('value'):
                if self.matches_pynput_shortcut(event, sc.get('value')):
                    button.simulate_click()
                    event.accept()
                    return

        super().keyPressEvent(event)

    def matches_pynput_shortcut(self, event, shortcut_str: str) -> bool:
        """Check if QKeyEvent matches pynput shortcut string."""
        return event_matches_shortcut(event, shortcut_str)

    def set_buttons(self, configs: list[dict], appearance_config: dict | None = None, update_height=True):
        """Set button configurations using (row, col) based positioning."""
        if appearance_config:
            page_count = appearance_config.get('pages', 3)
            self._page_count = page_count
            if hasattr(self, 'btn_page_indicator'):
                self.btn_page_indicator.set_page_count(page_count)
            if self._current_page >= page_count:
                self._current_page = 0
                if hasattr(self, 'btn_page_indicator'):
                    self.btn_page_indicator.set_page(0)
            if hasattr(self, 'btn_page_indicator'):
                self.btn_page_indicator.setVisible(page_count > 1)
                self._update_footer_widths()
        self.grid_manager.set_buttons(configs, appearance_config=appearance_config, update_height=update_height)

    def _update_footer_widths(self):
        """No-op: footer side buttons now use stretch layout and resize automatically."""
        pass

    def _forward_move_to_page(self, slot: int, target_page: int):
        self.move_to_page_requested.emit(slot, target_page)

    def switch_to_page(self, delta: int):
        self.jump_to_page(self._current_page + delta)

    def jump_to_page(self, index: int):
        """Switch to the given page index."""
        new_page = max(0, min(index, self._page_count - 1))
        if new_page == self._current_page:
            return
        if hasattr(self, 'overlay_manager'):
            self.overlay_manager._pending_open_action = None
            self.overlay_manager.close_all_overlays()
        self._current_page = new_page
        self.btn_page_indicator.set_page(new_page)
        self.grid_manager.set_buttons(self._all_button_configs, update_height=False)
        self.page_change_requested.emit(new_page)
        self._trigger_button_entry_animations()

    def _trigger_button_entry_animations(self):
        """Fire entry animations on all currently visible gauge/perimeter/sun buttons."""
        for btn in self.buttons:
            if btn.isVisible():
                btn.play_entry_animation()

    def paintEvent(self, event):
        """Paint overlay and effects."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Glass UI: Draw frosted desktop blur behind the container
        if self._uses_glass_capture() and getattr(self, '_glass_bg_pixmap', None):
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

        # Ensure window receives mouse events (transparent windows pass events through on Windows).
        # Wayland delivers them anyway, and the margin around the blurred container stays clear.
        if not is_native_wayland_blur_active():
            painter.setBrush(QColor(0, 0, 0, 1))  # Alpha 1/255
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(self.rect())
        painter.end()

        # Only draw if animating and effect is active
        # Use border_anim state to control drawing duration
        if self.border_anim.state() == QPropertyAnimation.State.Running:
            rect = QRectF(self.container.geometry()).adjusted(0, 0, 0, 0)
            if self._border_effect == 'Rainbow':
                draw_rainbow_border(painter, rect, self._border_progress)
            elif self._border_effect == 'Aurora Borealis':
                draw_aurora_border(painter, rect, self._border_progress)
            elif self._border_effect == 'Prism Shard':
                draw_prism_shard_border(painter, rect, self._border_progress)
            elif self._border_effect == 'Liquid Mercury':
                draw_liquid_mercury_border(painter, rect, self._border_progress)

    def _on_dimmer_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_dimmer(slot, rect, config)

    def _on_climate_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_climate(slot, rect, config)

    def _on_weather_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)

        # Forecast fetching requires an HA API call, so forward the request to
        # the main app rather than handling it here.
        self.weather_forecast_requested.emit(slot, rect, config)

    def _on_volume_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_volume(slot, rect, config)

    def _on_printer_requested(self, slot: int, rect: QRect, config: dict):
        self.overlay_manager.start_printer(slot, rect, config)

    def _on_mower_requested(self, slot: int, rect: QRect):
        self.overlay_manager.start_mower(slot, rect)

    def _on_vacuum_requested(self, slot: int, rect: QRect):
        self.overlay_manager.start_vacuum(slot, rect)

    def _on_alarm_requested(self, slot: int, rect: QRect):
        config = self._get_button_config(slot)
        self.overlay_manager.start_alarm(slot, rect, config)

    def _on_camera_requested(self, slot: int, rect: QRect, config: dict):
        self.overlay_manager.start_camera(slot, rect, config)

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

    def update_entity_state(self, entity_id: str, state: dict, live: bool = False):
        """Update a button/widget when entity state changes.

        `live` marks a genuine push from Home Assistant, as opposed to an
        internal refetch/replay of already-known state. Only a live update
        flashes the opt-in per-tile refresh indicator.
        """
        self._entity_states[entity_id] = state
        for button in self.buttons:
            role = _entity_role(button.config, entity_id) if button.config else None
            if role == 'state':
                button.apply_ha_state(state)
                if live and state:
                    button.flash_refresh_indicator()
            elif role == 'redraw':
                # The painter reads the latest value from _entity_states.
                button.update_content()
        self.overlay_manager.update_entity_state(entity_id, state)

    def update_camera_image(self, entity_id: str, pixmap):
        """Update a camera button with a new image."""
        for button in self.buttons:
            cfg = button.config
            if not cfg:
                continue

            shows_camera = (cfg.get('type') == 'camera' and cfg.get('entity_id') == entity_id) or (
                cfg.get('type') == '3d_printer' and cfg.get('printer_camera_entity') == entity_id
            )
            if shows_camera:
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
        for button in self.buttons:
            button.invalidate_style_cache()
        self.update_style()

    def show_near_tray(self, tray_geometry: QRect | None = None):
        """Position and show the dashboard near the system tray."""
        screen = self._screen_for_tray_geometry(tray_geometry)
        if not screen:
            self._ignore_focus_loss = True
            self.show()
            QTimer.singleShot(300, self._activate_after_show)
            return

        self.refresh_tray_anchor(tray_geometry=tray_geometry)

        if self._wayland_stage is not None:
            self.anim.stop()
            self._anim_progress = 0.0
            self.move(self._target_pos)
            self._ignore_focus_loss = True
            self._wayland_stage.present_fade_in(
                screen,
                blur_enabled=self._glass_ui,
                on_finished=self._on_stage_fade_in_finished,
            )
            return

        # Set initial state (Hidden & Positioned) BEFORE showing
        # This prevents the window from flashing in the center/wrong place
        self.set_anim_progress(0.0)

        # Capture frosted glass background BEFORE showing (so we grab the clean desktop)
        if self._uses_glass_capture():
            # Exclude this window from OS screen grabs so live refresh doesn't capture itself
            self._set_capture_exclusion(True)
            # Position the window first so geometry is correct for capture
            self.move(self._target_pos)
            self._glass_bg_pixmap, self._glass_capture_pos = capture_glass_background(self)
            # Timer starts in _on_anim_finished once open animation completes
        else:
            self.move(self._target_pos)

        # Prevent focus-loss logic from closing the window while it's fading in
        self._ignore_focus_loss = True

        # Show without activating — activation happens after animation completes
        self.raise_()
        super().show()
        # No showEvent if this interrupted a close animation; resume notices either way.
        self.notice_center.dashboard_shown()

        # Start Entrance Animation
        self.anim.stop()
        self.anim.setDuration(250)  # Fast, snappy
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

        # Start Border Animation (Independent)
        self.border_anim.stop()
        self.border_anim.setStartValue(0.0)
        self.border_anim.setEndValue(1.0)
        self.border_anim.start()

        self._trigger_button_entry_animations()

    def _on_stage_fade_in_finished(self):
        """Finish opening after WaylandStageWindow's in-place fade completes."""
        self._anim_progress = 1.0
        self._ignore_focus_loss = False
        self._raise_stage()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.notice_center.dashboard_shown()

        self.border_anim.stop()
        self.border_anim.setStartValue(0.0)
        self.border_anim.setEndValue(1.0)
        self.border_anim.start()

        self._trigger_button_entry_animations()

    def toggle(self, tray_geometry: QRect | None = None):
        if self._wayland_stage is not None:
            if self._wayland_stage.is_showing():
                self.close_animated()
            else:
                self.show_near_tray(tray_geometry=tray_geometry)
            return
        if self.isVisible() and self.windowOpacity() > 0.1:
            self.close_animated()
        else:
            self.show_near_tray(tray_geometry=tray_geometry)

    def show_for_assist(self, tray_geometry: QRect | None = None):
        """Show the window at full opacity with no entrance animation, so a
        hotkey-triggered Assist open doesn't flash the grid in behind it."""
        screen = self._screen_for_tray_geometry(tray_geometry)
        if screen:
            self.refresh_tray_anchor(tray_geometry=tray_geometry)
            self.move(self._target_pos)
        self._ignore_focus_loss = True
        self.anim.stop()
        self.set_anim_progress(1.0)
        super().show()
        self.notice_center.dashboard_shown()
        QTimer.singleShot(0, self._activate_after_show)

    def hide_instant(self):
        """Hide immediately with no fade/slide — the counterpart to show_for_assist()."""
        self.notice_center.dashboard_hidden()
        self.anim.stop()
        self.border_anim.stop()
        self._glass_refresh_timer.stop()
        self._set_capture_exclusion(False)
        self.set_anim_progress(0.0)
        if self._wayland_stage is not None:
            self._wayland_stage.hide_immediately()
            return
        super().hide()

    def close_animated(self):
        """Fade out and slide toward the tray edge, then hide."""
        self.notice_center.dashboard_closing()
        self.anim.stop()
        self.border_anim.stop()
        self._glass_refresh_timer.stop()

        if self._wayland_stage is not None:
            self._wayland_stage.fade_out_and_hide()
            return

        # Recalculate target position from current window position
        self._target_pos = QPoint(self.x(), self.y())

        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self.anim.setStartValue(self._anim_progress)
        self.anim.setEndValue(0.0)
        self.anim.start()

    # ── Glass UI ────────────────────────────────────────────────────────────

    def _glass_ui_usable(self) -> bool:
        """On Linux Glass UI needs the compositor's blur, so also a native Wayland window."""
        if not sys.platform.startswith('linux'):
            return True
        return is_native_wayland_blur_active()

    def _uses_glass_capture(self) -> bool:
        """True if Glass UI is on and Prism has to blur a screen capture itself."""
        return self._glass_ui and not is_native_wayland_blur_active()

    def set_glass_ui(self, requested: bool):
        """Apply the Glass UI setting, as far as this session can show it."""
        self._glass_ui = bool(requested) and self._glass_ui_usable()
        self._sync_wayland_blur()

    def _wayland_blur_region(self) -> tuple[int, int, int, int, int]:
        """The container's rect in the blurred window's coordinates, and its corner radius."""
        geo = self.container.geometry()
        if self._wayland_stage is not None:
            geo = geo.translated(self.pos())
        return geo.x(), geo.y(), geo.width(), geo.height(), 12

    def _sync_wayland_blur(self):
        if self._wayland_stage is not None:
            self._wayland_stage.set_blur_wanted(self._glass_ui)
            if self._wayland_blur is not None and not self._wayland_stage.blur_suppressed:
                self._wayland_blur.set_enabled(self._glass_ui)
            self._wayland_stage.sync_child_region()
        elif self._wayland_blur is not None:
            self._wayland_blur.set_enabled(self._glass_ui)

    def _set_capture_exclusion(self, exclude: bool):
        """Hide/show this window from grabWindow() so the glass capture doesn't include itself."""
        if sys.platform != 'win32':
            return
        try:
            hwnd = int(self.winId())
            affinity = 0x00000011 if exclude else 0x00000000  # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        except (OSError, AttributeError, ValueError, RuntimeError, ctypes.ArgumentError):
            pass

    def _on_overlay_morph(self, progress: float):
        self.notice_center.raise_pill()
        if not self._uses_glass_capture():
            return
        # Pause only during transition; resume when fully open (1.0) or fully closed (0.0)
        if progress <= 0 or progress >= 1:
            if not self._glass_refresh_timer.isActive():
                self._glass_refresh_timer.start()
        else:
            self._glass_refresh_timer.stop()

    def _refresh_glass_background(self):
        if self._uses_glass_capture() and self.isVisible():
            t0 = time.perf_counter()
            self._glass_bg_pixmap, self._glass_capture_pos = capture_glass_background(self)
            cost_ms = (time.perf_counter() - t0) * 1000.0
            self.update()
            self._adapt_glass_interval(cost_ms)

    def _adapt_glass_interval(self, cost_ms: float):
        """Throttle the glass capture frame rate based on how long captures take.

        capture_glass_background() runs synchronously on the UI thread, so its
        wall-clock duration is exactly the cost it imposes on the frame. We keep
        an exponential moving average of that cost and set the timer interval so
        the capture stays under GLASS_WORK_BUDGET of each frame, clamped between
        the ~30 FPS cap and a low-frame-rate floor for struggling machines.
        """
        if self._glass_cost_ema is None:
            self._glass_cost_ema = cost_ms
        else:
            self._glass_cost_ema += 0.3 * (cost_ms - self._glass_cost_ema)

        desired = self._glass_cost_ema / GLASS_WORK_BUDGET
        new_interval = int(max(GLASS_MIN_INTERVAL_MS, min(GLASS_MAX_INTERVAL_MS, desired)))
        if new_interval != self._glass_refresh_timer.interval():
            self._glass_refresh_timer.setInterval(new_interval)

    def _on_anim_finished(self):
        """Handle animation completion (hide if closing, start glass timer if opening)."""
        if self._anim_progress < 0.01:
            self._glass_refresh_timer.stop()
            self._set_capture_exclusion(False)
            super().hide()
        elif self._anim_progress >= 0.99:
            # Activate only after the window is fully visible — prevents focus theft
            # at opacity 0 which causes the previous window's title bar to flash.
            self._ignore_focus_loss = False
            self._raise_stage()
            self.raise_()
            self.activateWindow()
            self.setFocus()
            if self._uses_glass_capture() and not self._glass_refresh_timer.isActive():
                # Start optimistic at the FPS cap; adapt down only if captures prove slow
                self._glass_cost_ema = None
                self._glass_refresh_timer.setInterval(GLASS_MIN_INTERVAL_MS)
                self._glass_refresh_timer.start()

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

    def refresh_pin_buttons(self):
        """Redraw all pin_window buttons to reflect the current pin state."""
        for btn in self.buttons:
            if btn.config.get('type') == 'pin_window':
                btn._app_config = self.config
                btn._content_fp = None
                btn.update_content()
                btn.update_style()

    def _check_hide(self):
        # If we are not the active window, close.
        if self._ignore_focus_loss:
            return

        if self.config.get('appearance', {}).get('pin_window', False):
            return

        if not self.isActiveWindow():
            self.close_animated()

    def get_anim_progress(self):
        return self._anim_progress

    def set_anim_progress(self, val):
        self._anim_progress = val

        # 1. Opacity (QtWayland has none, and warns on every call)
        if not QApplication.platformName().startswith('wayland'):
            self.setWindowOpacity(val)

        # 2. Slide relative to the tray edge.
        if hasattr(self, '_target_pos'):
            offset = int((1.0 - val) * 20)
            direction = 1 if self._get_tray_position() == 'bottom' else -1
            self.move(self._target_pos.x(), self._target_pos.y() + (offset * direction))

        self.update()  # Trigger repaint for border effects

    anim_progress = pyqtProperty(float, get_anim_progress, set_anim_progress)

    def get_glow_progress(self):
        return self._border_progress

    @pyqtSlot(float)
    def set_glow_progress(self, val):
        self._border_progress = val
        self.update()

    glow_progress = pyqtProperty(float, get_glow_progress, set_glow_progress)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_wayland_blur()
        self.notice_center.dashboard_shown()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.notice_center.dashboard_hidden()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_wayland_blur()
        self.notice_center.reposition_soon()
        if self._assist_active:
            return
        if hasattr(self, 'assist_overlay') and self.assist_overlay.isVisible():
            self.assist_overlay.hide()
            self.assist_overlay.finished.emit()

    def _activate_after_show(self):
        """Activate the window after a no-animation show (fallback path)."""
        self._ignore_focus_loss = False
        self._raise_stage()
        self.activateWindow()
        self.setFocus()

    # ============ VIEW SWITCHING (Grid <-> Settings) ============

    def _init_settings_widget(self, config: dict, input_manager=None, assist_input_manager=None):
        """Initialize the SettingsWidget (call from main.py after Dashboard creation)."""
        # IMPORT Settings Widget
        self.settings_widget = SettingsWidget(
            config,
            self.theme_manager,
            input_manager,
            self.version,
            self,
            dashboard=self,
            assist_input_manager=assist_input_manager,
        )
        self.settings_widget.back_requested.connect(self.hide_settings)
        self.settings_widget.settings_saved.connect(self._on_settings_saved)
        self.settings_widget.content_height_changed.connect(self._on_settings_content_changed)
        self.settings_widget.open_button_editor_requested.connect(self._on_settings_open_editor)
        self.settings_widget.request_delete_shortcut.connect(self._on_settings_delete_shortcut)

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
        self._settings_locked_height = None

        # Init Button Editor (Embedded)
        # Create a placeholder instance to be ready
        self.edit_widget = ButtonEditWidget(
            [], theme_manager=self.theme_manager, input_manager=self.input_manager, parent=self
        )
        self.edit_widget.saved.connect(self._on_edit_saved)
        self.edit_widget.cancelled.connect(self._on_edit_cancelled)
        self.edit_widget.size_changed.connect(self._on_edit_size_changed)
        self.edit_widget.custom_colors_changed.connect(self._on_custom_colors_changed)

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
        # macOS/Qt Fix: Prevent window auto-hide from triggering when focus changes
        # during the morph back to the grid view.
        self._ignore_focus_loss = True

        # Update local config immediately
        app = config.get('appearance', {})
        self._border_effect = app.get('border_effect', 'Rainbow')
        self.overlay_manager.set_border_effect(self._border_effect)

        # Update custom colors
        self._show_dimming = app.get('show_dimming', False)
        self._refresh_indicators_enabled = app.get('refresh_indicators', False)
        self.set_glass_ui(app.get('glass_ui', False))
        if self._uses_glass_capture():
            self._set_capture_exclusion(True)
            self._glass_bg_pixmap, self._glass_capture_pos = capture_glass_background(self)
            if not self._glass_refresh_timer.isActive():
                self._glass_refresh_timer.start()
        else:
            self._glass_refresh_timer.stop()
            self._set_capture_exclusion(False)
            self._glass_bg_pixmap = None
        self._button_style = app.get('button_style', 'Gradient').capitalize()
        self._temperature_unit = app.get('temperature_unit', 'celsius')

        self._live_dimming = True

        # Propagate to buttons
        for btn in self.buttons:
            btn.set_border_effect(self._border_effect)
            btn.show_dimming = self._show_dimming
            btn.refresh_indicators_enabled = self._refresh_indicators_enabled
            btn.button_style = self._button_style
            btn.set_temperature_unit_preference(self._temperature_unit)

        self.overlay_manager.set_temperature_unit_preference(self._temperature_unit)

        # Update display (handles height/width changes nicely)
        if 'rows' in app and app['rows'] != self._rows:
            self.set_rows(app['rows'])

        self.update_style()
        self.refresh_tray_anchor(move_now=True)

        self.settings_saved.emit(config)
        self.hide_settings()

        # macOS/Qt: Re-raise and ensure window maintains active focus on the grid
        self.raise_()
        self.activateWindow()
        self.setFocus()
        QTimer.singleShot(800, lambda: self.set_ignore_focus_loss(False))

    # ── Public accessors for the app controller ─────────────────────────────

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def entity_states(self) -> dict:
        """Last known entity states (entity_id -> full state dict)."""
        return self._entity_states

    @property
    def all_button_configs(self) -> list[dict]:
        """Button configs across all pages, as last passed to set_buttons()."""
        return self._all_button_configs

    def set_ignore_focus_loss(self, value: bool):
        """Suppress (or restore) hide-on-focus-loss, e.g. while a banner needs attention."""
        self._ignore_focus_loss = value

    def take_settings_editor_target(self) -> tuple | None:
        """Return and clear the (page, row, col) stashed by the settings shortcut list."""
        target = self._settings_editor_target
        self._settings_editor_target = None
        return target

    def _on_settings_open_editor(self, btn_cfg: dict):
        """Open the button editor for a specific button (triggered from settings shortcut list)."""
        # Stash the button's page/row/col so _open_button_editor uses the stored
        # position rather than recalculating from the live _cols (which may differ
        # from the column count when the button was originally placed).
        page = btn_cfg.get('page', 0)
        row = btn_cfg.get('row', 0)
        col = btn_cfg.get('col', 0)
        self._settings_editor_target = (page, row, col)
        slot = row * self._cols + col
        self.edit_button_requested.emit(slot)

    def _on_settings_delete_shortcut(self, btn_cfg: dict):
        """Show a confirm banner then delete the shortcut on confirmation."""
        from ui.notifications import notify_delete_shortcut

        name = btn_cfg.get('label') or btn_cfg.get('entity_id', '')
        sw = self.settings_widget
        notify_delete_shortcut(self, name, lambda: sw.apply_shortcut_delete(btn_cfg))

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

    def _on_custom_colors_changed(self, colors: list):
        if 'appearance' not in self.config:
            self.config['appearance'] = {}
        self.config['appearance']['custom_colors'] = colors
        self.save_config_requested.emit()

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
        self.setMaximumHeight(QWIDGETSIZE_MAX)

        self._anim_start_height = self.height()
        self._anim_target_height = target_height
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.18

        # Anchor exactly to the current bottom so sizing is perfectly stable,
        # _on_transition_done will run when finished to reset the anchor precisely
        self._anchor_top_y = self.geometry().y()
        self._anchor_bottom_y = self.geometry().y() + self.height()
        self._anchor_right_x = self.geometry().x() + self.width()

        self._lock_view_sizes('edit_button', target_height)
        self._animation_timer.start()

    def show_edit_button(self, slot: int, config: dict | None = None, entities: list | None = None):
        """Open the embedded button editor."""
        if self._current_view == 'edit_button':
            return

        # Update the widget content
        self.edit_widget.slot = slot
        self.edit_widget.config = config or {}
        self.edit_widget.entities = entities or []
        # Populate entities with initial/cached list, then load config
        self.edit_widget.populate_entities()
        custom_colors = self.config.get('appearance', {}).get('custom_colors', [])
        self.edit_widget.set_custom_colors(custom_colors)
        self.edit_widget.load_config()

        # Transition immediately
        self.transition_to('edit_button')

    def update_edit_entities(self, entities: list):
        """Update available entities in the open edit widget without interrupting the user."""
        if self._current_view == 'edit_button' and hasattr(self, 'edit_widget'):
            self.edit_widget.update_entities(entities)

    def _calculate_view_height(self, view_name: str, include_drawer: bool = True) -> int:
        """Calculate target window height for a given view (plus the notice drawer)."""
        drawer = self._drawer_h() if include_drawer else 0
        if view_name == 'grid':
            # The height the grid had, else one computed from the rows
            return (self._grid_height or (self._rows * 80) + ((self._rows - 1) * 8)) + drawer
        if view_name == 'settings':
            return self._settings_view_height() + drawer
        if view_name == 'edit_button':
            return self._edit_view_height() + drawer
        return 400 + drawer  # unknown views

    def _settings_view_height(self) -> int:
        if self._settings_locked_height is not None:
            return self._settings_locked_height
        if not self.settings_widget:
            return 450
        # Called before show_settings locked a height: fit the content, within the screen.
        room = self._room_height()
        max_h = int(room * 0.9) if room else 800
        return max(300, min(self.settings_widget.get_content_height() + 30, max_h))

    def _edit_view_height(self) -> int:
        if not hasattr(self, 'edit_widget'):
            return 400
        h = self.edit_widget.get_content_height() + 30  # container margins
        room = self._room_height()
        if room:
            h = min(h, int(room * 0.95))  # never taller than the screen
        return max(300, min(h, 2000))

    def _room_height(self) -> int | None:
        """How tall the window could be: the work area, or None if there is no screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        if self._wayland_stage is not None:
            # availableGeometry() ignores panels on Wayland; the stage's size doesn't.
            return self._wayland_stage.bounds(self._screen_for_tray_geometry()).height()
        return screen.availableGeometry().height()

    def _lock_view_sizes(self, target_view: str, target_height: int):
        """Lock widget sizes before animation to prevent jitter."""
        width = self._fixed_width - (ROOT_MARGIN * 2)

        if target_view == 'settings':
            if self.settings_widget:
                self.settings_widget.setMinimumSize(0, 0)
                self.settings_widget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
                if self._is_top_anchored():
                    # Top-anchored: size to current panel; will animate on each switch.
                    self.settings_widget.setFixedHeight(self.settings_widget.get_content_height())
                # Bottom-anchored: height already fixed to max in show_settings; don't override.

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
            self._grid_height = self.height() - self._drawer_h()
            self._captured_grid_widget_h = self.grid_widget.height()
        # May close the notice drawer; the resize below starts from the current
        # height, so it absorbs that without a jump.
        self.notice_center.view_changed()
        if view_name == 'grid':
            self._restore_grid_width()

        # 2. Update view state and work out the heights
        self._current_view = view_name
        start_height = self.height()
        content_height = self._calculate_view_height(view_name, include_drawer=False)
        target_height = content_height + self._drawer_h()
        # The glass background is not re-captured here: capturing while visible would
        # include the dashboard itself. show_near_tray() captures the full column up front.

        # 3. Prepare the animation
        self._anim_start_height = start_height
        self._anim_target_height = target_height
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.25
        self._anchor_top_y = self.geometry().y()
        self._anchor_bottom_y = self.geometry().y() + self.height()
        self._anchor_right_x = self.geometry().x() + self.width()

        # 4. Footer and button opacity
        if view_name == 'grid':
            # The footer fades back in after the animation (_on_transition_done).
            for btn in self.buttons:
                btn.set_opacity(1.0)
        else:
            self.footer_widget.hide()

        # 5. Unlock window constraints, switch the stack, lock the content, animate
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self._lock_view_sizes(view_name, content_height)
        self._show_view_widget(view_name)
        self._animation_timer.start()

    def _restore_grid_width(self):
        """Any path back to the grid restores the grid's own width.

        Settings widens the window and the button editor inherits that width, so a
        save/cancel straight out of the editor would otherwise leave the grid stuck
        at the settings width. hide_settings() already did this, so it is a no-op there.
        """
        self._settings_locked_height = None
        grid_width = calculate_width(self._cols)
        if grid_width != self._fixed_width:
            self._anim_start_width = self.width()
            self._fixed_width = grid_width

    def _show_view_widget(self, view_name: str):
        if view_name == 'settings':
            self.stack_widget.setCurrentWidget(self.settings_scroll)
        elif view_name == 'grid':
            self.stack_widget.setCurrentWidget(self.grid_scroll)
        elif view_name == 'edit_button' and hasattr(self, 'edit_scroll'):
            self.stack_widget.setCurrentWidget(self.edit_scroll)

    def show_settings(self):
        """Morph from Grid view to Settings view."""
        if self.overlay_manager:
            self.overlay_manager.close_all_overlays()

        # Bottom-anchored: lock height to the tallest panel so panel switches never
        # move the window top (which would shift the pill buttons under the cursor).
        # Top-anchored: let each panel animate to its natural height (window grows
        # downward, pills stay in place).
        if self.settings_widget and not self._is_top_anchored():
            # Unlock any stale size constraints before measuring, so the
            # layout is free to report its true preferred height.
            self.settings_widget.setMinimumSize(0, 0)
            self.settings_widget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
            content_h = self.settings_widget.get_max_content_height()
            settings_height = content_h + 30
            room = self._room_height()
            if room:
                settings_height = max(300, min(settings_height, int(room * 0.9)))
            else:
                settings_height = max(300, min(settings_height, 800))
            self._settings_locked_height = settings_height
            self.settings_widget.setFixedHeight(content_h)

        # Expand to at least 6 columns wide when entering settings
        min_settings_cols = 6
        target_width = max(self._fixed_width, calculate_width(min_settings_cols))
        self._anim_start_width = self.width()
        self._fixed_width = target_width
        self.transition_to('settings')

    def hide_settings(self):
        """Morph from Settings view back to Grid view."""
        self._settings_locked_height = None
        grid_width = calculate_width(self._cols)
        self._anim_start_width = self.width()
        self._fixed_width = grid_width
        self.transition_to('grid')

    def _on_settings_content_changed(self):
        """Re-animate window height when the active settings panel changes.
        Only runs when top-anchored — bottom-anchored uses a fixed height so the
        pill buttons never shift under the cursor.
        """
        if self._current_view != 'settings':
            return
        if not self._is_top_anchored():
            return
        if self.settings_widget:
            self.settings_widget.setMinimumSize(0, 0)
            self.settings_widget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
            self.settings_widget.setFixedHeight(self.settings_widget.get_content_height())
        target_h = self._calculate_view_height('settings')
        if abs(target_h - self.height()) < 4:
            return
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self._anchor_top_y = self.geometry().y()
        self._anchor_bottom_y = self.geometry().y() + self.height()
        self._animation_timer.stop()
        self._anim_start_height = self.height()
        self._anim_target_height = target_h
        self._anim_start_time = time.perf_counter()
        self._anim_duration = 0.2
        self._animation_timer.start()

    def _on_animation_frame(self):
        """Custom high-precision animation loop."""
        now = time.perf_counter()
        elapsed = now - self._anim_start_time
        progress = min(1.0, elapsed / self._anim_duration)

        # Cubic Ease Out: 1 - pow(1 - x, 3)
        eased = 1.0 - pow(1.0 - progress, 3)

        # Calculate current height
        current_h = int(self._anim_start_height + (self._anim_target_height - self._anim_start_height) * eased)

        if self._is_top_anchored():
            new_y = self._anchor_top_y
        else:
            new_y = self._anchor_bottom_y - current_h

        # Animate width if it changed (e.g. settings expansion)
        start_w = getattr(self, '_anim_start_width', None)
        if start_w is not None:
            current_w = int(start_w + (self._fixed_width - start_w) * eased)
            anchor_right = getattr(self, '_anchor_right_x', self.x() + self.width())
            new_x = anchor_right - current_w
        else:
            current_w = self._fixed_width
            new_x = self.x()

        # Single atomic update
        self.setGeometry(new_x, new_y, current_w, current_h)

        if progress >= 1.0:
            self._animation_timer.stop()
            self._anim_start_width = None
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
            # Defensive: height_anim isn't used for window transitions anymore,
            # but disconnect in case a stale connection still exists.
            if hasattr(self, 'height_anim') and self.height_anim:
                self.height_anim.finished.disconnect(self._on_transition_done)
        except TypeError:  # not connected
            pass

        # Unlock grid size so it behaves normally (if we are in grid view)
        if self._current_view == 'grid':
            self.grid_widget.setMinimumSize(0, 0)
            self.grid_widget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)

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
        if self._current_view == 'grid':
            t_height = self._grid_height + self._drawer_h() if self._grid_height else None
        else:
            t_height = self._anim_target_height

        # Safety fallback
        if not t_height:
            t_height = self.height()

        self.setFixedSize(self._fixed_width, int(t_height))

        # Show footer now that animation is complete (with fade-in) -- ONLY IF GRID
        if self._current_view == 'grid':
            self._fade_in_footer()
            self.raise_()
            self.activateWindow()
            self.setFocus()
            QTimer.singleShot(600, lambda: self.set_ignore_focus_loss(False))

        # Reposition window to the configured tray edge.
        self._reposition_after_morph()

    def _reposition_after_morph(self):
        """Reposition window to keep it anchored to the configured tray edge."""
        if self._wayland_stage is not None:
            self.refresh_tray_anchor(move_now=True)
            return
        try:
            screen = self.screen()
        except RuntimeError:  # underlying C++ window already deleted
            screen = QApplication.primaryScreen()

        if not screen:
            screen = QApplication.primaryScreen()

        if not screen:
            return
        screen_rect = screen.availableGeometry()
        x = screen_rect.right() - self.width() - 10
        if self._is_top_anchored():
            y = screen_rect.top() + 10
        else:
            y = screen_rect.bottom() - self.height() - 10
        self.move(x, y)

    # ============ DRAG TO RESIZE HANDLERS ============

    def mousePressEvent(self, event):
        """Handle window resize drag start."""
        # Only allow resizing in Grid View (and not while Assist is open)
        if self._current_view != 'grid' or self._assist_active:
            super().mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            y = event.position().y()
            vertical_resize_from_bottom = self._is_top_anchored()

            # Identify resize zone
            mode = None
            # Disable corner drag. When the window is top-anchored, use the
            # bottom edge so row growth happens downward instead of upward.
            if vertical_resize_from_bottom:
                if y > self.height() - RESIZE_MARGIN:
                    mode = 'bottom'
                elif x < RESIZE_MARGIN:
                    mode = 'left'
            elif y < RESIZE_MARGIN:
                mode = 'top'
            elif x < RESIZE_MARGIN:
                mode = 'left'

            if mode:
                self._is_resizing_window = True
                self._resize_mode = mode
                self._resize_start_pos = self._drag_position(event)
                self._glass_refresh_timer.stop()
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
            self.unsetCursor()
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        """App-level event filter to reset resize cursor when mouse moves over child widgets."""
        from PyQt6.QtCore import QEvent

        if (
            event.type() == QEvent.Type.MouseMove
            and not self._is_resizing_window
            and self._current_view == 'grid'
            and not self._assist_active
            and isinstance(obj, QWidget)
            and (obj is self or self.isAncestorOf(obj))
        ):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            near_vertical_resize = (
                pos.y() > self.height() - RESIZE_MARGIN if self._is_top_anchored() else pos.y() < RESIZE_MARGIN
            )
            if not near_vertical_resize and pos.x() >= RESIZE_MARGIN:
                self.unsetCursor()
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event):
        """Handle resize drag and hover cursor."""
        # Only allow resizing in Grid View (and not while Assist is open)
        if self._current_view != 'grid' or self._assist_active:
            self.unsetCursor()
            super().mouseMoveEvent(event)
            return
        if not self._is_resizing_window:
            self._update_resize_cursor(event.position().x(), event.position().y())
            super().mouseMoveEvent(event)
            return

        target_rows, target_cols = self._resize_drag_target(self._drag_position(event) - self._resize_start_pos)
        # Snap to whole rows and columns.
        current_rows = getattr(self, '_pending_rows_update', None)
        if current_rows is None:
            current_rows = self._rows
        if target_rows != current_rows:
            self.set_rows(target_rows)
        if target_cols != self._cols:
            self.set_cols(target_cols)
        event.accept()

    def _drag_position(self, event) -> QPoint:
        """The pointer position that a resize drag is measured with.

        On native Wayland the window stays put while Qt believes setGeometry
        moved it, so global positions jump at every step; local ones don't.
        On the Wayland stage the dashboard really moves, inside a window that
        doesn't, so stage coordinates are the steady ones.
        """
        if self._wayland_stage is not None:
            return self.mapToParent(event.position().toPoint())
        if QApplication.platformName().startswith('wayland'):
            return event.position().toPoint()
        return event.globalPosition().toPoint()

    def _update_resize_cursor(self, x: float, y: float):
        """Resize cursor over the draggable edges: the left edge, and the edge away from the tray."""
        vertical_edge = y > self.height() - RESIZE_MARGIN if self._is_top_anchored() else y < RESIZE_MARGIN
        if vertical_edge:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif x < RESIZE_MARGIN:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()

    def _resize_drag_target(self, delta) -> tuple:
        """(rows, cols) for the window size the drag has reached."""
        target_rows = self._resize_start_rows
        target_cols = self._resize_start_cols
        start_w, start_h = self._resize_start_geo[2], self._resize_start_geo[3]
        if 'top' in self._resize_mode:
            target_rows = self._get_rows_at_height(start_h - delta.y())
        elif 'bottom' in self._resize_mode:
            target_rows = self._get_rows_at_height(start_h + delta.y())
        if 'left' in self._resize_mode:
            target_cols = self._get_cols_at_width(start_w - delta.x())
        return target_rows, target_cols

    def mouseReleaseEvent(self, event):
        """End resize drag."""
        if self._is_resizing_window:
            self._is_resizing_window = False
            self._resize_mode = None
            self.unsetCursor()

            # Resume live glass capture after resize
            if self._uses_glass_capture():
                self._refresh_glass_background()
                self._glass_refresh_timer.start()

            # Prevent focus-loss close for a brief moment
            # (In case mouse release happened outside window)
            self._ignore_focus_loss = True
            QTimer.singleShot(500, lambda: setattr(self, '_ignore_focus_loss', False))

            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _get_rows_at_height(self, target_h):
        """Find nearest row count for a target window height."""
        target_h -= self._drawer_h()
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


def _entity_role(cfg: dict, entity_id: str) -> str | None:
    """'state' if the entity drives this tile, 'redraw' if the tile only shows it, else None.

    A 3D printer tile is driven by its state entity and also shows its
    camera, nozzle and bed entities.
    """
    if cfg.get('entity_id') == entity_id:
        return 'state'
    if cfg.get('type') != '3d_printer':
        return None
    if entity_id == cfg.get('printer_state_entity'):
        return 'state'
    shown = (cfg.get('printer_camera_entity'), cfg.get('printer_nozzle_entity'), cfg.get('printer_bed_entity'))
    return 'redraw' if entity_id in shown else None
