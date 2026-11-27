"""Exercise media framing, private serving, and cleanup without Blink access."""
import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/Blinky/Bridge"))
import live


def live_response():
    return {"server": "immis://relay.example.test:443/connection123__IMDS_test?client_id=1234",
            "command_id": 77, "polling_interval": 15, "session_duration": 300,
            "liveview_token": "private-media-token"}


def packet(payload, kind=0, sequence=1):
    return live.FRAME_HEADER.pack(kind, sequence, len(payload)) + payload


class FakeWriter:
    def __init__(self, on_close=None):
        self.data = bytearray()
        self.closed = False
        self.on_close = on_close

    def write(self, data):
        if self.closed:
            raise BrokenPipeError
        self.data.extend(data)

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True
        if self.on_close:
            self.on_close()

    async def wait_closed(self):
        return None


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.finished = asyncio.Event()
        self.stdin = FakeWriter(lambda: self.finish(0))
        self.terminated = False

    def finish(self, code):
        self.returncode = code
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class FixtureManager(live.LiveManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reader = asyncio.StreamReader()
        self.writer = FakeWriter()
        self.encoder = FakeProcess()
        self.finished_command = AsyncMock()
        self.last_directory = None
        self.create_ready_playlist = True

    async def _request_liveview(self, camera, blink):
        return live_response()

    async def _request_status(self, session):
        return {"status_code": 908, "commands": [{"id": 77, "state_condition": "running"}]}

    async def _request_done(self, session):
        await self.finished_command(session.command_id)

    async def _connect_source(self, source):
        return self.reader, self.writer

    async def _create_ffmpeg(self, session, executable):
        self.last_directory = Path(session.directory.name)
        if self.create_ready_playlist:
            self.write_ready_playlist()
        return self.encoder

    def write_ready_playlist(self):
        (self.last_directory / "segment000001.ts").write_bytes(b"\x47" + bytes(187))
        (self.last_directory / "stream.m3u8").write_text("#EXTM3U\n#EXTINF:1.0,\nsegment000001.ts\n")

    async def _start_http(self, session):
        session.url = f"http://127.0.0.1:12345/{session.capability}/stream.m3u8"


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.camera = SimpleNamespace(serial="CAMERA123456", camera_id="11", camera_type="default",
                                      sync=SimpleNamespace(network_id="7"))

    def test_header_carries_token_and_opaque_connection_without_disclosing_them(self):
        source = live.MediaSource.from_response(self.camera, live_response())
        self.assertEqual(len(source.auth_header), 122)
        self.assertEqual(source.auth_header[34:98].rstrip(b"\0"), b"private-media-token")
        self.assertEqual(source.auth_header[102:118].rstrip(b"\0"), b"connection123")
        self.assertNotIn("private-media-token", repr(source))
        self.assertNotIn("relay.example.test", repr(source))
        self.assertEqual(source.duration, 90)

    def test_legacy_missing_token_and_camel_case_token_are_supported(self):
        response = live_response()
        del response["liveview_token"]
        self.assertEqual(live.MediaSource.from_response(self.camera, response).auth_header[34:98], bytes(64))
        response["liveViewToken"] = "camel-token"
        self.assertEqual(live.MediaSource.from_response(self.camera, response).auth_header[34:98].rstrip(b"\0"), b"camel-token")

    def test_blink_long_connection_id_is_encoded_at_wire_width(self):
        response = live_response()
        response["server"] = "immis://relay.example.test:443/_34c3rs56rdgdfsdfs?client_id=47"
        source = live.MediaSource.from_response(self.camera, response)
        self.assertEqual(source.auth_header[102:118], b"_34c3rs56rdgdfsdfs"[:16])
        self.assertEqual(int.from_bytes(source.auth_header[24:28], "big"), 47)

    def test_immis_missing_client_id_defaults_to_zero_but_invalid_ids_fail(self):
        response = live_response()
        response["server"] = "immis://relay.example.test:443/connection"
        self.assertEqual(live.MediaSource.from_response(self.camera, response).auth_header[24:28], bytes(4))
        for query in ("client_id=", "client_id=4294967296", "client_id=1&client_id=2"):
            response["server"] = f"immis://relay.example.test:443/connection?{query}"
            with self.subTest(query=query), self.assertRaises(live.LiveError) as failure:
                live.MediaSource.from_response(self.camera, response)
            self.assertEqual(failure.exception.code, "live_session_incomplete")

    def test_legacy_source_does_not_require_immis_credentials(self):
        for host in ("legacy.immedia-semi.com", "legacy.immedia-semi.com:332"):
            address = f"rtsps://{host}/private-session?blinkRTSP=true"
            source = live.MediaSource.from_response(SimpleNamespace(serial=None), {"server": address})
            self.assertEqual(source.transport, "rtsps")
            self.assertEqual(source.port, 332 if ":332" in host else 443)
            self.assertEqual(source.address, address)
            self.assertEqual(source.auth_header, b"")
            self.assertNotIn("private-session", repr(source))

    def test_legacy_source_requires_validated_vendor_route(self):
        for host in ("relay.example.test:332", "relay.immedia-semi.com:80", "127.0.0.1:332",
                     "224.1.2.3:332", "relay.immedia-semi.com.attacker.test:332"):
            with self.subTest(host=host), self.assertRaises(live.LiveError) as failure:
                live.MediaSource.from_response(self.camera, {"server": f"rtsps://{host}/private-session?blinkRTSP=1"})
            self.assertEqual(failure.exception.code, "live_relay_unsupported")
            self.assertNotIn("private-session", str(failure.exception))

    def test_invalid_source_errors_never_contain_remote_credentials(self):
        response = live_response()
        response["server"] = "http://secret-user:secret-password@remote-private.test/private"
        with self.assertRaises(live.LiveError) as failure:
            live.MediaSource.from_response(self.camera, response)
        self.assertEqual(failure.exception.code, "live_unsupported")
        self.assertNotIn("secret", str(failure.exception))
        self.assertNotIn("remote-private", str(failure.exception))

    async def test_fragmented_header_and_payload_are_read_whole(self):
        reader = asyncio.StreamReader()
        data = packet(b"\x47" + bytes(1315))
        async def send_fragments():
            for offset in range(0, len(data), 13):
                reader.feed_data(data[offset:offset + 13])
                await asyncio.sleep(0)
            reader.feed_eof()
        sender = asyncio.create_task(send_fragments())
        kind, payload = await live.read_packet(reader)
        self.assertEqual(kind, 0)
        self.assertEqual(payload, data[9:])
        self.assertIsNone(await live.read_packet(reader))
        await sender

    async def test_truncation_and_oversize_fail_without_waiting_for_unbounded_data(self):
        for data in (b"\0\0", live.FRAME_HEADER.pack(0, 1, 5) + b"12",
                     live.FRAME_HEADER.pack(0, 1, live.MAX_FRAME_BYTES + 1)):
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            with self.assertRaises(live.LiveError):
                await live.read_packet(reader)

    async def test_empty_control_packet_is_valid(self):
        reader = asyncio.StreamReader()
        reader.feed_data(packet(b"", kind=10))
        self.assertEqual(await live.read_packet(reader), (10, b""))


class TLSIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_public_ip_uses_only_reviewed_vendor_anchor_and_explicit_pin_profile(self):
        context, name = live.video_tls_identity("8.8.8.8")
        self.assertEqual(name, "8.8.8.8")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_flags, ssl.create_default_context().verify_flags)
        anchors = context.get_ca_certs(binary_form=True)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(hashlib.sha256(anchors[0]).hexdigest(), live._VENDOR_CA_SHA256)

    def test_dns_keeps_normal_public_ca_and_hostname_verification(self):
        context, name = live.video_tls_identity("video.example.test")
        self.assertEqual(name, "video.example.test")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_flags, ssl.create_default_context().verify_flags)
        self.assertNotIn(live._VENDOR_CA_SHA256,
                         {hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)})

    async def test_legacy_relay_resolves_public_destination_and_preserves_tls_hostname(self):
        host = "relay.immedia-semi.com"
        source = live.MediaSource(host, 332, b"", 15, 90, transport="rtsps")
        manager = live.LiveManager()
        loop = asyncio.get_running_loop()
        addresses = [(None, None, None, "", (address, 332)) for address in ("127.0.0.1", "8.8.8.8")]
        with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=addresses)), \
                patch.object(asyncio, "open_connection", new=AsyncMock(return_value=(object(), FakeWriter()))) as connect:
            await manager._connect_source(source)
        self.assertEqual(connect.call_args.args, ("8.8.8.8", 332))
        self.assertEqual(connect.call_args.kwargs["server_hostname"], host)
        self.assertTrue(connect.call_args.kwargs["ssl"].check_hostname)
        self.assertEqual(connect.call_args.kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
        await manager.stop()

    def test_vendor_trust_is_rejected_for_private_addresses_or_unexpected_ports(self):
        for address, port in (("127.0.0.1", 443), ("10.0.0.1", 443), ("169.254.1.1", 443),
                              ("::1", 443), ("8.8.8.8", 80)):
            with self.subTest(address=address, port=port), self.assertRaises(live.LiveError):
                live.video_tls_identity(address, port)

    def test_modified_or_missing_anchor_fails_closed(self):
        with patch.object(live, "_VENDOR_CA_SHA256", "0" * 64):
            with self.assertRaises(live.LiveError) as failure:
                live.video_tls_identity("8.8.8.8")
            self.assertEqual(failure.exception.code, "live_tls_config")
        with patch.object(live, "_VENDOR_CA_PATH", Path("/missing/blinky-private-path.pem")):
            with self.assertRaises(live.LiveError) as failure:
                live.video_tls_identity("8.8.8.8")
            self.assertNotIn("blinky-private-path", str(failure.exception))

    async def test_connection_retains_destination_and_requires_reviewed_peer_certificate(self):
        source = live.MediaSource("8.8.8.8", 443, b"private-auth", 15, 90)
        manager = live.LiveManager()
        try:
            with patch.object(asyncio, "open_connection", new_callable=AsyncMock) as connect:
                writer = FakeWriter()
                reviewed_der = ssl.PEM_cert_to_DER_cert(live._VENDOR_CA_PATH.read_text())
                writer.get_extra_info = lambda _: SimpleNamespace(getpeercert=lambda **_: reviewed_der)
                connect.return_value = (object(), writer)
                await manager._connect_source(source)
            self.assertEqual(connect.call_args.args, ("8.8.8.8", 443))
            self.assertEqual(connect.call_args.kwargs["server_hostname"], "8.8.8.8")
            self.assertFalse(connect.call_args.kwargs["ssl"].check_hostname)
            self.assertEqual(connect.call_args.kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(writer.data, b"")
        finally:
            await manager.stop()

    async def test_unexpected_peer_is_closed_before_stream_authentication(self):
        source = live.MediaSource("8.8.8.8", 443, b"private-auth", 15, 90)
        writer = FakeWriter()
        writer.get_extra_info = lambda _: SimpleNamespace(getpeercert=lambda **_: b"different-certificate")
        manager = live.LiveManager()
        try:
            with patch.object(asyncio, "open_connection", new_callable=AsyncMock) as connect:
                connect.return_value = (object(), writer)
                with self.assertRaises(live.LiveError) as failure:
                    await manager._connect_source(source)
            self.assertEqual(failure.exception.code, "live_tls_verification")
            self.assertTrue(writer.closed)
            self.assertEqual(writer.data, b"")
        finally:
            await manager.stop()

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required to generate a local TLS fixture")
    async def test_tls_accepts_reviewed_identity_and_rejects_unknown_certificate_before_auth(self):
        with tempfile.TemporaryDirectory(prefix="blinky-tls-test-") as folder:
            certificate = Path(folder) / "server.pem"
            private_key = Path(folder) / "server.key"
            await asyncio.to_thread(subprocess.run, [
                shutil.which("openssl"), "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(private_key), "-out", str(certificate), "-days", "1",
                "-subj", "/CN=*.immedia-semi.com",
                "-addext", "subjectAltName=DNS:*.immedia-semi.com",
            ], check=True, capture_output=True, timeout=15)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(str(certificate), str(private_key))
            accepted = asyncio.Event()
            loop = asyncio.get_running_loop()
            previous_handler = loop.get_exception_handler()
            def expected_handshake_rejection(event_loop, context):
                # The negative fixture intentionally aborts the server handshake.
                if isinstance(context.get("exception"), (ConnectionResetError, ssl.SSLError)):
                    return
                if previous_handler:
                    previous_handler(event_loop, context)
                else:
                    event_loop.default_exception_handler(context)
            loop.set_exception_handler(expected_handshake_rejection)
            async def server_connection(reader, writer):
                accepted.set()
                try:
                    await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(server_connection, "127.0.0.1", 0, ssl=server_context)
            original_connect = asyncio.open_connection
            async def connect_fixture(host, port, **kwargs):
                self.assertEqual((host, port), ("8.8.8.8", 443))
                return await original_connect("127.0.0.1", server.sockets[0].getsockname()[1], **kwargs)
            manager = live.LiveManager()
            source = live.MediaSource("8.8.8.8", 443, b"private-auth", 15, 90)
            try:
                with patch.object(asyncio, "open_connection", side_effect=connect_fixture):
                    # An unrelated self-signed certificate must not be accepted.
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        await manager._connect_source(source)
                    self.assertFalse(accepted.is_set())
                    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
                    # This test-only anchor models the independently reviewed vendor CA.
                    with patch.object(live, "_VENDOR_CA_PATH", certificate), \
                            patch.object(live, "_VENDOR_CA_SHA256", digest):
                        _, writer = await manager._connect_source(source)
                        await asyncio.wait_for(accepted.wait(), 2)
                        writer.close()
                        await writer.wait_closed()
            finally:
                await manager.stop()
                server.close()
                await server.wait_closed()
                loop.set_exception_handler(previous_handler)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.camera = SimpleNamespace(serial="CAMERA123456", camera_id="11", camera_type="default",
                                      sync=SimpleNamespace(network_id="7"))
        self.manager = FixtureManager(startup_timeout=0.2)
        self.ffmpeg = patch.object(live, "find_ffmpeg", return_value="/fake/ffmpeg")
        self.ffmpeg.start()

    async def asyncTearDown(self):
        await self.manager.stop()
        self.ffmpeg.stop()

    async def test_stop_closes_encoder_socket_and_command_and_removes_media(self):
        result = await self.manager.start(self.camera, object())
        self.assertTrue(self.manager.status()["active"])
        self.assertTrue(result["url"].startswith("http://127.0.0.1:"))
        self.assertTrue(self.manager.last_directory.is_dir())
        self.manager.reader.feed_data(packet(b"ignored", kind=21) + packet(b"\x47" + bytes(187)))
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(self.manager.encoder.stdin.data, b"\x47" + bytes(187))
        self.assertIn(live.FRAME_HEADER.pack(10, 1, 0), self.manager.writer.data)
        await self.manager.stop()
        await self.manager.stop()
        self.assertFalse(self.manager.status()["active"])
        self.assertTrue(self.manager.writer.closed)
        self.assertEqual(self.manager.encoder.returncode, 0)
        self.assertFalse(self.manager.last_directory.exists())
        self.manager.finished_command.assert_awaited_once_with(77)

    async def test_startup_failure_cleans_up_before_returning_safe_error(self):
        self.manager.create_ready_playlist = False
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_start_timeout")
        self.assertFalse(self.manager.status()["active"])
        self.assertFalse(self.manager.last_directory.exists())
        self.assertTrue(self.manager.writer.closed)
        self.manager.finished_command.assert_awaited_once()

    async def test_completed_start_command_keeps_a_running_live_view_open(self):
        self.manager._request_status = AsyncMock(return_value={
            "status_code": 908, "complete": True,
            "commands": [{"id": 77, "state_condition": "running"}],
        })
        await self.manager.start(self.camera, object())
        await asyncio.sleep(0.02)
        self.assertTrue(self.manager.status()["active"])
        self.assertFalse(self.manager.writer.closed)

    async def test_stream_eof_automatically_stops_all_owned_resources(self):
        await self.manager.start(self.camera, object())
        self.manager.reader.feed_eof()
        for _ in range(30):
            if self.manager.writer.closed:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.manager.status()["active"])
        await asyncio.sleep(0.02)
        self.assertFalse(self.manager.last_directory.exists())
        self.manager.finished_command.assert_awaited_once()

    async def test_expiry_enforces_a_short_wall_clock_lifetime(self):
        self.manager._max_duration = 0.08
        await self.manager.start(self.camera, object())
        await asyncio.sleep(0.15)
        self.assertFalse(self.manager.status()["active"])
        self.assertFalse(self.manager.last_directory.exists())
        self.assertEqual(self.manager.status()["reason"], "expired")

    async def test_cancelling_startup_cleans_up_partial_session(self):
        self.manager.create_ready_playlist = False
        task = asyncio.create_task(self.manager.start(self.camera, object()))
        while self.manager.last_directory is None:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.manager.last_directory.exists())
        self.assertTrue(self.manager.writer.closed)
        self.manager.finished_command.assert_awaited_once()

    async def test_missing_ffmpeg_does_not_wake_camera(self):
        with patch.object(live, "find_ffmpeg", return_value=None), \
                patch.object(self.manager, "_request_liveview", new_callable=AsyncMock) as request:
            with self.assertRaises(live.LiveError) as failure:
                await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "missing_ffmpeg")
        request.assert_not_awaited()

    async def test_source_error_is_sanitized_and_still_releases_command(self):
        self.manager._connect_source = AsyncMock(side_effect=OSError("private-token at remote-private.test"))
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertNotIn("private", str(failure.exception))
        self.manager.finished_command.assert_awaited_once()

    async def test_certificate_rejection_has_a_specific_safe_error_and_stops_command(self):
        self.manager._connect_source = AsyncMock(side_effect=ssl.SSLCertVerificationError("private-token at remote-private.test"))
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_tls_verification")
        self.assertNotIn("private", str(failure.exception))
        self.manager.finished_command.assert_awaited_once()

    async def test_connection_timeout_and_network_failure_are_distinct_from_media_timeout(self):
        for error, code in ((asyncio.TimeoutError(), "live_connect_timeout"),
                            (ConnectionRefusedError("private-endpoint"), "live_connect_failed")):
            with self.subTest(code=code):
                self.manager._connect_source = AsyncMock(side_effect=error)
                with self.assertRaises(live.LiveError) as failure:
                    await self.manager.start(self.camera, object())
                self.assertEqual(failure.exception.code, code)
                self.assertNotIn("private", str(failure.exception))

    async def test_request_timeout_is_reported_before_connecting_to_media(self):
        self.manager._request_liveview = AsyncMock(side_effect=asyncio.TimeoutError())
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_request_timeout")
        self.assertIsNone(self.manager._session)

    async def test_blink_command_with_no_media_server_is_released_with_availability_error(self):
        self.manager._request_liveview = AsyncMock(return_value={"command_id": 77, "join_available": False})
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_unavailable")
        self.manager.finished_command.assert_awaited_once_with(77)

    async def test_encoder_start_failure_is_sanitized_and_closes_media_socket(self):
        self.manager._create_ffmpeg = AsyncMock(side_effect=OSError("/private-user/secret-tool"))
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_encoder_failed")
        self.assertNotIn("private-user", str(failure.exception))
        self.assertTrue(self.manager.writer.closed)
        self.manager.finished_command.assert_awaited_once()

    async def test_listener_failure_cleans_encoder_and_temporary_files(self):
        self.manager._start_http = AsyncMock(side_effect=OSError("private-local-path"))
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_local_server_failed")
        self.assertNotIn("private-local", str(failure.exception))
        self.assertFalse(self.manager.last_directory.exists())
        self.assertEqual(self.manager.encoder.returncode, 0)

    async def test_encoder_exit_before_playlist_reports_encoder_error(self):
        self.manager.encoder.finish(1)
        with self.assertRaises(live.LiveError) as failure:
            await self.manager.start(self.camera, object())
        self.assertEqual(failure.exception.code, "live_encoder_failed")
        self.assertFalse(self.manager.last_directory.exists())


