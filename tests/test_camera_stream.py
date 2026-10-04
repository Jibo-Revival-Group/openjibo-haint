"""Offline entity tests and local HTTP integration tests (requires aiohttp)."""

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

try:
    import aiohttp
    from aiohttp import web
    HTTP_AVAILABLE = hasattr(web, "Application")
except ImportError:
    HTTP_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "jibo"


def module(name):
    return sys.modules.setdefault(name, types.ModuleType(name))


module("custom_components").__path__ = []
module("custom_components.jibo").__path__ = [str(ROOT)]
module("homeassistant").__path__ = []
module("homeassistant.components").__path__ = []
module("homeassistant.helpers").__path__ = []


class HomeAssistantError(Exception):
    pass


class Coordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None
        self.last_update_success = False

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except HomeAssistantError:
            self.last_update_success = False

    def async_set_updated_data(self, data):
        self.data = data
        self.last_update_success = True

    async def async_shutdown(self):
        pass


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class Camera:
    def __init__(self):
        self.content_type = "image/jpeg"

    @property
    def is_on(self):
        return True


module("homeassistant.exceptions").HomeAssistantError = HomeAssistantError
helper = module("homeassistant.helpers.update_coordinator")
helper.DataUpdateCoordinator = Coordinator
helper.CoordinatorEntity = CoordinatorEntity
helper.UpdateFailed = HomeAssistantError
module("homeassistant.helpers.aiohttp_client").async_get_clientsession = lambda hass: hass.session
module("homeassistant.components.camera").Camera = Camera
module("homeassistant.components.button").ButtonEntity = type("ButtonEntity", (), {})
module("homeassistant.components.ffmpeg").get_ffmpeg_manager = lambda hass: types.SimpleNamespace(binary="ffmpeg")

if not HTTP_AVAILABLE:
    module("aiohttp").ClientTimeout = lambda **kwargs: kwargs
    module("aiohttp").ClientError = type("ClientError", (Exception,), {})
    module("aiohttp").ClientResponse = object
    module("aiohttp").web = types.SimpleNamespace(Request=object, StreamResponse=object)

from custom_components.jibo.button import JiboCameraButton, async_setup_entry as setup_buttons
from custom_components.jibo.camera import JiboCamera, INACTIVE_IMAGE
from custom_components.jibo.camera_stream_client import CameraStreamClient
from custom_components.jibo.const import CONF_COMMAND_SECRET, CONF_JIBO_IP, DOMAIN


