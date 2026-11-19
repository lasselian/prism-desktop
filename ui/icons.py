"""
MDI Icons Helper Module
Material Design Icons via icon font.
"""

import json
import re
import urllib.request
import ssl
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QFont
from core.utils import get_resource_path, get_config_path

# Icon font name (after loading)
MDI_FONT_FAMILY = "Material Design Icons"

# Flag to track if font is loaded
_font_loaded = False


class _IconSignals(QObject):
    mapping_loaded = pyqtSignal()


icon_signals = _IconSignals()


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
    LIGHTBULB = "\U000f0335"  # mdi-lightbulb
    LIGHTBULB_OFF = "\U000f0336"  # mdi-lightbulb-off
    LIGHTBULB_OUTLINE = "\U000f0336"  # mdi-lightbulb-outline
    LAMP = "\U000f06b5"  # mdi-lamp

    # Covers/Blinds/Curtains
    BLINDS = "\U000f00ce"  # mdi-blinds
    BLINDS_OPEN = "\U000f1011"  # mdi-blinds-open
    CURTAINS = "\U000f1846"  # mdi-curtains
    CURTAINS_CLOSED = "\U000f1847"  # mdi-curtains-closed
    WINDOW_SHUTTER = "\U000f111c"  # mdi-window-shutter
    WINDOW_SHUTTER_OPEN = "\U000f111d"  # mdi-window-shutter-open

    # Climate/Temperature
    THERMOMETER = "\U000f050f"  # mdi-thermometer
    THERMOMETER_HIGH = "\U000f10c2"  # mdi-thermometer-high
    THERMOMETER_LOW = "\U000f10c3"  # mdi-thermometer-low
    SNOWFLAKE = "\U000f0717"  # mdi-snowflake (cooling)
    FIRE = "\U000f0238"  # mdi-fire (heating)
    FAN = "\U000f0210"  # mdi-fan
    HVAC = "\U000f1352"  # mdi-hvac

    # Sensors
    GAUGE = "\U000f029a"  # mdi-gauge
    FLASH = "\U000f0241"  # mdi-flash (power)
    FLASH_OUTLINE = "\U000f0242"  # mdi-flash-outline
    WEATHER_PARTLY_CLOUDY = "\U000f0595"  # mdi-weather-partly-cloudy

    # Common
    PLUS = "\U000f0415"  # mdi-plus
    PLUS_CIRCLE = "\U000f0417"  # mdi-plus-circle
    COG = "\U000f0493"  # mdi-cog (settings)
    CHECK = "\U000f012c"  # mdi-check
    CLOSE = "\U000f0156"  # mdi-close
    POWER = "\U000f0425"  # mdi-power
    POWER_PLUG = "\U000f06a5"  # mdi-power-plug
    CHEVRON_UP = "\U000f0143"  # mdi-chevron-up
    CHEVRON_DOWN = "\U000f0140"  # mdi-chevron-down
    CHEVRON_LEFT = "\U000f0141"  # mdi-chevron-left
    CHEVRON_RIGHT = "\U000f0142"  # mdi-chevron-right

    # Switches
    TOGGLE_SWITCH = "\U000f0521"  # mdi-toggle-switch
    TOGGLE_SWITCH_OFF = "\U000f0522"  # mdi-toggle-switch-off
    SWITCH = "\U000f07e2"  # same as lightbulb, use for generic

    # Scripts
    SCRIPT = "\U000f0488"  # mdi-script-text-outline
    PALETTE_OUTLINE = "\U000f0e0c"  # mdi-palette-outline (Corrected)
    SCENE_THEME = PALETTE_OUTLINE  # Default scene icon

    # Automation
    AUTOMATION = "\U000f0469"  # mdi-robot

    # Empty/placeholder
    CIRCLE_OUTLINE = "\U000f0766"  # mdi-circle-outline
    CHECKBOX_BLANK_CIRCLE_OUTLINE = "\U000f0130"

    # Forbidden / blocked
    FORBIDDEN = "\U000f0119"  # mdi-block-helper

    # Weather
    WEATHER_NIGHT = "\U000f0594"  # mdi-weather-night
    WEATHER_CLOUDY = "\U000f0590"  # mdi-weather-cloudy
    WEATHER_FOG = "\U000f0591"  # mdi-weather-fog
    WEATHER_HAIL = "\U000f0592"  # mdi-weather-hail
    WEATHER_LIGHTNING = "\U000f0593"  # mdi-weather-lightning
    WEATHER_LIGHTNING_RAINY = "\U000f067e"  # mdi-weather-lightning-rainy
    # WEATHER_PARTLY_CLOUDY already defined above
    WEATHER_POURING = "\U000f0598"  # mdi-weather-pouring
    WEATHER_RAINY = "\U000f0596"  # mdi-weather-rainy
    WEATHER_SNOWY = "\U000f0597"  # mdi-weather-snowy
    WEATHER_SNOWY_RAINY = "\U000f067f"  # mdi-weather-snowy-rainy
    WEATHER_SUNNY = "\U000f0599"  # mdi-weather-sunny
    WEATHER_WINDY = "\U000f059d"  # mdi-weather-windy
    WEATHER_WINDY_VARIANT = "\U000f059e"  # mdi-weather-windy-variant
    ALERT_CIRCLE = "\U000f0028"  # mdi-alert-circle
    ALERT_CIRCLE_OUTLINE = "\U000f05d6"  # mdi-alert-circle-outline

    # Camera
    CAMERA = "\U000f0024"  # mdi-camera
    VIDEO = "\U000f0567"  # mdi-video
    VIDEO_OFF = "\U000f0568"  # mdi-video-off

    # Media
    PLAY = "\U000f040a"  # mdi-play
    PAUSE = "\U000f03e4"  # mdi-pause
    STOP = "\U000f04db"  # mdi-stop
    NEXT = "\U000f04ad"  # mdi-skip-next
    PREVIOUS = "\U000f04ae"  # mdi-skip-previous
    PLAY_PAUSE = "\U000f040e"  # mdi-play-pause

    # Security / Lock
    LOCK = "\U000f033e"  # mdi-lock
    LOCK_OPEN = "\U000f0341"  # mdi-lock-open

    # 3D Printer
    PRINTER_3D = "\U000f042b"  # mdi-printer-3d
    PRINTER_3D_NOZZLE = "\U000f0e46"  # mdi-printer-3d-nozzle
    RADIATOR = "\U000f0445"  # mdi-radiator (used for heat bed)

    # Lawn Mower
    MOWER = "\U000f11f7"  # mdi-robot-mower

    # Vacuum
    VACUUM = "\U000f097c"  # mdi-robot-vacuum

    # Network / Connection
    LAN_CONNECT = "\U000f0318"  # mdi-lan-connect
    LAN_DISCONNECT = "\U000f0319"  # mdi-lan-disconnect

    # Pin / window pinning
    PIN = "\U000f0403"  # mdi-pin

    # Support
    COFFEE = "\U000f0176"  # mdi-coffee

    # Editing / management
    PENCIL_OUTLINE = "\U000f03eb"  # mdi-pencil (F03EB; outline variant not in bundled font)
    TRASH_CAN_OUTLINE = "\U000f0a79"  # mdi-trash-can-outline


