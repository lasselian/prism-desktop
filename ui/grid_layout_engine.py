"""
Grid Layout Engine for Prism Desktop
Handles the mathematical logic for placing buttons in the dashboard grid.
Uses explicit (row, col) coordinates for stable positioning across resizes.
"""


class GridLayoutEngine:
    """
    Calculates grid positions for buttons based on their configuration and spans.
    Buttons store their position as (row, col) which is independent of the
    current grid dimensions. Buttons outside the visible area are hidden.
    """

    def __init__(self, cols: int = 4):
        self.cols = cols

    def calculate_layout(self, buttons: list, rows: int) -> list[tuple]:
        """
        Calculate positions for all buttons.

        Args:
            buttons: List of DashboardButton objects (or objects with .config, .span_x/y attrs)
            rows: Total number of rows in the grid

        Returns:
            List of tuples: (button, row, col, span_y, span_x)
            Buttons outside the visible grid are excluded.
        """
        occupied = set()
        placements = []
        self._forbidden_cells = set()  # cells blocked by out-of-bounds or colliding buttons

        configured = [b for b in buttons if b.config and is_real_button(b.config)]
        empty = [b for b in buttons if not (b.config and is_real_button(b.config))]

        # Pass 1: configured buttons at their (row, col), in (row, col) order.
        configured.sort(key=lambda b: (b.config.get('row', 0), b.config.get('col', 0)))
        for button in configured:
            self._place_configured(button, rows, occupied, placements)

        # Pass 2: fill the remaining holes with empty "Add" buttons.
        free_cells = [divmod(i, self.cols) for i in range(rows * self.cols) if divmod(i, self.cols) not in occupied]
        # Either list may run out first: leftover cells stay empty, leftover buttons unplaced.
        for button, (r, c) in zip(empty, free_cells, strict=False):
            placements.append((button, r, c, 1, 1))
            occupied.add((r, c))
        return placements

    def _place_configured(self, button, rows: int, occupied: set, placements: list) -> None:
        span_x = getattr(button, 'span_x', 1)
        span_y = getattr(button, 'span_y', 1)
        r = button.config.get('row', 0)
        c = button.config.get('col', 0)

        if c + span_x > self.cols or r + span_y > rows:
            # Partly outside the visible grid: not placed. Its cells inside the grid
            # become forbidden but stay unoccupied, so pass 2 fills them with empty
            # buttons that the dashboard shows as blocked instead of "Add".
            self._forbid_visible_cells(_cells(r, c, span_x, span_y), rows)
        elif self._can_place(r, c, span_x, span_y, rows, occupied):
            self._mark_occupied(r, c, span_x, span_y, occupied)
            placements.append((button, r, c, span_y, span_x))
        else:
            # Collision: its visible cells not taken by an earlier button are forbidden.
            self._forbid_visible_cells(_cells(r, c, span_x, span_y) - occupied, rows)

    def _forbid_visible_cells(self, cells: set, rows: int) -> None:
        self._forbidden_cells.update((r, c) for r, c in cells if r < rows and c < self.cols)

    def get_forbidden_cells(self) -> set:
        """Return the set of (row, col) cells that are forbidden (blocked by out-of-bounds buttons)."""
        return getattr(self, '_forbidden_cells', set())

    def find_first_empty_slot(self, buttons: list, rows: int, span_x: int = 1, span_y: int = 1) -> tuple:
        """Find the first visible (row, col) that is completely empty and fits the span.

        Returns (row, col) or (-1, -1) if no space.
        """
        occupied = set()

        for button in buttons:
            if not button.config:
                continue
            if not button.isVisible():
                continue

            r = button.config.get('row', 0)
            c = button.config.get('col', 0)
            sx = getattr(button, 'span_x', 1)
            sy = getattr(button, 'span_y', 1)

            self._mark_occupied(r, c, sx, sy, occupied)

        r, c = self._find_first_available(span_x, span_y, rows, occupied)
        if r is not None:
            return (r, c)
        return (-1, -1)

    def find_relocations(self, resizing_btn, new_span_x, new_span_y, all_buttons, rows):
        """
        Compute relocations needed when resizing_btn expands to (new_span_x, new_span_y).

        Returns:
            List of (button, new_row, new_col) tuples if all displaced buttons
            can be relocated, or None if the resize should be blocked.
        """
        if not resizing_btn.config:
            return []

        footprint = _cells(resizing_btn.config.get('row', 0), resizing_btn.config.get('col', 0), new_span_x, new_span_y)
        others = [b for b in all_buttons if b is not resizing_btn and b.config and is_real_button(b.config)]
        displaced = [b for b in others if _cells_of(b) & footprint]
        if not displaced:
            return []  # no conflicts

        # Everything that stays put, plus the resized button's new footprint.
        displaced_ids = {id(b) for b in displaced}
        occupied = set(footprint)
        for btn in others:
            if id(btn) not in displaced_ids:
                occupied |= _cells_of(btn)

        relocations = []
        for btn in displaced:
            span_x, span_y = _spans(btn)
            new_r, new_c = self._find_first_available(span_x, span_y, rows, occupied)
            if new_r is None:
                return None  # no room: block the resize
            self._mark_occupied(new_r, new_c, span_x, span_y, occupied)
            relocations.append((btn, new_r, new_c))
        return relocations

    # --- Helper Methods ---

    def _can_place(self, r, c, span_x, span_y, max_rows, occupied):
        """Check if a rect fits at (r,c)."""
        if c + span_x > self.cols:
            return False
        if r + span_y > max_rows:
            return False

        for dy in range(span_y):
            for dx in range(span_x):
                if (r + dy, c + dx) in occupied:
                    return False
        return True

    def _mark_occupied(self, r, c, span_x, span_y, occupied):
        """Mark cells as occupied."""
        for dy in range(span_y):
            for dx in range(span_x):
                occupied.add((r + dy, c + dx))

    def _find_first_available(self, span_x, span_y, max_rows, occupied):
        """Finds first (r, c) that fits, or (None, None)."""
        for r in range(max_rows):
            for c in range(self.cols):
                if self._can_place(r, c, span_x, span_y, max_rows, occupied):
                    return r, c
        return None, None


