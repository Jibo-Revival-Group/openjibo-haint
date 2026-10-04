"""Explicit camera stream controls; viewing never starts capture."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

BUTTONS = (
    ("start", "Start Camera Stream", "mdi:video"),
    ("stop", "Stop Camera Stream", "mdi:video-off"),
    ("toggle", "Toggle Camera Stream", "mdi:video-switch"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data.get("camera_stream")
    if client is not None:
        async_add_entities([JiboCameraButton(client, entry, data["name"], *button) for button in BUTTONS])


class JiboCameraButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, client, entry, name, action, label, icon) -> None:
        super().__init__(client)
        self._action = action
        self._attr_unique_id = f"{entry.entry_id}_camera_stream_{action}"
        self._attr_name = label
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": name,
            "manufacturer": "Jibo Inc.", "model": "Jibo",
        }

    async def async_press(self) -> None:
        await self.coordinator.async_command(self._action)