# --- MDI Mapping Logic ---

_mdi_cache = {}
MDI_CSS_URL = "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/master/css/materialdesignicons.css"
_is_fetching = False


def fetch_mdi_mapping_worker():
    """Background worker to fetch MDI mapping."""
    global _mdi_cache, _is_fetching
    try:
        print("Fetching MDI mapping in background...")
        # Verified TLS; the CA bundle comes from configure_ssl() (SSL_CERT_FILE)
        context = ssl.create_default_context()
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
        icon_signals.mapping_loaded.emit()
    except Exception as e:
        print(f"Failed to fetch MDI mapping: {e}")
    finally:
        _is_fetching = False


def get_icon(name: str) -> str:
    """
    Get unicode character for mdi icon name (e.g. 'mdi:home' or 'home').
    Non-blocking: Returns placeholder if not loaded, fetches in background.
    """
    if not name:
        return Icons.CIRCLE_OUTLINE
    if name.startswith("mdi:"):
        name = name[4:]
    if not _mdi_cache and not _load_saved_mapping():
        _fetch_mapping_in_background()
        return Icons.CIRCLE_OUTLINE  # placeholder until the mapping arrives
    return _mdi_cache.get(name, Icons.CIRCLE_OUTLINE)


def _read_mapping(path) -> dict | None:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _load_saved_mapping() -> bool:
    """Fill the cache from the bundled mapping, else from one fetched earlier."""
    global _mdi_cache
    bundled_file = get_resource_path("mdi_mapping.json")  # read-only in the exe
    mapping = _read_mapping(bundled_file) if bundled_file.exists() else None
    if mapping is None:
        cached_file = get_config_path("mdi_mapping.json")
        if cached_file.exists() and cached_file != bundled_file:
            mapping = _read_mapping(cached_file)
    if mapping:
        _mdi_cache = mapping
    return bool(_mdi_cache)


def _fetch_mapping_in_background() -> None:
    global _is_fetching
    if _is_fetching:
        return
    import threading

    _is_fetching = True
    threading.Thread(target=fetch_mdi_mapping_worker, daemon=True).start()


def get_icon_for_type(entity_type: str, state: str = "off") -> str:
    """Get the appropriate icon for an entity type and state."""
    icon = _TYPE_ICONS.get(entity_type)
    if icon is None:
        return Icons.PLUS_CIRCLE  # default / empty slot
    return icon(state) if callable(icon) else icon


# Alarm control panel: HA state -> mdi icon name (looked up via get_icon)
_ALARM_ICON_MAP = {
    "disarmed": "shield-off-outline",
    "armed_home": "shield-home",
    "armed_away": "shield-lock",
    "armed_night": "shield-moon",
    "armed_vacation": "shield-airplane",
    "armed_custom_bypass": "shield-half-full",
    "arming": "shield-alert-outline",
    "pending": "shield-alert-outline",
    "disarming": "shield-off-outline",
    "triggered": "shield-alert",
}

_CLIMATE_ICONS = {"heat": Icons.FIRE, "cool": Icons.SNOWFLAKE, "on": Icons.THERMOMETER_HIGH}


def _lock_icon(state: str) -> str:
    if state == "jammed":
        return get_icon("lock-alert")
    return Icons.LOCK if state == "locked" else Icons.LOCK_OPEN


# Entity type -> icon, or a function of the state for types whose icon changes.
_TYPE_ICONS = {
    "switch": lambda state: Icons.LIGHTBULB if state == "on" else Icons.LIGHTBULB_OFF,
    "script": Icons.SCRIPT,
    "automation": Icons.AUTOMATION,
    "curtain": lambda state: Icons.BLINDS_OPEN if state == "open" else Icons.BLINDS,
    "climate": lambda state: _CLIMATE_ICONS.get(state, Icons.THERMOMETER),
    "widget": Icons.GAUGE,
    "fan": Icons.FAN,
    "lock": _lock_icon,
    "alarm_control_panel": lambda state: get_icon(_ALARM_ICON_MAP.get(state, "shield-off-outline")),
    "sun": Icons.WEATHER_SUNNY,  # MDI fallback; the painter draws the real sun tile
}