def is_real_button(cfg: dict) -> bool:
    """A tile bound to an entity, or the local pin-window tile; not an empty slot."""
    return bool(cfg.get('entity_id') or cfg.get('type') == 'pin_window')


def _cells(r: int, c: int, span_x: int, span_y: int) -> set:
    return {(r + dy, c + dx) for dy in range(span_y) for dx in range(span_x)}


def _spans(btn) -> tuple:
    """(span_x, span_y) of a button, preferring its live spans over the config."""
    return getattr(btn, 'span_x', btn.config.get('span_x', 1)), getattr(btn, 'span_y', btn.config.get('span_y', 1))


def _cells_of(btn) -> set:
    span_x, span_y = _spans(btn)
    return _cells(btn.config.get('row', 0), btn.config.get('col', 0), span_x, span_y)


def plan_move(configs: list, source: dict, target, tgt_row: int, tgt_col: int, cols: int, rows: int):
    """Plan dropping `source` onto the cell (tgt_row, tgt_col).

    If `target` (a button config occupying that cell) is given, the two buttons
    swap origins; otherwise `source` just moves. `configs` are all button configs
    on the current page.

    Returns {id(config): (row, col)} for the buttons that move, or None when the
    result would push a moved button outside the grid or onto another button
    (spans are respected, so a 2-wide tile can't land on its neighbour).
    """
    moves = {id(source): (tgt_row, tgt_col)}
    if target is not None and target is not source:
        moves[id(target)] = (source.get('row', 0), source.get('col', 0))

    engine = GridLayoutEngine(cols=cols)
    occupied = set()
    for cfg in configs:
        if id(cfg) in moves or not is_real_button(cfg):
            continue
        engine._mark_occupied(
            cfg.get('row', 0), cfg.get('col', 0), cfg.get('span_x', 1), cfg.get('span_y', 1), occupied
        )

    for cfg in (source, None if target is source else target):
        if cfg is None or id(cfg) not in moves:
            continue
        r, c = moves[id(cfg)]
        if not engine._can_place(r, c, cfg.get('span_x', 1), cfg.get('span_y', 1), rows, occupied):
            return None
        engine._mark_occupied(r, c, cfg.get('span_x', 1), cfg.get('span_y', 1), occupied)
    return moves
