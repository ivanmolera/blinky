"""Bridge tests use fake Blink devices and never contact Blink or need credentials."""
import base64
import contextlib
import io
import json
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/Blinky/Bridge"))
import bridge


class TwoFARequired(Exception):
    pass


class UnauthorizedError(Exception):
    pass


class TokenRefreshFailed(Exception):
    pass


class LoginError(Exception):
    pass


class FakeAuth:
    def __init__(self, data, **kwargs):
        self.data = dict(data)
        self.data.update(token="private-access", refresh_token="private-refresh", hardware_id="1332e056-6822-4c8c-b2e7-c7935db10313")

    @property
    def login_attributes(self):
        return self.data


class FakeSession:
    def __init__(self, **kwargs):
        self.close = AsyncMock()


class FakeBlink:
    mode = "authenticated"

    def __init__(self, **kwargs):
        self.available = False
        self.sync = {}
        self.urls = SimpleNamespace(base_url="https://rest-prde.immedia-semi.com")
        self.get_videos_metadata = AsyncMock(return_value=[])
        self.refresh = AsyncMock(return_value=True)
        self.do_http_get = AsyncMock()

    async def start(self):
        if type(self).mode == "2fa":
            raise TwoFARequired()
        if type(self).mode == "failure":
            return False
        self.available = True
        return True

    async def send_2fa_code(self, code):
        if code != "123456":
            return False
        self.available = True
        return True


def dependencies():
    return SimpleNamespace(Blink=FakeBlink, Auth=FakeAuth, ClientSession=FakeSession,
                           ClientTimeout=lambda **kwargs: object(), TwoFARequired=TwoFARequired,
                           AuthenticationErrors=(UnauthorizedError, TokenRefreshFailed, LoginError))


