
import re
import requests
from PyQt6.QtCore import QThread, pyqtSignal

class UpdateCheckerThread(QThread):
    """
    Checks for updates from GitHub releases.
    """
    update_available = pyqtSignal(str) # Emits new version tag if available
    up_to_date = pyqtSignal()
    error_occurred = pyqtSignal(str)
    
    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version
        self.repo_url = "https://api.github.com/repos/HomeRiz/prism-desktop/releases/latest"
        
    def _extract_version(self, text):
        """Extract first version-like pattern (e.g. 1.0, v1.2.3)."""
        if not text: return ""
        match = re.search(r'v?(\d+(?:\.\d+)+)', text)
        return match.group(1) if match else text.strip()

    def _parse_version(self, ver: str) -> tuple:
        """Convert version string to comparable tuple, e.g. '1.5' == '1.5.0'."""
        try:
            return tuple(int(x) for x in ver.split('.'))
        except ValueError:
            return (0,)

    def run(self):
        try:
            response = requests.get(self.repo_url, timeout=5)
            response.raise_for_status()

            data = response.json()
            tag_name = data.get("tag_name", "").strip()
            name = data.get("name", "").strip()

            # Try to get meaningful version from tag, then name
            remote_ver = self._extract_version(tag_name)
            if not re.match(r'\d', remote_ver) and name:
                remote_ver = self._extract_version(name)

            local_ver = self._extract_version(self.current_version)

            if remote_ver and self._parse_version(remote_ver) > self._parse_version(local_ver):
                self.update_available.emit(remote_ver)
            else:
                self.up_to_date.emit()
                
        except Exception as e:
            self.error_occurred.emit(str(e))
