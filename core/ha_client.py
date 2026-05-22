import asyncio
import logging
import aiohttp
from aiohttp import ClientTimeout
from typing import Optional

class HAClient:
    """Asynchronous client for Home Assistant REST API."""
    
    def __init__(self, url: str = "", token: str = ""):
        self.url = url.rstrip('/')
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self.logger = logging.getLogger(__name__)
    
    def configure(self, url: str, token: str):
        """Update connection settings."""
        self.url = url.rstrip('/')
        self.token = token
        
        # If there's an active session, close it so a new one is spawned with new token headers
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._session.close())
            except RuntimeError:
                pass  # No running event loop
        self._session = None
    
    @property
    def headers(self) -> dict:
        """Return authorization headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
    
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test connection to Home Assistant.
        Returns (success, message).
        """
        if not self.url or not self.token:
            return False, "URL and token are required"
        
        try:
            # Create a temporary session or use the shared one? Use shared.
            # But wait, if we are testing a NEW config, we shouldn't use the old keyed session.
            # But usually test_connection is called with current self.url/token.
            
            # Use a one-off session for testing to avoid polluting the main pool if auth fails?
            # Or just use the standard flow.
            # Let's use a one-off for safety to ensure it tests exactly what's configured
            # regardless of pooled state, although keep-alive is nice.
            # Actually, standard flow is fine.
            
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(f"{self.url}/api/", timeout=ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        return True, "Connected"
                    elif response.status == 401:
                        return False, "Invalid access token"
                    else:
                        return False, f"HTTP {response.status}"
        except aiohttp.ClientError as e:
            self.logger.error(f"Connection test error: {e}")
            return False, f"Connection error: {e}"
        except Exception as e:
             return False, f"Error: {e}"
    
    async def get_entities(self) -> list[dict]:
        """Fetch all entities."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.url}/api/states", timeout=ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
                return []
        except Exception as e:
            self.logger.error(f"Error fetching entities: {e}")
            return []

    async def get_config(self) -> Optional[dict]:
        """Fetch Home Assistant instance config."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.url}/api/config", timeout=ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching HA config: {e}")
            return None
    
    async def get_state(self, entity_id: str) -> Optional[dict]:
        """Get state of a specific entity."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.url}/api/states/{entity_id}", timeout=ClientTimeout(total=5)) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching state for {entity_id}: {e}")
            return None
            
    async def get_weather_forecast(self, entity_id: str, forecast_type="daily") -> list:
        """Fetch weather forecast for a given entity."""
        try:
            session = await self._get_session()
            payload = {"type": forecast_type, "entity_id": entity_id}
            async with session.post(
                f"{self.url}/api/services/weather/get_forecasts?return_response=true",
                json=payload,
                timeout=ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Response format: {"service_response": {"weather.entity": {"forecast": [...]}}}
                    service_resp = data.get("service_response", {})
                    entity_data = service_resp.get(entity_id, {})
                    return entity_data.get("forecast", [])
                return []
        except Exception as e:
            self.logger.error(f"Error fetching weather forecast for {entity_id}: {e}")
            return []
    
    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        data: Optional[dict] = None,
        timeout: int = 10
    ) -> bool:
        """Call a service."""
        try:
            payload = data or {}
            if entity_id:
                payload["entity_id"] = entity_id
            
            session = await self._get_session()
            async with session.post(
                f"{self.url}/api/services/{domain}/{service}",
                json=payload,
                timeout=ClientTimeout(total=timeout),
            ) as response:
                return response.status == 200
        except Exception as e:
            self.logger.error(f"Service call failed for {domain}.{service}: {e}")
            return False
    
    async def get_camera_image(self, entity_id: str) -> Optional[bytes]:
        """Fetch camera snapshot image."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/api/camera_proxy/{entity_id}",
                timeout=ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return await response.read()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching camera image for {entity_id}: {e}")
            return None
            
    async def get_media_image(self, image_path: str) -> Optional[bytes]:
        """Fetch media player album art."""
        if not image_path:
            return None
        try:
            # entity_picture is a relative URL like /api/media_player_proxy/...
            url = f"{self.url}{image_path}" if image_path.startswith('/') else image_path
            
            session = await self._get_session()
            async with session.get(url, timeout=ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.read()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching media image: {e}")
            return None
