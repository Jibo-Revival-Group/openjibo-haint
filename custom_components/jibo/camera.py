"""Local native WebM camera, decoded on HA with credentials passed in headers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import struct
import zlib

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _inactive_image() -> bytes:
    """An opaque dark frame with a gray crossed-out camera; no stale imagery."""
    width, height = 640, 360
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            outline = ((260 <= x <= 350 and y in (145, 215)) or (x in (260, 350) and 145 <= y <= 215))
            lens = 350 < x <= 380 and abs(y - 180) <= (x - 350) // 2
            slash = 250 <= x <= 390 and abs(y - (x - 140)) < 3
            row.extend((110, 110, 110) if outline or lens or slash else (24, 24, 24))
        rows.append(b"\0" + row)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


INACTIVE_IMAGE = _inactive_image()


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data.get("camera_stream")
    if client is not None:
        async_add_entities([JiboCamera(client, entry, data["name"], get_ffmpeg_manager(hass).binary)])


class JiboCamera(CoordinatorEntity, Camera):
    _attr_has_entity_name = True
    _attr_name = "Camera"
    _attr_icon = "mdi:video"

    def __init__(self, client, entry, name, binary) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, client)
        self.content_type = "image/png"
        self._binary = binary
        self._attr_unique_id = f"{entry.entry_id}_camera"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": name,
            "manufacturer": "Jibo Inc.", "model": "Jibo",
        }

    @property
    def is_streaming(self) -> bool:
        return bool(self.coordinator.last_update_success and self.coordinator.data
                    and self.coordinator.data.get("state") == "streaming")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {"stream_state": data.get("state", "off"), "stream_error": data.get("error")}

    @asynccontextmanager
    async def _decoder(self, still=False):
        """Feed ffmpeg via stdin so no credentials appear in URLs or argv."""
        async with self.coordinator.video() as response:
            args = [self._binary, "-hide_banner", "-loglevel", "error",
                    "-analyzeduration", "100000", "-probesize", "32768", "-i", "pipe:0",
                    "-an", "-vf", "scale=640:360", "-r", "15"]
            args += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png"] if still else ["-f", "mpjpeg", "-q:v", "5"]
            args += ["pipe:1"]
            try:
                process = await asyncio.create_subprocess_exec(
                    *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                raise HomeAssistantError("HA's ffmpeg decoder is unavailable") from None

            async def pump():
                try:
                    async for data in response.content.iter_chunked(65536):
                        process.stdin.write(data)
                        await process.stdin.drain()
                finally:
                    process.stdin.close()

            task = asyncio.create_task(pump())
            try:
                yield process.stdout
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.terminate()
                # Drain stdout as well as waiting: an unread pipe can keep the
                # asyncio subprocess transport open after ffmpeg exits.
                try:
                    await asyncio.wait_for(process.communicate(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.communicate()

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        if not self.is_streaming:
            return INACTIVE_IMAGE
        try:
            async with self._decoder(still=True) as reader:
                async with asyncio.timeout(10):
                    image = await reader.read(4 * 1024 * 1024)
                    # A pipe read may return part of the frame; collect to EOF.
                    chunks = [image]
                    size = len(image)
                    while part := await reader.read(65536):
                        size += len(part)
                        if size > 4 * 1024 * 1024:
                            raise HomeAssistantError("Camera image is too large")
                        chunks.append(part)
                    image = b"".join(chunks)
                await self.coordinator.async_refresh()
                return image if self.is_streaming and image.startswith(b"\x89PNG") else INACTIVE_IMAGE
        except (HomeAssistantError, TimeoutError, ConnectionError):
            return INACTIVE_IMAGE

    async def handle_async_mjpeg_stream(self, request):
        if not self.is_streaming:
            return web.Response(body=INACTIVE_IMAGE, content_type="image/png", headers={"Cache-Control": "no-store"})
        async with self._decoder() as reader:
            response = web.StreamResponse(headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=ffmpeg",
                "Cache-Control": "no-store",
            })
            # Verify output before committing an HTTP success response.
            try:
                first = await asyncio.wait_for(reader.read(65536), 10)
            except TimeoutError:
                raise HomeAssistantError("Jibo camera decoder timed out") from None
            if not first:
                raise HomeAssistantError("Jibo camera decoder returned no video")
            await response.prepare(request)
            try:
                await response.write(first)
                while self.is_streaming and (data := await reader.read(65536)):
                    await response.write(data)
            except ConnectionError:
                pass
            return response
