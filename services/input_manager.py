"""
Input Manager for Prism Desktop
Handles global keyboard shortcuts and mouse button triggers using pynput.
Includes ButtonShortcutManager for per-button global shortcuts active while in tray.
"""

import sys
import logging
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from pynput import keyboard, mouse
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# macOS crash fix: Monkey-patch pynput's Carbon keyboard layout queries.
#
# On modern macOS (14+), the Carbon/HIToolbox function
# TISCopyCurrentKeyboardInputSource (and friends) MUST be called on the main
# dispatch queue.  pynput calls these from background listener threads via
# keycode_context(), which triggers:
#   _dispatch_assert_queue_fail → SIGILL (Illegal instruction: 4)
#
# The fix:
#   1. Call keycode_context() once NOW on the main thread and cache its result
#      (keyboard_type, layout_data).
#   2. Replace pynput._util.darwin.keycode_context with a version that always
#      yields the cached tuple, so background threads never touch Carbon TIS.
#   3. Also replace pynput.keyboard._darwin.keycode_context (the imported
#      reference) so Listener._run() uses the cached version too.
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    try:
        import contextlib
        import pynput._util.darwin as _pynput_darwin
        import pynput.keyboard._darwin as _pynput_kb_darwin

        # Step 1: Call the ORIGINAL keycode_context on the main thread to
        # obtain the keyboard layout data (keyboard_type, layout_data).
        with _pynput_darwin.keycode_context() as _cached_ctx:
            _CACHED_KEYCODE_CONTEXT = _cached_ctx  # (keyboard_type, layout_data)

        # Step 2: Build a replacement that just yields the cached tuple.
        @contextlib.contextmanager
        def _cached_keycode_context():
            """Thread-safe replacement for pynput's keycode_context().
            Always yields the keyboard layout captured on the main thread
            so background listener threads never call Carbon TIS functions."""
            yield _CACHED_KEYCODE_CONTEXT

        # Step 3: Patch both the module-level function AND the imported
        # reference inside pynput.keyboard._darwin so every code path
        # (Listener._run, get_unicode_to_keycode_map, etc.) uses the cache.
        _pynput_darwin.keycode_context = _cached_keycode_context
        _pynput_kb_darwin.keycode_context = _cached_keycode_context

        # Also pre-warm the unicode→keycode map (used by Controller.__init__)
        _pynput_darwin.get_unicode_to_keycode_map()

        logger.info("[InputManager] macOS: Cached keyboard layout from main thread (pynput SIGILL fix applied).")
    except Exception as _e:
        logger.warning(f"[InputManager] Could not apply macOS pynput fix: {_e}")

_WAYLAND_PORTAL_AVAILABLE = False
try:
    from services.wayland_global_shortcut import (
        WaylandGlobalShortcut,
        is_wayland_session,
        supports_wayland_global_shortcuts,
    )
    _WAYLAND_PORTAL_AVAILABLE = True
except Exception:
    WaylandGlobalShortcut = None

    def is_wayland_session():
        return False

    def supports_wayland_global_shortcuts():
        return False

