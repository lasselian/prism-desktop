"""
Input Manager for Prism Desktop
Handles global keyboard shortcuts and mouse button triggers using pynput.
"""

from PyQt6.QtCore import QObject, pyqtSignal, QThread
from pynput import keyboard, mouse
import threading

class InputManager(QObject):
    """
    Manages global input listeners for keyboard and mouse.
    executes in its own thread to avoid blocking GUI.
    """
    
    triggered = pyqtSignal()
    recorded = pyqtSignal(dict) # {type: 'keyboard'|'mouse', value: str}
    
    def __init__(self):
        super().__init__()
        self._keyboard_listener = None
        self._mouse_listener = None
        self._current_shortcut = None
        self._is_recording = False
        self._pressed_keys = set()
        
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
            return

        print(f"InputManager: Setting shortcut to {config}")
        
        if config.get('type') == 'keyboard':
            self._start_keyboard_listener()
        elif config.get('type') == 'mouse':
            self._start_mouse_listener()

    def start_recording(self):
        """Start recording next input."""
        self.stop_listening()
        self._is_recording = True
        
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
        self._is_recording = False
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        self._pressed_keys.clear()

    # --- Trigger Logic (Active Mode) ---

    def _start_keyboard_listener(self):
        """Start listener for specific keyboard shortcut."""
        shortcut_str = self._current_shortcut.get('value')
        if not shortcut_str:
            return

        try:
            # Pynput GlobalHotKeys is robust
            self._keyboard_listener = keyboard.GlobalHotKeys({
                shortcut_str: self._on_trigger
            })
            self._keyboard_listener.start()
        except Exception as e:
            print(f"InputManager: Invalid hotkey '{shortcut_str}': {e}")

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

    # --- Recording Logic ---

    def _on_record_key_press(self, key):
        """Handle key press during recording."""
        if not self._is_recording: return
        self._pressed_keys.add(key)

    def _on_record_key_release(self, key):
        """Handle key release during recording - Finalize record."""
        if not self._is_recording: return
        
        # Determine the combination from currently pressed keys + the released key.
        # Ensure the released key is accounted for even if it was just removed (logic below handles removal AFTER)
        
        # We need to construct string from the set of keys that were active.
        # If I release 'h', 'h' should supply the char part.
        
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
        
        # Ignore left/right click if you want to allow UI interaction?
        # User said "mouse buttons". Usually Middle/Side.
        # We can capture all, but maybe warn if L/R. 
        # Actually, if they click "Record", the next click is captured.
        # If they click "Record" with Left Click, that click triggers the button. We start listening AFTER.
        # So next click is safe.
        
        btn_str = self._mouse_map.get(button, str(button))
        
        # Prevent capturing Left Click immediately if it was used to press the GUI button?
        # Pynput might catch the release of the Record button click?
        # We handle 'pressed' only.
        
        print(f"Recorded Mouse: {btn_str}")
        self.recorded.emit({'type': 'mouse', 'value': btn_str})
        self.stop_listening()

    def _format_combo(self, keys):
        """Format a set of keys into a pynput hotkey string (e.g., '<ctrl>+<alt>+h')."""
        # Mapping for pynput special keys
        # pynput expects <ctrl>, <alt>, <shift>, <cmd>
        
        mods = []
        char_key = None
        
        has_ctrl = any(k.name.startswith('ctrl') for k in keys if hasattr(k, 'name'))
        has_alt = any(k.name.startswith('alt') for k in keys if hasattr(k, 'name'))
        has_shift = any(k.name.startswith('shift') for k in keys if hasattr(k, 'name'))
        has_cmd = any(k.name.startswith('cmd') or k.name == 'win' for k in keys if hasattr(k, 'name'))
        
        if has_ctrl: mods.append('<ctrl>')
        if has_alt: mods.append('<alt>')
        if has_shift: mods.append('<shift>')
        if has_cmd: mods.append('<cmd>')
        
        # Find the non-modifier key
        for k in keys:
            if hasattr(k, 'name') and (k.name.startswith('ctrl') or k.name.startswith('alt') or k.name.startswith('shift') or k.name.startswith('cmd') or k.name =='win'):
                continue
            
            # Found a character or other special key (e.g. F1, esc, space)
            # Priorities:
            # 1. k.char if it's a normal printable character (not control char)
            # 2. k.vk mapping if it looks like a letter/digit (covers Ctrl+Key issues and missing char)
            # 3. k.name (special keys like 'esc', 'space')
            # 4. Fallback to str(k)
            
            vk = getattr(k, 'vk', None)
            char = getattr(k, 'char', None)
            
            # Helper to check if VK is a standard ASCII letters/digit
            # 48-57: 0-9
            # 65-90: A-Z
            is_standard_vk = vk and ((48 <= vk <= 57) or (65 <= vk <= 90))
            
            if is_standard_vk:
                 # Check if we should prefer VK over char
                 # If char is missing OR char is control code (<32)
                 if not char or ord(char) < 32:
                     char_key = chr(vk).lower()
                 else:
                     char_key = char.lower()
            elif char and ord(char) >= 32:
                char_key = char.lower()
            elif hasattr(k, 'name'):
                char_key = f"<{k.name}>"
            else:
                char_key = str(k)
            break
            
        if not char_key and not mods:
            return None
            
        if not char_key: 
            # Only modifiers? Don't record yet
            return None

        # Sort mods to be deterministic
        # pynput order convention: <ctrl>+<alt>+<shift>+key
        
        parts = []
        if '<ctrl>' in mods: parts.append('<ctrl>')
        if '<alt>' in mods: parts.append('<alt>')
        if '<shift>' in mods: parts.append('<shift>')
        if '<cmd>' in mods: parts.append('<cmd>')
        parts.append(char_key)
        
        return '+'.join(parts)
