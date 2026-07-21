"""
Worker Threads for Prism Desktop
"""

import asyncio
from PyQt6.QtCore import QThread, pyqtSignal
from core.ha_client import HAClient

class ConnectionTestThread(QThread):
    """Background thread for testing connection."""
    
    finished = pyqtSignal(bool, str)
    
    def __init__(self, url: str, token: str):
        super().__init__()
        self.url = url
        self.token = token
    
    def run(self):
        """Test connection in background."""
        try:
            asyncio.run(self._async_run())
        except Exception as e:
            self.finished.emit(False, str(e))

    async def _async_run(self):
        client = HAClient(self.url, self.token)
        try:
            success, message = await client.test_connection()
            self.finished.emit(success, message)
        finally:
            await client.close()
