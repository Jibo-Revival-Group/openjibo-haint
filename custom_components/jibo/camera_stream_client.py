"""Authenticated local camera controls and status for native BE robots."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
import ipaddress
import re

import aiohttp

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import BEACON_PORT, CONF_COMMAND_SECRET, CONF_JIBO_IP

import logging

_LOGGER = logging.getLogger(__name__)


class CameraStreamClient(DataUpdateCoordinator[dict]):
    """One status coordinator shared by all camera and button entities."""

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, _LOGGER, name=f"{entry.title} camera", update_interval=timedelta(seconds=5))
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self._responses: set[aiohttp.ClientResponse] = set()
        self._last_ip: str | None = None
        self._closed = False

    def endpoint(self, resource: str, *, api: str = "camera-stream") -> str:
        ip = self.entry.data.get(CONF_JIBO_IP, "")
        if not isinstance(ip, str) or not ip:
            raise HomeAssistantError("Jibo's local address is unavailable")
        try:
            address = ipaddress.ip_address(ip)
            host = f"[{address}]" if address.version == 6 else str(address)
        except ValueError:
            if len(ip) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", ip):
                raise HomeAssistantError("Jibo's local address is invalid") from None
            host = ip
        return f"http://{host}:{BEACON_PORT}/api/{api}/{resource}".rstrip("/")

    def headers(self) -> dict[str, str]:
        secret = self.entry.data.get(CONF_COMMAND_SECRET)
        if not isinstance(secret, str) or not secret:
            raise HomeAssistantError("Pair Jibo before using camera streaming")
        return {"Authorization": f"Bearer {secret}"}

    async def _request(self, resource: str, action: str | None = None) -> dict:
        if self._closed:
            raise HomeAssistantError("Jibo camera connection is closed")
        method = "POST" if action is not None else "GET"
        payload = {"action": action} if action is not None else None
        try:
            async with self._session.request(
                method, self.endpoint(resource), json=payload, headers=self.headers(),
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=35, connect=5),
            ) as response:
                body = await response.json()
                if response.status != 200:
                    message = body.get("error", "Camera request failed") if isinstance(body, dict) else "Camera request failed"
                    # BEacon errors are fixed public messages; never echo request URLs/headers.
                    raise HomeAssistantError(str(message))
                if not isinstance(body, dict) or body.get("state") not in {"off", "starting", "streaming", "stopping"}:
                    raise HomeAssistantError("Jibo returned invalid camera status")
                return body
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise HomeAssistantError("Cannot connect to Jibo's camera controls") from None

    async def _async_update_data(self) -> dict:
        ip = self.entry.data.get(CONF_JIBO_IP)
        if self._last_ip is not None and ip != self._last_ip:
            self.close_viewers()
        self._last_ip = ip
        try:
            data = await self._request("status")
        except HomeAssistantError as error:
            self.close_viewers()
            raise UpdateFailed(str(error)) from None
        if data["state"] != "streaming":
            self.close_viewers()
        return data

    async def async_command(self, action: str) -> None:
        if action not in {"start", "stop", "toggle"}:
            raise HomeAssistantError("Invalid camera stream command")
        try:
            result = await self._request("control", action)
        except HomeAssistantError:
            # Failed startup may have rolled back or left cleanup pending.
            await self.async_refresh()
            raise
        if result["state"] != "streaming":
            self.close_viewers()
        self.async_set_updated_data(result)

    async def async_sleep(self) -> None:
        if self._closed:
            raise HomeAssistantError("Jibo connection is closed")
        try:
            async with self._session.post(
                self.endpoint("", api="sleep"), headers=self.headers(), allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20, connect=5),
            ) as response:
                body = await response.json()
                if response.status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
                    raise HomeAssistantError(body.get("error", "Cannot put Jibo to sleep") if isinstance(body, dict) else "Cannot put Jibo to sleep")
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise HomeAssistantError("Cannot connect to Jibo's sleep control") from None

    @asynccontextmanager
    async def video(self):
        if self._closed or not self.data or self.data.get("state") != "streaming":
            raise HomeAssistantError("Jibo camera stream is inactive")
        try:
            async with self._session.get(
                self.endpoint("video"), headers=self.headers(), allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=8),
            ) as response:
                if response.status != 200 or response.content_type != "video/webm":
                    raise HomeAssistantError("Jibo camera video is unavailable")
                self._responses.add(response)
                try:
                    yield response
                finally:
                    self._responses.discard(response)
        except (aiohttp.ClientError, TimeoutError):
            raise HomeAssistantError("Jibo camera connection was lost") from None

    def close_viewers(self) -> None:
        for response in self._responses:
            response.close()
        self._responses.clear()

    async def async_close(self) -> None:
        self._closed = True
        self.close_viewers()
        await self.async_shutdown()
