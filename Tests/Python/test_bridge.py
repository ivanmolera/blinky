"""Bridge tests use fake Blink devices and never contact Blink or need credentials."""
import base64
import contextlib
import datetime as dt
import io
import json
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

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
        self.account_id = "42"
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
    api = SimpleNamespace(
        request_videos=AsyncMock(return_value={"media": []}),
        request_local_storage_manifest=AsyncMock(return_value={"id": 501}),
        get_local_storage_manifest=AsyncMock(return_value={"manifest_id": 123, "clips": []}),
        request_local_storage_clip=AsyncMock(return_value={"id": 502}),
        http_post=AsyncMock(return_value={"id": 600, "network_id": "7"}),
        request_command_status=AsyncMock(return_value={"status_code": 908, "complete": True}),
        modern_command_status=AsyncMock(return_value=None))
    async def raw_get(blink, url, **kwargs):
        parsed = urlparse(url)
        if parsed.path.endswith("/media/changed"):
            query = parse_qs(parsed.query)
            since = dt.datetime.fromisoformat(query["since"][0].replace("Z", "+00:00")).timestamp()
            return await api.request_videos(blink, time=since, page=int(query["page"][0]))
        if "/commands/" in parsed.path:
            return await api.modern_command_status(blink, url)
        return None
    api.http_get = AsyncMock(side_effect=raw_get)
    return SimpleNamespace(Blink=FakeBlink, Auth=FakeAuth, ClientSession=FakeSession,
                           ClientTimeout=lambda **kwargs: object(), TwoFARequired=TwoFARequired,
                           AuthenticationErrors=(UnauthorizedError, TokenRefreshFailed, LoginError),
                           RecordingAPI=api)


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
                           cameras={name: camera}, async_arm=AsyncMock(return_value={"id": 104}),
                           local_storage=False, sync_id="99", _names_table={})
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
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 1)
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
        self.bridge.recording_api.request_videos.side_effect = [{"media": [
            {"id": 1, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/1.mp4"},
            {"id": 2, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/2.mp4", "deleted": True},
            {"id": 3, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "https://attacker.test/clip.mp4"},
        ]}, {"media": []}]
        result = (await self.request("snapshot"))["result"]
        self.assertEqual(len(result["clips"]), 1)
        self.assertEqual(result["clips"][0]["cameraID"], "7:default:11")
        self.assertNotIn("/clip/", json.dumps(result))

    async def test_download_uses_discovered_path_and_releases_response(self):
        await self.login()
        self.bridge._clip_index = {"1": {"source": "cloud", "address": "/clip/1.mp4", "filename": "blinky-camera.mp4"}}
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
        self.bridge._clip_index = {"1": {"source": "cloud", "address": "/clip/1.mp4", "filename": "camera.mp4"}}
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
        self.assertTrue(first["result"]["completed"])
        second = await self.request("refresh_thumbnail", camera_id="7:default:11")
        self.assertEqual(second["error"]["code"], "rate_limited")
        self.bridge.action_api.http_post.assert_awaited_once()
        camera.snap_picture.assert_not_awaited()

    async def test_command_failure_does_not_optimistically_change_state(self):
        await self.login()
        _, sync = camera_fixture()
        self.bridge.action_api.http_post.return_value = None
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
                self.bridge.recording_api.request_videos.side_effect = exception_type("private-refresh https://private-url")
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
        self.bridge.recording_api.request_videos.side_effect = RuntimeError("unrelated failure")
        http = self.bridge._http
        result = await self.request("snapshot")
        self.assertEqual(result["result"]["recordingsStatus"], "unavailable")
        self.assertTrue(result["result"]["recordingWarnings"])
        self.assertTrue(self.bridge._authenticated)
        self.assertIsNotNone(self.bridge._blink)
        http.close.assert_not_awaited()

    async def test_health_returns_valid_result_without_blinkpy(self):
        self.bridge._dependencies = None
        with patch.object(bridge, "load_dependencies", side_effect=bridge.BridgeError("missing_runtime", "Missing")):
            result = await self.request("health")
        self.assertFalse(result["result"]["blinkpyAvailable"])
        self.assertEqual(result["result"]["bridgeVersion"], "1")

    async def test_cloud_catalogue_includes_older_clips_and_follows_pages(self):
        await self.login()
        first = {"id": 10, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-01T13:00:00Z", "media": "/clip/10.mp4", "deleted": "false"}
        second = {"id": 11, "device_id": 11, "device_name": "Entrada", "created_at": 1730379600000, "media": "/clip/11.mp4"}
        self.bridge.recording_api.request_videos.side_effect = [{"media": [first]}, {"media": [second]}, {"media": []}]
        result = (await self.request("snapshot"))["result"]
        self.assertEqual({clip["id"] for clip in result["clips"]}, {"cloud:10", "cloud:11"})
        self.assertTrue(all(clip["source"] == "cloud" for clip in result["clips"]))
        calls = self.bridge.recording_api.request_videos.await_args_list
        self.assertEqual([call.kwargs["page"] for call in calls], [0, 1, 2])
        self.assertTrue(all(call.kwargs["time"] == 0 for call in calls))
        self.assertEqual(result["recordingsStatus"], "ready")

    async def test_raw_cloud_dictionary_collection_and_datetime_are_normalized(self):
        await self.login()
        item = {"id": 10, "device_id": 11, "device_name": "Entrada", "created_at": dt.datetime(2026, 11, 1, 13, tzinfo=dt.timezone.utc), "media": "/clip/10.mp4"}
        self.bridge.recording_api.request_videos.side_effect = [{"media": {"10": item}}, {"media": []}]
        result = (await self.request("snapshot"))["result"]
        self.assertEqual(result["clips"][0]["createdAt"], "2026-11-01T13:00:00Z")

    async def setup_local_catalogue(self, *, include_cloud=False):
        await self.login()
        _, sync = camera_fixture(name="Puerta Entrada")
        sync.local_storage = True
        sync._names_table = {"PuertaEntrada": "Puerta Entrada"}
        self.bridge._blink.sync = {"Casa": sync}
        manifest = {"version": "1.0", "manifest_id": "123", "clips": [
            {"id": "866333964", "size": "234", "camera_name": "PuertaEntrada", "created_at": "2026-11-01T13:00:00"}]}
        self.bridge.recording_api.get_local_storage_manifest.return_value = manifest
        if include_cloud:
            self.bridge.recording_api.request_videos.side_effect = [{"media": [
                {"id": "866333964", "device_id": 11, "device_name": "Puerta Entrada", "created_at": "2026-11-01T13:00:00Z", "media": "/clip/1.mp4"}]}, {"media": []}]
        result = (await self.request("snapshot"))["result"]
        return result

    async def test_usb_catalogue_loads_without_cloud_subscription_and_does_not_download(self):
        result = await self.setup_local_catalogue()
        self.assertEqual(len(result["clips"]), 1)
        clip = result["clips"][0]
        self.assertEqual(clip["source"], "local")
        self.assertEqual(clip["cameraName"], "Puerta Entrada")
        self.assertEqual(clip["cameraID"], "7:default:11")
        self.assertEqual(clip["createdAt"], "2026-11-01T13:00:00Z")
        self.bridge.recording_api.request_local_storage_clip.assert_not_awaited()
        self.bridge._blink.do_http_get.assert_not_awaited()
        self.assertEqual(result["recordingsStatus"], "ready")

    async def test_cloud_and_usb_ids_do_not_collide(self):
        result = await self.setup_local_catalogue(include_cloud=True)
        self.assertEqual(len(result["clips"]), 2)
        self.assertEqual({clip["source"] for clip in result["clips"]}, {"cloud", "local"})
        self.assertEqual(len({clip["id"] for clip in result["clips"]}), 2)

    async def test_usb_download_prepares_then_reads_without_deleting(self):
        snapshot = await self.setup_local_catalogue()
        clip_id = snapshot["clips"][0]["id"]
        response = FakeResponse([b"local-video"])
        self.bridge._blink.do_http_get.return_value = response
        result = (await self.request("download_clip", clip_id=clip_id))["result"]
        self.assertEqual(base64.b64decode(result["dataBase64"]), b"local-video")
        self.bridge.recording_api.request_local_storage_clip.assert_awaited_once_with(self.bridge._blink, "7", "99", "123", "866333964")
        self.assertEqual(self.bridge._blink.do_http_get.await_args.args[0], "/api/v1/accounts/42/networks/7/sync_modules/99/local_storage/manifest/123/clip/request/866333964")
        response.release.assert_called_once()
        self.assertTrue(self.bridge._clips[0]["downloaded"])

    async def test_usb_failure_keeps_last_known_recordings_and_reports_warning(self):
        before = await self.setup_local_catalogue()
        self.bridge._last_metadata = float("-inf")
        self.bridge.recording_api.request_local_storage_manifest.return_value = None
        after = (await self.request("snapshot"))["result"]
        self.assertEqual(after["clips"], before["clips"])
        self.assertEqual(after["recordingsStatus"], "partial")
        self.assertTrue(after["recordingWarnings"])

    async def test_cloud_api_failure_has_visible_unavailable_status(self):
        await self.login()
        self.bridge.recording_api.request_videos.return_value = None
        result = (await self.request("snapshot"))["result"]
        self.assertEqual(result["clips"], [])
        self.assertEqual(result["recordingsStatus"], "unavailable")
        self.assertTrue(result["recordingWarnings"])

    async def test_cloud_page_failure_keeps_successfully_fetched_recordings(self):
        await self.login()
        item = {"id": 10, "device_id": 11, "device_name": "Entrada", "created_at": "2026-11-01T13:00:00Z", "media": "/clip/10.mp4"}
        self.bridge.recording_api.request_videos.side_effect = [{"media": [item]}, None]
        result = (await self.request("snapshot"))["result"]
        self.assertEqual(len(result["clips"]), 1)
        self.assertEqual(result["recordingsStatus"], "partial")
        self.assertTrue(result["recordingWarnings"])

    async def test_cloud_pagination_is_bounded_and_exposes_truncation(self):
        await self.login()
        def page_response(blink, *, time, page):
            return {"media": [{"id": page, "device_name": "Entrada", "created_at": "2026-11-01T13:00:00Z", "media": f"/clip/{page}.mp4"}]}
        self.bridge.recording_api.request_videos.side_effect = page_response
        with patch.object(bridge, "MAX_CLOUD_PAGES", 2):
            result = (await self.request("snapshot"))["result"]
        self.assertEqual(len(result["clips"]), 2)
        self.assertTrue(result["recordingsTruncated"])
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 2)

    async def test_pending_usb_manifest_is_retried_before_reporting_failure(self):
        await self.login()
        _, sync = camera_fixture()
        sync.local_storage = True
        self.bridge._blink.sync = {"Casa": sync}
        self.bridge.recording_api.get_local_storage_manifest.side_effect = [
            {"status": "pending"}, {"manifest_id": 123, "clips": []}]
        with patch.object(bridge.asyncio, "sleep", new=AsyncMock()):
            result = (await self.request("snapshot"))["result"]
        self.assertEqual(self.bridge.recording_api.get_local_storage_manifest.await_count, 2)
        self.assertEqual(result["recordingsStatus"], "ready")

    async def test_incompatible_recording_metadata_does_not_silently_look_empty(self):
        await self.login()
        self.bridge.recording_api.request_videos.side_effect = [
            {"media": [{"id": 1, "created_at": "broken-date", "media": "/clip/1.mp4"}]}, {"media": []}]
        result = (await self.request("snapshot"))["result"]
        self.assertTrue(result["recordingWarnings"])
        self.assertNotEqual(result["recordingsStatus"], "ready")

    async def test_recording_warning_does_not_include_private_exception_details(self):
        await self.login()
        self.bridge.recording_api.request_videos.side_effect = RuntimeError("https://private-media?token=private-access")
        result = await self.request("snapshot")
        self.assertTrue(result["result"]["recordingWarnings"])
        self.assertNotIn("private-media", json.dumps(result))
        self.assertNotIn("private-access", json.dumps(result))

    async def test_usb_pending_json_is_not_exported_as_video(self):
        snapshot = await self.setup_local_catalogue()
        pending = FakeResponse([b'{"status":"pending"}'])
        pending.content_type = "application/json"
        video = FakeResponse([b"prepared-video"])
        self.bridge._blink.do_http_get.side_effect = [pending, video]
        with patch.object(bridge.asyncio, "sleep", new=AsyncMock()):
            result = (await self.request("download_clip", clip_id=snapshot["clips"][0]["id"]))["result"]
        self.assertEqual(base64.b64decode(result["dataBase64"]), b"prepared-video")
        pending.release.assert_called_once()
        video.release.assert_called_once()

    async def test_oversized_usb_clip_is_rejected_before_uploading(self):
        snapshot = await self.setup_local_catalogue()
        clip_id = snapshot["clips"][0]["id"]
        self.bridge._clip_index[clip_id]["size"] = str(bridge.MAX_MEDIA_BYTES + 1)
        result = await self.request("download_clip", clip_id=clip_id)
        self.assertEqual(result["error"]["code"], "media_too_large")
        self.bridge.recording_api.request_local_storage_clip.assert_not_awaited()

    async def test_first_cloud_page_zero_is_not_skipped(self):
        await self.login()
        item = {"id": 42, "camera_id": 11, "network_id": 7, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/42.mp4"}
        def first_page_only(blink, *, time, page):
            return {"media": [item] if page == 0 else []}
        self.bridge.recording_api.request_videos.side_effect = first_page_only
        response = (await self.request("snapshot"))["result"]
        self.assertEqual([clip["id"] for clip in response["clips"]], ["cloud:42"])
        self.assertEqual(self.bridge.recording_api.request_videos.await_args_list[0].kwargs["page"], 0)

    async def test_cloud_request_encodes_epoch_utc_and_keeps_blink_origin(self):
        await self.login()
        await self.request("snapshot")
        url = self.bridge.recording_api.http_get.await_args_list[0].args[1]
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "rest-prde.immedia-semi.com")
        self.assertEqual(parsed.path, "/api/v1/accounts/42/media/changed")
        self.assertEqual(parse_qs(parsed.query), {"since": ["1970-01-01T00:00:00Z"], "page": ["0"]})
        self.assertIn("00%3A00%3A00Z", parsed.query)
        self.assertNotIn("+0000", parsed.query)

    async def test_explicit_refresh_reloads_empty_catalogue_within_poll_window(self):
        await self.login()
        empty = (await self.request("snapshot"))["result"]
        self.assertEqual(empty["clips"], [])
        item = {"id": 42, "camera_id": 11, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/42.mp4"}
        self.bridge.recording_api.request_videos.side_effect = [{"media": [item]}, {"media": []}]
        cached = (await self.request("snapshot"))["result"]
        self.assertEqual(cached["clips"], [])
        refreshed = (await self.request("snapshot", refresh_recordings=True))["result"]
        self.assertEqual([clip["id"] for clip in refreshed["clips"]], ["cloud:42"])
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 3)

    async def test_explicit_refresh_does_not_replace_known_clips_on_source_failure(self):
        await self.login()
        item = {"id": 42, "device_name": "Entrada", "created_at": "2026-11-25T13:00:00Z", "media": "/clip/42.mp4"}
        self.bridge.recording_api.request_videos.side_effect = [{"media": [item]}, {"media": []}]
        before = (await self.request("snapshot"))["result"]
        self.bridge.recording_api.request_videos.side_effect = None
        self.bridge.recording_api.request_videos.return_value = None
        after = (await self.request("snapshot", refresh_recordings=True))["result"]
        self.assertEqual(after["clips"], before["clips"])
        self.assertTrue(after["recordingWarnings"])
        self.assertEqual(after["recordingsStatus"], "partial")

    async def test_explicit_refresh_has_short_independent_rate_limit(self):
        await self.login()
        await self.request("snapshot", refresh_recordings=True)
        await self.request("snapshot", refresh_recordings=True)
        self.assertEqual(self.bridge.recording_api.request_videos.await_count, 1)

    async def test_explicit_recording_refresh_rejects_non_boolean_parameter(self):
        await self.login()
        response = await self.request("snapshot", refresh_recordings="true")
        self.assertEqual(response["error"]["code"], "invalid_request")

    async def test_malformed_and_unauthenticated_requests_have_safe_errors(self):
        malformed = await self.bridge.handle_request({"id": "bad", "method": "login", "params": []})
        self.assertEqual(malformed["error"]["code"], "invalid_request")
        unauthenticated = await self.request("snapshot")
        self.assertEqual(unauthenticated["error"]["code"], "not_authenticated")
        unsupported = await self.request("stream")
        self.assertEqual(unsupported["error"]["code"], "unsupported_method")


if __name__ == "__main__":
    unittest.main()
