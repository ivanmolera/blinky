import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/Blinky/Bridge"))

import legacy_rtsp


ADDRESS = "rtsps://relay.example.test/session?token=private-token"
SDP = (
    "v=0\r\n"
    "m=video 0 RTP/AVP 33\r\n"
    "a=rtpmap:33 MP2T/90000\r\n"
    "a=control:trackID=0\r\n"
).encode("ascii")
TS = b"\x47" + bytes(187)


def reply(*headers, body=b"", status="200 OK"):
    fields = [f"RTSP/1.0 {status}", "CSeq: 1", *headers]
    if body:
        fields.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(fields) + "\r\n\r\n").encode("ascii") + body


def rtp(payload, *, flags=0x80, payload_type=33, options=b""):
    return bytes((flags, payload_type)) + bytes(10) + options + payload


def frame(channel, packet):
    return b"$" + bytes((channel,)) + len(packet).to_bytes(2, "big") + packet


class FakeWriter:
    def __init__(self):
        self.requests = []

    def write(self, data):
        self.requests.append(data.decode("ascii"))

    async def drain(self):
        pass


class LegacyRTSPTests(unittest.IsolatedAsyncioTestCase):
    async def run_relay(self, data, address=ADDRESS):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        writer = FakeWriter()
        media = AsyncMock()
        await legacy_rtsp.stream_mpegts(reader, writer, address, media)
        return writer.requests, media

    async def test_blink_legacy_handshake_with_fixed_sequence_and_early_video(self):
        # The measured Blink reply uses CSeq 1 for all four requests, and
        # SETUP leaves Session and Transport implicit while data already arrives.
        data = (
            reply("Public: OPTIONS, DESCRIBE, SETUP, PLAY")
            + reply("Content-Type: application/sdp", body=SDP)
            + frame(0, rtp(TS))
            + reply()
            + frame(1, b"RTCP")
            + frame(0, rtp(TS * 2))
            + reply()
            + frame(0, rtp(TS * 3))
        )
        requests, media = await self.run_relay(data)
        self.assertEqual([line.split()[0] for line in requests], ["OPTIONS", "DESCRIBE", "SETUP", "PLAY"])
        self.assertIn("Accept: application/sdp\r\n", requests[1])
        self.assertIn("Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n", requests[2])
        self.assertTrue(requests[2].startswith("SETUP rtsps://relay.example.test/session/trackID=0?token=private-token RTSP/1.0\r\n"))
        self.assertIn("CSeq: 4\r\n", requests[3])
        self.assertEqual([call.args[0] for call in media.await_args_list], [TS, TS * 2, TS * 3])

    async def test_negotiated_channels_session_and_extended_rtp_header(self):
        sdp = SDP.replace(b"33", b"96")
        data = (
            reply()
            + reply("Content-Base: rtsps://relay.example.test/alternate/", body=sdp)
            + reply("Session: opaque-session;timeout=60", "Transport: RTP/AVP/TCP;unicast;interleaved=4-5")
            + reply()
            + frame(0, rtp(TS, payload_type=96))
            + frame(5, rtp(TS, payload_type=96))
            + frame(4, rtp(TS + bytes((0, 2)), flags=0xb1, payload_type=96,
                            options=bytes(4) + b"\x00\x00\x00\x01" + bytes(4)))
        )
        requests, media = await self.run_relay(data)
        self.assertTrue(requests[2].startswith("SETUP rtsps://relay.example.test/alternate/trackID=0 "))
        self.assertIn("Session: opaque-session\r\n", requests[3])
        media.assert_awaited_once_with(TS)

    async def test_control_url_cannot_redirect_session_or_use_cleartext(self):
        for control in (
            "rtsps://other.example.test/track",
            "rtsps://relay.example.test:444/track",
            "rtsp://relay.example.test/track",
            "//other.example.test/track",
        ):
            with self.subTest(control=control), self.assertRaises(legacy_rtsp.LegacyRTSPError) as failure:
                hostile_sdp = SDP.replace(b"trackID=0", control.encode("ascii"))
                await self.run_relay(reply() + reply(body=hostile_sdp))
            self.assertNotIn(control, str(failure.exception))
            self.assertNotIn("private-token", str(failure.exception))

    async def test_redirect_and_non_interleaved_media_are_rejected(self):
        with self.assertRaises(legacy_rtsp.LegacyRTSPError):
            await self.run_relay(reply("Location: rtsps://other.example.test/", status="302 Found"))
        with self.assertRaises(legacy_rtsp.LegacyRTSPError):
            await self.run_relay(reply() + reply(body=SDP) + reply("Transport: RTP/AVP;unicast;server_port=7000-7001"))

    async def test_rejects_malformed_or_unbounded_response_and_truncated_media(self):
        for data in (
            b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 65537\r\n\r\n",
            b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nX-Too-Long: " + b"a" * (17 * 1024) + b"\r\n\r\n",
            reply() + reply(body=SDP) + reply() + reply() + b"$\x00\xff\xfftruncated",
        ):
            with self.assertRaises(legacy_rtsp.LegacyRTSPError):
                await self.run_relay(data)

    async def test_rejects_connection_url_credentials_and_control_characters(self):
        for url in (
            "rtsp://relay.example.test/session",
            "rtsps://user:private-token@relay.example.test/",
            ADDRESS + "\r\nX-Injected: value",
        ):
            with self.subTest(url=url), self.assertRaises(legacy_rtsp.LegacyRTSPError) as failure:
                await self.run_relay(b"", address=url)
            self.assertNotIn("private-token", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
