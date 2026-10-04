import aiohttp
import voluptuous as vol
from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
import logging

from .const import CONF_COMMAND_SECRET, CONF_JIBO_IP, CONF_SERVER_MODE, CONF_WEBHOOK_ID, DOMAIN, PLATFORMS, is_beefy_mode
from .camera_stream_client import CameraStreamClient
from .coordinator import JiboCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

_SAY_SCHEMA = vol.Schema({
    vol.Required("message"): str,
    vol.Optional("robot"): str,
})


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry):
    hass.data.setdefault(DOMAIN, {})

    coordinator = JiboCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data[DOMAIN][entry.entry_id] = {
        "jibo_ip": entry.data.get(CONF_JIBO_IP, ""),
        "name": entry.data.get("name", entry.title),
        "coordinator": coordinator,
    }

    if is_beefy_mode(entry.data.get(CONF_SERVER_MODE)) and entry.data.get(CONF_COMMAND_SECRET):
        camera_client = CameraStreamClient(hass, entry)
        hass.data[DOMAIN][entry.entry_id]["camera_stream"] = camera_client
        # An offline robot leaves the entities unavailable rather than failing setup.
        await camera_client.async_refresh()

    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if is_beefy_mode(entry.data.get(CONF_SERVER_MODE)) and webhook_id:
        webhook.async_register(
            hass,
            DOMAIN,
            "Jibo",
            webhook_id,
            _handle_robot_webhook,
            local_only=True,
        )

    if not hass.services.has_service(DOMAIN, "say"):
        async def handle_say(call: ServiceCall):
            message = call.data["message"]
            robot_filter = call.data.get("robot")

            targets = [
                data["jibo_ip"]
                for data in hass.data[DOMAIN].values()
                if data.get("jibo_ip") and (robot_filter is None or data["name"] == robot_filter)
            ]

            for data in hass.data[DOMAIN].values():
                camera_client = data.get("camera_stream")
                if data.get("jibo_ip") in targets and camera_client and camera_client.data and camera_client.data.get("state") != "off":
                    raise HomeAssistantError("Stop Jibo's camera stream before asking him to speak")

            if not targets:
                _LOGGER.warning(
                    "No Jibo robot matched filter %r. Configured robots: %s",
                    robot_filter,
                    [d["name"] for d in hass.data[DOMAIN].values()],
                )
                return

            payload = {
                "prompt": message,
                "locale": "en-us",
                "voice": "griffin",
                "duration_stretch": 1,
                "pitch": 3,
                "pitchBandwidth": 0.4,
                "mode": "text",
                "outputMode": "stream",
                "timeout": None,
                "volume": 0,
                "whisper": "FALSE",
                "samplerate": 48000,
                "postfilter": 0.4,
                "framerate": 240,
                "unvoicedvoiced": 0.35,
                "allPass": 0.76,
                "gvMCEP": 0.9,
                "cached": "TRUE",
            }

            async with aiohttp.ClientSession() as session:
                for ip in targets:
                    url = f"http://{ip}:8089/tts_speak"
                    try:
                        async with session.post(url, json=payload) as response:
                            if response.status != 200:
                                _LOGGER.error(
                                    "Jibo at %s returned %s: %s",
                                    ip, response.status, await response.text(),
                                )
                    except aiohttp.ClientError as e:
                        _LOGGER.error("Error communicating with Jibo at %s: %s", ip, e)

        hass.services.async_register(DOMAIN, "say", handle_say, schema=_SAY_SCHEMA)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _handle_robot_webhook(hass: HomeAssistant, webhook_id: str, request: web.Request):
    coordinator = None
    for data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(data, dict):
            continue
        candidate = data.get("coordinator")
        if candidate and candidate.entry.data.get(CONF_WEBHOOK_ID) == webhook_id:
            coordinator = candidate
            break

    if coordinator is None:
        return web.Response(status=404, text="unknown webhook")

    try:
        payload = await request.json()
    except (ValueError, aiohttp.ContentTypeError):
        return web.json_response({"status": "error", "message": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"status": "error", "message": "invalid_json"}, status=400)

    result = await coordinator.async_handle_local_request(payload)
    status = 401 if result.get("message") == "auth_failed" else 200
    return web.json_response(result, status=status)


async def async_unload_entry(hass: HomeAssistant, entry):
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data and (camera_client := entry_data.get("camera_stream")):
            await camera_client.async_close()
        if entry_data and (coordinator := entry_data.get("coordinator")):
            await coordinator.async_shutdown()

        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "say")

    return unloaded