class InputManager(QObject):
    """
    Manages global input listeners for keyboard and mouse.
    executes in its own thread to avoid blocking GUI.
    """
    
    triggered = pyqtSignal()
    recorded = pyqtSignal(dict)  # {type: 'keyboard'|'mouse', value: str}
    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self._keyboard_listener = None
        self._mouse_listener = None
        self._current_shortcut = None
        self._is_recording = False
        self._pressed_keys = set()
        self._wayland_shortcut = None
        
        # Health check timer - detect silently dead listener threads
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(30000)  # Check every 30 seconds
        self._health_timer.timeout.connect(self._check_listener_alive)
        
        # Mouse button mapping - x1/x2 (side buttons) may not exist on Linux
        self._mouse_map = {
            mouse.Button.left: "Button.left",
            mouse.Button.right: "Button.right",
            mouse.Button.middle: "Button.middle",
        }
        # Add side buttons if available (Windows only)
        if hasattr(mouse.Button, 'x1'):
            self._mouse_map[mouse.Button.x1] = "Button.x1"  # Back
        if hasattr(mouse.Button, 'x2'):
            self._mouse_map[mouse.Button.x2] = "Button.x2"  # Forward

    def update_shortcut(self, config: dict):
        """Update the active shortcut from config."""
        self.stop_listening()
        self._current_shortcut = config
        
        if not config:
            self._health_timer.stop()
            return

        print(f"InputManager: Setting shortcut to {config}")

        if self._is_unsupported_wayland_keyboard_shortcut():
            print("InputManager: Global keyboard shortcut disabled on this Wayland desktop")
            return
        
        if config.get('type') == 'keyboard':
            self._start_keyboard_listener()
        elif config.get('type') == 'mouse':
            self._start_mouse_listener()
        
        # Start health monitoring
        self._health_timer.start()
    
    def restore_shortcut(self):
        """Restore the previously configured shortcut listener.
        Call this after recording ends or is cancelled to bring back the hotkey."""
        self.stop_listening()  # emits recording_stopped if was recording
        if not self._current_shortcut:
            return
        print(f"InputManager: Restoring shortcut {self._current_shortcut}")
        if self._is_unsupported_wayland_keyboard_shortcut():
            print("InputManager: Global keyboard shortcut disabled on this Wayland desktop")
            return
        if self._current_shortcut.get('type') == 'keyboard':
            self._start_keyboard_listener()
        elif self._current_shortcut.get('type') == 'mouse':
            self._start_mouse_listener()
        self._health_timer.start()

    def start_recording(self):
        """Start recording next input."""
        self.stop_listening()
        self._is_recording = True
        self.recording_started.emit()
        
        # Start both listeners to capture whichever comes first
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_record_key_press,
            on_release=self._on_record_key_release
        )
        self._mouse_listener = mouse.Listener(
            on_click=self._on_record_mouse_click
        )
        
        self._keyboard_listener.start()
        self._mouse_listener.start()
        print("InputManager: Recording started...")

    def stop_listening(self):
        """Stop all listeners."""
        if self._is_recording:
            self.recording_stopped.emit()
        self._is_recording = False
        self._health_timer.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._wayland_shortcut:
            self._wayland_shortcut.stop()
            self._wayland_shortcut = None
        self._pressed_keys.clear()

    # --- Trigger Logic (Active Mode) ---

    def _start_keyboard_listener(self):
        """Start listener for specific keyboard shortcut."""
        shortcut_str = self._current_shortcut.get('value')
        if not shortcut_str:
            return

        if self._should_use_wayland_portal():
            try:
                self._wayland_shortcut = WaylandGlobalShortcut(shortcut_str, self._on_trigger)
                self._wayland_shortcut.start()
                print(f"InputManager: Started Wayland portal shortcut registration for '{shortcut_str}'")
                return
            except Exception as e:
                print(f"InputManager: Wayland portal shortcut setup failed, falling back to pynput: {e}")
                self._wayland_shortcut = None

        # VK-based keys like <102> (numpad with modifiers) cannot be reliably matched
        # by GlobalHotKeys on all platforms — use a manual listener instead.
        if re.search(r'<\d+>', shortcut_str):
            self._start_vk_keyboard_listener(shortcut_str)
            return

        try:
            # Pynput GlobalHotKeys is robust
            self._keyboard_listener = keyboard.GlobalHotKeys({
                shortcut_str: self._on_trigger
            })
            self._keyboard_listener.start()
        except Exception as e:
            print(f"InputManager: Invalid hotkey '{shortcut_str}': {e}")

    def _start_vk_keyboard_listener(self, shortcut_str):
        """Manual listener for hotkeys containing raw VK codes like <102>.
        GlobalHotKeys can misidentify these on some Linux/X11 setups because
        modifier keys suppress the character output during recording, yielding a
        VK-only KeyCode that may not round-trip through GlobalHotKeys matching."""
        required_mods = set()
        target_vk = None

        for part in shortcut_str.split('+'):
            part = part.strip()
            if part == '<ctrl>':
                required_mods.add('ctrl')
            elif part == '<alt>':
                required_mods.add('alt')
            elif part == '<shift>':
                required_mods.add('shift')
            elif part == '<cmd>':
                required_mods.add('cmd')
            else:
                m = re.fullmatch(r'<(\d+)>', part)
                if m:
                    target_vk = int(m.group(1))

        if target_vk is None:
            print(f"InputManager: Could not parse VK from hotkey '{shortcut_str}'")
            return

        print(f"InputManager: VK-based listener — mods={required_mods}, vk={target_vk}")
        pressed_mods = set()

        def _mod_name(key):
            name = getattr(key, 'name', None) or ''
            if name.startswith('ctrl'): return 'ctrl'
            if name.startswith('alt'): return 'alt'
            if name.startswith('shift'): return 'shift'
            if name.startswith('cmd') or name == 'win': return 'cmd'
            return None

        def on_press(key):
            mod = _mod_name(key)
            if mod:
                pressed_mods.add(mod)
            else:
                vk = getattr(key, 'vk', None)
                if vk == target_vk and pressed_mods >= required_mods:
                    self._on_trigger()

        def on_release(key):
            mod = _mod_name(key)
            if mod:
                pressed_mods.discard(mod)

        self._keyboard_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._keyboard_listener.start()

    def _start_mouse_listener(self):
        """Start listener for specific mouse button."""
        target_btn_str = self._current_shortcut.get('value')
        
        def on_click(x, y, button, pressed):
            if not pressed: return
            
            btn_str = self._mouse_map.get(button, str(button))
            if btn_str == target_btn_str:
                self._on_trigger()

        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._mouse_listener.start()

    def _on_trigger(self):
        """Emit trigger signal."""
        print("InputManager: Triggered!")
        self.triggered.emit()
    
    def _check_listener_alive(self):
        """Periodic health check: restart listener if its thread died."""
        if self._is_recording or not self._current_shortcut:
            return
        if self._is_unsupported_wayland_keyboard_shortcut():
            return
        
        shortcut_type = self._current_shortcut.get('type')
        
        if shortcut_type == 'keyboard' and self._keyboard_listener:
            if not self._keyboard_listener.is_alive():
                print("InputManager: Keyboard listener died, restarting...")
                self._keyboard_listener = None
                self._start_keyboard_listener()
        elif shortcut_type == 'keyboard' and self._wayland_shortcut:
            if not self._wayland_shortcut.is_alive():
                print("InputManager: Wayland shortcut backend died, restarting...")
                self._wayland_shortcut = None
                self._start_keyboard_listener()
        elif shortcut_type == 'mouse' and self._mouse_listener:
            if not self._mouse_listener.is_alive():
                print("InputManager: Mouse listener died, restarting...")
                self._mouse_listener = None
                self._start_mouse_listener()
        elif shortcut_type == 'keyboard' and not self._keyboard_listener and not self._wayland_shortcut:
            print("InputManager: Keyboard listener missing, restarting...")
            self._start_keyboard_listener()
        elif shortcut_type == 'mouse' and not self._mouse_listener:
            print("InputManager: Mouse listener missing, restarting...")
            self._start_mouse_listener()

    # --- Recording Logic ---

    def _on_record_key_press(self, key):
        """Handle key press during recording."""
        if not self._is_recording: return
        self._pressed_keys.add(key)

    def _on_record_key_release(self, key):
        """Handle key release during recording - Finalize record."""
        if not self._is_recording: return

        # Build the combo from keys still held, including the one just released
        # (it's removed from _pressed_keys below, after this).
        combo_str = self._format_combo(self._pressed_keys)
        
        if combo_str:
             print(f"Recorded Keyboard: {combo_str}")
             self.recorded.emit({'type': 'keyboard', 'value': combo_str})
             self.stop_listening()
        
        if key in self._pressed_keys:
            self._pressed_keys.remove(key)

    def _on_record_mouse_click(self, x, y, button, pressed):
        """Handle mouse click during recording."""
        if not self._is_recording or not pressed: return

        # Listening starts after the "Record" button click returns, so that
        # click itself is never captured here.
        btn_str = self._mouse_map.get(button, str(button))

        print(f"Recorded Mouse: {btn_str}")
        self.recorded.emit({'type': 'mouse', 'value': btn_str})
        self.stop_listening()

    def _format_combo(self, keys):
        """Format a set of keys into a pynput hotkey string (e.g., '<ctrl>+<alt>+h')."""
        mods = []
        char_key = None
        
        has_ctrl = any(getattr(k, 'name', '').startswith('ctrl') for k in keys)
        has_alt = any(getattr(k, 'name', '').startswith('alt') for k in keys)
        has_shift = any(getattr(k, 'name', '').startswith('shift') for k in keys)
        has_cmd = any(getattr(k, 'name', '').startswith('cmd') or getattr(k, 'name', '') == 'win' for k in keys)
        
        if has_ctrl: mods.append('<ctrl>')
        if has_alt: mods.append('<alt>')
        if has_shift: mods.append('<shift>')
        if has_cmd: mods.append('<cmd>')
        
        potential_chars = []
        
        for k in keys:
            if hasattr(k, 'name') and (k.name.startswith('ctrl') or k.name.startswith('alt') or k.name.startswith('shift') or k.name.startswith('cmd') or k.name =='win'):
                continue
            
            # Found a character or other special key (e.g. F1, esc, space)
            vk = getattr(k, 'vk', None)
            char = getattr(k, 'char', None)

            is_standard_vk = vk and ((48 <= vk <= 57) or (65 <= vk <= 90))
            
            key_str = ""
            if is_standard_vk:
                 if not char or ord(char) < 32:
                     key_str = chr(vk).lower()
                 else:
                     key_str = char.lower()
            elif char and ord(char) >= 32:
                # Non-standard VK with a char (e.g. numpad 6 → VK 102, char '6').
                # Prefer the VK form so the listener matches by VK regardless of
                # whether modifier keys suppress the char during the trigger press.
                if vk:
                    key_str = f'<{vk}>'
                else:
                    key_str = char.lower()
            elif hasattr(k, 'name'):
                key_str = f"<{k.name}>"
            else:
                key_str = str(k)
            
            potential_chars.append(key_str)
            
        if not potential_chars and not mods:
            return None
            
        # Sort to ensure determinism if multiple keys are pressed
        potential_chars.sort()
        
        if potential_chars:
            char_key = potential_chars[0]
            
        if not char_key: 
            # Only modifiers? Don't record yet
            return None

        # Sort mods to be deterministic (already done by append order above)
        # pynput order convention: <ctrl>+<alt>+<shift>+key
        
        parts = []
        if '<ctrl>' in mods: parts.append('<ctrl>')
        if '<alt>' in mods: parts.append('<alt>')
        if '<shift>' in mods: parts.append('<shift>')
        if '<cmd>' in mods: parts.append('<cmd>')
        parts.append(char_key)
        
        return '+'.join(parts)

    def _should_use_wayland_portal(self) -> bool:
        """Use the portal backend for keyboard shortcuts on Linux Wayland."""
        if not _WAYLAND_PORTAL_AVAILABLE or not self._current_shortcut:
            return False
        if self._current_shortcut.get('type') != 'keyboard':
            return False
        return is_wayland_session() and supports_wayland_global_shortcuts()

    def _is_unsupported_wayland_keyboard_shortcut(self) -> bool:
        """Return whether keyboard shortcuts are unsupported on this Wayland desktop."""
        if not self._current_shortcut or self._current_shortcut.get('type') != 'keyboard':
            return False
        return is_wayland_session() and not supports_wayland_global_shortcuts()


