import asyncio
import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_JIBO_IP, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)
_CONNECT_TIMEOUT = 5.0
_JIBO_PORT = 8089


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [JiboConnectivitySensor(entry, data.get("jibo_ip", ""), data["name"], data.get("coordinator"))],
        update_before_add=True,
    )
    telemetry = data.get("telemetry")
    if telemetry is not None:
        async_add_entities([
            JiboTelemetryBinarySensor(telemetry, entry, data["name"], "plugged_in", "Plugged in", BinarySensorDeviceClass.PLUG),
            JiboTelemetryBinarySensor(telemetry, entry, data["name"], "hatch_open", "Hatch State", BinarySensorDeviceClass.OPENING),
        ])
    activity = data.get("activity")
    if activity is not None:
        async_add_entities([
            JiboTelemetryBinarySensor(activity, entry, data["name"], "head_touch", "Head Touch", None),
            JiboTelemetryBinarySensor(activity, entry, data["name"], "sleeping", "Sleeping", None),
        ])


class JiboTelemetryBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, name, key, label, device_class):
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = label
        self._attr_device_class = device_class
        if key == "sleeping":
            self._attr_icon = "mdi:sleep"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": name,
            "manufacturer": "Jibo Inc.", "model": "Jibo",
        }

    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.coordinator.data
                    and self.coordinator.data.get(self._key) is not None)

    @property
    def is_on(self):
        return self.coordinator.data.get(self._key) if self.available else None


class JiboConnectivitySensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_should_poll = True

    def __init__(self, entry: ConfigEntry, ip: str, name: str, coordinator) -> None:
        self._entry = entry
        self._ip = ip
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_name = f"{name} Online"
        self._attr_is_on = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": name,
            "manufacturer": "Jibo Inc.",
            "model": "Jibo",
        }

    async def async_update(self) -> None:
        self._ip = self._entry.data.get(CONF_JIBO_IP, "") or self._ip
        if self._ip:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._ip, _JIBO_PORT),
                    timeout=_CONNECT_TIMEOUT,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                self._attr_is_on = True
            except (asyncio.TimeoutError, OSError):
                self._attr_is_on = False
            return

        self._attr_is_on = bool(self._coordinator and self._coordinator.connected)
