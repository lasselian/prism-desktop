"""
MDI Icons Helper Module
Material Design Icons via icon font.
"""

from pathlib import Path
import json
import re
import urllib.request
import ssl
import certifi
from PyQt6.QtGui import QFontDatabase, QFont
from utils import get_resource_path, get_config_path

# Icon font name (after loading)
MDI_FONT_FAMILY = "Material Design Icons"

# Flag to track if font is loaded
_font_loaded = False

def load_mdi_font():
    """Load the MDI font into the application. Call once at startup."""
    global _font_loaded
    if _font_loaded:
        return True
    
    font_path = get_resource_path("materialdesignicons-webfont.ttf")
    if not font_path.exists():
        print(f"MDI font not found at {font_path}")
        return False
    
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id == -1:
        print("Failed to load MDI font")
        return False
    
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        global MDI_FONT_FAMILY
        MDI_FONT_FAMILY = families[0]
        print(f"Loaded MDI font: {MDI_FONT_FAMILY}")
    
    _font_loaded = True
    return True


def get_mdi_font(size: int = 24) -> QFont:
    """Get a QFont configured for MDI icons."""
    return QFont(MDI_FONT_FAMILY, size)


# MDI Icon Unicode Codepoints
# Reference: https://pictogrammers.com/library/mdi/
# Format: icon_name -> unicode character

class Icons:
    """MDI icon unicode characters (Common ones)."""
    
    # Lights
    LIGHTBULB = "\U000F0335"           # mdi-lightbulb
    LIGHTBULB_OFF = "\U000F0336"       # mdi-lightbulb-off  
    LIGHTBULB_OUTLINE = "\U000F0336"   # mdi-lightbulb-outline
    LAMP = "\U000F06B5"                # mdi-lamp
    
    # Covers/Blinds/Curtains
    BLINDS = "\U000F00CE"              # mdi-blinds
    BLINDS_OPEN = "\U000F1011"         # mdi-blinds-open
    CURTAINS = "\U000F1846"            # mdi-curtains
    CURTAINS_CLOSED = "\U000F1847"     # mdi-curtains-closed
    WINDOW_SHUTTER = "\U000F111C"      # mdi-window-shutter
    WINDOW_SHUTTER_OPEN = "\U000F111D" # mdi-window-shutter-open
    
    # Climate/Temperature
    THERMOMETER = "\U000F050F"         # mdi-thermometer
    THERMOMETER_HIGH = "\U000F10C2"    # mdi-thermometer-high
    THERMOMETER_LOW = "\U000F10C3"     # mdi-thermometer-low
    SNOWFLAKE = "\U000F0717"           # mdi-snowflake (cooling)
    FIRE = "\U000F0238"                # mdi-fire (heating)
    FAN = "\U000F0210"                 # mdi-fan
    HVAC = "\U000F1352"                # mdi-hvac
    
    # Sensors
    GAUGE = "\U000F029A"               # mdi-gauge
    FLASH = "\U000F0241"               # mdi-flash (power)
    FLASH_OUTLINE = "\U000F0242"       # mdi-flash-outline
    WEATHER_PARTLY_CLOUDY = "\U000F0595"  # mdi-weather-partly-cloudy
    
    # Common
    PLUS = "\U000F0415"                # mdi-plus
    PLUS_CIRCLE = "\U000F0417"         # mdi-plus-circle
    COG = "\U000F0493"                 # mdi-cog (settings)
    CHECK = "\U000F012C"               # mdi-check
    CLOSE = "\U000F0156"               # mdi-close
    POWER = "\U000F0425"               # mdi-power
    POWER_PLUG = "\U000F06A5"          # mdi-power-plug
    
    # Switches
    TOGGLE_SWITCH = "\U000F0521"       # mdi-toggle-switch
    TOGGLE_SWITCH_OFF = "\U000F0522"   # mdi-toggle-switch-off
    SWITCH = "\U000F07E2"              # same as lightbulb, use for generic
    
    # Scripts
    SCRIPT = "\U000F0488"              # mdi-script-text-outline
    PALETTE_OUTLINE = "\U000F0E0C"     # mdi-palette-outline (Corrected)
    SCENE_THEME = PALETTE_OUTLINE      # Default scene icon
    
    # Image/Camera
    IMAGE = "\U000F0313"               # mdi-image
    CAMERA = "\U000F0100"              # mdi-camera
    
    # Empty/placeholder
    CIRCLE_OUTLINE = "\U000F0766"      # mdi-circle-outline
    CHECKBOX_BLANK_CIRCLE_OUTLINE = "\U000F0130"


