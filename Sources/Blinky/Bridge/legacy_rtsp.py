"""Read MPEG-TS over authenticated Blink RTSPS on an existing TLS stream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit, urlunsplit


class LegacyRTSPError(Exception):
    """The relay did not provide a usable RTSP video stream."""


_REQUEST_TIMEOUT = 12
_READ_TIMEOUT = 20
_MAX_HEADERS = 16 * 1024
_MAX_BODY = 64 * 1024
_TS_PACKET_SIZE = 188


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class _Frame:
    channel: int
    packet: bytes


def _secure_origin(address: str) -> tuple[str, int]:
    try:
        if len(address) > 4096 or not re.fullmatch(r"[!-~]+", address):
            raise ValueError
        url = urlsplit(address)
        if url.scheme != "rtsps" or not url.hostname or url.username is not None or url.password is not None or url.fragment:
            raise ValueError
        port = url.port if url.port is not None else 443
        if not 1 <= port <= 65535:
            raise ValueError
        return url.hostname, port
    except ValueError:
        raise LegacyRTSPError("Invalid RTSPS address.") from None


def _resolve_control(base: str, control: str) -> str:
    if not control or control == "*":
        return base
    if not re.fullmatch(r"[!-~]+", control):
        raise LegacyRTSPError("Invalid RTSP track.")
    origin = _secure_origin(base)
    try:
        target = urlsplit(control)
        if target.scheme:
            address = control
        else:
            if target.netloc or target.fragment:
                raise ValueError
            parent = urlsplit(base)
            # Resolve against the session resource as a directory, retaining
            # its session query for relative track identifiers.
            parent_path = parent.path.rstrip("/") + "/"
            resolved = urlsplit(urljoin(urlunsplit(("https", parent.netloc, parent_path, "", "")), control))
            query = target.query if "?" in control else parent.query
            address = urlunsplit(("rtsps", resolved.netloc, resolved.path, query, ""))
        if _secure_origin(address) != origin:
            raise ValueError
        return address
    except ValueError:
        raise LegacyRTSPError("Invalid RTSP track.") from None


def _video_track(body: bytes) -> tuple[str, set[int]]:
    try:
        lines = body.decode("ascii").splitlines()
    except UnicodeError:
        raise LegacyRTSPError("Invalid RTSP session description.") from None

    video = False
    formats: set[int] = set()
    mp2t: set[int] = {33}
    control = ""
    for line in lines:
        if line.startswith("m="):
            if video:
                break
            fields = line[2:].split()
            video = len(fields) >= 4 and fields[0] == "video" and fields[2] in {"RTP/AVP", "RTP/AVP/TCP"}
            if video:
                formats = {int(kind) for kind in fields[3:] if re.fullmatch(r"[0-9]{1,3}", kind) and int(kind) <= 127}
        elif video and line.startswith("a=rtpmap:"):
            match = re.fullmatch(r"a=rtpmap:([0-9]{1,3}) +MP2T/90000", line, flags=re.IGNORECASE)
            if match:
                mp2t.add(int(match[1]))
        elif video and line.startswith("a=control:"):
            control = line[len("a=control:"):].strip()
    if not (types := formats & mp2t):
        raise LegacyRTSPError("RTSP session does not offer MPEG-TS video.")
    return control, types


async def _read_unit(reader: asyncio.StreamReader) -> _Reply | _Frame | None:
    first = await reader.read(1)
    if not first:
        return None
    if first == b"$":
        frame = await reader.readexactly(3)
        return _Frame(frame[0], await reader.readexactly(int.from_bytes(frame[1:], "big")))
    try:
        block = first + await reader.readuntil(b"\r\n\r\n")
        if len(block) > _MAX_HEADERS:
            raise ValueError
        status, *lines = block[:-4].decode("ascii").split("\r\n")
        response = re.fullmatch(r"RTSP/1\.0 ([0-9]{3})(?: [\x20-\x7E]*)?", status)
        if response is None:
            raise ValueError
        headers = {}
        for line in lines:
            name, separator, value = line.partition(":")
            key = name.lower()
            if not separator or not re.fullmatch(r"[A-Za-z0-9-]+", name) or key in headers:
                raise ValueError
            headers[key] = value.strip()
        raw_length = headers.get("content-length", "0")
        if not re.fullmatch(r"[0-9]{1,5}", raw_length) or int(raw_length) > _MAX_BODY:
            raise ValueError
        length = int(raw_length)
    except (ValueError, asyncio.LimitOverrunError):
        raise LegacyRTSPError("Invalid RTSP response.") from None
    return _Reply(int(response[1]), headers, await reader.readexactly(length) if length else b"")


def _mpegts_payload(packet: bytes, types: set[int]) -> bytes | None:
    if len(packet) < 12 or packet[0] >> 6 != 2 or packet[1] & 0x7f not in types:
        return None
    offset = 12 + 4 * (packet[0] & 0x0f)
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            return None
        offset += 4 + 4 * int.from_bytes(packet[offset + 2:offset + 4], "big")
    end = len(packet)
    if packet[0] & 0x20:
        padding = packet[-1]
        if not 0 < padding <= end - offset:
            return None
        end -= padding
    payload = packet[offset:end]
    if not payload or len(payload) % _TS_PACKET_SIZE or any(
            payload[index] != 0x47 for index in range(0, len(payload), _TS_PACKET_SIZE)):
        return None
    return payload


async def stream_mpegts(reader, writer, address, write_media):
    """Negotiate interleaved media, then forward TS packets without exposing the URL."""
    _secure_origin(address)
    sequence, channel = 0, 0
    session = ""
    types: set[int] = set()

    async def forward(unit: _Frame):
        if unit.channel == channel and (payload := _mpegts_payload(unit.packet, types)):
            await write_media(payload)

    async def request(method, destination, extra_header=None):
        nonlocal sequence
        sequence += 1
        lines = [f"{method} {destination} RTSP/1.0", f"CSeq: {sequence}", "User-Agent: Blinky"]
        if session:
            lines.append(f"Session: {session}")
        if extra_header:
            lines.append(extra_header)
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        async def await_reply():
            await writer.drain()
            while (unit := await _read_unit(reader)) is not None:
                if isinstance(unit, _Frame):
                    await forward(unit)
                else:
                    return unit
            raise LegacyRTSPError("RTSP connection closed before the response.")

        try:
            reply = await asyncio.wait_for(await_reply(), _REQUEST_TIMEOUT)
        except (TimeoutError, asyncio.IncompleteReadError):
            raise LegacyRTSPError("RTSP response was incomplete or timed out.") from None
        # Blink XT and first-generation Indoor relays can reply with CSeq 1
        # for every request. Only one command is in flight on this connection.
        if reply.headers.get("cseq") not in (str(sequence), "1"):
            raise LegacyRTSPError("Invalid RTSP response sequence.")
        if reply.status != 200:
            raise LegacyRTSPError("Blink rejected the RTSP video request.")
        return reply

    await request("OPTIONS", address)
    description = await request("DESCRIBE", address, "Accept: application/sdp")
    control, types = _video_track(description.body)
    base = _resolve_control(address, description.headers.get("content-base", ""))
    track = _resolve_control(base, control)
    setup = await request("SETUP", track, "Transport: RTP/AVP/TCP;unicast;interleaved=0-1")
    # Some Blink relays omit both SETUP headers and nevertheless use channel 0.
    session = setup.headers.get("session", "").split(";", 1)[0].strip()
    if session and not re.fullmatch(r"[!-~]{1,128}", session):
        raise LegacyRTSPError("Invalid RTSP session identifier.")
    if transport := setup.headers.get("transport"):
        parts = transport.lower().split(";")
        if parts[0].strip() != "rtp/avp/tcp" or "multicast" in {part.strip() for part in parts[1:]}:
            raise LegacyRTSPError("RTSP relay did not select interleaved TCP.")
        if channels := re.search(r"(?:^|;)\s*interleaved=([0-9]{1,3})-([0-9]{1,3})(?:;|$)", transport, re.IGNORECASE):
            channel, rtcp = int(channels[1]), int(channels[2])
            if channel >= 255 or rtcp != channel + 1:
                raise LegacyRTSPError("Invalid RTSP media channels.")
    await request("PLAY", address)
    try:
        while (unit := await asyncio.wait_for(_read_unit(reader), _READ_TIMEOUT)) is not None:
            if isinstance(unit, _Frame):
                await forward(unit)
    except (TimeoutError, asyncio.IncompleteReadError):
        raise LegacyRTSPError("RTSP video stream stalled or ended mid-packet.") from None