def camera_fixture(*, camera_id="11", name="Entrada", network_id="7"):
    camera = SimpleNamespace(camera_id=camera_id, name=name, network_id=network_id,
                             camera_type="default", product_type="catalina", status="online",
                             battery="ok", wifi_strength=-55, temperature_c=20.5,
                             motion_enabled=None, image_from_cache=b"jpeg-content",
                             thumbnail="https://rest-prde.immedia-semi.com/thumbnail?ts=1700000000&ext=",
                             async_arm=AsyncMock(return_value={"id": 101}),
                             snap_picture=AsyncMock(return_value={"id": 102}),
                             record=AsyncMock(return_value={"id": 103}))
    sync = SimpleNamespace(network_id=network_id, name="Casa", arm=None,
                           cameras={name: camera}, async_arm=AsyncMock(return_value={"id": 104}))
    return camera, sync


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks, *, status=200, content_length=None):
        self.content = FakeContent(chunks)
        self.status = status
        self.content_length = content_length
        self.release = Mock()


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBlink.mode = "authenticated"
        self.bridge = bridge.BlinkBridge(dependencies(), min_spacing=0)

    async def asyncTearDown(self):
        await self.bridge.close()

    async def request(self, method, **params):
        return await self.bridge.handle_request({"id": "test-request", "method": method, "params": params})

    async def login(self):
        result = await self.request("login", username="user@example.test", password=" password-exact ")
        self.assertNotIn("error", result)
        return result["result"]

    async def test_login_session_never_persists_password_or_pin(self):
        result = await self.login()
        session = json.loads(result["session"])
        self.assertEqual(result["status"], "authenticated")
        self.assertEqual(session["refresh_token"], "private-refresh")
        self.assertNotIn("password", session)
        self.assertNotIn("password", self.bridge._blink.auth.data)
        self.assertNotIn("2fa_code", session)

    async def test_pin_is_separate_step_and_failed_pin_remains_retryable(self):
        FakeBlink.mode = "2fa"
        result = await self.login()
        self.assertEqual(result["status"], "requires_2fa")
        self.assertIsNone(result["session"])
        bad_pin = await self.request("verify", code="999999")
        self.assertEqual(bad_pin["error"]["code"], "invalid_pin")
        good_pin = await self.request("verify", code="123456")
        self.assertEqual(good_pin["result"]["status"], "authenticated")
        self.assertNotIn("password", json.loads(good_pin["result"]["session"]))

    async def test_restore_uses_refresh_token_without_password(self):
        session = json.dumps({"username": "user@example.test", "refresh_token": "refresh",
                              "password": "must-be-discarded", "host": "rest-prde.immedia-semi.com",
                              "region_id": "prde"})
        result = await self.request("restore", session=session)
        self.assertEqual(result["result"]["status"], "authenticated")
        self.assertNotIn("password", self.bridge._blink.auth.data)

    async def test_restore_rejects_untrusted_host_before_connecting(self):
        result = await self.request("restore", session=json.dumps({"refresh_token": "secret", "host": "attacker.test"}))
        self.assertEqual(result["error"]["code"], "invalid_session")
        self.assertIsNone(self.bridge._http)

    async def test_failed_login_closes_http_session(self):
        FakeBlink.mode = "failure"
        result = await self.request("login", username="a@example.test", password="secret")
        self.assertEqual(result["error"]["code"], "authentication_failed")
        self.assertIsNone(self.bridge._http)
        self.assertIsNone(self.bridge._blink)

    async def test_expired_restore_signs_out_instead_of_requesting_password(self):
        FakeBlink.mode = "failure"
        result = await self.request("restore", session=json.dumps({"refresh_token": "expired"}))
        self.assertEqual(result["result"]["status"], "signed_out")
        self.assertIsNone(result["result"]["session"])

    async def test_snapshot_keeps_unknown_security_states_unknown_and_uses_opaque_ids(self):
        await self.login()
        camera, sync = camera_fixture()
        self.bridge._blink.sync = {"Casa": sync}
        result = (await self.request("snapshot"))["result"]
        self.assertIsNone(result["systems"][0]["armed"])
        self.assertIsNone(result["cameras"][0]["motionEnabled"])
        self.assertEqual(result["cameras"][0]["id"], "7:default:11")
        self.assertEqual(base64.b64decode(result["cameras"][0]["thumbnailBase64"]), b"jpeg-content")
        self.assertEqual(result["cameras"][0]["updatedAt"], "2023-11-14T22:13:20Z")

    async def test_refresh_button_does_not_repeatedly_poll_blink(self):
        await self.login()
        await self.request("snapshot")
        await self.request("snapshot")
        self.assertEqual(self.bridge._blink.get_videos_metadata.await_count, 1)
        self.assertEqual(self.bridge._blink.refresh.await_count, 0)

    async def test_duplicate_camera_names_in_different_networks_are_retained(self):
        await self.login()
        _, sync_a = camera_fixture(network_id="1")
        _, sync_b = camera_fixture(network_id="2")
        self.bridge._blink.sync = {"A": sync_a, "B": sync_b}
        result = (await self.request("snapshot"))["result"]
        self.assertEqual({c["id"] for c in result["cameras"]}, {"1:default:11", "2:default:11"})

    async def test_clip_metadata_excludes_deleted_and_foreign_urls(self):
        await self.login()
        _, sync = camera_fixture()
        self.bridge._blink.sync = {"Casa": sync}
        self.bridge._blink.get_videos_metadata.return_value = [
            {"id": 1, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/1.mp4"},
            {"id": 2, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/2.mp4", "deleted": True},
            {"id": 3, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "https://attacker.test/clip.mp4"},
        ]
        result = (await self.request("snapshot"))["result"]
        self.assertEqual(len(result["clips"]), 1)
        self.assertEqual(result["clips"][0]["cameraID"], "7:default:11")
        self.assertNotIn("/clip/", json.dumps(result))

    async def test_download_uses_discovered_path_and_releases_response(self):
        await self.login()
        self.bridge._clip_index = {"1": ("/clip/1.mp4", "blinky-camera.mp4")}
        response = FakeResponse([b"video-", b"content"])
        self.bridge._blink.do_http_get.return_value = response
        result = (await self.request("download_clip", clip_id="1"))["result"]
        self.assertEqual(base64.b64decode(result["dataBase64"]), b"video-content")
        self.assertEqual(result["filename"], "blinky-camera.mp4")
        response.release.assert_called_once()
        unknown = await self.request("download_clip", clip_id="https://attacker.test/private")
        self.assertEqual(unknown["error"]["code"], "not_found")

    async def test_streaming_media_enforces_limit_even_without_content_length(self):
        await self.login()
        self.bridge._clip_index = {"1": ("/clip/1.mp4", "camera.mp4")}
        response = FakeResponse([b"12345678", b"abcdefghi"])
        self.bridge._blink.do_http_get.return_value = response
        with patch.object(bridge, "MAX_MEDIA_BYTES", 16):
            result = await self.request("download_clip", clip_id="1")
        self.assertEqual(result["error"]["code"], "media_too_large")
        response.release.assert_called_once()

    async def test_explicit_camera_capture_is_rate_limited(self):
        await self.login()
        camera, sync = camera_fixture()
        self.bridge._blink.sync = {"Casa": sync}
        first = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(first["result"], {})
        second = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(second["error"]["code"], "rate_limited")
        camera.snap_picture.assert_awaited_once()

    async def test_command_failure_does_not_optimistically_change_state(self):
        await self.login()
        _, sync = camera_fixture()
        sync.async_arm.return_value = None
        self.bridge._blink.sync = {"Casa": sync}
        result = await self.request("set_system_armed", system_id="7", armed=True)
        self.assertEqual(result["error"]["code"], "command_failed")
        self.assertIsNone(sync.arm)

    async def test_no_credentials_in_exception_or_third_party_prints(self):
        async def broken(method, params):
            print("private-access https://private-url")
            raise RuntimeError("password=secret refresh_token=private-refresh")
        output = io.StringIO()
        with patch.object(self.bridge, "dispatch", side_effect=broken), contextlib.redirect_stdout(output):
            result = await self.request("snapshot")
        self.assertEqual(output.getvalue(), "")
        serialized = json.dumps(result)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("private-refresh", serialized)
        self.assertNotIn("private-access", serialized)

    async def test_authentication_failure_expires_session_and_closes_connection(self):
        for exception_type in (UnauthorizedError, TokenRefreshFailed, LoginError):
            with self.subTest(exception=exception_type.__name__):
                await self.login()
                http = self.bridge._http
                self.bridge._blink.get_videos_metadata.side_effect = exception_type("private-refresh https://private-url")
                result = await self.request("snapshot")
                self.assertEqual(result["error"]["code"], "session_expired")
                self.assertNotIn("private-refresh", json.dumps(result))
                self.assertFalse(self.bridge._authenticated)
                self.assertIsNone(self.bridge._http)
                self.assertIsNone(self.bridge._blink)
                http.close.assert_awaited_once()
                self.assertEqual((await self.request("session"))["result"]["status"], "signed_out")

    async def test_non_authentication_failure_does_not_expire_session(self):
        await self.login()
        self.bridge._blink.get_videos_metadata.side_effect = RuntimeError("unrelated failure")
        http = self.bridge._http
        result = await self.request("snapshot")
        self.assertEqual(result["error"]["code"], "blink_unavailable")
        self.assertTrue(self.bridge._authenticated)
        self.assertIsNotNone(self.bridge._blink)
        http.close.assert_not_awaited()

    async def test_health_returns_valid_result_without_blinkpy(self):
        self.bridge._dependencies = None
        with patch.object(bridge, "load_dependencies", side_effect=bridge.BridgeError("missing_runtime", "Missing")):
            result = await self.request("health")
        self.assertFalse(result["result"]["blinkpyAvailable"])
        self.assertEqual(result["result"]["bridgeVersion"], "1")

    async def test_malformed_and_unauthenticated_requests_have_safe_errors(self):
        malformed = await self.bridge.handle_request({"id": "bad", "method": "login", "params": []})
        self.assertEqual(malformed["error"]["code"], "invalid_request")
        unauthenticated = await self.request("snapshot")
        self.assertEqual(unauthenticated["error"]["code"], "not_authenticated")
        unsupported = await self.request("stream")
        self.assertEqual(unsupported["error"]["code"], "unsupported_method")


if __name__ == "__main__":
    unittest.main()
