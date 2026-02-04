"""
Home Assistant REST API Client (Synchronous)
Handles HTTP requests to Home Assistant.
"""

import re
import base64
import requests
from typing import Optional, Any
from urllib.parse import urlparse, quote

# Valid entity_id pattern: domain.object_id (alphanumeric, underscore)
ENTITY_ID_PATTERN = re.compile(r'^[a-z_]+\.[a-z0-9_]+$', re.IGNORECASE)
# Valid service/domain pattern
SERVICE_PATTERN = re.compile(r'^[a-z_]+$', re.IGNORECASE)


def _validate_entity_id(entity_id: str) -> bool:
    """Validate entity_id format to prevent path injection."""
    if not entity_id:
        return False
    return bool(ENTITY_ID_PATTERN.match(entity_id))


def _validate_service_name(name: str) -> bool:
    """Validate domain/service name format."""
    if not name:
        return False
    return bool(SERVICE_PATTERN.match(name))


def _validate_url(url: str) -> bool:
    """Validate URL is a proper HTTP(S) URL."""
    if not url:
        return False
    try:
        result = urlparse(url)
        return result.scheme in ('http', 'https') and bool(result.netloc)
    except Exception:
        return False


class HAClient:
    """Synchronous client for Home Assistant REST API."""
    
    def __init__(self, url: str = "", token: str = ""):
        self._url = ""
        self._token = ""
        self._session: Optional[requests.Session] = None
        if url:
            self.url = url
        if token:
            self.token = token
    
    @property
    def url(self) -> str:
        return self._url
    
    @url.setter
    def url(self, value: str):
        value = value.rstrip('/') if value else ""
        if value and not _validate_url(value):
            raise ValueError(f"Invalid URL format: {value}")
        self._url = value
    
    @property
    def token(self) -> str:
        return self._token
    
    @token.setter
    def token(self, value: str):
        # Basic token validation - should be non-empty string without newlines
        if value and ('\n' in value or '\r' in value):
            raise ValueError("Token contains invalid characters")
        self._token = value
    
    def configure(self, url: str, token: str):
        """Update connection settings."""
        # Reset session on config change
        if self._session:
            self._session.close()
            self._session = None
        # Use property setters for validation
        self.url = url
        self.token = token
    
    @property
    def headers(self) -> dict:
        """Return authorization headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    def _get_session(self) -> requests.Session:
        """Get or create requests session."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self.headers)
        return self._session
    
    def close(self):
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None
    
    def test_connection(self) -> tuple[bool, str]:
        """
        Test connection to Home Assistant.
        Returns (success, message).
        """
        if not self.url or not self.token:
            return False, "URL and token are required"
        
        try:
            session = self._get_session()
            response = session.get(
                f"{self.url}/api/",
                timeout=5
            )
            if response.status_code == 200:
                return True, "Connected"
            elif response.status_code == 401:
                return False, "Invalid access token"
            else:
                return False, f"HTTP {response.status_code}"
        except requests.RequestException as e:
            return False, f"Connection error: {e}"
    
    def get_entities(self) -> list[dict]:
        """
        Fetch all entities.
        Returns list of state objects.
        """
        try:
            session = self._get_session()
            response = session.get(
                f"{self.url}/api/states",
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return []
        except Exception:
            return []
    
    def get_state(self, entity_id: str) -> Optional[dict]:
        """
        Get state of a specific entity.
        Returns entity state object or None.
        """
        if not _validate_entity_id(entity_id):
            return None
        try:
            session = self._get_session()
            # URL-encode entity_id for safety
            safe_entity_id = quote(entity_id, safe='')
            response = session.get(
                f"{self.url}/api/states/{safe_entity_id}",
                timeout=5
            )
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None
    
    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        data: Optional[dict] = None
    ) -> bool:
        """
        Call a service.
        Returns True if successful.
        """
        # Validate domain and service names
        if not _validate_service_name(domain) or not _validate_service_name(service):
            return False
        # Validate entity_id if provided
        if entity_id and not _validate_entity_id(entity_id):
            return False
        try:
            payload = data or {}
            if entity_id:
                payload["entity_id"] = entity_id
            
            session = self._get_session()
            # URL-encode domain and service for safety
            safe_domain = quote(domain, safe='')
            safe_service = quote(service, safe='')
            response = session.post(
                f"{self.url}/api/services/{safe_domain}/{safe_service}",
                json=payload,
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def get_services(self) -> dict:
        """
        Get available services from Home Assistant.
        Returns dict of domain -> services.
        """
        try:
            session = self._get_session()
            response = session.get(
                f"{self.url}/api/services",
                timeout=10
            )
            if response.status_code == 200:
                services = response.json()
                # Convert to dict format
                result = {}
                for item in services:
                    domain = item.get('domain', '')
                    result[domain] = list(item.get('services', {}).keys())
                return result
            return {}
        except Exception:
            return {}

    def get_image(self, image_path: str, access_token: str = '') -> Optional[str]:
        """
        Fetch an image from Home Assistant and return as base64.
        
        Args:
            image_path: The path to the image (e.g., /api/image_proxy/image.doorbell)
            access_token: Optional access token for the image (only used if not already in URL)
            
        Returns:
            Base64 encoded image data or None on error.
        """
        if not image_path:
            return None
        
        try:
            session = self._get_session()
            
            # Build full URL if path is relative
            if image_path.startswith('/'):
                url = f"{self.url}{image_path}"
            else:
                url = image_path
            
            # Only append access token if not already in URL
            if access_token and 'token=' not in url:
                separator = '&' if '?' in url else '?'
                url = f"{url}{separator}token={access_token}"
            
            response = session.get(url, timeout=10)
            
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', 'image/jpeg')
                image_data = base64.b64encode(response.content).decode('utf-8')
                return f"data:{content_type};base64,{image_data}"
            return None
        except Exception:
            return None

