"""
Hotkey strings in pynput's format, such as ``<ctrl>+<alt>+h``, ``<f1>`` or ``<ctrl>+<102>``.

One parser, formatter and matcher shared by the global shortcut listener, the
per-button shortcuts and the dashboard's own key handling. Keys are pynput key
objects; only their ``name``, ``char`` and ``vk`` attributes are read, so this
module imports neither pynput nor Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# pynput's order for modifiers in a hotkey string.
MODIFIER_ORDER = ('ctrl', 'alt', 'shift', 'cmd')
_MODIFIER_TOKENS = {f'<{m}>': m for m in MODIFIER_ORDER}
_VK_TOKEN = re.compile(r'<(\d+)>')


@dataclass(frozen=True)
class Shortcut:
    """A parsed hotkey: the modifiers that must be held and the key to press."""

    mods: frozenset[str]
    key: str | None  # 'h', '<f1>' or a raw virtual-key code like '<102>'

    @property
    def vk(self) -> int | None:
        """The virtual-key code when the key is written as one (``<102>``)."""
        match = _VK_TOKEN.fullmatch(self.key or '')
        return int(match.group(1)) if match else None


def parse_shortcut(text: str) -> Shortcut:
    """Split a hotkey string into modifiers and its (last) non-modifier key."""
    mods: set[str] = set()
    key = None
    for part in text.split('+'):
        part = part.strip()
        mod = _MODIFIER_TOKENS.get(part)
        if mod:
            mods.add(mod)
        elif part:
            key = part
    return Shortcut(frozenset(mods), key)


def modifier_name(key) -> str | None:
    """'ctrl', 'alt', 'shift' or 'cmd' for a modifier key (either side), else None."""
    name = getattr(key, 'name', None) or ''
    if name == 'win':
        return 'cmd'
    return next((mod for mod in MODIFIER_ORDER if name.startswith(mod)), None)


def _is_standard_vk(vk) -> bool:
    """Letter and digit VKs, where chr(vk) is the key's character.

    Numpad VKs (96-105) fall in the same byte range, but chr() gives the wrong
    character for them.
    """
    return bool(vk) and (48 <= vk <= 57 or 65 <= vk <= 90)


def key_token(key) -> str:
    """The hotkey-string token for a non-modifier key."""
    vk = getattr(key, 'vk', None)
    char = getattr(key, 'char', None)
    printable = bool(char) and ord(char) >= 32
    if _is_standard_vk(vk):
        return char.lower() if printable else chr(vk).lower()
    if printable:
        # Non-standard VK with a char (e.g. numpad 6 -> VK 102, char '6'). Prefer the
        # VK form so the listener matches by VK even when held modifiers suppress
        # the char during the trigger press.
        return f'<{vk}>' if vk else char.lower()
    if hasattr(key, 'name'):
        return f'<{key.name}>'
    return str(key)


def format_combo(keys) -> str | None:
    """Hotkey string for keys held together, or None while only modifiers are held.

    Modifiers come first in pynput order; with several other keys held, the
    alphabetically first one is used so the result is deterministic.
    """
    held_mods = {modifier_name(k) for k in keys}
    tokens = sorted(key_token(k) for k in keys if modifier_name(k) is None)
    if not tokens or not tokens[0]:
        return None
    return '+'.join([f'<{m}>' for m in MODIFIER_ORDER if m in held_mods] + [tokens[0]])


def key_matches(key, token: str) -> bool:
    """Whether a pressed key is the given hotkey token."""
    vk = getattr(key, 'vk', None)
    match = _VK_TOKEN.fullmatch(token)
    if match:
        return vk == int(match.group(1))
    if len(token) == 1:
        char = getattr(key, 'char', None)
        if char and char.lower() == token.lower():
            return True
        return _is_standard_vk(vk) and chr(vk).lower() == token.lower()
    if token.startswith('<') and token.endswith('>'):
        return getattr(key, 'name', None) == token[1:-1]
    return False
