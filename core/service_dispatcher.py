import logging
import time

logger = logging.getLogger(__name__)

# (domain, service, entity_id or None, service data)
ServiceCall = tuple[str, str, str | None, dict]


class ServiceDispatcher:
    """Handles dispatching of Home Assistant services based on UI actions."""

    def __init__(self, ha_client):
        self.ha_client = ha_client
        self._last_click_time: dict[str, float] = {}  # entity_id -> timestamp
        self._click_cooldown = 0.5

    async def handle_button_click(self, config: dict):
        """Process a standard button click."""
        entity_id = config.get('entity_id', '')
        if not entity_id or self._debounced(entity_id, config):
            return

        call = self._explicit_service_call(config, entity_id)
        if call is None:
            call = await self._service_call_for_type(config, entity_id)
        if call is None:
            return
        domain, service, target, data = call
        await self.ha_client.call_service(domain, service, target, data)

    async def handle_volume_scroll(self, entity_id: str, volume: float):
        """Handle mouse wheel scroll for volume adjustment."""
        await self.ha_client.call_service('media_player', 'volume_set', entity_id, {'volume_level': volume})

    async def handle_media_command(self, entity_id: str, command: str):
        """Handle specific media playback commands."""
        await self.ha_client.call_service('media_player', command, entity_id)

    # ── Click → service call ──

    def _debounced(self, entity_id: str, config: dict) -> bool:
        """True if this click follows the previous one on the same entity too closely."""
        now = time.time()
        last = self._last_click_time.get(entity_id, 0)
        if not config.get('skip_debounce', False) and now - last < self._click_cooldown:
            return True
        self._last_click_time[entity_id] = now
        return False

    @staticmethod
    def _explicit_service_call(config: dict, entity_id: str) -> ServiceCall | None:
        """A service named in the config (e.g. by the dimmer) wins over the tile type."""
        full_service = config.get('service')
        if not full_service:
            return None
        if '.' in full_service:
            domain, service = full_service.split('.', 1)
        else:
            domain, service = 'homeassistant', full_service
        return domain, service, entity_id, config.get('service_data', {})

    async def _service_call_for_type(self, config: dict, entity_id: str) -> ServiceCall | None:
        """The default action of a tile type; None means do nothing."""
        btn_type = config.get('type', 'switch')
        stateful = self._STATEFUL_HANDLERS.get(btn_type)
        if stateful is not None:
            return await stateful(self, config, entity_id)
        handler = _STATIC_HANDLERS.get(btn_type)
        if handler is not None:
            return handler(config, entity_id)
        if config.get('action') == 'set_input_number':
            return 'input_number', 'set_value', entity_id, {'value': config.get('value', 0.0)}
        return 'homeassistant', 'toggle', entity_id, {}

    async def _lock(self, config: dict, entity_id: str) -> ServiceCall | None:
        action = config.get('action')
        if action in ('lock', 'unlock'):
            # Explicit request (tile setting, or the tap/hold the user made).
            return 'lock', action, entity_id, {}
        current = await self._current_state(entity_id)
        if current is None:
            # Never guess a direction for a lock: a failed read must not
            # turn into an unlock.
            logger.warning("Lock toggle skipped: could not read state of %s", entity_id)
            return None
        return 'lock', 'unlock' if current == 'locked' else 'lock', entity_id, {}

    async def _lawn_mower(self, config: dict, entity_id: str) -> ServiceCall:
        current = await self._current_state(entity_id) or 'docked'
        service = 'pause' if current in ('mowing', 'returning') else 'start_mowing'
        return 'lawn_mower', service, entity_id, {}

    async def _vacuum(self, config: dict, entity_id: str) -> ServiceCall:
        current = await self._current_state(entity_id) or 'docked'
        service = 'pause' if current in ('cleaning', 'returning') else 'start'
        return 'vacuum', service, entity_id, {}

    async def _current_state(self, entity_id: str) -> str | None:
        state = await self.ha_client.get_state(entity_id)
        return state.get('state') if state else None

    # Tile types whose action depends on the entity's current state.
    _STATEFUL_HANDLERS = {
        'lock': _lock,
        'lawn_mower': _lawn_mower,
        'vacuum': _vacuum,
    }


def _curtain(config: dict, entity_id: str) -> ServiceCall:
    return 'cover', 'toggle', entity_id, {}


def _media_player(config: dict, entity_id: str) -> ServiceCall:
    return 'media_player', config.get('action', 'media_play_pause'), entity_id, {}


def _script(config: dict, entity_id: str) -> ServiceCall:
    variables = config.get('script_variables', {})
    if variables:
        # Call the script by name so variables arrive as top-level data.
        return 'script', entity_id.replace('script.', '', 1), None, variables
    return 'script', 'turn_on', entity_id, {}


def _automation(config: dict, entity_id: str) -> ServiceCall:
    service = 'trigger' if config.get('action') == 'trigger' else 'toggle'
    return 'automation', service, entity_id, {}


def _scene(config: dict, entity_id: str) -> ServiceCall:
    return 'scene', 'turn_on', entity_id, {}


_STATIC_HANDLERS = {
    'curtain': _curtain,
    'media_player': _media_player,
    'script': _script,
    'automation': _automation,
    'scene': _scene,
}
