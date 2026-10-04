"""Local Jibo battery percentage."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    # Local authenticated entities are only supported on the native pairing path.
    coordinator = data.get("telemetry")
    if coordinator is not None:
        async_add_entities([JiboTelemetrySensor(coordinator, entry, data["name"], *description)
                            for description in SENSORS])


SENSORS = (
    ("battery", "Battery", SensorDeviceClass.BATTERY, "%"),
    ("battery_temperature", "Battery Temp", SensorDeviceClass.TEMPERATURE, "°C"),
    ("main_board_temperature", "Main board Temp", SensorDeviceClass.TEMPERATURE, "°C"),
    ("cpu_temperature", "CPU Temp", SensorDeviceClass.TEMPERATURE, "°C"),
    ("system_voltage", "System Voltage", SensorDeviceClass.VOLTAGE, "V"),
    ("fan_speed", "Fan Speed", None, "%"),
    ("speaker_volume", "Speaker Volume", None, "%"),
)


class JiboTelemetrySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, name, key, label, device_class, unit):
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = label
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = 0 if unit == "%" else 1
        self._attr_icon = {"fan_speed": "mdi:fan", "speaker_volume": "mdi:volume-high"}.get(key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": name,
            "manufacturer": "Jibo Inc.", "model": "Jibo",
        }

    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.coordinator.data
                    and self.coordinator.data.get(self._key) is not None)

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key) if self.available else None
