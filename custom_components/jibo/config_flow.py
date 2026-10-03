import uuid

import aiohttp
import voluptuous as vol
from homeassistant import config_entries

from .const import (
    BEACON_PORT,
    BEEFY_MODES,
    CONF_COMMAND_SECRET,
    CONF_HA_PORT,
    CONF_INSTANCE_ID,
    CONF_JIBO_IP,
    CONF_SERVER_MODE,
    CONF_SERVER_URL,
    CONF_WEBHOOK_ID,
    DOMAIN,
    FIVE_X1_URL,
    MODE_5X1,
    MODE_OPENJIBO_COM,
    MODE_SELF_HOST_BEEFY,
    MODE_SELF_HOST_OPENJIBO,
    OPENJIBO_COM_URL,
    PAIR_PATH,
    PAIR_TIMEOUT_SECONDS,
)


class JiboConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(
            step_id="user",
            menu_options=[
                "five_x1",
                "self_host_beefy",
                "openjibo_com",
                "self_host_openjibo",
                "phoenix",
            ],
        )

    async def async_step_phoenix(self, user_input=None):
        return self.async_abort(reason="not_supported")

    async def async_step_five_x1(self, user_input=None):
        return await self._async_step_beefy(user_input, MODE_5X1, "five_x1")

    async def async_step_self_host_beefy(self, user_input=None):
        return await self._async_step_beefy(
            user_input, MODE_SELF_HOST_BEEFY, "self_host_beefy"
        )

    async def async_step_openjibo_com(self, user_input=None):
        errors = {}
        if user_input is not None:
            return await self._async_create_openjibo_entry(
                OPENJIBO_COM_URL,
                user_input.get("name", ""),
                user_input.get(CONF_JIBO_IP, ""),
                MODE_OPENJIBO_COM,
            )

        return self.async_show_form(
            step_id="openjibo_com",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Optional(CONF_JIBO_IP, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_self_host_openjibo(self, user_input=None):
        errors = {}
        if user_input is not None:
            server_url = user_input[CONF_SERVER_URL].strip().rstrip("/")
            return await self._async_create_openjibo_entry(
                server_url,
                user_input.get("name", ""),
                user_input.get(CONF_JIBO_IP, ""),
                MODE_SELF_HOST_OPENJIBO,
            )

        return self.async_show_form(
            step_id="self_host_openjibo",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVER_URL): str,
                    vol.Required("name"): str,
                    vol.Optional(CONF_JIBO_IP, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _async_step_beefy(self, user_input, mode: str, step_id: str):
        errors = {}
        if user_input is not None:
            name = user_input.get("name", "").strip() or "OpenJibo"
            jibo_ip = user_input.get(CONF_JIBO_IP, "").strip()
            if not jibo_ip:
                errors[CONF_JIBO_IP] = "cannot_connect"
            else:
                instance_id = str(uuid.uuid4())
                webhook_id = uuid.uuid4().hex
                ha_port = _ha_port(self.hass)
                error, password = await _pair_with_robot(
                    jibo_ip,
                    {
                        "instanceId": instance_id,
                        "haPort": ha_port,
                        "webhookId": webhook_id,
                        "mode": mode,
                    },
                )
                if error:
                    errors["base"] = error
                else:
                    await self.async_set_unique_id(f"beefy:{jibo_ip}")
                    self._abort_if_unique_id_configured()
                    server_url = FIVE_X1_URL if mode == MODE_5X1 else ""
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_SERVER_MODE: mode,
                            CONF_SERVER_URL: server_url,
                            CONF_INSTANCE_ID: instance_id,
                            CONF_JIBO_IP: jibo_ip,
                            CONF_COMMAND_SECRET: password,
                            CONF_WEBHOOK_ID: webhook_id,
                            CONF_HA_PORT: ha_port,
                            "name": name,
                        },
                    )

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required(CONF_JIBO_IP): str,
                }
            ),
            errors=errors,
        )

    async def _async_create_openjibo_entry(
        self, server_url: str, name: str, jibo_ip: str, mode: str
    ):
        friendly = name.strip() or "OpenJibo"
        ip = jibo_ip.strip()
        instance_id = str(uuid.uuid4())
        await self.async_set_unique_id(instance_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=friendly,
            data={
                CONF_SERVER_MODE: mode,
                CONF_SERVER_URL: server_url,
                CONF_INSTANCE_ID: instance_id,
                CONF_JIBO_IP: ip,
                "name": friendly,
            },
        )


def _ha_port(hass) -> int:
    http = getattr(hass, "http", None)
    port = getattr(http, "server_port", None)
    if isinstance(port, int) and port > 0:
        return port
    return 8123


async def _pair_with_robot(jibo_ip: str, payload: dict) -> tuple[str | None, str | None]:
    url = f"http://{jibo_ip}:{BEACON_PORT}{PAIR_PATH}"
    timeout = aiohttp.ClientTimeout(total=PAIR_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                status = response.status
                try:
                    body = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    return "unknown", None
    except TimeoutError:
        return "timeout", None
    except aiohttp.ClientError:
        return "cannot_connect", None

    if not isinstance(body, dict):
        return "unknown", None

    error = body.get("error")
    if status == 403 or error == "rejected":
        return "rejected", None
    if status == 408 or error == "timeout":
        return "timeout", None
    if error == "no_screen":
        return "no_screen", None
    if status >= 400 or not body.get("ok"):
        return "cannot_connect", None

    password = body.get("password")
    if not isinstance(password, str) or not password:
        return "invalid_auth", None
    if payload.get("mode") not in BEEFY_MODES:
        return "unknown", None
    return None, password
