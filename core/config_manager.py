import json
import copy
from pathlib import Path
from core.utils import get_config_path
from core.token_storage import store_token, load_token


class ConfigManager:
    """Manages application configuration and secure token storage."""
    
    def __init__(self, config_filename: str = "config.json"):
        self.config_path = get_config_path(config_filename)
        self.config = self.load_config()
        self.save_config()  # Scrub sensitive data (tokens) from disk immediately

    def get(self, key: str, default=None):
        return self.config.get(key, default)
        
    def __getitem__(self, key):
        return self.config[key]
        
    def __setitem__(self, key, value):
        self.config[key] = value

    def load_config(self) -> dict:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                    ha_config = config.get('home_assistant', {})
                    token_in_file = ha_config.get('token', '')
                    
                    # Load token from secure storage (keyring or encrypted file)
                    stored_token = load_token()
                    
                    if stored_token:
                        # Token found in secure storage — use it
                        ha_config['token'] = stored_token
                    elif token_in_file:
                        # Legacy: plaintext token in config.json — migrate it
                        print("[ConfigManager] Migrating plaintext token to secure storage...")
                        store_token(token_in_file)
                        ha_config['token'] = token_in_file
                    
                    # --- Auto-migrate legacy slot-only configs to (row, col) ---
                    cols = config.get('appearance', {}).get('cols', 4)
                    for btn_cfg in config.get('buttons', []):
                        if 'row' not in btn_cfg and 'slot' in btn_cfg:
                            slot = btn_cfg['slot']
                            btn_cfg['row'] = slot // cols
                            btn_cfg['col'] = slot % cols
                    
                    return config
            except Exception as e:
                print(f"Error loading config: {e}")
        
        return {
            "home_assistant": {"url": "", "token": ""},
            "appearance": {"theme": "system", "rows": 2, "button_style": "Gradient", "tray_position": "bottom"},
            "shortcut": {"type": "keyboard", "value": "<ctrl>+<alt>+h"},
            "buttons": []
        }

    def save_raw_config(self, config_to_save: dict):
        """Save a specific config dict without modifying self.config."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=2)
        except Exception as e:
            print(f"Error saving raw config: {e}")

    def save_config(self):
        """Save current configuration to file."""
        try:
            config_to_save = copy.deepcopy(self.config)
            ha_config = config_to_save.get('home_assistant', {})
            token = ha_config.get('token', '')
            
            if token:
                # Store token securely (keyring or encrypted file)
                store_token(token)
                ha_config['token'] = ''  # Always scrub from config.json
            
            self.save_raw_config(config_to_save)
        except Exception as e:
            print(f"Error saving config: {e}")
