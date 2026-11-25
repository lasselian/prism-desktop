import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Tuple

import aiohttp
from yarl import URL

from core.utils import keep_task

SERVICE_CALL_TIMEOUT = 10  # seconds

# Characters Home Assistant allows in a camera entity id. Checked before the id
# goes into a URL path, so a hand-edited config cannot point the authenticated
# request at another endpoint.
CAMERA_ENTITY_RE = re.compile(r"camera\.[a-z0-9_]+")
FLOW_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


class HAClient:
    """Asynchronous client for Home Assistant REST API."""

    def __init__(self, url: str = "", token: str = ""):
        self.url = url.rstrip('/')
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self._anon_session: Optional[aiohttp.ClientSession] = None
        self.logger = logging.getLogger(__name__)

    def configure(self, url: str, token: str):
        """Update connection settings."""
        self.url = url.rstrip('/')
        self.token = token

        # If there's an active session, close it so a new one is spawned with new token headers
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_running_loop()
                keep_task(loop.create_task(self._session.close()))
            except RuntimeError:
                pass  # No running event loop
        self._session = None

    @property
    def headers(self) -> dict:
        """Return authorization headers."""
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    def _get_anon_session(self) -> aiohttp.ClientSession:
        """Get or create the credential-free session used for foreign origins.

        Deliberately carries no Authorization header and stores no cookies, so
        nothing sensitive can ride along to a third-party host.
        """
        if self._anon_session is None or self._anon_session.closed:
            self._anon_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        return self._anon_session

    def resolve_media_url(self, raw_url: str) -> Optional[Tuple[URL, bool]]:
        """Resolve an entity-provided media URL. Returns (url, same_origin).

        entity_picture and TTS URLs are entity-provided data, not trusted API
        endpoints. Home Assistant hands out absolute third-party URLs whenever
        an integration sets media_image_remotely_accessible (cast, heos, wiim,
        music_assistant, xbox, ...), so the caller must know whether a URL
        leaves the configured HA origin before deciding to authenticate.

        Returns None if the URL is unusable or the HA base URL is unset/invalid.
        """
        if not raw_url or not isinstance(raw_url, str):
            return None
        # Control characters and backslashes can smuggle request syntax.
        if any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in raw_url):
            return None

        try:
            base = URL(self.url)
            if (
                base.scheme not in ('http', 'https')
                or not base.raw_host
                or base.user is not None
                or base.password is not None
            ):
                return None

            raw = raw_url.strip()
            if raw.startswith('//'):
                # Protocol-relative: inherits the HA scheme, not the HA host.
                candidate = f"{base.scheme}:{raw}"
            elif raw.startswith('/'):
                # Plain concat, not URL.join(), so a HA instance hosted under a
                # subpath keeps its prefix (e.g. https://example.com/ha).
                candidate = f"{self.url}{raw}"
            else:
                candidate = raw

            url = URL(candidate).with_fragment(None)
            if url.scheme not in ('http', 'https') or url.user is not None or url.password is not None:
                return None

            same_origin = (url.scheme, url.raw_host, url.port) == (base.scheme, base.raw_host, base.port)
            return url, same_origin
        except Exception:
            return None

    async def close(self):
        """Close the HTTP sessions."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        if self._anon_session and not self._anon_session.closed:
            await self._anon_session.close()
        self._anon_session = None

    async def test_connection(self) -> tuple[bool, str]:
        """
        Test connection to Home Assistant.
        Returns (success, message).
        """
        if not self.url or not self.token:
            return False, "URL and token are required"

        try:
            # One-off session so the test always uses the currently configured
            # URL/token rather than a pooled session built from old headers.
            async with (
                aiohttp.ClientSession(headers=self.headers) as session,
                session.get(f"{self.url}/api/", timeout=5) as response,
            ):
                if response.status == 200:
                    return True, "Connected"
                elif response.status == 401:
                    return False, "Invalid access token"
                else:
                    return False, f"HTTP {response.status}"
        except asyncio.TimeoutError:
            self.logger.error("Connection test timed out")
            return False, "Connection timed out"
        except aiohttp.ClientError as e:
            detail = str(e) or type(e).__name__
            self.logger.error(f"Connection test error: {detail}")
            return False, f"Connection error: {detail}"
        except Exception as e:
            detail = str(e) or type(e).__name__
            self.logger.error(f"Connection test error: {detail}")
            return False, f"Error: {detail}"

    async def get_entities(self) -> list[dict]:
        """Fetch all entities."""
        try:
            session = self._get_session()
            async with session.get(f"{self.url}/api/states", timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                return []
        except Exception as e:
            self.logger.error(f"Error fetching entities: {e}")
            return []

    async def get_services(self) -> list[dict]:
        """Fetch all available Home Assistant service domains and their services."""
        status, data = await self._request_json("GET", "/api/services")
        return data if status == 200 and isinstance(data, list) else []

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        timeout: int = 10,
    ) -> tuple[int, object]:
        """Send an authenticated JSON request to HA. Returns (http_status, parsed_json_or_text).

        Status 0 indicates a transport/client error or missing credentials.
        """
        if not self.url or not self.token:
            return 0, "URL and token are required"
        try:
            session = self._get_session()
            kwargs: dict = {"timeout": timeout, "allow_redirects": False}
            if payload is not None:
                kwargs["json"] = payload
            async with session.request(method, f"{self.url}{path}", **kwargs) as response:
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = await response.text()
                return response.status, body
        except Exception as e:
            detail = str(e) or type(e).__name__
            self.logger.error(f"HA request {method} {path} failed: {detail}")
            return 0, detail

    async def get_config_entries(self) -> tuple[int, object]:
        """List Home Assistant config entries."""
        return await self._request_json("GET", "/api/config/config_entries/entry")

    async def start_config_flow(self, handler: str, show_advanced_options: bool = False) -> tuple[int, object]:
        """Start a Home Assistant config entry flow for `handler` (e.g. 'template')."""
        return await self._request_json(
            "POST",
            "/api/config/config_entries/flow",
            {"handler": handler, "show_advanced_options": show_advanced_options},
        )

    async def continue_config_flow(self, flow_id: str, user_input: dict) -> tuple[int, object]:
        """Submit the next step of an active Home Assistant config flow."""
        if not isinstance(flow_id, str) or not FLOW_ID_RE.fullmatch(flow_id):
            return 0, "Invalid flow_id"
        return await self._request_json("POST", f"/api/config/config_entries/flow/{flow_id}", user_input)

    async def abort_config_flow(self, flow_id: str) -> bool:
        """Abort an in-progress Home Assistant config flow."""
        if not isinstance(flow_id, str) or not FLOW_ID_RE.fullmatch(flow_id):
            return False
        status, _ = await self._request_json("DELETE", f"/api/config/config_entries/flow/{flow_id}")
        return status == 200

    async def start_options_flow(self, entry_id: str) -> tuple[int, object]:
        """Start an options flow for an existing Home Assistant config entry."""
        return await self._request_json("POST", "/api/config/config_entries/options/flow", {"handler": entry_id})

    async def continue_options_flow(self, flow_id: str, user_input: dict) -> tuple[int, object]:
        """Submit the next step of an active Home Assistant options flow."""
        if not isinstance(flow_id, str) or not FLOW_ID_RE.fullmatch(flow_id):
            return 0, "Invalid flow_id"
        return await self._request_json("POST", f"/api/config/config_entries/options/flow/{flow_id}", user_input)

    async def abort_options_flow(self, flow_id: str) -> bool:
        """Abort an in-progress Home Assistant options flow."""
        if not isinstance(flow_id, str) or not FLOW_ID_RE.fullmatch(flow_id):
            return False
        status, _ = await self._request_json("DELETE", f"/api/config/config_entries/options/flow/{flow_id}")
        return status == 200

    async def get_config(self) -> Optional[dict]:
        """Fetch Home Assistant instance config."""
        try:
            session = self._get_session()
            async with session.get(f"{self.url}/api/config", timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching HA config: {e}")
            return None

    async def get_state(self, entity_id: str) -> Optional[dict]:
        """Get state of a specific entity."""
        try:
            session = self._get_session()
            async with session.get(f"{self.url}/api/states/{entity_id}", timeout=5) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching state for {entity_id}: {e}")
            return None

    async def get_weather_forecast(self, entity_id: str, forecast_type="daily") -> list:
        """Fetch weather forecast for a given entity."""
        try:
            session = self._get_session()
            payload = {"type": forecast_type, "entity_id": entity_id}
            async with session.post(
                f"{self.url}/api/services/weather/get_forecasts?return_response=true", json=payload, timeout=10
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
        self, domain: str, service: str, entity_id: Optional[str] = None, data: Optional[dict] = None
    ) -> bool:
        """Call a service."""
        try:
            payload = dict(data or {})
            if entity_id:
                payload["entity_id"] = entity_id

            session = self._get_session()
            async with session.post(
                f"{self.url}/api/services/{domain}/{service}", json=payload, timeout=SERVICE_CALL_TIMEOUT
            ) as response:
                return response.status == 200
        except Exception as e:
            self.logger.error(f"Service call failed for {domain}.{service}: {e}")
            return False

    async def get_camera_image(self, entity_id: str) -> Optional[bytes]:
        """Fetch camera snapshot image."""
        try:
            session = self._get_session()
            async with session.get(f"{self.url}/api/camera_proxy/{entity_id}", timeout=10) as response:
                if response.status == 200:
                    return await response.read()
                return None
        except Exception as e:
            self.logger.error(f"Error fetching camera image for {entity_id}: {e}")
            return None

    @asynccontextmanager
    async def camera_stream(self, entity_id: str, read_timeout: float = 45) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open Home Assistant's MJPEG stream for a camera and yield the response.

        The caller checks the status and reads the body. Works for every
        camera: HA passes native MJPEG through and builds a stream from stills
        for the rest. A still stream only sends a frame when the picture
        changes, so a quiet static camera hits `read_timeout`; callers should
        treat that as "reconnect", not as a failure.
        """
        if not isinstance(entity_id, str) or not CAMERA_ENTITY_RE.fullmatch(entity_id):
            raise ValueError("not a camera entity id")
        if not self.url or not self.token:
            raise ValueError("Home Assistant URL and token are required")
        # The session is fetched per call: configure() and close() replace it
        # when the settings change, and the next reconnect must pick that up.
        session = self._get_session()
        # A stream runs for hours, so no total limit (the session default is
        # five minutes); sock_read catches a connection that has gone silent.
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=read_timeout)
        async with session.get(
            f"{self.url}/api/camera_proxy_stream/{entity_id}",
            timeout=timeout,
            # HA does not redirect here; following one could carry the token
            # to another host.
            allow_redirects=False,
        ) as response:
            yield response

    # Artwork/audio is decorative; cap it so a hostile host cannot stream
    # unbounded data into memory.
    MAX_MEDIA_BYTES = 16 * 1024 * 1024

    async def fetch_media(self, raw_url: str, kind: str = "media") -> Optional[bytes]:
        """Fetch entity-provided media, authenticating only on the HA origin.

        Same-origin URLs use the authenticated session and refuse redirects, so
        the bearer token cannot be bounced to a foreign host. Anything else is
        fetched with no credentials at all -- which is exactly what Home
        Assistant's own frontend does with these URLs.
        """
        resolved = self.resolve_media_url(raw_url)
        if resolved is None:
            self.logger.warning("Rejected unusable %s URL", kind)
            return None
        url, same_origin = resolved

        try:
            if same_origin:
                session = self._get_session()
                # Fail closed: the HA proxy endpoints do not redirect, and
                # following one could carry the token off-origin.
                allow_redirects = False
            else:
                session = self._get_anon_session()
                # Safe to follow: this session carries no credentials.
                allow_redirects = True

            async with session.get(url, timeout=10, allow_redirects=allow_redirects) as response:
                if response.status != 200:
                    return None
                # Accumulate in chunks: StreamReader.read(n) returns *up to* n
                # bytes, so a single sized read cannot enforce the cap.
                buf = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > self.MAX_MEDIA_BYTES:
                        self.logger.warning("Oversized %s response discarded", kind)
                        return None
                return bytes(buf)
        except Exception as e:
            # Never log the URL itself; it can carry signed query secrets.
            self.logger.error("Error fetching %s (%s)", kind, type(e).__name__)
            return None

    async def get_media_image(self, image_path: str) -> Optional[bytes]:
        """Fetch media player album art."""
        if not image_path:
            return None
        return await self.fetch_media(image_path, kind="media image")

    async def get_entity_image(self, entity_id: str, state: Optional[dict] = None) -> Optional[bytes]:
        """Fetch a snapshot for an `image.*` domain entity via its entity_picture attribute."""
        if state is None:
            state = await self.get_state(entity_id)
        pic_path = (state or {}).get('attributes', {}).get('entity_picture')
        if not pic_path:
            return None
        return await self.get_media_image(pic_path)
