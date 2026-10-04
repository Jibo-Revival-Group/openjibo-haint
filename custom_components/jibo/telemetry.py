"""Shared local robot telemetry polling."""

from datetime import timedelta
import logging
import math

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)
RANGES = {
    "battery": (0, 100), "fan_speed": (0, 100), "speaker_volume": (0, 100),
    "battery_temperature": (-100, 200), "main_board_temperature": (-100, 200),
    "cpu_temperature": (-100, 200), "system_voltage": (0, 100),
}


class JiboTelemetry(DataUpdateCoordinator):
    def __init__(self, hass, client, entry):
        super().__init__(hass, _LOGGER, name=f"{entry.title} telemetry", update_interval=timedelta(seconds=30))
        self._client = client
        self._session = async_get_clientsession(hass)

    async def _async_update_data(self):
        try:
            async with self._session.get(
                self._client.endpoint("", api="telemetry"), headers=self._client.headers(),
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=10, connect=5),
            ) as response:
                if response.status != 200:
                    raise UpdateFailed("Jibo telemetry is unavailable")
                body = await response.json()
                if not isinstance(body, dict):
                    raise UpdateFailed("Invalid Jibo telemetry")
        except (aiohttp.ClientError, TimeoutError, ValueError, HomeAssistantError):
            raise UpdateFailed("Cannot read Jibo telemetry") from None
        result = {}
        for key, (low, high) in RANGES.items():
            value = body.get(key)
            result[key] = value if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high else None
        for key in ("plugged_in", "hatch_open"):
            value = body.get(key)
            result[key] = value if isinstance(value, bool) else None
        return result
