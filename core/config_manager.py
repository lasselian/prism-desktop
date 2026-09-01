import sys
import json
import copy
import logging
import os
import tempfile
from core.utils import get_config_path
from core.token_storage import store_token, load_token

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration and secure token storage."""
    
    def __init__(self, config_filename: str = "config.json"):
        self.config_path = get_config_path(config_filename)
        self.config = self.load_config()
        self.save_config()  # Scrub sensitive data (tokens) from disk immediately

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
                        logger.info("[ConfigManager] Migrating plaintext token to secure storage...")
                        store_token(token_in_file)
                        ha_config['token'] = token_in_file

                    # --- Auto-migrate legacy slot-only configs to (row, col) ---
                    cols = config.get('appearance', {}).get('cols', 4)
                    for btn_cfg in config.get('buttons', []):
                        if 'row' not in btn_cfg and 'slot' in btn_cfg:
                            slot = btn_cfg['slot']
                            btn_cfg['row'] = slot // cols
                            btn_cfg['col'] = slot % cols

                    # --- Migrate buttons without a page key to page 0 ---
                    for btn_cfg in config.get('buttons', []):
                        if 'page' not in btn_cfg:
                            btn_cfg['page'] = 0

                    return config
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                self._backup_corrupt_config()

        return {
            "home_assistant": {"url": "", "token": ""},
            "appearance": {
                "theme": "system",
                "language": "en",
                "rows": 2,
                "button_style": "Gradient",
                "border_effect": "Liquid Mercury",
                # macOS menu bar is located at the top of the screen;
                # Windows and Linux taskbars conventionally default to the bottom.
                "tray_position": "top" if sys.platform == "darwin" else "bottom",
                "pages": 1,
                "pin_window": False,
            },
            "shortcut": {"type": "keyboard", "value": "<ctrl>+<alt>+h"},
            "assist_shortcut": {},
            "assist": {
                "enabled": False,
                "show_in_footer": True,
                "mic_device_id": "",
                "speaker_device_id": "",
                "auto_listen": False,
            },
            "buttons": [],
            "welcome_shown": False,
        }

    def _backup_corrupt_config(self):
        """Preserve an unreadable config file so a failed load never destroys user data."""
        backup_path = self.config_path.with_suffix('.json.bak')
        try:
            os.replace(self.config_path, backup_path)
            logger.warning(f"Corrupt config backed up to {backup_path}")
        except OSError as e:
            logger.error(f"Could not back up corrupt config: {e}")

    def save_raw_config(self, config_to_save: dict):
        """Save a specific config dict without modifying self.config."""
        try:
            # Write to a temp file then atomically replace, so a crash mid-write
            # can never leave a truncated config.json behind.
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.config_path.parent), prefix='.config_', suffix='.tmp'
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(config_to_save, f, indent=2)
                os.replace(tmp_path, self.config_path)
            except BaseException:
                os.unlink(tmp_path)
                raise
        except Exception as e:
            logger.error(f"Error saving raw config: {e}")

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
            logger.error(f"Error saving config: {e}")
