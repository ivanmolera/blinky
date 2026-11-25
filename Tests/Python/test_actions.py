"""Action regressions use real Blink acknowledgement/status shapes without contacting devices."""
import asyncio
import datetime as dt
import json
import unittest
from unittest.mock import AsyncMock, patch

from test_bridge import FakeBlink, camera_fixture, dependencies
import bridge


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBlink.mode = "authenticated"
        self.bridge = bridge.BlinkBridge(dependencies(), min_spacing=0)
        login = await self.request("login", username="user@example.test", password="secret")
        self.assertIn("result", login)
        self.camera, self.system = camera_fixture()
        self.camera.motion_enabled = False
        self.system.arm = False
        self.bridge._blink.sync = {"Casa": self.system}

    async def asyncTearDown(self):
        await self.bridge.close()

    async def request(self, method, **params):
        with patch.object(bridge.asyncio, "sleep", new=AsyncMock()):
            return await self.bridge.handle_request({"id": "action-test", "method": method, "params": params})

    async def test_system_arm_posts_correct_endpoint_and_confirms_server_state(self):
        self.bridge.action_api.http_post.return_value = {"id": 42, "network_id": 7, "code": 908}
        self.bridge.action_api.request_command_status.side_effect = [
            {"status_code": 908, "complete": False}, {"status_code": 908, "complete": True}]
        async def refreshed(**kwargs):
            self.system.arm = True
            return True
        self.bridge._blink.refresh.side_effect = refreshed
        response = (await self.request("set_system_armed", system_id="7", armed=True))["result"]
        self.assertTrue(response["completed"])
        self.assertTrue(response["systemArmed"])
        self.assertIn("detección habilitada", response["message"])
        self.assertEqual(self.bridge.action_api.http_post.await_args.args[1], "https://rest-prde.immedia-semi.com/api/v1/accounts/42/networks/7/state/arm")
        self.bridge._blink.refresh.assert_awaited_once_with(force=True, force_cache=True)
        self.assertEqual(self.bridge.action_api.request_command_status.await_count, 2)
        self.system.async_arm.assert_not_awaited()

    async def test_system_disarm_uses_distinct_route_and_verifies_state(self):
        self.system.arm = True
        async def refreshed(**kwargs):
            self.system.arm = False
            return True
        self.bridge._blink.refresh.side_effect = refreshed
        response = (await self.request("set_system_armed", system_id="7", armed=False))["result"]
        self.assertTrue(response["completed"])
        self.assertFalse(response["systemArmed"])
        self.assertTrue(self.bridge.action_api.http_post.await_args.args[1].endswith("/state/disarm"))

    async def test_acknowledged_system_arm_with_unchanged_state_is_an_error(self):
        response = await self.request("set_system_armed", system_id="7", armed=True)
        self.assertEqual(response["error"]["code"], "state_mismatch")
        self.assertFalse(self.system.arm)
        self.assertNotIn("result", response)

    async def test_motion_enabled_on_disarmed_system_does_not_arm_whole_house(self):
        async def refreshed(**kwargs):
            self.camera.motion_enabled = True
            return True
        self.bridge._blink.refresh.side_effect = refreshed
        response = (await self.request("set_motion_enabled", camera_id="7:default:11", enabled=True))["result"]
        self.assertTrue(response["completed"])
        self.assertTrue(response["motionEnabled"])
        self.assertFalse(response["systemArmed"])
        self.assertIn("desarmado", response["message"])
        self.assertTrue(self.bridge.action_api.http_post.await_args.args[1].endswith("/network/7/camera/11/enable"))
        self.system.async_arm.assert_not_awaited()
        self.assertEqual(self.bridge.action_api.http_post.await_count, 1)

    async def test_motion_ack_without_changed_flag_is_an_error(self):
        response = await self.request("set_motion_enabled", camera_id="7:default:11", enabled=True)
        self.assertEqual(response["error"]["code"], "state_mismatch")
        self.assertFalse(self.camera.motion_enabled)

    async def test_mini_motion_uses_config_endpoint_with_boolean_payload(self):
        self.camera.camera_type = "mini"
        self.camera.motion_enabled = True
        async def refreshed(**kwargs):
            self.camera.motion_enabled = False
            return True
        self.bridge._blink.refresh.side_effect = refreshed
        response = (await self.request("set_motion_enabled", camera_id="7:mini:11", enabled=False))["result"]
        self.assertFalse(response["motionEnabled"])
        call = self.bridge.action_api.http_post.await_args
        self.assertTrue(call.args[1].endswith("/networks/7/owls/11/config"))
        self.assertEqual(json.loads(call.kwargs["data"]), {"enabled": False})

    async def test_missing_command_id_never_becomes_success(self):
        self.bridge.action_api.http_post.return_value = {}
        response = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_unconfirmed")
        self.bridge.action_api.request_command_status.assert_not_awaited()
        self.bridge._blink.refresh.assert_not_awaited()

    async def test_explicit_rejection_code_is_visible_without_echoing_payload(self):
        self.bridge.action_api.http_post.return_value = {"code": 307, "message": "private-access"}
        response = await self.request("record", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_failed")
        self.assertIn("307", response["error"]["message"])
        self.assertNotIn("private-access", json.dumps(response))

    async def test_command_status_failure_overrides_initial_acknowledgement(self):
        self.bridge.action_api.request_command_status.return_value = {"status_code": 905, "complete": False}
        response = await self.request("record", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_failed")
        self.assertIn("905", response["error"]["message"])
        self.bridge.recording_api.request_videos.assert_not_awaited()

    async def test_complete_without_success_status_is_unconfirmed(self):
        self.bridge.action_api.request_command_status.return_value = {"complete": True}
        response = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_unconfirmed")

    async def test_permanently_pending_command_stops_with_explicit_timeout(self):
        self.bridge.action_api.request_command_status.return_value = {"status_code": 908, "complete": False}
        response = await self.request("record", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_timeout")
        self.assertEqual(self.bridge.action_api.request_command_status.await_count, 12)
        self.bridge.recording_api.request_videos.assert_not_awaited()

    async def test_command_status_transport_timeout_has_safe_error(self):
        self.bridge.action_api.request_command_status.side_effect = asyncio.TimeoutError()
        response = await self.request("record", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_timeout")

    async def test_command_from_different_network_is_rejected(self):
        self.bridge.action_api.http_post.return_value = {"id": 42, "network_id": 8}
        response = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(response["error"]["code"], "command_unconfirmed")
        self.bridge.action_api.request_command_status.assert_not_awaited()

    async def test_fresh_state_request_failure_is_not_reported_as_success(self):
        self.bridge._blink.refresh.return_value = None
        response = await self.request("set_system_armed", system_id="7", armed=True)
        self.assertEqual(response["error"]["code"], "command_unconfirmed")

    async def test_delayed_recording_is_discovered_before_reporting_availability(self):
        created = dt.datetime.now(dt.timezone.utc).isoformat()
        self.bridge.recording_api.request_videos.side_effect = [{"media": []}, {"media": [
            {"id": 99, "device_id": 11, "device_name": "Entrada", "created_at": created, "media": "/clip/99.mp4"}]}]
        response = (await self.request("record", camera_id="7:default:11"))["result"]
        self.assertTrue(response["completed"])
        self.assertTrue(response["recordingRequested"])
        self.assertTrue(response["recordingAvailable"])
        self.assertEqual(response["recordingID"], "cloud:99")
        self.assertEqual(self.bridge._clips[0]["id"], "cloud:99")
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 2)
        self.bridge._blink.do_http_get.assert_not_awaited()
        self.bridge._blink.refresh.assert_not_awaited()

    async def test_recording_not_yet_present_is_processing_not_false_success(self):
        response = (await self.request("record", camera_id="7:default:11"))["result"]
        self.assertFalse(response["completed"])
        self.assertTrue(response["commandCompleted"])
        self.assertTrue(response["recordingRequested"])
        self.assertFalse(response["recordingAvailable"])
        self.assertIn("pendiente", response["message"])
        self.assertIn("desarmado", response["message"])
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 3)
        self.bridge._blink.do_http_get.assert_not_awaited()

    async def test_old_or_other_camera_recording_does_not_confirm_requested_clip(self):
        self.bridge.recording_api.request_videos.return_value = {"media": [
            {"id": 10, "device_id": 11, "device_name": "Entrada", "created_at": "2025-01-01T00:00:00Z", "media": "/old.mp4"},
            {"id": 11, "device_id": 22, "device_name": "Otra", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "media": "/other.mp4"}]}
        response = (await self.request("record", camera_id="7:default:11"))["result"]
        self.assertFalse(response["completed"])
        self.assertFalse(response["recordingAvailable"])

    async def test_doorbell_recording_uses_correct_endpoint(self):
        self.camera.camera_type = "doorbell"
        response = (await self.request("record", camera_id="7:doorbell:11"))["result"]
        self.assertTrue(response["recordingRequested"])
        self.assertTrue(self.bridge.action_api.http_post.await_args.args[1].endswith("/networks/7/doorbells/11/clip"))

    async def test_usb_acknowledgement_validation_does_not_require_completion(self):
        self.assertIsNone(self.bridge._check_command({"id": 42}))
        self.assertIsNone(self.bridge._check_command({"id": 43, "code": 908}))


if __name__ == "__main__":
    unittest.main()
