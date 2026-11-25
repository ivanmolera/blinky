#!/usr/bin/env python3
"""Blinky's private JSON-lines adapter for blinkpy.

Nothing is written to disk. stdin/stdout carry secrets and must never be logged.
The Swift client owns Keychain storage and user-selected media destinations.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import hashlib
import importlib
import importlib.metadata
import io
import json
import logging
import math
import re
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BRIDGE_VERSION = "1"
POLL_SECONDS = 60.0
MAX_REQUEST_BYTES = 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024
SESSION_KEYS = frozenset({
    "username", "token", "expires_in", "expiration_date", "refresh_token",
    "host", "region_id", "client_id", "account_id", "user_id", "hardware_id",
    "uid", "device_id",
})
# blinkpy debug/error logs may include authenticated URLs, payloads and account IDs.
logging.disable(logging.CRITICAL)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def finite_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def known_bool(value):
    if isinstance(value, bool):
        return value
    if value == 1:
        return True
    if value == 0:
        return False
    return None


class BridgeError(Exception):
    """An explicitly safe error message that can be shown to the user."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DiscardOutput(io.TextIOBase):
    """Discard third-party prints rather than mixing them into the protocol."""
    def write(self, value):
        return len(value)


def load_dependencies():
    try:
        aiohttp = importlib.import_module("aiohttp")
        blinkpy = importlib.import_module("blinkpy.blinkpy")
        auth = importlib.import_module("blinkpy.auth")
        return SimpleNamespace(
            Blink=blinkpy.Blink,
            Auth=auth.Auth,
            ClientSession=aiohttp.ClientSession,
            ClientTimeout=aiohttp.ClientTimeout,
            TwoFARequired=auth.BlinkTwoFARequiredError,
            AuthenticationErrors=(auth.UnauthorizedError, auth.TokenRefreshFailed, auth.LoginError),
        )
    except (ImportError, AttributeError):
        raise BridgeError("missing_runtime", "Instala el conector con scripts/bootstrap.sh y selecciona su Python en Ajustes.") from None


