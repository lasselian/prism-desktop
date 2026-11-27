"""
Worker Threads for Prism Desktop
"""

import asyncio
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from core.ha_client import HAClient

logger = logging.getLogger(__name__)


class ConnectionTestThread(QThread):
    """Background thread for testing connection."""

    finished = pyqtSignal(bool, str)

    def __init__(self, url: str, token: str, fallback_url: str = ""):
        super().__init__()
        self.url = url
        self.token = token
        self.fallback_url = fallback_url

    def run(self):
        """Test connection in background."""
        try:
            asyncio.run(self._async_run())
        except Exception as e:
            logger.error(f"Connection test crashed: {e}", exc_info=True)
            self.finished.emit(False, str(e))

    async def _async_run(self):
        success, message = await self._test(self.url)
        if not success and self.fallback_url:
            fallback_ok, _ = await self._test(self.fallback_url)
            if fallback_ok:
                success, message = True, "Connected via fallback URL"
        self.finished.emit(success, message)

    async def _test(self, url: str) -> tuple[bool, str]:
        client = HAClient(url, self.token)
        try:
            return await client.test_connection()
        finally:
            await client.close()
