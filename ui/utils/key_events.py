"""Match Qt key events against pynput-style hotkey strings (see core.shortcuts)."""

from PyQt6.QtCore import Qt

from core.shortcuts import parse_shortcut

# Named pynput keys and their Qt equivalents.
_SPECIAL_KEYS = {
    'esc': Qt.Key.Key_Escape,
    'space': Qt.Key.Key_Space,
    'enter': Qt.Key.Key_Return,
    'backspace': Qt.Key.Key_Backspace,
    'tab': Qt.Key.Key_Tab,
    'up': Qt.Key.Key_Up,
    'down': Qt.Key.Key_Down,
    'left': Qt.Key.Key_Left,
    'right': Qt.Key.Key_Right,
    'delete': Qt.Key.Key_Delete,
    'home': Qt.Key.Key_Home,
    'end': Qt.Key.Key_End,
    'page_up': Qt.Key.Key_PageUp,
    'page_down': Qt.Key.Key_PageDown,
    **{f'f{n}': getattr(Qt.Key, f'Key_F{n}') for n in range(1, 13)},
}

# Modifiers compared against the event; cmd/win is not checked.
_MODIFIER_FLAGS = (
    ('ctrl', Qt.KeyboardModifier.ControlModifier),
    ('alt', Qt.KeyboardModifier.AltModifier),
    ('shift', Qt.KeyboardModifier.ShiftModifier),
)


def event_matches_shortcut(event, shortcut_str: str) -> bool:
    """Whether a QKeyEvent is the given hotkey (e.g. '<ctrl>+<alt>+h')."""
    if not shortcut_str:
        return False
    shortcut = parse_shortcut(shortcut_str)
    modifiers = event.modifiers()
    if any((mod in shortcut.mods) != bool(modifiers & flag) for mod, flag in _MODIFIER_FLAGS):
        return False
    if not shortcut.key:
        return False  # modifiers only
    if len(shortcut.key) == 1:
        return _char_matches(event, shortcut.key)
    return _named_key_matches(event, shortcut.key)


def _char_matches(event, char: str) -> bool:
    # text() follows the keyboard layout, but held modifiers can turn it into a
    # control character (Ctrl+A gives '\x01'), so fall back to the key code.
    text = event.text().lower()
    if text and text == char:
        return True
    key = event.key()
    return 32 <= key <= 126 and chr(key).lower() == char


def _named_key_matches(event, token: str) -> bool:
    if not (token.startswith('<') and token.endswith('>')):
        return False
    name = token[1:-1].lower()
    # A raw VK code, recorded from a key whose char was suppressed by
    # modifiers (e.g. numpad keys): compare with the platform's native VK.
    if name.isdigit() and event.nativeVirtualKey() == int(name):
        return True
    return _SPECIAL_KEYS.get(name) == event.key()
