"""Local Jibo battery percentage."""

from datetime import timedelta
import math

import aiohttp
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .camera_stream_client import CameraStreamClient
from .const import DOMAIN


SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    # Local authenticated entities are only supported on the native pairing path.
    client = data.get("camera_stream")
    if client is not None:
        async_add_entities([JiboBatterySensor(client, entry, data["name"])], update_before_add=True)


class JiboBatterySensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 0
    _attr_should_poll = True

    def __init__(self, client: CameraStreamClient, entry, name) -> None:
        self._client = client
        self._session = async_get_clientsession(client.hass)
        self._attr_available = False
        self._attr_native_value = None
        self._attr_unique_id = f"{entry.entry_id}_battery"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": name,
            "manufacturer": "Jibo Inc.", "model": "Jibo",
        }

    async def async_update(self) -> None:
        try:
            url = self._client.endpoint("", api="battery")
            async with self._session.get(
                url, headers=self._client.headers(), allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
            ) as response:
                if response.status != 200:
                    raise UpdateFailed("Battery reading unavailable")
                body = await response.json()
                value = body.get("battery") if isinstance(body, dict) else None
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise UpdateFailed("Invalid battery reading")
        except (aiohttp.ClientError, TimeoutError, ValueError, HomeAssistantError):
            self._attr_available = False
            self._attr_native_value = None
            return
        self._attr_native_value = value
        self._attr_available = True