class ButtonShortcutManager(QObject):
    """
    Global keyboard listener for per-button custom shortcuts.
    Uses a single pynput Listener so shortcuts fire even when the dashboard
    is hidden (app sitting in the system tray).

    Pauses automatically while the user is recording a new shortcut so that
    an existing combo doesn't trigger a button in the background mid-recording.
    """

    shortcut_triggered = pyqtSignal(dict)  # emits the full button config

    def __init__(self):
        super().__init__()
        self._listener = None
        self._shortcuts = {}   # shortcut_str -> button config dict
        self._pressed_mods = set()
        self._paused = False

    def update_shortcuts(self, button_configs: list):
        """Re-register all enabled button shortcuts from the given config list."""
        self._stop_listener()
        self._shortcuts = {
            sc['value']: cfg
            for cfg in button_configs
            if (sc := cfg.get('custom_shortcut', {})).get('enabled') and sc.get('value')
        }
        if self._shortcuts:
            self._start_listener()
        print(f"ButtonShortcutManager: {len(self._shortcuts)} active shortcut(s): {list(self._shortcuts.keys())}")

    def set_paused(self, paused: bool):
        self._paused = paused
        if paused:
            self._pressed_mods.clear()

    def stop(self):
        self._stop_listener()

    # --- Internal ---

    def _start_listener(self):
        self._pressed_mods = set()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

    def _stop_listener(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._pressed_mods.clear()

    @staticmethod
    def _mod_name(key):
        name = getattr(key, 'name', None) or ''
        if name.startswith('ctrl'):  return 'ctrl'
        if name.startswith('alt'):   return 'alt'
        if name.startswith('shift'): return 'shift'
        if name.startswith('cmd') or name == 'win': return 'cmd'
        return None

    def _on_press(self, key):
        if self._paused:
            return
        mod = self._mod_name(key)
        if mod:
            self._pressed_mods.add(mod)
            return
        for shortcut_str, config in self._shortcuts.items():
            if self._matches(key, shortcut_str):
                self.shortcut_triggered.emit(config)
                break

    def _on_release(self, key):
        mod = self._mod_name(key)
        if mod:
            self._pressed_mods.discard(mod)

    def _matches(self, key, shortcut_str: str) -> bool:
        required_mods = set()
        target = None

        for part in shortcut_str.split('+'):
            part = part.strip()
            if part == '<ctrl>':   required_mods.add('ctrl')
            elif part == '<alt>':  required_mods.add('alt')
            elif part == '<shift>':required_mods.add('shift')
            elif part == '<cmd>':  required_mods.add('cmd')
            else: target = part

        if required_mods != self._pressed_mods or target is None:
            return False

        # VK-based key like <102>
        m = re.fullmatch(r'<(\d+)>', target)
        if m:
            return getattr(key, 'vk', None) == int(m.group(1))

        # Single printable character: 'f', '6', etc.
        if len(target) == 1:
            char = getattr(key, 'char', None)
            if char and char.lower() == target.lower():
                return True
            vk = getattr(key, 'vk', None)
            # Only use chr(vk) for standard letter/digit VKs where it maps correctly.
            # Numpad VKs (96-105) are in this byte range but chr() gives wrong values.
            is_standard_vk = vk and ((48 <= vk <= 57) or (65 <= vk <= 90))
            if is_standard_vk:
                try:
                    return chr(vk).lower() == target.lower()
                except Exception:
                    pass
            return False

        # Named special key: <f1>, <space>, <esc>, etc.
        if target.startswith('<') and target.endswith('>'):
            return getattr(key, 'name', None) == target[1:-1]

        return False
