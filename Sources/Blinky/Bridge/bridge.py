#!/usr/bin/env python3
"""Blinky's private JSON-lines adapter for blinkpy.

Authentication stays in memory. stdin/stdout must never be logged.
Live HLS segments use a private temporary directory that is removed on stop.
The Swift client owns Keychain storage and user-selected export destinations.
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
import signal
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse
from live import LiveError, LiveManager, is_ffmpeg_available

BRIDGE_VERSION = "1"
POLL_SECONDS = 60.0
MAX_REQUEST_BYTES = 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_CLOUD_PAGES = 8
MAX_LOCAL_RECORDINGS = 500
MAX_RECORDINGS = 500
MAX_LOCAL_SYSTEMS = 4
RECORDING_FETCH_SECONDS = 75.0
EXPLICIT_RECORDING_FETCH_SECONDS = 50.0
EXPLICIT_RECORDING_REFRESH_SECONDS = 3.0
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
    """Normalize Blink ISO strings, datetime objects and numeric epoch timestamps."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, dt.datetime):
            parsed = value
        elif isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)):
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000.0
            parsed = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
        elif isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
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
        self._last_explicit_metadata = float("-inf")
        self._last_cloud_discovery_status = "unknown"
        self._last_actions = {}
        self._clip_index = {}
        self._clips = []
        self._recording_warnings = []
        self._recordings_status = "ready"
        self._recordings_truncated = False
        self._downloaded = set()
        self._force_refresh = False
        self._refreshed_at = None
        self._min_spacing = min_spacing
        self._live = None

    @property
    def dependencies(self):
        if self._dependencies is None:
            self._dependencies = load_dependencies()
        return self._dependencies

    async def close(self):
        if self._live is not None:
            await self._live.stop()
        self._authenticated = False
        self._awaiting_pin = False
        self._blink = None
        self._clip_index.clear()
        self._clips.clear()
        self._recording_warnings.clear()
        self._recordings_status = "ready"
        self._recordings_truncated = False
        self._downloaded.clear()
        self._last_actions.clear()
        self._last_refresh = float("-inf")
        self._last_metadata = float("-inf")
        self._last_explicit_metadata = float("-inf")
        self._last_cloud_discovery_status = "unknown"
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

    @property
    def recording_api(self):
        """Use raw recording endpoints so an API failure cannot masquerade as no clips."""
        return getattr(self.dependencies, "RecordingAPI", None) or importlib.import_module("blinkpy.api")

    def _is_authentication_error(self, error):
        return isinstance(error, getattr(self._dependencies, "AuthenticationErrors", ()))

    async def _recording_call(self, action, deadline):
        if deadline - time.monotonic() < 0.5:
            raise asyncio.TimeoutError()
        await self._network_pause()
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            raise asyncio.TimeoutError()
        return await asyncio.wait_for(action(), timeout=min(15.0, remaining))

    def _recording_items(self, response, key):
        if isinstance(response, list):
            return response
        if not isinstance(response, dict) or key not in response:
            raise BridgeError("recordings_unavailable", "Blink no ha devuelto una lista de grabaciones válida.")
        items = response[key]
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            raise BridgeError("recordings_unavailable", "Blink no ha devuelto una lista de grabaciones válida.")
        return items

    def _clip_camera(self, item, cameras, *, sync=None):
        raw_id = item.get("device_id", item.get("camera_id"))
        raw_name = str(item.get("device_name") or item.get("camera_name") or "Cámara Blink")
        if sync is not None:
            raw_name = getattr(sync, "_names_table", {}).get(raw_name, raw_name)
        candidates = [(identifier, camera, system) for identifier, camera, system in cameras
                      if sync is None or str(system.network_id) == str(sync.network_id)]
        matches = [(identifier, camera) for identifier, camera, system in candidates
                   if raw_id is not None and str(camera.camera_id) == str(raw_id)
                   and (item.get("network_id") is None or str(system.network_id) == str(item["network_id"]))]
        if len(matches) != 1:
            matches = [(identifier, camera) for identifier, camera, _ in candidates if camera.name == raw_name]
        if len(matches) != 1 and sync is not None:
            normalized = re.sub(r"\W+", "", raw_name)
            matches = [(identifier, camera) for identifier, camera, _ in candidates
                       if re.sub(r"\W+", "", camera.name) == normalized]
        if len(matches) == 1:
            return matches[0][0], matches[0][1].name
        # Unmapped names still appear in All recordings rather than silently disappearing.
        scope = str(sync.network_id) if sync is not None else str(item.get("network_id", "cloud"))
        return f"unmapped:{scope}:{raw_id or raw_name}", raw_name

    def _clip_record(self, item, cameras, *, source, sync=None, manifest_id=None):
        if not isinstance(item, dict):
            raise ValueError("Invalid recording entry")
        deleted = item.get("deleted", False)
        if deleted is True or deleted == 1 or str(deleted).lower() in {"true", "1"}:
            return None
        created_at = iso_date(item.get("created_at", item.get("createdAt", item.get("time"))))
        if created_at is None:
            raise ValueError("Invalid recording date")
        camera_id, name = self._clip_camera(item, cameras, sync=sync)
        duration = finite_number(item.get("duration", item.get("duration_seconds")))
        address = None
        raw_id = item.get("id", item.get("clip_id"))
        if source == "cloud":
            address = item.get("media", item.get("url"))
            if not isinstance(address, str):
                raise ValueError("Missing recording address")
            address = self._safe_media_address(address)
            raw_id = raw_id if raw_id is not None else hashlib.sha256(address.encode()).hexdigest()[:24]
            identifier = f"cloud:{raw_id}"
        else:
            if raw_id is None or not str(raw_id).isdigit():
                raise ValueError("Invalid local recording ID")
            identifier = f"local:{sync.network_id}:{sync.sync_id}:{raw_id}"
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:60] or "camera"
        timestamp = re.sub(r"[^0-9T]", "", created_at)
        filename = f"blinky-{source}-{safe_name}-{timestamp}.mp4"
        record = {"id": identifier, "cameraID": camera_id, "cameraName": name,
                  "createdAt": created_at, "durationSeconds": int(duration) if duration is not None else None,
                  "downloaded": identifier in self._downloaded, "source": source}
        entry = {"source": source, "address": address, "filename": filename}
        if source == "local":
            record["systemID"] = str(sync.network_id)
            entry.update(networkID=str(sync.network_id), syncID=str(sync.sync_id),
                         manifestID=str(manifest_id), clipID=str(raw_id), size=item.get("size"))
        return record, entry

    async def _cloud_page(self, *, since=None, page=0, deadline):
        """Use the zero-based changed-media API with an explicitly encoded UTC timestamp."""
        timestamp = "1970-01-01T00:00:00Z" if since is None else iso_date(since)
        if timestamp is None:
            raise BridgeError("recordings_unavailable", "La fecha de consulta de grabaciones no es válida.")
        query = urlencode({"since": timestamp, "page": page})
        path = f"/api/v1/accounts/{self._blink.account_id}/media/changed?{query}"
        url = self._blink.urls.base_url + self._safe_media_address(path)
        return await self._recording_call(lambda: self.recording_api.http_get(self._blink, url), deadline)

    async def _cloud_recordings(self, cameras, deadline):
        records, index, warnings = [], {}, []
        truncated, complete = False, True
        for page in range(MAX_CLOUD_PAGES):
            try:
                # Query the complete retained catalogue; the old 48-hour window omitted valid clips.
                response = await self._cloud_page(page=page, deadline=deadline)
                items = self._recording_items(response, "media")
            except Exception as error:
                if self._is_authentication_error(error):
                    raise
                warnings.append("No se ha podido actualizar la lista de grabaciones en la nube de Blink.")
                complete = False
                break
            if not items:
                break
            previous_count = len(index)
            invalid_count = 0
            for item in items:
                try:
                    result = self._clip_record(item, cameras, source="cloud")
                    if result is None:
                        continue
                    record, entry = result
                    index[record["id"]] = entry
                    records.append(record)
                except (ValueError, TypeError, BridgeError):
                    invalid_count += 1
            if invalid_count:
                warnings.append("Algunas grabaciones de la nube tienen datos incompletos o incompatibles y no se han podido mostrar.")
            if len(index) == previous_count and any(isinstance(item, dict) and not item.get("deleted") for item in items):
                warnings.append("Blink no ha permitido avanzar en la lista completa de grabaciones de la nube.")
                complete = False
                break
            if isinstance(response, dict) and response.get("has_more") is False:
                break
            if page == MAX_CLOUD_PAGES - 1:
                truncated = True
        return records, index, warnings, truncated, complete

    async def _local_recordings(self, sync, cameras, deadline):
        requested = await self._recording_call(
            lambda: self.recording_api.request_local_storage_manifest(self._blink, sync.network_id, sync.sync_id), deadline)
        if not isinstance(requested, dict) or requested.get("id") is None:
            raise BridgeError("local_storage_unavailable", "El almacenamiento local de Blink no ha respondido.")
        manifest = None
        for attempt in range(3):
            response = await self._recording_call(
                lambda: self.recording_api.get_local_storage_manifest(
                    self._blink, sync.network_id, sync.sync_id, requested["id"]), deadline)
            if isinstance(response, dict) and "clips" in response and response.get("manifest_id") is not None:
                manifest = response
                break
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        if manifest is None:
            raise BridgeError("local_storage_unavailable", "El almacenamiento local de Blink sigue ocupado o no está disponible.")
        items = self._recording_items(manifest, "clips")
        # A manifest can contain the whole USB drive. Sort first, then apply a visible bound.
        items.sort(key=lambda item: iso_date(item.get("created_at")) or "" if isinstance(item, dict) else "", reverse=True)
        truncated = len(items) > MAX_LOCAL_RECORDINGS
        records, index, invalid_count = [], {}, 0
        for item in items[:MAX_LOCAL_RECORDINGS]:
            try:
                result = self._clip_record(item, cameras, source="local", sync=sync, manifest_id=manifest["manifest_id"])
                if result is None:
                    continue
                record, entry = result
                records.append(record)
                index[record["id"]] = entry
            except (ValueError, TypeError, BridgeError):
                invalid_count += 1
        warnings = ["Algunas grabaciones locales tienen datos incompletos o incompatibles y no se han podido mostrar."] if invalid_count else []
        return records, index, warnings, truncated

    async def _metadata(self, *, force=False, budget=None):
        now = time.monotonic()
        if force:
            if now - self._last_explicit_metadata < EXPLICIT_RECORDING_REFRESH_SECONDS:
                return
            self._last_explicit_metadata = now
        elif now - self._last_metadata < POLL_SECONDS:
            return
        cameras = list(self._cameras())
        deadline = now + (budget if budget is not None else RECORDING_FETCH_SECONDS)
        records, index, warnings, truncated, cloud_complete = await self._cloud_recordings(cameras, deadline)
        successful_sources = int(cloud_complete)
        failed_local = set()
        local_systems = [sync for sync in self._blink.sync.values() if getattr(sync, "local_storage", False) is True]
        if len(local_systems) > MAX_LOCAL_SYSTEMS:
            truncated = True
            warnings.append("Hay más sistemas con almacenamiento local de los que se pueden consultar en una actualización.")
            failed_local.update(str(sync.network_id) for sync in local_systems[MAX_LOCAL_SYSTEMS:])
        for sync in local_systems[:MAX_LOCAL_SYSTEMS]:
            try:
                local_records, local_index, local_warnings, local_truncated = await self._local_recordings(sync, cameras, deadline)
                records.extend(local_records)
                index.update(local_index)
                warnings.extend(local_warnings)
                truncated = truncated or local_truncated
                successful_sources += 1
            except Exception as error:
                if self._is_authentication_error(error):
                    raise
                failed_local.add(str(sync.network_id))
                warnings.append("No se han podido actualizar las grabaciones del almacenamiento local. Comprueba que el Sync Module y la unidad USB están disponibles.")
        # Retain the last known recordings from a failed source instead of replacing them with an empty list.
        for old_record in self._clips:
            old_entry = self._clip_index.get(old_record["id"], {})
            preserve = old_entry.get("source") == "cloud" and not cloud_complete
            preserve = preserve or (old_entry.get("source") == "local" and old_entry.get("networkID") in failed_local)
            if preserve and old_record["id"] not in index:
                records.append(old_record)
                index[old_record["id"]] = old_entry
        unique_records = {record["id"]: record for record in records}
        ordered = sorted(unique_records.values(), key=lambda item: item["createdAt"], reverse=True)
        truncated = truncated or len(ordered) > MAX_RECORDINGS
        self._clips = ordered[:MAX_RECORDINGS]
        self._clip_index = {record["id"]: index[record["id"]] for record in self._clips}
        self._recording_warnings = list(dict.fromkeys(warnings))
        self._recordings_truncated = truncated
        self._recordings_status = "ready" if not warnings else ("partial" if successful_sources or records else "unavailable")
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

    async def _snapshot(self, *, refresh_recordings=False):
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
        await self._metadata(force=refresh_recordings, budget=EXPLICIT_RECORDING_FETCH_SECONDS if refresh_recordings else None)
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
        return {"systems": systems, "cameras": cameras, "clips": self._clips, "refreshedAt": self._refreshed_at,
                "recordingWarnings": self._recording_warnings, "recordingsStatus": self._recordings_status,
                "recordingsTruncated": self._recordings_truncated}

    def _action_gate(self, key, interval):
        now = time.monotonic()
        remaining = interval - (now - self._last_actions.get(key, float("-inf")))
        if remaining > 0:
            raise BridgeError("rate_limited", f"Espera {math.ceil(remaining)} segundos antes de repetir esta acción.")
        self._last_actions[key] = now

    @property
    def action_api(self):
        return getattr(self.dependencies, "ActionAPI", None) or self.recording_api

    def _command_error(self, code=None):
        suffix = f" (código {code})" if isinstance(code, int) and not isinstance(code, bool) else ""
        return BridgeError("command_failed", f"Blink ha rechazado la acción{suffix}. La cámara o el sistema pueden estar ocupados.")

    def _command_code(self, value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
            return int(value)
        return None

    def _check_command(self, result):
        """Validate acknowledgement only; completion requires a separate status request."""
        if not isinstance(result, dict):
            raise BridgeError("command_failed", "Blink no ha confirmado la acción. Actualiza el estado antes de repetirla.")
        for key in ("code", "status_code"):
            if key in result:
                code = self._command_code(result[key])
                if code not in (200, 201, 908):
                    raise self._command_error(code)
        command_id = result.get("id", result.get("command_id"))
        if isinstance(command_id, bool) or not isinstance(command_id, (int, str)) or not str(command_id).isdigit() or int(command_id) <= 0:
            raise BridgeError("command_unconfirmed", "Blink no ha devuelto un identificador de comando válido. Actualiza el estado antes de repetir la acción.")

    async def _action_call(self, action, deadline, *, timeout=20.0):
        if deadline - time.monotonic() < 0.5:
            raise BridgeError("command_timeout", "Blink no ha confirmado la acción a tiempo. Actualiza el estado antes de repetirla.")
        await self._network_pause()
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            raise BridgeError("command_timeout", "Blink no ha confirmado la acción a tiempo. Actualiza el estado antes de repetirla.")
        try:
            return await asyncio.wait_for(action(), timeout=min(timeout, remaining))
        except asyncio.TimeoutError:
            raise BridgeError("command_timeout", "Blink no ha confirmado la acción a tiempo. Actualiza el estado antes de repetirla.") from None

    async def _wait_for_action(self, acknowledgement, network_id, deadline, *, modern_fallback=False):
        """Confirm status_code 908 and complete, rather than treating an acknowledgement as success."""
        self._check_command(acknowledgement)
        command_network = acknowledgement.get("network_id", network_id)
        if str(command_network) != str(network_id):
            raise BridgeError("command_unconfirmed", "Blink ha devuelto un comando para otro sistema. Actualiza el estado.")
        status_deadline = min(deadline, time.monotonic() + 40.0)
        command_id = acknowledgement.get("id", acknowledgement.get("command_id"))
        for attempt in range(12):
            status = await self._action_call(
                lambda: self.action_api.request_command_status(self._blink, network_id, command_id),
                status_deadline, timeout=10.0)
            modern_status = False
            if modern_fallback and (not isinstance(status, dict) or not status or "status_code" not in status):
                path = f"/accounts/{self._blink.account_id}/networks/{network_id}/commands/{command_id}"
                url = self._blink.urls.base_url + self._safe_media_address(path)
                response = await self._action_call(
                    lambda: self.recording_api.http_get(self._blink, url), status_deadline, timeout=10.0)
                if isinstance(response, dict) and ("complete" in response or "status" in response):
                    status, modern_status = response, True
            if isinstance(status, dict):
                if modern_status and status.get("status") == "failed":
                    raise self._command_error()
                code = self._command_code(status.get("status_code"))
                if code is not None and code != 908:
                    raise self._command_error(code)
                if known_bool(status.get("complete")) is True or (modern_status and status.get("status") == "complete"):
                    if code != 908 and not modern_status:
                        raise BridgeError("command_unconfirmed", "Blink no ha confirmado el resultado del comando.")
                    return
            if attempt < 11:
                await asyncio.sleep(1.0)
        raise BridgeError("command_timeout", "Blink sigue procesando la acción. Actualiza el estado antes de repetirla.")

    def _action_endpoint(self, method, sync, camera=None, requested=None):
        """Build the same routes as blinkpy 0.25.9, without its unchecked wait_for_command result."""
        network_id = str(sync.network_id)
        data = None
        if method == "set_system_armed":
            state = "arm" if requested else "disarm"
            path = f"/api/v1/accounts/{self._blink.account_id}/networks/{network_id}/state/{state}"
        else:
            camera_type = camera.camera_type or "default"
            if camera_type == "mini":
                base = f"/api/v1/accounts/{self._blink.account_id}/networks/{network_id}/owls/{camera.camera_id}"
            elif camera_type == "doorbell":
                base = f"/api/v1/accounts/{self._blink.account_id}/networks/{network_id}/doorbells/{camera.camera_id}"
            elif camera_type == "default":
                base = f"/network/{network_id}/camera/{camera.camera_id}"
            else:
                raise BridgeError("unsupported_model", "Este modelo no admite todavía esta acción en Blinky.")
            if method == "set_motion_enabled":
                if camera_type == "mini":
                    path, data = f"{base}/config", json.dumps({"enabled": requested})
                else:
                    path = f"{base}/{'enable' if requested else 'disable'}"
            elif method == "record":
                path = f"{base}/clip"
            else:
                path = f"{base}/thumbnail"
        return self._blink.urls.base_url + self._safe_media_address(path), data

    async def _refresh_action_state(self, deadline):
        updated = await self._action_call(
            lambda: self._blink.refresh(force=True, force_cache=True), deadline, timeout=35.0)
        if updated is not True or not self._blink.available:
            self._force_refresh = True
            raise BridgeError("command_unconfirmed", "Blink ha recibido la acción, pero no se ha podido comprobar el estado actualizado.")
        self._last_refresh = time.monotonic()
        self._refreshed_at = utc_now()
        self._force_refresh = False

    def _system_by_id(self, identifier):
        return next((system for system in self._blink.sync.values() if str(system.network_id) == str(identifier)), None)

    def _merge_recordings(self, records, index):
        merged = {clip["id"]: clip for clip in self._clips}
        merged.update({clip["id"]: clip for clip in records})
        combined_index = dict(self._clip_index)
        combined_index.update(index)
        ordered = sorted(merged.values(), key=lambda clip: clip["createdAt"], reverse=True)
        self._recordings_truncated = self._recordings_truncated or len(ordered) > MAX_RECORDINGS
        self._clips = ordered[:MAX_RECORDINGS]
        self._clip_index = {clip["id"]: combined_index[clip["id"]] for clip in self._clips}

    async def _discover_requested_recording(self, camera_id, sync, known_ids, requested_at, deadline):
        """Poll metadata only; listing a requested recording never exports or deletes its media."""
        cameras = list(self._cameras())
        earliest = requested_at - dt.timedelta(seconds=2)
        discovery_deadline = min(deadline, time.monotonic() + 40.0)
        cloud_checked = False
        self._last_cloud_discovery_status = "unknown"
        for attempt in range(3):
            records, index = [], {}
            try:
                response = await self._cloud_page(since=earliest, page=0, deadline=discovery_deadline)
                items = self._recording_items(response, "media")
                cloud_checked = True
                for item in items:
                    try:
                        result = self._clip_record(item, cameras, source="cloud")
                        if result is not None:
                            record, entry = result
                            records.append(record)
                            index[record["id"]] = entry
                    except (ValueError, TypeError, BridgeError):
                        continue
            except Exception as error:
                if self._is_authentication_error(error):
                    raise
            new_clip = next((clip for clip in records if clip["cameraID"] == camera_id and clip["id"] not in known_ids
                             and dt.datetime.fromisoformat(clip["createdAt"].replace("Z", "+00:00")) >= earliest), None)
            if new_clip is None and getattr(sync, "local_storage", False) is True:
                try:
                    local_records, local_index, _, _ = await self._local_recordings(sync, cameras, discovery_deadline)
                    records.extend(local_records)
                    index.update(local_index)
                    new_clip = next((clip for clip in local_records if clip["cameraID"] == camera_id and clip["id"] not in known_ids
                                     and dt.datetime.fromisoformat(clip["createdAt"].replace("Z", "+00:00")) >= earliest), None)
                except Exception as error:
                    if self._is_authentication_error(error):
                        raise
            self._merge_recordings(records, index)
            if new_clip is not None:
                self._last_metadata = time.monotonic()
                self._last_cloud_discovery_status = "found"
                return new_clip
            if discovery_deadline - time.monotonic() < 5.0:
                break
            if attempt < 2:
                await asyncio.sleep(2.0)
        self._last_cloud_discovery_status = "empty" if cloud_checked else "unavailable"
        return None

    async def _execute_action(self, method, params):
        self._require_auth()
        deadline = time.monotonic() + 90.0
        camera, requested = None, None
        if method == "set_system_armed":
            identifier = self._require_text(params, "system_id")
            requested = self._require_bool(params, "armed")
            sync = self._system_by_id(identifier)
            if sync is None:
                raise BridgeError("not_found", "No se encuentra ese sistema Blink.")
        else:
            identifier = self._require_text(params, "camera_id")
            camera, sync = self._camera(identifier)
            if method == "set_motion_enabled":
                requested = self._require_bool(params, "enabled")
        interval = 5 if method in {"set_system_armed", "set_motion_enabled"} else POLL_SECONDS
        self._action_gate((method, identifier), interval)
        known_recordings = {clip["id"] for clip in self._clips}
        requested_at = dt.datetime.now(dt.timezone.utc)
        if method == "record":
            self._last_metadata = float("-inf")
        else:
            # The server may apply a request even when its acknowledgement times out.
            self._force_refresh = True
        endpoint, data = self._action_endpoint(method, sync, camera, requested)
        acknowledgement = await self._action_call(
            lambda: self.action_api.http_post(self._blink, endpoint, data=data), deadline)
        await self._wait_for_action(acknowledgement, sync.network_id, deadline, modern_fallback=method == "record")
        if method == "record":
            self._last_metadata = float("-inf")
            recording = await self._discover_requested_recording(identifier, sync, known_recordings, requested_at, deadline)
            # Discovery already queried the sources; avoid another full catalogue scan
            # inside the immediate post-action snapshot, including the pending case.
            self._last_metadata = time.monotonic()
            system_armed = known_bool(sync.arm)
            if recording is not None:
                return {"completed": True, "commandCompleted": True, "systemArmed": system_armed,
                        "recordingRequested": True, "recordingAvailable": True, "recordingID": recording["id"],
                        "message": "La nueva grabación ya está disponible en Grabaciones."}
            if self._last_cloud_discovery_status == "unavailable":
                message = "Blink ha confirmado el comando, pero no se ha podido comprobar el guardado en la nube. Actualiza Grabaciones."
            else:
                message = "Blink ha confirmado el comando, pero todavía no ha publicado el clip en la nube. Actualiza Grabaciones para comprobarlo."
            return {"completed": False, "commandCompleted": True, "systemArmed": system_armed,
                    "recordingRequested": True, "recordingAvailable": False, "message": message}
        await self._refresh_action_state(deadline)
        system = self._system_by_id(sync.network_id)
        system_armed = known_bool(system.arm) if system is not None else None
        if method == "set_system_armed":
            if system_armed is not requested:
                raise BridgeError("state_mismatch", "Blink ha recibido el comando, pero el estado del sistema todavía no coincide con lo solicitado. Actualiza el estado antes de repetirlo.")
            message = "Sistema activado. Las cámaras con detección habilitada podrán registrar movimiento." if requested else "Sistema desactivado."
            return {"completed": True, "systemArmed": system_armed, "message": message}
        if method == "set_motion_enabled":
            updated_camera, _ = self._camera(identifier)
            motion_enabled = known_bool(updated_camera.motion_enabled)
            if motion_enabled is not requested:
                raise BridgeError("state_mismatch", "Blink ha recibido el comando, pero no ha confirmado el cambio de detección de movimiento. Actualiza el estado antes de repetirlo.")
            message = "Detección habilitada." if requested else "Detección desactivada."
            if requested and system_armed is False:
                message += " El sistema está desarmado; actívalo para registrar movimiento."
            return {"completed": True, "systemArmed": system_armed, "motionEnabled": motion_enabled, "message": message}
        return {"completed": True, "systemArmed": system_armed, "message": "Blink ha confirmado la captura de imagen."}

    async def _action(self, method, params):
        try:
            return await asyncio.wait_for(self._execute_action(method, params), timeout=95.0)
        except asyncio.TimeoutError:
            raise BridgeError("command_timeout", "Blink no ha confirmado la acción a tiempo. Actualiza el estado antes de repetirla.") from None

    async def _read_media(self, response):
        if response is None or getattr(response, "status", None) != 200:
            if response is not None:
                response.release()
            raise BridgeError("media_unavailable", "No se ha podido descargar el clip desde Blink.")
        try:
            content_type = str(getattr(response, "content_type", "")).lower()
            if content_type in {"application/json", "text/html", "text/plain"}:
                raise BridgeError("media_unavailable", "Blink todavía no ha preparado el archivo de vídeo.")
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
        if not isinstance(entry, dict):
            raise BridgeError("not_found", "El clip ya no está en la lista. Actualiza las grabaciones.")
        self._action_gate(("download_clip", identifier), 5)
        source = entry.get("source", "cloud")
        deadline = time.monotonic() + 90.0
        if source == "local":
            try:
                reported_size = int(entry.get("size") or 0)
            except (ValueError, TypeError):
                reported_size = 0
            if reported_size > MAX_MEDIA_BYTES:
                raise BridgeError("media_too_large", "El clip supera el límite de 20 MB de esta versión.")
            for key in ("networkID", "syncID", "manifestID", "clipID"):
                if not str(entry.get(key, "")).isdigit():
                    raise BridgeError("media_unavailable", "Los datos de la grabación local no son válidos. Actualiza la lista.")
            prepared = await self._recording_call(
                lambda: self.recording_api.request_local_storage_clip(
                    self._blink, entry["networkID"], entry["syncID"], entry["manifestID"], entry["clipID"]), deadline)
            self._check_command(prepared)
            address = (f"/api/v1/accounts/{self._blink.account_id}/networks/{entry['networkID']}"
                       f"/sync_modules/{entry['syncID']}/local_storage/manifest/{entry['manifestID']}"
                       f"/clip/request/{entry['clipID']}")
        else:
            address = entry.get("address")
            if not isinstance(address, str):
                raise BridgeError("media_unavailable", "Blink no ha proporcionado la dirección del clip.")
        address = self._safe_media_address(address)
        data = None
        for attempt in range(3 if source == "local" else 1):
            response = await self._recording_call(lambda: self._blink.do_http_get(address), deadline)
            status = getattr(response, "status", None)
            pending_content = str(getattr(response, "content_type", "")).lower() == "application/json"
            if source == "local" and (status in {404, 409, 412, 503} or pending_content) and attempt < 2:
                if response is not None:
                    response.release()
                await asyncio.sleep(attempt + 1)
                continue
            data = await self._read_media(response)
            break
        if data is None:
            raise BridgeError("media_unavailable", "El almacenamiento local sigue ocupado. Vuelve a intentar la descarga.")
        self._downloaded.add(identifier)
        for clip in self._clips:
            if clip["id"] == identifier:
                clip["downloaded"] = True
        return {"dataBase64": base64.b64encode(data).decode("ascii"), "mimeType": "video/mp4", "filename": entry["filename"]}

    async def dispatch(self, method, params):
        if method == "health":
            try:
                self.dependencies
                available = True
                version = importlib.metadata.version("blinkpy")
            except (BridgeError, importlib.metadata.PackageNotFoundError):
                available, version = False, None
            return {"bridgeVersion": BRIDGE_VERSION, "pythonVersion": sys.version.split()[0],
                    "blinkpyAvailable": available, "blinkpyVersion": version,
                    "ffmpegAvailable": is_ffmpeg_available()}
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
            refresh_recordings = params.get("refresh_recordings", False)
            if not isinstance(refresh_recordings, bool):
                raise BridgeError("invalid_request", "El formato de actualización de grabaciones no es válido.")
            return await self._snapshot(refresh_recordings=refresh_recordings)
        if method == "live_start":
            self._require_auth()
            camera, _ = self._camera(self._require_text(params, "camera_id"))
            await self._network_pause()
            if self._live is None:
                self._live = LiveManager()
            return await self._live.start(camera, self._blink)
        if method == "live_stop":
            if self._live is not None:
                await self._live.stop()
            return {}
        if method == "live_status":
            return self._live.status() if self._live is not None else {"active": False}
        if method in {"set_system_armed", "set_motion_enabled", "refresh_thumbnail", "record"}:
            if self._live is not None and self._live.status().get("active"):
                raise BridgeError("camera_busy", "Detén el directo antes de modificar la cámara o solicitar un clip.")
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
        except LiveError as error:
            if error.code == "session_expired":
                await self.close()
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
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(stop_signal, main_task.cancel)
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
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(main())
