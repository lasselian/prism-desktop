"""
Mobile App Integration for Prism Desktop
Registers Prism as an HA mobile app to expose a native notify service.
"""

import asyncio
import platform
import socket
import uuid
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# Prism's app identifier (stable — must not change between versions)
APP_ID = "io.prism.desktop"
APP_NAME = "Prism Desktop"
MANUFACTURER = "Prism"
MODEL = "Desktop"
OS_VERSION = platform.version()


def _get_device_name() -> str:
    """Return a human-readable device name based on hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "Prism Desktop"


def _get_or_create_device_id(config: dict) -> str:
    """Return a stable device ID, creating one if absent."""
    mobile_app_cfg = config.setdefault("mobile_app", {})
    device_id = mobile_app_cfg.get("device_id", "")
    if not device_id:
        device_id = str(uuid.uuid4()).replace("-", "")
        mobile_app_cfg["device_id"] = device_id
    return device_id


async def register_mobile_app(
    ha_url: str,
    ha_token: str,
    config: dict,
    save_config_fn,
) -> Optional[str]:
    """
    Register Prism as a Mobile App with Home Assistant if not already done.

    Returns the webhook_id on success, or None on failure.
    Saves the webhook_id into config['mobile_app']['webhook_id'] via save_config_fn.
    """
    mobile_app_cfg = config.setdefault("mobile_app", {})

    # Already registered — verify the webhook is still valid on HA side
    existing_webhook_id = mobile_app_cfg.get("webhook_id", "")
    if existing_webhook_id:
        logger.info(f"[MobileApp] Already registered (webhook_id={existing_webhook_id[:8]}...)")
        still_valid = await _update_registration(ha_url, existing_webhook_id)
        if still_valid:
            return existing_webhook_id
        # Webhook is dead (device deleted in HA) — clear and re-register below
        logger.warning("[MobileApp] Registration lost — HA no longer recognises this webhook. Re-registering...")
        mobile_app_cfg.pop("webhook_id", None)
        save_config_fn()

    if not ha_url or not ha_token:
        logger.warning("[MobileApp] Missing HA URL or token — skipping registration")
        return None

    device_id = _get_or_create_device_id(config)
    device_name = _get_device_name()

    payload = {
        "device_id": device_id,
        "app_id": APP_ID,
        "app_name": APP_NAME,
        "app_version": "1.0",
        "device_name": device_name,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "os_version": OS_VERSION,
        "supports_encryption": False,
        # Required: tells HA to create notify.mobile_app_* and enable WS delivery
        "app_data": {
            "push_websocket_channel": True,
        },
    }

    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    url = ha_url.rstrip("/") + "/api/mobile_app/registrations"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=payload, timeout=10) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    webhook_id = data.get("webhook_id", "")
                    if webhook_id:
                        mobile_app_cfg["webhook_id"] = webhook_id
                        mobile_app_cfg["device_id"] = device_id
                        mobile_app_cfg["device_name"] = device_name
                        save_config_fn()
                        logger.info(
                            f"[MobileApp] Registered successfully as '{device_name}' "
                            f"(webhook_id={webhook_id[:8]}...)"
                        )
                        return webhook_id
                    else:
                        logger.error("[MobileApp] Registration response missing webhook_id")
                        return None
                elif response.status == 409:
                    # Already registered — HA returned conflict
                    # We lost our local webhook_id. Reset and re-register on next start.
                    logger.warning(
                        "[MobileApp] HA reports device already registered (409). "
                        "Clearing local registration and will retry on next startup."
                    )
                    mobile_app_cfg.pop("webhook_id", None)
                    save_config_fn()
                    return None
                else:
                    text = await response.text()
                    logger.error(f"[MobileApp] Registration failed: HTTP {response.status} — {text}")
                    return None
    except asyncio.TimeoutError:
        logger.error("[MobileApp] Registration timed out")
        return None
    except Exception as e:
        logger.error(f"[MobileApp] Registration error: {e}")
        return None


async def send_location_update(ha_url: str, webhook_id: str, location: dict) -> bool:
    """
    POST an update_location action to the HA mobile app webhook.

    location should be: {"gps": [lat, lon], "gps_accuracy": metres}
    The webhook endpoint is unauthenticated — no Authorization header needed.
    Returns True on success (HTTP 200/201).
    """
    url = ha_url.rstrip("/") + f"/api/webhook/{webhook_id}"
    payload = {"type": "update_location", "data": location}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as response:
                if response.status in (200, 201):
                    logger.info("[MobileApp] Location update sent successfully")
                    return True
                else:
                    logger.warning(f"[MobileApp] Location update returned HTTP {response.status}")
                    return False
    except asyncio.TimeoutError:
        logger.warning("[MobileApp] Location update timed out")
        return False
    except Exception as e:
        logger.warning(f"[MobileApp] Location update error: {e}")
        return False


async def _update_registration(ha_url: str, webhook_id: str) -> bool:
    """
    Update an existing mobile app registration via the webhook POST endpoint
    to ensure push_websocket_channel is enabled (required for notify service creation).

    Returns True if the webhook is still valid, False if HA no longer
    recognises it (device was deleted → 404/410/etc.).
    """
    url = ha_url.rstrip("/") + f"/api/webhook/{webhook_id}"
    payload = {
        "type": "update_registration",
        "data": {
            "app_version": "1.0",
            "device_name": _get_device_name(),
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "os_version": OS_VERSION,
            "app_data": {
                "push_websocket_channel": True,
            },
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as response:
                if response.status in (200, 201):
                    logger.info("[MobileApp] Registration updated (push_websocket_channel=True)")
                    return True
                elif response.status in (404, 410):
                    logger.warning(f"[MobileApp] Webhook gone (HTTP {response.status})")
                    return False
                else:
                    # Other errors (e.g. 500) — assume still valid, don't wipe registration
                    logger.warning(f"[MobileApp] Registration update returned HTTP {response.status}")
                    return True
    except Exception as e:
        # Network error — don't assume the registration is lost
        logger.warning(f"[MobileApp] Registration update error (non-critical): {e}")
        return True
