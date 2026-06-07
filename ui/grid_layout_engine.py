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
        self._forbidden_cells = set()  # Track cells blocked by out-of-bounds buttons
        
        # Separate configured buttons from empty Add buttons
        configured_buttons = []
        empty_buttons = []
        
        for button in buttons:
            if button.config and (button.config.get('entity_id') or button.config.get('type') == 'pin_window'):
                configured_buttons.append(button)
            else:
                empty_buttons.append(button)
        
        # Sort configured buttons by (row, col) for deterministic placement
        configured_buttons.sort(key=lambda b: (
            b.config.get('row', 0),
            b.config.get('col', 0)
        ))
        
        # Pass 1: Place configured buttons at their (row, col)
        for button in configured_buttons:
            span_x = getattr(button, 'span_x', 1)
            span_y = getattr(button, 'span_y', 1)
            
            r = button.config.get('row', 0)
            c = button.config.get('col', 0)
            
            # Visibility check: skip if ANY part falls outside current grid
            if c + span_x > self.cols or r + span_y > rows:
                # Button is outside visible area — don't place it.
                # Mark the cells that ARE within the grid as forbidden
                # so they show a blocked indicator instead of an Add button.
                for dy in range(span_y):
                    for dx in range(span_x):
                        cell_r = r + dy
                        cell_c = c + dx
                        if cell_r < rows and cell_c < self.cols:
                            self._forbidden_cells.add((cell_r, cell_c))
                            # Do NOT mark as occupied, so Pass 2 fills it with an empty button
                            # which we then convert to 'forbidden' in the dashboard.
                continue
            
            # Check if cells are available
            if self._can_place(r, c, span_x, span_y, rows, occupied):
                self._mark_occupied(r, c, span_x, span_y, occupied)
                placements.append((button, r, c, span_y, span_x))
            else:
                # Collision detected!
                # Mark visible parts of THIS button as forbidden if they aren't occupied
                for dy in range(span_y):
                    for dx in range(span_x):
                        cell_r = r + dy
                        cell_c = c + dx
                        # If cell is valid and NOT occupied by a higher-priority button
                        if cell_r < rows and cell_c < self.cols:
                            if (cell_r, cell_c) not in occupied:
                                self._forbidden_cells.add((cell_r, cell_c))
        
        # Pass 2: Fill empty holes with "Add" buttons
        empty_idx = 0
        total_cells = rows * self.cols
        
        for i in range(total_cells):
            r = i // self.cols
            c = i % self.cols
            
            if (r, c) not in occupied:
                if empty_idx < len(empty_buttons):
                    btn = empty_buttons[empty_idx]
                    empty_idx += 1
                    
                    placements.append((btn, r, c, 1, 1))
                    occupied.add((r, c))
                    
        return placements

    def get_forbidden_cells(self) -> set:
        """Return the set of (row, col) cells that are forbidden (blocked by out-of-bounds buttons)."""
        return getattr(self, '_forbidden_cells', set())

    def find_first_empty_slot(self, buttons: list, rows: int, span_x: int = 1, span_y: int = 1) -> tuple:
        """Find the first visible (row, col) that is completely empty and fits the span.
        
        Returns (row, col) or (-1, -1) if no space.
        """
        occupied = set()
        
        for button in buttons:
            if not button.config: continue 
            if not button.isVisible(): continue
            
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
        
        src_r = resizing_btn.config.get('row', 0)
        src_c = resizing_btn.config.get('col', 0)
        
        # 1. Compute the footprint of the resized button
        new_footprint = set()
        for dy in range(new_span_y):
            for dx in range(new_span_x):
                new_footprint.add((src_r + dy, src_c + dx))
        
        # 2. Identify displaced buttons (configured buttons whose cells overlap)
        displaced = []
        for btn in all_buttons:
            if btn is resizing_btn:
                continue
            if not btn.config or not (btn.config.get('entity_id') or btn.config.get('type') == 'pin_window'):
                continue

            br = btn.config.get('row', 0)
            bc = btn.config.get('col', 0)
            bsx = getattr(btn, 'span_x', btn.config.get('span_x', 1))
            bsy = getattr(btn, 'span_y', btn.config.get('span_y', 1))

            # Check if any cell of this button overlaps the new footprint
            overlaps = False
            for dy in range(bsy):
                for dx in range(bsx):
                    if (br + dy, bc + dx) in new_footprint:
                        overlaps = True
                        break
                if overlaps:
                    break
            
            if overlaps:
                displaced.append(btn)
        
        if not displaced:
            return []  # No conflicts
        
        # 3. Build occupied set from all non-displaced configured buttons + resized button
        occupied = set()
        
        # Mark resized button's new footprint
        for cell in new_footprint:
            occupied.add(cell)
        
        # Mark all other configured buttons that are NOT displaced
        displaced_set = set(id(b) for b in displaced)
        for btn in all_buttons:
            if btn is resizing_btn:
                continue
            if not btn.config or not (btn.config.get('entity_id') or btn.config.get('type') == 'pin_window'):
                continue
            if id(btn) in displaced_set:
                continue
            
            br = btn.config.get('row', 0)
            bc = btn.config.get('col', 0)
            bsx = getattr(btn, 'span_x', btn.config.get('span_x', 1))
            bsy = getattr(btn, 'span_y', btn.config.get('span_y', 1))
            self._mark_occupied(br, bc, bsx, bsy, occupied)
        
        # 4. Try to relocate each displaced button
        relocations = []
        for btn in displaced:
            bsx = getattr(btn, 'span_x', btn.config.get('span_x', 1))
            bsy = getattr(btn, 'span_y', btn.config.get('span_y', 1))
            
            new_r, new_c = self._find_first_available(bsx, bsy, rows, occupied)
            if new_r is None:
                # No room — block the resize
                return None
            
            self._mark_occupied(new_r, new_c, bsx, bsy, occupied)
            relocations.append((btn, new_r, new_c))
        
        return relocations

    # --- Helper Methods ---

    def _can_place(self, r, c, span_x, span_y, max_rows, occupied):
        """Check if a rect fits at (r,c)."""
        if c + span_x > self.cols: return False
        if r + span_y > max_rows: return False
        
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