@unittest.skipUnless(live.find_ffmpeg() and importlib.util.find_spec("aiohttp"), "FFmpeg and aiohttp required for the local HLS integration check")
class LocalHLSTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_ffmpeg_hls_is_capability_scoped_and_cleaned_up(self):
        await self.check_hls(legacy=False)

    async def test_legacy_blink_video_reaches_the_same_hls_player(self):
        await self.check_hls(legacy=True)

    async def check_hls(self, *, legacy):
        from aiohttp import ClientSession
        executable = live.find_ffmpeg()
        generator = os.environ.get("BLINKY_TEST_MEDIA_ENCODER") or executable
        inputs = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10"]
        audio = []
        if legacy:
            inputs += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
            audio = ["-c:a", "aac"]
        generated = (await asyncio.to_thread(subprocess.run, [
            generator, "-hide_banner", "-loglevel", "error", *inputs,
            "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", *audio,
            "-pix_fmt", "yuv420p", "-g", "10", "-f", "mpegts", "pipe:1",
        ], capture_output=True, timeout=15, check=True)).stdout
        manager = FixtureManager(startup_timeout=5)
        manager._create_ffmpeg = live.LiveManager._create_ffmpeg.__get__(manager)
        manager._start_http = live.LiveManager._start_http.__get__(manager)
        if legacy:
            info = live_response() | {"server": "rtsps://relay.immedia-semi.com:332/private-session?blinkRTSP=true"}
            manager._request_liveview = AsyncMock(return_value=info)
        camera = SimpleNamespace(serial="CAMERA123456", camera_id="11", camera_type="default",
                                 sync=SimpleNamespace(network_id="7"))
        # The fake camera splits TCP writes and stays open until the consumer closes.
        disconnected = asyncio.Event()
        received_auth = asyncio.get_running_loop().create_future()
        async def camera_server(reader, writer):
            try:
                if legacy:
                    requests = []
                    for _ in range(4):
                        request = await reader.readuntil(b"\r\n\r\n")
                        method = request.split(b" ", 1)[0]
                        requests.append(method)
                        body = b"m=video 0 RTP/AVP 33\r\na=rtpmap:33 MP2T/90000\r\n" if method == b"DESCRIBE" else b""
                        writer.write(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                        await writer.drain()
                    received_auth.set_result(requests)
                    for offset in range(0, len(generated), 1316):
                        rtp = b"\x80\x21" + bytes(10) + generated[offset:offset + 1316]
                        writer.write(b"$\0" + len(rtp).to_bytes(2, "big") + rtp)
                        await writer.drain()
                else:
                    received_auth.set_result(await reader.readexactly(122))
                    framed = packet(generated)
                    for offset in range(0, len(framed), 101):
                        writer.write(framed[offset:offset + 101])
                        await writer.drain()
                        await asyncio.sleep(0)
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                disconnected.set()
        source = await asyncio.start_server(camera_server, "127.0.0.1", 0)
        async def connect_source(_):
            return await asyncio.open_connection("127.0.0.1", source.sockets[0].getsockname()[1])
        manager._connect_source = connect_source
        try:
            result = await manager.start(camera, object(), executable)
            if legacy:
                self.assertEqual(await received_auth, [b"OPTIONS", b"DESCRIBE", b"SETUP", b"PLAY"])
            else:
                self.assertIn(b"private-media-token", await received_auth)
            folder = Path(manager._session.directory.name)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            async with ClientSession() as client:
                async with client.get(result["url"]) as response:
                    self.assertEqual(response.status, 200)
                    playlist = await response.text()
                    self.assertIn("#EXTINF:", playlist)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                root = result["url"].rsplit("/", 2)[0]
                for suffix in ("/", "/wrong/stream.m3u8", "/stream.m3u8"):
                    async with client.get(root + suffix) as response:
                        self.assertEqual(response.status, 404)
                segment = next(line for line in playlist.splitlines() if line.endswith(".ts"))
                async with client.get(result["url"].rsplit("/", 1)[0] + "/" + segment,
                                      headers={"Range": "bytes=0-187"}) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(len(await response.read()), 188)
                async with client.get(result["url"], headers={"Host": "attacker.test"}) as response:
                    self.assertEqual(response.status, 404)
        finally:
            await manager.stop()
            source.close()
            await source.wait_closed()
            await asyncio.wait_for(disconnected.wait(), 2.0)
        self.assertFalse(folder.exists())
        manager.finished_command.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
