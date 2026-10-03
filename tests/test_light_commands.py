"""Receiver regressions using HA doubles; no running HA installation required."""
import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'jibo'


def module(name):
    value = types.ModuleType(name)
    sys.modules[name] = value
    return value


module('custom_components').__path__ = []
module('custom_components.jibo').__path__ = [str(ROOT)]
module('homeassistant').__path__ = []
module('homeassistant.core').HomeAssistant = object
module('homeassistant.config_entries').ConfigEntry = object
module('homeassistant.helpers').__path__ = []
class Coordinator:
    def __class_getitem__(cls, item):
        return cls
module('homeassistant.helpers.update_coordinator').DataUpdateCoordinator = Coordinator
entities = module('homeassistant.helpers.entity_registry')
devices = module('homeassistant.helpers.device_registry')
try:
    import aiohttp
except ImportError:
    aiohttp = module('aiohttp')
    aiohttp.ClientTimeout = lambda **kw: kw
    aiohttp.ClientSession = None
    aiohttp.WSMsgType = types.SimpleNamespace(CLOSE=1, CLOSED=2, ERROR=3, TEXT=4)
from custom_components.jibo.coordinator import JiboCoordinator
from custom_components.jibo.websocket_client import OpenJiboWebSocketClient


class LightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = object.__new__(JiboCoordinator)
        self.coordinator.hass = types.SimpleNamespace(
            services=types.SimpleNamespace(async_call=AsyncMock()))
        self.coordinator._send_command_result = AsyncMock()
        self.coordinator._get_jibo_area_id = lambda: 'room'
        self.coordinator.entry = types.SimpleNamespace(data={})
        self.registry = types.SimpleNamespace(entities={})
        entities.async_get = lambda hass: self.registry
        devices.async_get = lambda hass: types.SimpleNamespace(
            async_get=lambda device_id: types.SimpleNamespace(area_id='room'))

    def add_light(self, area_id=None):
        self.registry.entities['light.lamp'] = types.SimpleNamespace(
            domain='light', entity_id='light.lamp', area_id=area_id, device_id='lamp')

    async def test_device_area_inheritance_and_blocking_call(self):
        self.add_light()
        await self.coordinator._handle_command(
            {'command': 'lights_off_current_room', 'requestId': 'r'}, authenticated=True)
        self.coordinator.hass.services.async_call.assert_awaited_once_with(
            'light', 'turn_off', {'entity_id': ['light.lamp']}, blocking=True)
        self.coordinator._send_command_result.assert_awaited_once_with('r', 'ok')

    async def test_entity_area_overrides_device_area(self):
        self.add_light('elsewhere')
        await self.coordinator._handle_lights_room('turn_off', 'r')
        self.coordinator.hass.services.async_call.assert_not_awaited()
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'not_found', message='no_lights')

    async def test_missing_area(self):
        self.coordinator._get_jibo_area_id = lambda: None
        await self.coordinator._handle_lights_room('turn_off', 'r')
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'not_found', message='missing_area')
        self.coordinator.hass.services.async_call.assert_not_awaited()

    async def test_empty_room(self):
        await self.coordinator._handle_lights_room('turn_on', 'r')
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'not_found', message='no_lights')

    async def test_service_failure_does_not_return_ok(self):
        self.add_light('room')
        self.coordinator.hass.services.async_call.side_effect = RuntimeError('failed')
        await self.coordinator._handle_command(
            {'command': 'lights_off_current_room', 'requestId': 'r'}, authenticated=True)
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'error', message='failed')

    async def test_authentication_rejection(self):
        self.coordinator._client = None
        await self.coordinator._handle_command(
            {'command': 'lights_off_current_room', 'requestId': 'r'})
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'error', message='auth_failed')
        self.coordinator.hass.services.async_call.assert_not_awaited()

    async def test_named_light_waits_for_service(self):
        self.coordinator._find_matching_light = lambda name, area: 'light.lamp'
        self.coordinator._friendly_name = lambda entity_id: 'Lamp'
        await self.coordinator._handle_lights_named('turn_off', 'lamp', 'r')
        self.coordinator.hass.services.async_call.assert_awaited_once_with(
            'light', 'turn_off', {'entity_id': 'light.lamp'}, blocking=True)
        self.coordinator._send_command_result.assert_awaited_once_with(
            'r', 'ok', matched_name='Lamp', heard_name='lamp')


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_failure_closes_every_session(self):
        client = OpenJiboWebSocketClient('https://example.test', 'i', AsyncMock())
        sessions = [types.SimpleNamespace(ws_connect=AsyncMock(side_effect=RuntimeError('502')),
                                         close=AsyncMock()) for _ in range(2)]
        with patch.object(aiohttp, 'ClientSession', side_effect=sessions):
            for session in sessions:
                with self.assertRaisesRegex(RuntimeError, '502'):
                    await client._connect_once()
                session.close.assert_awaited_once()
                self.assertIsNone(client._session)
                self.assertFalse(client.connected)

    async def test_socket_close_failure_still_closes_session(self):
        socket = types.SimpleNamespace(closed=False, close=AsyncMock(side_effect=RuntimeError('close')),
                                       send_json=AsyncMock(), receive=AsyncMock(
                                           return_value=types.SimpleNamespace(type=aiohttp.WSMsgType.CLOSED)))
        session = types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket), close=AsyncMock())
        client = OpenJiboWebSocketClient('https://example.test', 'i', AsyncMock())
        with patch.object(aiohttp, 'ClientSession', return_value=session):
            with self.assertRaisesRegex(RuntimeError, 'close'):
                await client._connect_once()
        session.close.assert_awaited_once()
        self.assertIsNone(client._session)
        self.assertIsNone(client._ws)

    async def test_close_and_cancellation_release_resources(self):
        for outcome in (types.SimpleNamespace(type=aiohttp.WSMsgType.CLOSED), asyncio.CancelledError()):
            socket = types.SimpleNamespace(closed=False, close=AsyncMock(), send_json=AsyncMock(),
                                           receive=AsyncMock())
            if isinstance(outcome, BaseException):
                socket.receive.side_effect = outcome
            else:
                socket.receive.return_value = outcome
            session = types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket), close=AsyncMock())
            client = OpenJiboWebSocketClient('https://example.test', 'i', AsyncMock())
            with patch.object(aiohttp, 'ClientSession', return_value=session):
                if isinstance(outcome, BaseException):
                    with self.assertRaises(asyncio.CancelledError):
                        await client._connect_once()
                else:
                    await client._connect_once()
            session.close.assert_awaited_once()
            socket.close.assert_awaited_once()
            self.assertFalse(client.connected)
            self.assertIsNone(client._ws)


if __name__ == '__main__':
    unittest.main()
