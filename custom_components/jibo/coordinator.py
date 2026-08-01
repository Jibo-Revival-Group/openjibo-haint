import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .command_auth import verify as verify_command_auth
from .const import (
    CONF_COMMAND_SECRET,
    CONF_INSTANCE_ID,
    CONF_JIBO_FRIENDLY_NAME,
    CONF_LINK_ID,
    CONF_SERVER_URL,
    DOMAIN,
    NOTIFICATION_ID_PREFIX,
)
from .websocket_client import OpenJiboWebSocketClient

_LOGGER = logging.getLogger(__name__)
_LIGHT_SUFFIXES = (" light", " lights", " lamp", " lamps")
_CLIMATE_SUFFIXES = (" thermostat", " hvac", " heat", " ac")
_DEFAULT_CLIMATE_DELTA = 2.0
_NONCE_TTL_SECONDS = 120


class JiboCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates the OpenJibo server WebSocket connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
        )
        self.entry = entry
        self._client = OpenJiboWebSocketClient(
            entry.data[CONF_SERVER_URL],
            entry.data[CONF_INSTANCE_ID],
            self._handle_message,
            entry.data.get(CONF_LINK_ID),
            entry.data.get(CONF_COMMAND_SECRET),
        )
        self._blacklist_heat = False
        self._blacklist_cool = False
        self._seen_nonces: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        return self._client.connected

    async def async_start(self) -> None:
        await self._client.start()

    async def async_shutdown(self) -> None:
        await self._client.stop()

    async def _handle_message(self, payload: dict[str, Any]) -> None:
        message_type = payload.get("type")
        if message_type == "verification_code":
            if self.entry.data.get(CONF_LINK_ID):
                _LOGGER.debug("Ignoring verification code; Home Assistant is already paired")
                return

            code = payload.get("code", "")
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "OpenJibo Pairing Code",
                    "message": (
                        f"Your OpenJibo verification code is **{code}**. "
                        "Enter this code in the OpenJibo portal after verifying your Jibo."
                    ),
                    "notification_id": notification_id,
                },
            )
            self.async_set_updated_data(
                {
                    "verification_code": code,
                    "paired": False,
                }
            )
            return

        if message_type == "paired":
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )

            link_id = payload.get("linkId")
            command_secret = payload.get("commandSecret")
            if not isinstance(command_secret, str) or not command_secret:
                command_secret = self.entry.data.get(CONF_COMMAND_SECRET)

            updates: dict[str, Any] = {
                CONF_LINK_ID: link_id,
                CONF_JIBO_FRIENDLY_NAME: payload.get("jiboFriendlyName"),
            }
            if isinstance(command_secret, str) and command_secret:
                updates[CONF_COMMAND_SECRET] = command_secret

            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, **{k: v for k, v in updates.items() if v}},
            )
            self._client.set_pairing(
                link_id if isinstance(link_id, str) else None,
                command_secret if isinstance(command_secret, str) else None,
            )
            self.async_set_updated_data(
                {
                    "verification_code": None,
                    "paired": True,
                    "jibo_friendly_name": payload.get("jiboFriendlyName"),
                    "link_id": link_id,
                }
            )
            return

        if message_type == "unpaired":
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )

            cleared_data = {
                key: value
                for key, value in self.entry.data.items()
                if key not in {CONF_LINK_ID, CONF_JIBO_FRIENDLY_NAME, CONF_COMMAND_SECRET}
            }
            self.hass.config_entries.async_update_entry(self.entry, data=cleared_data)
            self._client.clear_pairing()
            self._seen_nonces.clear()
            self.async_set_updated_data(
                {
                    "verification_code": None,
                    "paired": False,
                    "jibo_friendly_name": None,
                    "link_id": None,
                }
            )
            await self._client.force_reconnect()
            return

        if message_type == "error":
            _LOGGER.error("OpenJibo server error: %s", payload.get("message"))
            return

        if message_type == "command":
            await self._handle_command(payload)
            return

    def _purge_expired_nonces(self, now: float) -> None:
        expired = [
            nonce
            for nonce, seen_at in self._seen_nonces.items()
            if now - seen_at > _NONCE_TTL_SECONDS
        ]
        for nonce in expired:
            self._seen_nonces.pop(nonce, None)

    def _verify_command_auth(self, payload: dict[str, Any]) -> bool:
        command_secret = self.entry.data.get(CONF_COMMAND_SECRET) or self._client.command_secret
        link_id = self.entry.data.get(CONF_LINK_ID) or self._client.link_id
        if not command_secret or not link_id:
            return False

        if not verify_command_auth(
            payload,
            command_secret,
            expected_link_id=link_id,
        ):
            return False

        nonce = str(payload.get("nonce") or "")
        now = time.time()
        self._purge_expired_nonces(now)
        if nonce in self._seen_nonces:
            return False

        self._seen_nonces[nonce] = now
        return True

    async def _send_command_result(
        self,
        request_id: str | None,
        status: str,
        *,
        matched_name: str | None = None,
        heard_name: str | None = None,
        candidates: list[dict[str, str]] | None = None,
        message: str | None = None,
        current_temperature: float | None = None,
        unit: str | None = None,
    ) -> None:
        if not request_id:
            return

        payload: dict[str, Any] = {
            "type": "command_result",
            "requestId": request_id,
            "status": status,
        }
        if matched_name:
            payload["matchedName"] = matched_name
        if heard_name:
            payload["heardName"] = heard_name
        if candidates:
            payload["candidates"] = candidates
        if message:
            payload["message"] = message
        if current_temperature is not None:
            payload["currentTemperature"] = current_temperature
        if unit:
            payload["unit"] = unit

        sent = await self._client.async_send_json(payload)
        if not sent:
            _LOGGER.warning("Failed to send command_result for request %s", request_id)

    async def _handle_command(self, payload: dict[str, Any]) -> None:
        command = payload.get("command")
        request_id = payload.get("requestId")

        if not self._verify_command_auth(payload):
            _LOGGER.warning(
                "Rejected OpenJibo command %s: authentication failed",
                command,
            )
            await self._send_command_result(
                request_id, "error", message="auth_failed"
            )
            return

        try:
            if command == "lights_off_current_room":
                await self._handle_lights_room("turn_off")
                await self._send_command_result(request_id, "ok")
                return

            if command == "lights_on_current_room":
                await self._handle_lights_room("turn_on")
                await self._send_command_result(request_id, "ok")
                return

            if command == "lights_off_named":
                await self._handle_lights_named("turn_off", payload.get("targetName"), request_id)
                return

            if command == "lights_on_named":
                await self._handle_lights_named("turn_on", payload.get("targetName"), request_id)
                return

            if command == "climate_set_temperature_current_room":
                self._apply_blacklist_from_payload(payload)
                await self._handle_climate_room_set_temp(payload.get("temperature"), request_id)
                return

            if command == "climate_set_temperature_named":
                self._apply_blacklist_from_payload(payload)
                await self._handle_climate_named_set_temp(
                    payload.get("targetName"), payload.get("temperature"), request_id
                )
                return

            if command == "climate_cool_down_current_room":
                self._apply_blacklist_from_payload(payload)
                await self._handle_climate_room_adjust(
                    -self._parse_delta(payload.get("delta")), request_id
                )
                return

            if command == "climate_warm_up_current_room":
                self._apply_blacklist_from_payload(payload)
                await self._handle_climate_room_adjust(
                    self._parse_delta(payload.get("delta")), request_id
                )
                return

            if command == "climate_get_temperature_current_room":
                await self._handle_climate_room_get_temp(request_id)
                return

            if command == "climate_get_temperature_named":
                await self._handle_climate_named_get_temp(
                    payload.get("targetName"), request_id
                )
                return

            if command == "climate_apply_entity":
                self._apply_blacklist_from_payload(payload)
                await self._handle_climate_apply_entity(payload, request_id)
                return

            _LOGGER.warning("OpenJibo server sent unknown command: %s", command)
            await self._send_command_result(
                request_id, "error", message=f"unknown command: {command}"
            )
        except Exception as err:  # noqa: BLE001 - report failure to cloud
            _LOGGER.exception("OpenJibo command %s failed: %s", command, err)
            await self._send_command_result(request_id, "error", message=str(err))

    async def _handle_lights_room(self, service: str) -> None:
        area_id = self._get_jibo_area_id()
        if area_id is None:
            return

        await self.hass.services.async_call(
            "light",
            service,
            target={"area_id": [area_id]},
        )
        _LOGGER.info("Called light.%s for area %s", service, area_id)

    async def _handle_lights_named(
        self,
        service: str,
        target_name: str | None,
        request_id: str | None = None,
    ) -> None:
        heard_name = (target_name or "").strip() or "that light"
        if not target_name:
            _LOGGER.warning("OpenJibo named light command missing targetName")
            await self._send_command_result(
                request_id, "not_found", heard_name=heard_name
            )
            return

        area_id = self._get_jibo_area_id()
        entity_id = self._find_matching_light(target_name, area_id)
        if entity_id is None:
            _LOGGER.warning("No light matched target %r (jibo area %s)", target_name, area_id)
            await self._send_command_result(
                request_id, "not_found", heard_name=heard_name
            )
            return

        await self.hass.services.async_call(
            "light",
            service,
            {"entity_id": entity_id},
        )
        matched_name = self._friendly_name(entity_id)
        _LOGGER.info(
            "Called light.%s for entity %s (target %r -> %s)",
            service,
            entity_id,
            target_name,
            matched_name,
        )
        await self._send_command_result(
            request_id, "ok", matched_name=matched_name, heard_name=heard_name
        )

    async def _handle_climate_room_set_temp(
        self, temperature: Any, request_id: str | None = None
    ) -> None:
        from homeassistant.helpers import entity_registry as er

        parsed_temperature = self._parse_temperature(temperature)
        if parsed_temperature is None:
            _LOGGER.warning("OpenJibo climate set command missing valid temperature")
            await self._send_command_result(
                request_id, "error", message="missing temperature"
            )
            return

        resolution = self._resolve_room_climate_entities(er.async_get(self.hass))
        if resolution["status"] == "not_found":
            await self._send_command_result(request_id, "not_found")
            return
        if resolution["status"] == "needs_clarification":
            await self._send_command_result(
                request_id,
                "needs_clarification",
                candidates=resolution["candidates"],
            )
            return

        entity_ids = resolution["entity_ids"]
        for entity_id in entity_ids:
            await self._set_climate_temperature(entity_id, parsed_temperature)

        matched_name = (
            self._friendly_name(entity_ids[0]) if len(entity_ids) == 1 else None
        )
        await self._send_command_result(
            request_id, "ok", matched_name=matched_name
        )

    async def _handle_climate_named_set_temp(
        self,
        target_name: str | None,
        temperature: Any,
        request_id: str | None = None,
    ) -> None:
        if not target_name:
            _LOGGER.warning("OpenJibo named climate command missing targetName")
            await self._send_command_result(
                request_id, "not_found", heard_name=target_name or "that thermostat"
            )
            return

        parsed_temperature = self._parse_temperature(temperature)
        if parsed_temperature is None:
            _LOGGER.warning("OpenJibo named climate command missing valid temperature")
            await self._send_command_result(
                request_id, "error", message="missing temperature"
            )
            return

        area_id = self._get_jibo_area_id()
        entity_id = self._find_matching_climate(target_name, area_id)
        if entity_id is None:
            _LOGGER.warning("No climate entity matched target %r", target_name)
            await self._send_command_result(
                request_id, "not_found", heard_name=target_name
            )
            return

        await self._set_climate_temperature(entity_id, parsed_temperature)
        matched_name = self._friendly_name(entity_id)
        await self._send_command_result(
            request_id, "ok", matched_name=matched_name, heard_name=target_name
        )

    async def _handle_climate_room_adjust(
        self, delta: float, request_id: str | None = None
    ) -> None:
        from homeassistant.helpers import entity_registry as er

        resolution = self._resolve_room_climate_entities(er.async_get(self.hass))
        if resolution["status"] == "not_found":
            await self._send_command_result(request_id, "not_found")
            return
        if resolution["status"] == "needs_clarification":
            await self._send_command_result(
                request_id,
                "needs_clarification",
                candidates=resolution["candidates"],
            )
            return

        entity_ids = resolution["entity_ids"]
        for entity_id in entity_ids:
            await self._adjust_climate_entity(entity_id, delta)

        matched_name = (
            self._friendly_name(entity_ids[0]) if len(entity_ids) == 1 else None
        )
        await self._send_command_result(
            request_id, "ok", matched_name=matched_name
        )

    async def _handle_climate_room_get_temp(
        self, request_id: str | None = None
    ) -> None:
        from homeassistant.helpers import entity_registry as er

        resolution = self._resolve_room_climate_entities(er.async_get(self.hass))
        if resolution["status"] == "not_found":
            await self._send_command_result(request_id, "not_found")
            return
        if resolution["status"] == "needs_clarification":
            await self._send_command_result(
                request_id,
                "needs_clarification",
                candidates=resolution["candidates"],
            )
            return

        entity_ids = resolution["entity_ids"]
        # Reads need a single ambient value; multi-thermostat rooms clarify.
        if len(entity_ids) > 1:
            candidates = [
                {"entityId": entity_id, "name": self._friendly_name(entity_id)}
                for entity_id in entity_ids
            ]
            await self._send_command_result(
                request_id,
                "needs_clarification",
                candidates=candidates,
            )
            return

        await self._send_climate_temperature_reading(entity_ids[0], request_id)

    async def _handle_climate_named_get_temp(
        self,
        target_name: str | None,
        request_id: str | None = None,
    ) -> None:
        if not target_name:
            _LOGGER.warning("OpenJibo named climate get command missing targetName")
            await self._send_command_result(
                request_id, "not_found", heard_name=target_name or "that thermostat"
            )
            return

        area_id = self._get_jibo_area_id()
        entity_id = self._find_matching_climate(target_name, area_id)
        if entity_id is None:
            _LOGGER.warning("No climate entity matched target %r", target_name)
            await self._send_command_result(
                request_id, "not_found", heard_name=target_name
            )
            return

        await self._send_climate_temperature_reading(
            entity_id, request_id, heard_name=target_name
        )

    async def _handle_climate_apply_entity(
        self, payload: dict[str, Any], request_id: str | None = None
    ) -> None:
        entity_id = payload.get("entityId")
        if not entity_id or not isinstance(entity_id, str):
            await self._send_command_result(
                request_id, "error", message="missing entityId"
            )
            return

        action = str(payload.get("action") or "").lower()
        if action == "get_temperature":
            await self._send_climate_temperature_reading(entity_id, request_id)
            return

        if action == "set_temperature":
            parsed_temperature = self._parse_temperature(payload.get("temperature"))
            if parsed_temperature is None:
                await self._send_command_result(
                    request_id, "error", message="missing temperature"
                )
                return
            await self._set_climate_temperature(entity_id, parsed_temperature)
        elif action == "cool_down":
            await self._adjust_climate_entity(
                entity_id, -self._parse_delta(payload.get("delta"))
            )
        elif action == "warm_up":
            await self._adjust_climate_entity(
                entity_id, self._parse_delta(payload.get("delta"))
            )
        else:
            await self._send_command_result(
                request_id, "error", message=f"unknown action: {action}"
            )
            return

        await self._send_command_result(
            request_id, "ok", matched_name=self._friendly_name(entity_id)
        )

    async def _send_climate_temperature_reading(
        self,
        entity_id: str,
        request_id: str | None = None,
        *,
        heard_name: str | None = None,
    ) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unavailable", "unknown"}:
            await self._send_command_result(
                request_id,
                "error",
                message=f"climate entity {entity_id} unavailable",
                heard_name=heard_name,
            )
            return

        current_temp = state.attributes.get("current_temperature")
        if current_temp is None:
            await self._send_command_result(
                request_id,
                "error",
                message=f"climate entity {entity_id} has no current temperature",
                heard_name=heard_name,
                matched_name=self._friendly_name(entity_id),
            )
            return

        try:
            current_value = float(current_temp)
        except (TypeError, ValueError):
            await self._send_command_result(
                request_id,
                "error",
                message=f"climate entity {entity_id} has invalid current temperature",
                heard_name=heard_name,
                matched_name=self._friendly_name(entity_id),
            )
            return

        unit = self._temperature_unit()
        await self._send_command_result(
            request_id,
            "ok",
            matched_name=self._friendly_name(entity_id),
            heard_name=heard_name,
            current_temperature=current_value,
            unit=unit,
        )

    def _temperature_unit(self) -> str:
        unit = getattr(self.hass.config.units, "temperature_unit", None)
        if unit is None:
            return "°F"
        unit_text = str(unit)
        if unit_text in {"°C", "C", "celsius", "Celsius"}:
            return "°C"
        if unit_text in {"°F", "F", "fahrenheit", "Fahrenheit"}:
            return "°F"
        return unit_text if unit_text.startswith("°") else f"°{unit_text}"

    def _resolve_room_climate_entities(self, entity_registry: Any) -> dict[str, Any]:
        area_id = self._get_jibo_area_id()
        if area_id is None:
            return {"status": "not_found", "entity_ids": [], "candidates": []}

        room_entities = self._list_climate_entity_ids(entity_registry, area_id)
        if room_entities:
            return {
                "status": "ok",
                "entity_ids": room_entities,
                "candidates": [],
            }

        floor_entities = self._list_climate_entity_ids_on_floor(entity_registry, area_id)
        if not floor_entities:
            return {"status": "not_found", "entity_ids": [], "candidates": []}
        if len(floor_entities) == 1:
            return {
                "status": "ok",
                "entity_ids": floor_entities,
                "candidates": [],
            }

        candidates = [
            {"entityId": entity_id, "name": self._friendly_name(entity_id)}
            for entity_id in floor_entities
        ]
        return {
            "status": "needs_clarification",
            "entity_ids": [],
            "candidates": candidates,
        }

    def _list_climate_entity_ids_on_floor(
        self, entity_registry: Any, area_id: str
    ) -> list[str]:
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr

        area_reg = ar.async_get(self.hass)
        area = area_reg.async_get_area(area_id)
        if area is None or not area.floor_id:
            return []

        floor_area_ids = {
            entry.id
            for entry in ar.async_entries_for_floor(area_reg, area.floor_id)
        }
        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "climate":
                continue
            entity_area_id = self._resolve_entity_area_id(
                entity_registry, device_registry, entity
            )
            if entity_area_id in floor_area_ids:
                candidates.append(entity.entity_id)
        return candidates

    async def _adjust_climate_entity(self, entity_id: str, delta: float) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unavailable", "unknown"}:
            _LOGGER.warning("Climate entity %s unavailable for adjustment", entity_id)
            return

        current = state.attributes.get("temperature")
        if current is None:
            _LOGGER.warning("Climate entity %s has no setpoint to adjust", entity_id)
            return

        min_temp = state.attributes.get("min_temp")
        max_temp = state.attributes.get("max_temp")
        new_temp = float(current) + delta

        if min_temp is not None:
            new_temp = max(float(min_temp), new_temp)
        if max_temp is not None:
            new_temp = min(float(max_temp), new_temp)

        preferred_mode = "cool" if delta < 0 else "heat" if delta > 0 else None
        await self._set_climate_temperature(entity_id, new_temp, preferred_mode=preferred_mode)
        _LOGGER.info("Adjusted climate entity %s from %s to %s", entity_id, current, new_temp)

    async def _set_climate_temperature(
        self,
        entity_id: str,
        temperature: float,
        preferred_mode: str | None = None,
    ) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unavailable", "unknown"}:
            _LOGGER.warning("Climate entity %s unavailable for temperature set", entity_id)
            return

        mode = self._resolve_hvac_mode_for_target(state, temperature, preferred_mode)
        if mode is not None:
            await self._ensure_hvac_mode(entity_id, mode)

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": temperature},
        )

    def _resolve_hvac_mode_for_target(
        self,
        state: Any,
        target_temp: float,
        preferred_mode: str | None = None,
    ) -> str | None:
        blacklist_heat, blacklist_cool = self._hvac_blacklist()
        if blacklist_heat and blacklist_cool:
            _LOGGER.info(
                "Skipping HVAC mode change; heating and cooling are both blacklisted"
            )
            return None

        supported = {
            str(mode).lower()
            for mode in (state.attributes.get("hvac_modes") or [])
            if mode is not None
        }
        if not supported:
            return None

        allow_heat_cool = not blacklist_heat and not blacklist_cool

        def pick(candidate: str) -> str | None:
            candidate = candidate.lower()
            if candidate == "heat" and blacklist_heat:
                _LOGGER.info("Skipping HVAC mode heat; heating is blacklisted")
                return None
            if candidate == "cool" and blacklist_cool:
                _LOGGER.info("Skipping HVAC mode cool; cooling is blacklisted")
                return None
            if candidate in supported:
                return candidate
            if allow_heat_cool and "heat_cool" in supported:
                return "heat_cool"
            return None

        if preferred_mode:
            return pick(preferred_mode.lower())

        current_temp = state.attributes.get("current_temperature")
        if current_temp is None:
            return pick("heat_cool") if allow_heat_cool and "heat_cool" in supported else None

        try:
            current_value = float(current_temp)
        except (TypeError, ValueError):
            return pick("heat_cool") if allow_heat_cool and "heat_cool" in supported else None

        if target_temp > current_value:
            return pick("heat")
        if target_temp < current_value:
            return pick("cool")
        return None

    def _hvac_blacklist(self) -> tuple[bool, bool]:
        return self._blacklist_heat, self._blacklist_cool

    def _apply_blacklist_from_payload(self, payload: dict[str, Any]) -> None:
        if "blacklistHeat" in payload:
            self._blacklist_heat = _parse_bool(payload.get("blacklistHeat"))
        if "blacklistCool" in payload:
            self._blacklist_cool = _parse_bool(payload.get("blacklistCool"))

    async def _ensure_hvac_mode(self, entity_id: str, mode: str) -> None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return

        current_mode = str(state.state).lower()
        if current_mode == mode.lower():
            return

        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": mode},
        )
        _LOGGER.info("Set climate entity %s hvac_mode to %s (was %s)", entity_id, mode, current_mode)

    def _friendly_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        if state is not None and state.name:
            return str(state.name)
        return entity_id

    def _get_jibo_area_id(self) -> str | None:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
        if device is not None and device.area_id:
            return device.area_id

        entity_registry = er.async_get(self.hass)
        for entity in entity_registry.entities.values():
            if entity.config_entry_id != self.entry.entry_id or entity.platform != DOMAIN:
                continue

            area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
            if area_id:
                _LOGGER.info("Resolved OpenJibo area %s from entity %s", area_id, entity.entity_id)
                return area_id

        if device is None:
            _LOGGER.warning("OpenJibo device entry not found; cannot control room lights")
        else:
            _LOGGER.warning("OpenJibo device has no area assigned; cannot control room lights")
        return None

    def _resolve_entity_area_id(self, entity_registry, device_registry, entity) -> str | None:
        if entity.area_id:
            return entity.area_id

        if not entity.device_id:
            return None

        device = device_registry.async_get(entity.device_id)
        if device is None or not device.area_id:
            return None

        return device.area_id

    def _find_matching_light(self, target_name: str, area_id: str | None) -> str | None:
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        normalized_target = _normalize_light_name(target_name)
        if not normalized_target:
            return None

        # House-wide search; same-room and same-floor only affect ranking.
        all_candidates = self._list_light_entity_ids(entity_registry, None)
        floor_area_ids = self._floor_area_ids_for_area(area_id) if area_id else set()
        return self._match_light_entity(
            normalized_target,
            all_candidates,
            preferred_area_id=area_id,
            preferred_floor_area_ids=floor_area_ids,
        )

    def _floor_area_ids_for_area(self, area_id: str) -> set[str]:
        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(self.hass)
        area = area_reg.async_get_area(area_id)
        if area is None or not area.floor_id:
            return set()

        return {
            entry.id
            for entry in ar.async_entries_for_floor(area_reg, area.floor_id)
        }

    def _list_light_entity_ids(
        self,
        entity_registry: Any,
        area_id: str | None,
    ) -> list[str]:
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "light":
                continue

            if area_id is not None:
                entity_area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
                if entity_area_id != area_id:
                    continue

            candidates.append(entity.entity_id)
        return candidates

    def _light_location_bonus(
        self,
        entity_id: str,
        preferred_area_id: str | None,
        preferred_floor_area_ids: set[str],
    ) -> float:
        """Medium room boost, mild same-floor boost."""
        if preferred_area_id is None:
            return 0.0

        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        entity = entity_registry.async_get(entity_id)
        if entity is None:
            return 0.0

        entity_area_id = self._resolve_entity_area_id(
            entity_registry, dr.async_get(self.hass), entity
        )
        if entity_area_id is None:
            return 0.0
        if entity_area_id == preferred_area_id:
            return 0.25  # medium: same room
        if entity_area_id in preferred_floor_area_ids:
            return 0.10  # mild: same floor
        return 0.0

    def _match_light_entity(
        self,
        normalized_target: str,
        entity_ids: list[str],
        preferred_area_id: str | None = None,
        preferred_floor_area_ids: set[str] | None = None,
    ) -> str | None:
        floor_ids = preferred_floor_area_ids or set()
        best: tuple[float, int, str] | None = None
        # Sort key: (-score, distance, entity_id) so higher score wins;
        # distance breaks ties toward closer name matches.

        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            friendly_name = state.name if state is not None else entity_id
            normalized_friendly = _normalize_light_name(friendly_name)
            if not normalized_friendly:
                continue

            distance = _levenshtein_distance(normalized_target, normalized_friendly)
            max_len = max(len(normalized_target), len(normalized_friendly), 1)
            similarity = 1.0 - (distance / max_len)
            threshold = max(2, len(normalized_target) // 3)

            if normalized_friendly == normalized_target:
                name_score = 1.0
            elif (
                normalized_target in normalized_friendly
                or normalized_friendly in normalized_target
            ):
                name_score = 0.85
            elif distance <= threshold or similarity >= 0.55:
                name_score = similarity
            else:
                continue

            location_bonus = self._light_location_bonus(
                entity_id, preferred_area_id, floor_ids
            )
            score = name_score + location_bonus
            ranked = (-score, distance, entity_id)
            if best is None or ranked < best:
                best = ranked

        return best[2] if best is not None else None

    def _find_matching_climate(self, target_name: str, area_id: str | None) -> str | None:
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        normalized_target = _normalize_climate_name(target_name)
        if not normalized_target:
            return None

        area_candidates = self._list_climate_entity_ids(entity_registry, area_id)
        match = self._match_climate_entity(normalized_target, area_candidates)
        if match is not None:
            return match

        if area_id is not None:
            all_candidates = self._list_climate_entity_ids(entity_registry, None)
            return self._match_climate_entity(normalized_target, all_candidates)

        return None

    def _list_climate_entity_ids(
        self,
        entity_registry: Any,
        area_id: str | None,
    ) -> list[str]:
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "climate":
                continue

            if area_id is not None:
                entity_area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
                if entity_area_id != area_id:
                    continue

            candidates.append(entity.entity_id)
        return candidates

    def _match_climate_entity(self, normalized_target: str, entity_ids: list[str]) -> str | None:
        exact_match: str | None = None
        partial_match: str | None = None

        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            friendly_name = state.name if state is not None else entity_id
            normalized_friendly = _normalize_climate_name(friendly_name)
            if not normalized_friendly:
                continue

            if normalized_friendly == normalized_target:
                exact_match = entity_id
                break

            if (
                normalized_target in normalized_friendly
                or normalized_friendly in normalized_target
            ) and partial_match is None:
                partial_match = entity_id

        return exact_match or partial_match

    @staticmethod
    def _parse_temperature(value: Any) -> float | None:
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_delta(value: Any) -> float:
        if value is None:
            return _DEFAULT_CLIMATE_DELTA

        try:
            return float(value)
        except (TypeError, ValueError):
            return _DEFAULT_CLIMATE_DELTA


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _normalize_light_name(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"\s+", " ", normalized)
    for suffix in _LIGHT_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _normalize_climate_name(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"\s+", " ", normalized)
    for suffix in _CLIMATE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (0 if left_char == right_char else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _names_are_close(target: str, candidate: str) -> bool:
    normalized_target = target.lower().strip()
    normalized_candidate = candidate.lower().strip()
    if not normalized_target or not normalized_candidate:
        return False
    if (
        normalized_target == normalized_candidate
        or normalized_target in normalized_candidate
        or normalized_candidate in normalized_target
    ):
        return True

    distance = _levenshtein_distance(normalized_target, normalized_candidate)
    max_len = max(len(normalized_target), len(normalized_candidate), 1)
    similarity = 1.0 - (distance / max_len)
    threshold = max(2, len(normalized_target) // 3)
    return distance <= threshold or similarity >= 0.55
