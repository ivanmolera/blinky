"""Bounded Blink IMMIS sessions exposed as private, loopback-only HLS.

Protocol references (this implementation does not vendor external source):
https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/livestream.py
https://github.com/fronzbot/blinkpy/pull/1309
https://github.com/fronzbot/blinkpy/pull/1311
https://github.com/Derinbay-Labs/ha-blink-live
https://github.com/madmachinations/blink

Blink authentication stays in this process. FFmpeg receives MPEG-TS on stdin,
never a Blink URL or credential. HLS files are ephemeral and never archived.
The bridge must await stop() before closing its Blink HTTP session or exiting.
"""
from __future__ import annotations

import atexit
import asyncio
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import ssl
import struct
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse
import uuid

MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_SESSION_SECONDS = 90.0
FRAME_HEADER = struct.Struct(">BII")


class LiveError(Exception):
    """Safe, user-facing failure; never includes upstream exception text."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_ffmpeg(path: str | None = None) -> str | None:
    """Resolve an executable without running a shell or installing software."""
    override = path or os.environ.get("BLINKY_FFMPEG")
    candidates = [override] if override else [
        shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"
    ]
    for candidate in candidates:
        if candidate:
            location = Path(candidate).expanduser()
            if location.is_file() and os.access(location, os.X_OK):
                return str(location.absolute())
    return None


def is_ffmpeg_available(path: str | None = None) -> bool:
    return find_ffmpeg(path) is not None


def _seconds(value, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return default


@dataclass(frozen=True)
class MediaSource:
    host: str = field(repr=False)
    port: int
    auth_header: bytes = field(repr=False)
    poll_seconds: float
    duration: float

    @classmethod
    def from_response(cls, camera, response: dict) -> "MediaSource":
        try:
            address = response["server"]
            if not isinstance(address, str) or len(address) > 4096:
                raise ValueError
            target = urlparse(address)
            if target.scheme != "immis" or not target.hostname or target.username or target.password or target.fragment:
                raise ValueError
            port = target.port or 443
            client_id = int(parse_qs(target.query)["client_id"][0])
            if not 0 <= client_id <= 0xFFFFFFFF:
                raise ValueError
            serial = camera.serial
            connection = target.path.rsplit("/", 1)[-1].split("__", 1)[0]
            token = response.get("liveview_token", response.get("liveViewToken", "")) or ""
            if not isinstance(serial, str) or not serial or not connection or not isinstance(token, str):
                raise ValueError
            def string_field(value: str, width: int) -> bytes:
                encoded = value.encode("utf-8")
                if len(encoded) > width or b"\x00" in encoded:
                    raise ValueError
                return struct.pack(">I", width) + encoded.ljust(width, b"\x00")
            header = b"".join((
                struct.pack(">I", 40), string_field(serial, 16),
                struct.pack(">I", client_id), b"\x01\x08", string_field(token, 64),
                string_field(connection, 16), struct.pack(">I", 1),
            ))
            return cls(target.hostname, port, header,
                       min(30.0, max(5.0, _seconds(response.get("polling_interval"), 15.0))),
                       min(MAX_SESSION_SECONDS, _seconds(response.get("session_duration", response.get("duration")), MAX_SESSION_SECONDS)))
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            raise LiveError("live_unsupported", "Blink no ha proporcionado una sesión de vídeo compatible.") from None


async def read_packet(reader: asyncio.StreamReader, timeout: float = 15.0):
    """Read one whole IMMI packet, rejecting truncated or oversized frames."""
    try:
        header = await asyncio.wait_for(reader.readexactly(FRAME_HEADER.size), timeout)
    except asyncio.IncompleteReadError as error:
        if not error.partial:
            return None
        raise LiveError("live_protocol", "La conexión de vídeo se ha interrumpido.") from None
    except asyncio.TimeoutError:
        raise LiveError("live_timeout", "Blink ha dejado de enviar vídeo.") from None
    message_type, _, length = FRAME_HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise LiveError("live_protocol", "Blink ha enviado un paquete de vídeo no compatible.")
    try:
        payload = await asyncio.wait_for(reader.readexactly(length), timeout) if length else b""
    except (asyncio.IncompleteReadError, asyncio.TimeoutError):
        raise LiveError("live_protocol", "La conexión de vídeo se ha interrumpido.") from None
    return message_type, payload


@dataclass
class _Session:
    blink: object = field(repr=False)
    network_id: str
    command_id: object
    directory: tempfile.TemporaryDirectory = field(repr=False)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    capability: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    url: str | None = field(default=None, repr=False)
    expires_at: str | None = None
    deadline: float = 0
    reader: object = field(default=None, repr=False)
    writer: object = field(default=None, repr=False)
    process: object = field(default=None, repr=False)
    runner: object = field(default=None, repr=False)
    tasks: set = field(default_factory=set, repr=False)
    active: bool = True
    ready: bool = False
    reason: str | None = None
    last_request: float = field(default_factory=time.monotonic)

    def result(self) -> dict:
        return {"url": self.url, "sessionID": self.session_id, "expiresAt": self.expires_at}


class LiveManager:
    """Own at most one live view; every terminal path closes its resources."""

    def __init__(self, *, max_duration: float = MAX_SESSION_SECONDS,
                 startup_timeout: float = 35.0, idle_timeout: float = 15.0):
        self._max_duration = min(MAX_SESSION_SECONDS, _seconds(max_duration, MAX_SESSION_SECONDS))
        self._startup_timeout = _seconds(startup_timeout, 35.0)
        self._idle_timeout = _seconds(idle_timeout, 15.0)
        self._lock = asyncio.Lock()
        self._session: _Session | None = None
        self._last_reason: str | None = None
        atexit.register(self._emergency_cleanup)

    def status(self) -> dict:
        session = self._session
        if session and session.active and session.ready and not session.reason:
            return {"active": True, **session.result(), "reason": None}
        return {"active": False, "url": None, "sessionID": None,
                "expiresAt": None, "reason": self._last_reason or (session.reason if session else None)}

    async def start(self, camera, blink, ffmpeg_path: str | None = None) -> dict:
        async with self._lock:
            await self._stop_locked("replaced")
            executable = find_ffmpeg(ffmpeg_path)
            if not executable:
                raise LiveError("missing_ffmpeg", "El vídeo en directo necesita FFmpeg. Instálalo y vuelve a abrir Blinky.")
            session = None
            try:
                response = await asyncio.wait_for(self._request_liveview(camera, blink), 30.0)
                if not isinstance(response, dict):
                    raise LiveError("live_unavailable", "Blink no ha podido iniciar el vídeo. La cámara puede estar ocupada.")
                session = _Session(blink, str(camera.sync.network_id), response.get("command_id"),
                                   tempfile.TemporaryDirectory(prefix="blinky-live-"))
                self._session = session
                source = MediaSource.from_response(camera, response)
                lifetime = min(self._max_duration, source.duration)
                session.deadline = time.monotonic() + lifetime
                session.expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=lifetime)).isoformat(timespec="seconds").replace("+00:00", "Z")
                session.reader, session.writer = await asyncio.wait_for(self._connect_source(source), 15.0)
                session.writer.write(source.auth_header)
                await asyncio.wait_for(session.writer.drain(), 5.0)
                session.process = await self._create_ffmpeg(session, executable)
                await self._start_http(session)
                self._watch(session, lambda: self._pump(session), "ended")
                self._watch(session, lambda: self._keepalive(session), "ended")
                self._watch(session, lambda: self._poll(session, source.poll_seconds), "ended")
                self._watch(session, session.process.wait, "encoder_stopped")
                self._watch(session, lambda: self._lease(session), "expired")
                await self._wait_ready(session)
                session.ready = True
                session.last_request = time.monotonic()
                self._last_reason = None
                return session.result()
            except BaseException as error:
                await self._stop_locked("failed")
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, LiveError):
                    raise
                if type(error).__name__ in {"UnauthorizedError", "TokenRefreshFailed", "LoginError", "BlinkTwoFARequiredError"}:
                    raise LiveError("session_expired", "La sesión ha caducado. Vuelve a conectar tu cuenta Blink.") from None
                raise LiveError("live_unavailable", "No se ha podido abrir el vídeo de Blink. Comprueba la conexión y vuelve a intentarlo.") from None

    async def stop(self):
        async with self._lock:
            await self._stop_locked("stopped")

    async def _request_liveview(self, camera, blink):
        from blinkpy import api
        # Read the response before command polling so cancellation can still release it.
        family = {"": (6, "cameras"), "default": (6, "cameras"),
                  "mini": (2, "owls"), "doorbell": (2, "doorbells")}.get(camera.camera_type)
        if family is None:
            raise LiveError("live_unsupported", "Blink no ha proporcionado una sesión de vídeo compatible.")
        version, resource = family
        address = (f"{blink.urls.base_url}/api/v{version}/accounts/{blink.account_id}"
                   f"/networks/{camera.sync.network_id}/{resource}/{camera.camera_id}/liveview")
        return await api.http_post(blink, address,
                                   data=json.dumps({"intent": "liveview", "motion_event_start_time": None}))

    async def _request_status(self, session):
        from blinkpy import api
        return await api.request_command_status(session.blink, session.network_id, session.command_id)

    async def _request_done(self, session):
        from blinkpy import api
        return await api.request_command_done(session.blink, session.network_id, session.command_id)

    async def _connect_source(self, source):
        return await asyncio.open_connection(source.host, source.port,
                                             ssl=ssl.create_default_context(), server_hostname=source.host)

    async def _create_ffmpeg(self, session, executable):
        folder = Path(session.directory.name)
        return await asyncio.create_subprocess_exec(
            executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-fflags", "+genpts", "-analyzeduration", "1000000", "-probesize", "1000000",
            "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "4", "-hls_delete_threshold", "2",
            "-hls_flags", "delete_segments+temp_file+independent_segments", "-hls_allow_cache", "0",
            "-hls_segment_filename", str(folder / "segment%06d.ts"), str(folder / "stream.m3u8"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )

    async def _start_http(self, session):
        from aiohttp import web
        application = web.Application(client_max_size=1024)
        async def serve(request):
            if self._session is not session or not session.active:
                raise web.HTTPNotFound()
            expected_host = urlparse(session.url).netloc
            if request.host != expected_host:
                raise web.HTTPNotFound()
            name = request.match_info["name"]
            if not re.fullmatch(r"stream\.m3u8|segment[0-9]{6}\.ts", name):
                raise web.HTTPNotFound()
            path = Path(session.directory.name) / name
            if not path.is_file():
                raise web.HTTPNotFound()
            session.last_request = time.monotonic()
            return web.FileResponse(path, headers={
                "Content-Type": "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t",
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
        application.router.add_get(f"/{session.capability}/{{name}}", serve)
        session.runner = web.AppRunner(application, access_log=None, shutdown_timeout=1.0)
        await session.runner.setup()
        site = web.TCPSite(session.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        session.url = f"http://127.0.0.1:{port}/{session.capability}/stream.m3u8"

    async def _pump(self, session):
        while session.active:
            packet = await read_packet(session.reader)
            if packet is None:
                return
            message_type, payload = packet
            if message_type == 0 and payload.startswith(b"\x47"):
                session.process.stdin.write(payload)
                await asyncio.wait_for(session.process.stdin.drain(), 5.0)

    async def _keepalive(self, session):
        sequence, ticks = 0, 0
        while session.active:
            if ticks % 10 == 0:
                sequence += 1
                session.writer.write(FRAME_HEADER.pack(0x0A, sequence, 0))
            session.writer.write(FRAME_HEADER.pack(0x12, 1000, 24) + bytes(24))
            await asyncio.wait_for(session.writer.drain(), 5.0)
            ticks += 1
            await asyncio.sleep(1.0)

    async def _poll(self, session, interval):
        if session.command_id is None:
            return
        while session.active:
            response = await asyncio.wait_for(self._request_status(session), 10.0)
            if not isinstance(response, dict) or response.get("status_code") != 908:
                return
            for command in response.get("commands", []):
                if str(command.get("id")) == str(session.command_id) and command.get("state_condition") not in {"new", "running"}:
                    return
            await asyncio.sleep(interval)

    async def _lease(self, session):
        while session.active:
            now = time.monotonic()
            if now >= session.deadline:
                session.reason = "expired"
                return
            if session.ready and now - session.last_request >= self._idle_timeout:
                session.reason = "idle"
                return
            await asyncio.sleep(min(0.5, max(0.01, session.deadline - now)))

    async def _wait_ready(self, session):
        deadline = min(session.deadline, time.monotonic() + self._startup_timeout)
        playlist = Path(session.directory.name) / "stream.m3u8"
        while time.monotonic() < deadline:
            if session.reason or session.process.returncode is not None:
                break
            if playlist.is_file():
                text = playlist.read_text(encoding="utf-8")
                names = re.findall(r"^segment[0-9]{6}\.ts$", text, re.MULTILINE)
                if "#EXTINF:" in text and names and all((playlist.parent / name).is_file() for name in names):
                    return
            await asyncio.sleep(0.05)
        raise LiveError("live_start_timeout", "Blink no ha enviado vídeo reproducible a tiempo. Inténtalo más tarde.")

    def _watch(self, session, operation, reason):
        async def watch():
            try:
                await operation()
            except asyncio.CancelledError:
                return
            except Exception:
                reason_value = "failed"
            else:
                reason_value = session.reason or reason
            if session.active:
                session.reason = reason_value
                async with self._lock:
                    if self._session is session:
                        await self._stop_locked(reason_value)
        task = asyncio.create_task(watch())
        session.tasks.add(task)

    async def _stop_locked(self, reason):
        session = self._session
        if session is None:
            return
        self._last_reason = reason
        session.active = False
        current = asyncio.current_task()
        remaining = [task for task in session.tasks if task is not current]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        if session.writer is not None:
            session.writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.writer.wait_closed(), 2.0)
        process = session.process
        if process is not None:
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 1.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), 1.0)
        if session.runner is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.runner.cleanup(), 2.0)
        with contextlib.suppress(Exception):
            session.directory.cleanup()
        if session.command_id is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._request_done(session), 5.0)
        if self._session is session:
            self._session = None

    def _emergency_cleanup(self):
        """The normal EOF path awaits stop(); this also covers interpreter exit."""
        session = self._session
        if session:
            if session.process is not None and session.process.returncode is None:
                with contextlib.suppress(OSError):
                    os.kill(session.process.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                session.directory.cleanup()