class BlinkBridge:
    def __init__(self, dependencies=None, *, min_spacing=5.0):
        self._dependencies = dependencies
        self._blink = None
        self._http = None
        self._authenticated = False
        self._awaiting_pin = False
        self._last_network = float("-inf")
        self._last_refresh = float("-inf")
        self._last_metadata = float("-inf")
        self._last_actions = {}
        self._clip_index = {}
        self._clips = []
        self._downloaded = set()
        self._force_refresh = False
        self._refreshed_at = None
        self._min_spacing = min_spacing

    @property
    def dependencies(self):
        if self._dependencies is None:
            self._dependencies = load_dependencies()
        return self._dependencies

    async def close(self):
        self._authenticated = False
        self._awaiting_pin = False
        self._blink = None
        self._clip_index.clear()
        self._clips.clear()
        self._downloaded.clear()
        self._last_actions.clear()
        self._last_refresh = float("-inf")
        self._last_metadata = float("-inf")
        self._force_refresh = False
        self._refreshed_at = None
        if self._http is not None:
            http = self._http
            self._http = None
            await http.close()

    async def _network_pause(self):
        remaining = self._min_spacing - (time.monotonic() - self._last_network)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_network = time.monotonic()

    def _require_auth(self):
        if not self._authenticated or self._blink is None:
            raise BridgeError("not_authenticated", "Conecta tu cuenta Blink para continuar.")

    def _require_text(self, params, key, *, limit=512):
        value = params.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise BridgeError("invalid_request", "Falta un dato necesario o su formato no es válido.")
        return value.strip() if key != "password" else value

    def _require_bool(self, params, key):
        value = params.get(key)
        if not isinstance(value, bool):
            raise BridgeError("invalid_request", "El estado solicitado no es válido.")
        return value

    def _session_string(self):
        if not self._authenticated or self._blink is None:
            return None
        attributes = self._blink.auth.login_attributes
        # Never retain the password or PIN in Keychain. A refresh-token session is sufficient.
        clean = {key: value for key, value in attributes.items()
                 if key in SESSION_KEYS and (value is None or isinstance(value, (str, int, float)))}
        return json.dumps(clean, separators=(",", ":"), allow_nan=False)

    def _auth_result(self, status="authenticated", message=None):
        return {"status": status, "message": message, "session": self._session_string()}

    async def _connect(self, data, *, restoring=False):
        await self.close()
        deps = self.dependencies
        self._http = deps.ClientSession(timeout=deps.ClientTimeout(total=90))
        self._blink = deps.Blink(session=self._http, refresh_rate=POLL_SECONDS)
        self._blink.auth = deps.Auth(data, no_prompt=True, session=self._http)
        await self._network_pause()
        try:
            success = await self._blink.start()
        except deps.TwoFARequired:
            if restoring:
                await self.close()
                return self._auth_result("signed_out", "La sesión ha caducado. Vuelve a iniciar sesión.")
            self._awaiting_pin = True
            return self._auth_result("requires_2fa", "Introduce el código de verificación enviado por Blink.")
        if not success or not self._blink.available:
            await self.close()
            if restoring:
                return self._auth_result("signed_out", "La sesión ha caducado o Blink no está disponible. Vuelve a conectar.")
            raise BridgeError("authentication_failed", "No se pudo conectar con Blink. Revisa tus credenciales y la conexión.")
        self._authenticated = True
        self._last_refresh = time.monotonic()
        self._refreshed_at = utc_now()
        # Password is needed only for this login, not for ongoing token refresh.
        self._blink.auth.data.pop("password", None)
        self._blink.auth.data.pop("2fa_code", None)
        return self._auth_result()

    async def _login(self, params):
        username = self._require_text(params, "username")
        password = self._require_text(params, "password", limit=4096)
        return await self._connect({"username": username, "password": password})

    async def _restore(self, params):
        raw = self._require_text(params, "session", limit=65536)
        try:
            stored = json.loads(raw)
            if not isinstance(stored, dict):
                raise ValueError
            data = {key: value for key, value in stored.items() if key in SESSION_KEYS
                    and (value is None or isinstance(value, (str, int, float)))}
            if not isinstance(data.get("refresh_token"), str) or not data["refresh_token"]:
                raise ValueError
            # Only trusted Blink region hosts can enter blinkpy's URL construction.
            region = data.get("region_id")
            host = data.get("host")
            if region is not None and (not isinstance(region, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", region)):
                raise ValueError
            if host is not None and (not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+\.immedia-semi\.com", host)):
                raise ValueError
        except (ValueError, TypeError):
            raise BridgeError("invalid_session", "La sesión guardada no es válida. Vuelve a iniciar sesión.") from None
        return await self._connect(data, restoring=True)

    async def _verify(self, params):
        if self._blink is None or not self._awaiting_pin:
            raise BridgeError("verification_required", "Inicia sesión antes de introducir el código.")
        code = self._require_text(params, "code", limit=16)
        if not re.fullmatch(r"[0-9]{4,10}", code):
            raise BridgeError("invalid_pin", "Introduce el código numérico que te ha enviado Blink.")
        await self._network_pause()
        if not await self._blink.send_2fa_code(code):
            raise BridgeError("invalid_pin", "Blink no ha aceptado el código. Compruébalo o vuelve a iniciar sesión.")
        self._authenticated = True
        self._awaiting_pin = False
        self._last_refresh = time.monotonic()
        self._refreshed_at = utc_now()
        self._blink.auth.data.pop("password", None)
        self._blink.auth.data.pop("2fa_code", None)
        return self._auth_result()

    def _cameras(self):
        # Iterating sync modules avoids blinkpy's name-keyed merged camera map.
        for sync in self._blink.sync.values():
            for camera in sync.cameras.values():
                raw_id = str(camera.camera_id)
                camera_type = camera.camera_type or "default"
                identifier = f"{sync.network_id}:{camera_type}:{raw_id}"
                yield identifier, camera, sync

    def _camera(self, identifier):
        for camera_id, camera, sync in self._cameras():
            if camera_id == identifier:
                return camera, sync
        raise BridgeError("not_found", "No se encuentra esa cámara en tu cuenta.")

    def _image_date(self, camera):
        thumbnail = getattr(camera, "thumbnail", None)
        if not isinstance(thumbnail, str):
            return None
        timestamp = parse_qs(urlparse(thumbnail).query).get("ts", [None])[0]
        try:
            return dt.datetime.fromtimestamp(float(timestamp), dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    async def _metadata(self):
        if time.monotonic() - self._last_metadata < POLL_SECONDS:
            return
        await self._network_pause()
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).isoformat()
        # stop is exclusive upstream: stop=3 requests pages 1 and 2 (up to 50 clips).
        videos = await self._blink.get_videos_metadata(since=since, stop=3)
        cameras = list(self._cameras())
        new_clips, new_index = [], {}
        for item in videos if isinstance(videos, list) else []:
            if not isinstance(item, dict) or item.get("deleted"):
                continue
            address = item.get("media")
            created_at = iso_date(item.get("created_at"))
            if not isinstance(address, str) or not created_at:
                continue
            try:
                safe_address = self._safe_media_address(address)
            except BridgeError:
                continue
            identifier = str(item.get("id") or hashlib.sha256(address.encode()).hexdigest()[:24])
            device_id = str(item.get("device_id", item.get("camera_id", "")))
            name = str(item.get("device_name") or "Cámara Blink")
            matches = [(key, camera) for key, camera, sync in cameras
                       if str(camera.camera_id) == device_id
                       and (not item.get("network_id") or str(sync.network_id) == str(item["network_id"]))]
            if len(matches) != 1:
                matches = [(key, camera) for key, camera, _ in cameras if camera.name == name]
            camera_id = matches[0][0] if len(matches) == 1 else f"unmapped:{device_id}"
            duration = finite_number(item.get("duration", item.get("duration_seconds")))
            new_clips.append({"id": identifier, "cameraID": camera_id, "cameraName": name,
                              "createdAt": created_at, "durationSeconds": int(duration) if duration is not None else None,
                              "downloaded": identifier in self._downloaded})
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:60] or "camera"
            timestamp = re.sub(r"[^0-9T]", "", created_at)
            new_index[identifier] = (safe_address, f"blinky-{safe_name}-{timestamp}.mp4")
        self._clips = sorted(new_clips, key=lambda item: item["createdAt"], reverse=True)[:50]
        self._clip_index = {item["id"]: new_index[item["id"]] for item in self._clips}
        self._last_metadata = time.monotonic()

    def _safe_media_address(self, address):
        parsed = urlparse(address)
        base = urlparse(self._blink.urls.base_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "https" or parsed.netloc != base.netloc:
                raise BridgeError("media_unavailable", "La dirección del clip no es compatible.")
            address = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if not address.startswith("/") or address.startswith("//") or any(c in address for c in "\r\n\\"):
            raise BridgeError("media_unavailable", "La dirección del clip no es compatible.")
        return address

    async def _snapshot(self):
        self._require_auth()
        if self._force_refresh or time.monotonic() - self._last_refresh >= POLL_SECONDS:
            await self._network_pause()
            result = await self._blink.refresh(force_cache=self._force_refresh)
            if result:
                self._last_refresh = time.monotonic()
                self._refreshed_at = utc_now()
                self._force_refresh = False
            if not self._blink.available:
                raise BridgeError("blink_unavailable", "Blink no ha podido actualizar el estado. Inténtalo más tarde.")
        await self._metadata()
        systems = [{"id": str(sync.network_id), "name": sync.name, "armed": known_bool(sync.arm)}
                   for sync in self._blink.sync.values()]
        cameras = []
        for identifier, camera, sync in self._cameras():
            image = camera.image_from_cache
            status = str(camera.status) if camera.status is not None else "unknown"
            if status == "done":
                status = "online"
            if status not in {"online", "offline"}:
                status = "unknown"
            battery = camera.battery
            wifi = finite_number(camera.wifi_strength)
            cameras.append({
                "id": identifier, "systemID": str(sync.network_id), "name": camera.name,
                "model": str(camera.product_type or camera.camera_type or "Blink"),
                "status": status, "battery": str(battery) if battery is not None else None,
                "wifiStrength": int(wifi) if wifi is not None else None,
                "temperatureCelsius": finite_number(camera.temperature_c),
                "motionEnabled": known_bool(camera.motion_enabled),
                "thumbnailBase64": base64.b64encode(image).decode("ascii") if isinstance(image, bytes) and len(image) <= MAX_MEDIA_BYTES else None,
                "updatedAt": self._image_date(camera),
            })
        return {"systems": systems, "cameras": cameras, "clips": self._clips, "refreshedAt": self._refreshed_at}

    def _action_gate(self, key, interval):
        now = time.monotonic()
        remaining = interval - (now - self._last_actions.get(key, float("-inf")))
        if remaining > 0:
            raise BridgeError("rate_limited", f"Espera {math.ceil(remaining)} segundos antes de repetir esta acción.")
        self._last_actions[key] = now

    def _check_command(self, result):
        if result is None or result is False:
            raise BridgeError("command_failed", "Blink no ha confirmado la acción. Actualiza el estado antes de repetirla.")
        if isinstance(result, dict):
            code = result.get("code")
            if code not in (None, 200, 201):
                raise BridgeError("command_failed", "Blink ha rechazado la acción. La cámara puede estar ocupada.")
        elif isinstance(getattr(result, "status", None), int) and not 200 <= result.status < 300:
            raise BridgeError("command_failed", "Blink ha rechazado la acción. La cámara puede estar ocupada.")

    async def _action(self, method, params):
        self._require_auth()
        if method == "set_system_armed":
            identifier = self._require_text(params, "system_id")
            armed = self._require_bool(params, "armed")
            sync = next((s for s in self._blink.sync.values() if str(s.network_id) == identifier), None)
            if sync is None:
                raise BridgeError("not_found", "No se encuentra ese sistema Blink.")
            self._action_gate((method, identifier), 5)
            await self._network_pause()
            result = await sync.async_arm(armed)
        else:
            identifier = self._require_text(params, "camera_id")
            camera, _ = self._camera(identifier)
            self._action_gate((method, identifier), 5 if method == "set_motion_enabled" else POLL_SECONDS)
            await self._network_pause()
            if method == "set_motion_enabled":
                result = await camera.async_arm(self._require_bool(params, "enabled"))
            elif method == "refresh_thumbnail":
                result = await camera.snap_picture()
            else:
                result = await camera.record()
        self._check_command(result)
        # A following snapshot can make one explicit refresh to verify the mutation.
        self._force_refresh = True
        if method == "record":
            self._last_metadata = float("-inf")
        return {}

    async def _read_media(self, response):
        if response is None or getattr(response, "status", None) != 200:
            if response is not None:
                response.release()
            raise BridgeError("media_unavailable", "No se ha podido descargar el clip desde Blink.")
        try:
            declared = getattr(response, "content_length", None)
            if declared is not None and declared > MAX_MEDIA_BYTES:
                raise BridgeError("media_too_large", "El clip supera el límite de 20 MB de esta versión.")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_MEDIA_BYTES:
                    raise BridgeError("media_too_large", "El clip supera el límite de 20 MB de esta versión.")
            if not data:
                raise BridgeError("media_unavailable", "Blink ha devuelto un clip vacío.")
            return bytes(data)
        finally:
            response.release()

    async def _download(self, params):
        self._require_auth()
        identifier = self._require_text(params, "clip_id")
        entry = self._clip_index.get(identifier)
        if entry is None:
            raise BridgeError("not_found", "El clip ya no está en la lista. Actualiza las grabaciones.")
        self._action_gate(("download_clip", identifier), 5)
        await self._network_pause()
        address, filename = entry
        response = await self._blink.do_http_get(address)
        data = await self._read_media(response)
        self._downloaded.add(identifier)
        for clip in self._clips:
            if clip["id"] == identifier:
                clip["downloaded"] = True
        return {"dataBase64": base64.b64encode(data).decode("ascii"), "mimeType": "video/mp4", "filename": filename}

    async def dispatch(self, method, params):
        if method == "health":
            try:
                self.dependencies
                available = True
                version = importlib.metadata.version("blinkpy")
            except (BridgeError, importlib.metadata.PackageNotFoundError):
                available, version = False, None
            return {"bridgeVersion": BRIDGE_VERSION, "pythonVersion": sys.version.split()[0],
                    "blinkpyAvailable": available, "blinkpyVersion": version}
        if method == "login":
            return await self._login(params)
        if method == "verify":
            return await self._verify(params)
        if method == "restore":
            return await self._restore(params)
        if method == "logout":
            await self.close()
            return {}
        if method == "session":
            return self._auth_result("authenticated" if self._authenticated else "signed_out")
        if method == "snapshot":
            return await self._snapshot()
        if method in {"set_system_armed", "set_motion_enabled", "refresh_thumbnail", "record"}:
            return await self._action(method, params)
        if method == "download_clip":
            return await self._download(params)
        raise BridgeError("unsupported_method", "Esta función todavía no está disponible en Blinky.")

    async def handle_request(self, request):
        identifier = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
                raise BridgeError("invalid_request", "La petición no es válida.")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise BridgeError("invalid_request", "La petición no es válida.")
            # Never forward third-party prints; they may contain confidential metadata.
            with contextlib.redirect_stdout(DiscardOutput()), contextlib.redirect_stderr(DiscardOutput()):
                result = await asyncio.wait_for(self.dispatch(method, params), timeout=120)
            return {"id": identifier, "result": result}
        except BridgeError as error:
            return {"id": identifier, "error": {"code": error.code, "message": error.message}}
        except asyncio.TimeoutError:
            return {"id": identifier, "error": {"code": "timeout", "message": "Blink tarda demasiado en responder. Actualiza el estado antes de repetir una acción."}}
        except Exception as error:
            authentication_errors = getattr(self._dependencies, "AuthenticationErrors", ())
            if isinstance(error, authentication_errors):
                # Only concrete blinkpy authentication exceptions invalidate the session.
                # Cleanup must not leak third-party output or obscure the safe response.
                with contextlib.redirect_stdout(DiscardOutput()), contextlib.redirect_stderr(DiscardOutput()):
                    with contextlib.suppress(Exception):
                        await self.close()
                return {"id": identifier, "error": {"code": "session_expired", "message": "La sesión de Blink ha caducado. Vuelve a iniciar sesión."}}
            # Do not interpolate exception strings: libraries may include tokens or URLs.
            return {"id": identifier, "error": {"code": "blink_unavailable", "message": "No se ha podido completar la conexión con Blink. Inténtalo más tarde o vuelve a conectar."}}


async def main():
    bridge = BlinkBridge()
    protocol_output = sys.stdout
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_REQUEST_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST_BYTES:
                response = {"id": None, "error": {"code": "invalid_request", "message": "La petición supera el tamaño permitido."}}
                # Discard the rest of an oversized line before reading another request.
                while line and not line.endswith(b"\n"):
                    line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_REQUEST_BYTES + 1)
            else:
                try:
                    request = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    request = None
                response = await bridge.handle_request(request)
            protocol_output.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
            protocol_output.flush()
    finally:
        with contextlib.redirect_stdout(DiscardOutput()), contextlib.redirect_stderr(DiscardOutput()):
            await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