class CameraEntities(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = types.SimpleNamespace(entry_id="robot-1", title="Jibo", data={})
        self.client = types.SimpleNamespace(data={"state": "off"}, last_update_success=True,
                                            async_command=AsyncMock(), async_refresh=AsyncMock())
        self.camera = JiboCamera(self.client, self.entry, "Jibo", "ffmpeg")

    async def test_exact_buttons_same_device_and_commands(self):
        hass = types.SimpleNamespace(data={DOMAIN: {self.entry.entry_id: {
            "name": "Jibo", "camera_stream": self.client,
        }}})
        entities = []
        await setup_buttons(hass, self.entry, entities.extend)
        self.assertEqual([e._attr_name for e in entities], [
            "Start Camera Stream", "Stop Camera Stream", "Toggle Camera Stream",
        ])
        for entity, action in zip(entities, ["start", "stop", "toggle"]):
            await entity.async_press()
            self.client.async_command.assert_awaited_with(action)
            self.assertEqual(entity._attr_device_info["identifiers"], self.camera._attr_device_info["identifiers"])
        self.assertEqual(len({e._attr_unique_id for e in entities}), 3)

    async def test_cloud_pairing_has_no_local_entities(self):
        hass = types.SimpleNamespace(data={DOMAIN: {self.entry.entry_id: {"name": "Jibo"}}})
        entities = []
        await setup_buttons(hass, self.entry, entities.extend)
        self.assertEqual(entities, [])

    async def test_off_view_returns_placeholder_without_starting_capture(self):
        self.client.video = AsyncMock(side_effect=AssertionError("must not open camera"))
        self.assertEqual(await self.camera.async_camera_image(), INACTIVE_IMAGE)
        self.client.video.assert_not_called()
        self.client.async_command.assert_not_called()
        self.assertEqual(self.camera.content_type, "image/png")
        self.assertTrue(self.camera.is_on)  # HA permits the placeholder proxy request.
        self.assertFalse(self.camera.is_streaming)

    async def test_disconnected_status_cannot_show_stale_streaming_state(self):
        self.client.data = {"state": "streaming"}
        self.client.last_update_success = False
        self.assertFalse(self.camera.is_streaming)
        self.assertEqual(await self.camera.async_camera_image(), INACTIVE_IMAGE)

    async def test_still_completed_after_local_stop_is_discarded(self):
        self.client.data = {"state": "streaming"}
        reader = asyncio.StreamReader()
        reader.feed_data(INACTIVE_IMAGE)
        reader.feed_eof()
        @asynccontextmanager
        async def decoder(still=False):
            yield reader
        self.camera._decoder = decoder
        async def refresh():
            self.client.data = {"state": "off"}
        self.client.async_refresh.side_effect = refresh
        self.assertEqual(await self.camera.async_camera_image(), INACTIVE_IMAGE)
        self.assertFalse(self.camera.is_streaming)

    async def test_decoder_exit_terminates_process_even_if_pump_fails(self):
        self.client.data = {"state": "streaming"}
        class Content:
            async def iter_chunked(self, size):
                raise RuntimeError("broken input")
                yield b""
        @asynccontextmanager
        async def video():
            yield types.SimpleNamespace(content=Content())
        self.client.video = video
        process = types.SimpleNamespace(
            stdin=types.SimpleNamespace(close=lambda: None), stdout=asyncio.StreamReader(),
            returncode=None, communicate=AsyncMock(return_value=(b"", None)), terminate=lambda: calls.append("terminate"),
        )
        calls = []
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            async with self.camera._decoder():
                await asyncio.sleep(0)
            self.assertEqual(calls, ["terminate"])
            argv = spawn.call_args.args
            self.assertIn("pipe:0", argv)
            self.assertNotIn("Authorization", " ".join(argv))
            process.communicate.assert_awaited_once()


@unittest.skipUnless(HTTP_AVAILABLE, "Install aiohttp to run real HTTP camera tests")
class CameraHTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.state = "off"
        self.reject = False
        self.disconnect = asyncio.Event()

        async def handle(request):
            self.requests.append((request.method, request.path, request.headers.get("Authorization")))
            if self.reject:
                return web.json_response({"error": "Turn off privacy mode before streaming"}, status=409)
            if request.path.endswith("video"):
                response = web.StreamResponse(headers={"Content-Type": "video/webm"})
                await response.prepare(request)
                await response.write(b"webm bytes")
                await self.disconnect.wait()
                return response
            if request.method == "POST":
                body = await request.json()
                self.state = "off" if body["action"] == "stop" else "streaming"
            return web.json_response({"state": self.state, "streaming": self.state == "streaming"})

        app = web.Application()
        app.router.add_route("*", "/api/camera-stream/{resource}", handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.session = aiohttp.ClientSession()
        self.entry = types.SimpleNamespace(entry_id="robot-1", title="Jibo", data={
            CONF_JIBO_IP: "127.0.0.1", CONF_COMMAND_SECRET: "private-secret",
        })
        self.client = CameraStreamClient(types.SimpleNamespace(session=self.session), self.entry)
        self.port_patch = patch("custom_components.jibo.camera_stream_client.BEACON_PORT", self.port)
        self.port_patch.start()

    async def asyncTearDown(self):
        self.disconnect.set()
        await self.client.async_close()
        await self.session.close()
        await self.runner.cleanup()
        self.port_patch.stop()

    async def test_explicit_controls_headers_and_local_stop_status(self):
        await self.client.async_refresh()
        self.assertEqual(self.client.data["state"], "off")
        await self.client.async_command("start")
        self.assertEqual(self.client.data["state"], "streaming")
        self.assertEqual([item[0] for item in self.requests], ["GET", "POST"])
        self.assertTrue(all(item[2] == "Bearer private-secret" for item in self.requests))
        self.assertNotIn("private-secret", self.client.endpoint("video"))
        self.state = "off"  # Head touch stopped the robot.
        await self.client.async_refresh()
        self.assertEqual(self.client.data["state"], "off")

    async def test_privacy_failure_is_reported_and_status_refreshed(self):
        self.reject = True
        with self.assertRaisesRegex(HomeAssistantError, "privacy mode"):
            await self.client.async_command("start")
        self.assertFalse(self.client.last_update_success)
        self.assertEqual([item[0] for item in self.requests], ["POST", "GET"])

    async def test_stop_and_unload_close_viewers_without_implicit_commands(self):
        await self.client.async_command("start")
        async with self.client.video() as response:
            self.assertEqual(await response.content.read(10), b"webm bytes")
            await self.client.async_command("stop")
            self.assertTrue(response.closed)
        await self.client.async_command("start")
        async with self.client.video() as response:
            commands_before = len([r for r in self.requests if r[0] == "POST"])
            await self.client.async_close()
            self.assertTrue(response.closed)
            self.assertEqual(len([r for r in self.requests if r[0] == "POST"]), commands_before)

    async def test_address_changes_close_existing_viewers(self):
        await self.client.async_command("start")
        await self.client.async_refresh()
        async with self.client.video() as response:
            self.entry.data[CONF_JIBO_IP] = "localhost"
            await self.client.async_refresh()
            self.assertTrue(response.closed)
        self.entry.data[CONF_JIBO_IP] = "http://example.com/private-secret"
        with self.assertRaisesRegex(HomeAssistantError, "invalid"):
            self.client.endpoint("video")

    async def test_inactive_view_has_no_network_side_effect(self):
        await self.client.async_refresh()
        count = len(self.requests)
        with self.assertRaisesRegex(HomeAssistantError, "inactive"):
            async with self.client.video():
                pass
        self.assertEqual(len(self.requests), count)

    @unittest.skipUnless(os.environ.get("FFMPEG_BINARY"), "Set FFMPEG_BINARY for real codec tests")
    async def test_real_decoder_still_and_mjpeg_release_resources(self):
        binary = os.environ["FFMPEG_BINARY"]
        encoder = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=15", "-t", "2", "-c:v", "libvpx",
            "-g", "15", "-an", "-f", "webm", "-live", "1", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        fixture, errors = await encoder.communicate()
        self.assertEqual(encoder.returncode, 0, errors)
        class Content:
            async def iter_chunked(self, size):
                for offset in range(0, len(fixture), size):
                    yield fixture[offset:offset + size]
        @asynccontextmanager
        async def video():
            yield types.SimpleNamespace(content=Content())
        self.client.async_set_updated_data({"state": "streaming"})
        self.client.video = video
        self.client.async_refresh = AsyncMock()
        camera = JiboCamera(self.client, self.entry, "Jibo", binary)
        image = await camera.async_camera_image()
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertNotEqual(image, INACTIVE_IMAGE)
        async with camera._decoder() as reader:
            output = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            self.assertTrue(output.startswith(b"--ffmpeg"))
            self.assertIn(b"Content-type: image/jpeg", output)


if __name__ == "__main__":
    unittest.main()
