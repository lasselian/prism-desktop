"""
Shared Design Constants
Centralized source of truth for typography, dimensions, and styling.
"""

from core.utils import SYSTEM_FONT

class Typography:
    """Font families, sizes, and weights."""
    
    # Fonts
    FONT_FAMILY_MAIN = SYSTEM_FONT
    FONT_FAMILY_UI = "'Segoe UI', 'Ubuntu', 'Noto Sans', 'DejaVu Sans', sans-serif"
    
    # Font Sizes
    SIZE_HEADER = "18px"
    SIZE_BODY = "13px"
    SIZE_SMALL = "11px"
    
    # Dashboard Button Specific
    SIZE_BUTTON_VALUE = "20px"
    SIZE_BUTTON_LABEL = "11px"
    SIZE_BUTTON_ICON_LARGE = "26px"
    SIZE_BUTTON_ICON_SMALL = "20px"
    
    # Font Weights
    WEIGHT_REGULAR = "400"
    WEIGHT_MEDIUM = "500"
    WEIGHT_SEMIBOLD = "600"
    WEIGHT_BOLD = "700"
    
class Dimensions:
    """Spacing, border radii, and dimensions."""
    
    # Border Radii
    RADIUS_SMALL = "4px"
    RADIUS_MEDIUM = "6px"
    RADIUS_XLARGE = "12px"  # Standard for dashboard buttons / container
    
    # Padding
    PADDING_SMALL = "4px"
    PADDING_MEDIUM = "8px"
    PADDING_LARGE = "16px"
    