# --- MDI Mapping Logic ---

# --- MDI Mapping Logic ---

_mdi_cache = {}
MDI_CSS_URL = "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/master/css/materialdesignicons.css"
_is_fetching = False

def fetch_mdi_mapping_worker():
    """Background worker to fetch MDI mapping."""
    global _mdi_cache, _is_fetching
    try:
        print("Fetching MDI mapping in background...")
        # Use verified SSL context with certifi CA bundle
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(MDI_CSS_URL, context=context, timeout=30) as response:
            css_content = response.read().decode('utf-8')
            
        mapping = {}
        # Pattern: .mdi-name::before { content: "\F123"; }
        pattern = re.compile(r'\.mdi-([a-z0-9-]+)::?before\s*\{\s*content:\s*"\\([0-9a-f]+)"', re.IGNORECASE)
        
        for match in pattern.finditer(css_content):
            name = match.group(1)
            hex_val = match.group(2)
            mapping[name] = chr(int(hex_val, 16))
            
        # Save to file
        mapping_file = get_config_path("mdi_mapping.json")
        with open(mapping_file, 'w', encoding='utf-8') as f:
            json.dump(mapping, f)
            
        print(f"Saved {len(mapping)} icons to {mapping_file}")
        _mdi_cache = mapping
    except Exception as e:
        print(f"Failed to fetch MDI mapping: {e}")
    finally:
        _is_fetching = False

def get_icon(name: str) -> str:
    """
    Get unicode character for mdi icon name (e.g. 'mdi:home' or 'home').
    Non-blocking: Returns placeholder if not loaded, fetches in background.
    """
    if not name: return Icons.CIRCLE_OUTLINE
    
    # Normalize name
    if name.startswith("mdi:"):
        name = name[4:]
    
    global _mdi_cache, _is_fetching
    if not _mdi_cache:
        # Load from file first
        # 1. Try bundled resource (read-only in exe)
        bundled_file = get_resource_path("mdi_mapping.json")
        loaded = False
        
        if bundled_file.exists():
            try:
                with open(bundled_file, 'r', encoding='utf-8') as f:
                    _mdi_cache = json.load(f)
                    loaded = True
            except:
                pass
        
        # 2. If not bundled, try config path (cached file)
        if not loaded:
            cached_file = get_config_path("mdi_mapping.json")
            if cached_file.exists() and cached_file != bundled_file:
                try:
                    with open(cached_file, 'r', encoding='utf-8') as f:
                        _mdi_cache = json.load(f)
                except:
                    pass
        
        # If still empty and not fetching, fetch in background
        if not _mdi_cache:
            if not _is_fetching:
                import threading
                _is_fetching = True
                threading.Thread(target=fetch_mdi_mapping_worker, daemon=True).start()
            
            # Return placeholder while fetching
            return Icons.CIRCLE_OUTLINE
            
    return _mdi_cache.get(name, Icons.CIRCLE_OUTLINE)


# Convenience function to get icon for entity type
def get_icon_for_type(entity_type: str, state: str = "off") -> str:
    """Get the appropriate icon for an entity type and state."""
    
    if entity_type == "switch":
        return Icons.LIGHTBULB if state == "on" else Icons.LIGHTBULB_OFF
    
    elif entity_type == "script":
        return Icons.SCRIPT
    
    elif entity_type == "curtain":
        return Icons.BLINDS_OPEN if state == "open" else Icons.BLINDS
    
    elif entity_type == "climate":
        if state == "heat":
            return Icons.FIRE
        elif state == "cool":
            return Icons.SNOWFLAKE
        elif state == "on":
            return Icons.THERMOMETER_HIGH
        else:
            return Icons.THERMOMETER
    
    elif entity_type == "widget":
        return Icons.GAUGE
    
    # Default/empty
    return Icons.PLUS_CIRCLE
