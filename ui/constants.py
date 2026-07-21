"""
UI Constants
Shared dimensions and values for the Prism Desktop UI.
"""

# Grid Layout
DEFAULT_COLS = 4
GRID_MARGIN_LEFT = 12
GRID_MARGIN_RIGHT = 12
GRID_MARGIN_TOP = 12
GRID_MARGIN_BOTTOM = 8

# Button Dimensions
BUTTON_WIDTH = 90   # Standard single-column button width
BUTTON_HEIGHT = 80  # Standard grid button height
BUTTON_SPACING = 8  # Spacing between buttons

# Footer Dimensions
FOOTER_HEIGHT = 26
FOOTER_MARGIN_BOTTOM = 12
PAGE_INDICATOR_WIDTH = 72

# Animation Timings
ANIM_DURATION_ENTRANCE = 1500
ANIM_DURATION_HEIGHT = 400
ANIM_DURATION_WIDTH = 400
ANIM_DURATION_BORDER = 1500

# Glass UI live capture (self-limiting frame rate)
GLASS_MIN_INTERVAL_MS = 33   # Fastest refresh: ~30 FPS cap
GLASS_MAX_INTERVAL_MS = 200  # Slowest refresh floor: ~5 FPS on struggling machines
GLASS_WORK_BUDGET = 0.5      # Capture may use at most this fraction of a frame interval

# Root layout margins (each side)
ROOT_MARGIN = 10
RESIZE_MARGIN = 20 # Width of invisible resize handles (increased for better grip)


def calculate_width(cols: int) -> int:
    """Calculate the total window width for a given number of columns.
    
    Layout: root margin (10) + grid margin left (12) + buttons + spacing + grid margin right (12) + root margin (10)
    Buttons: cols * BUTTON_WIDTH + (cols - 1) * BUTTON_SPACING
    """
    inner = cols * BUTTON_WIDTH + (cols - 1) * BUTTON_SPACING
    return inner + GRID_MARGIN_LEFT + GRID_MARGIN_RIGHT + (ROOT_MARGIN * 2)


# Notification Banner
BANNER_HEIGHT = 46
BANNER_VERTICAL_MARGIN = 12
