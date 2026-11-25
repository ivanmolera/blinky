# Local Blink bridge

Blinky uses `blinkpy==0.25.9`, a community library for Blink's cloud service. The native app runs `bridge.py` as a child process without opening an HTTP port. Python 3.10 or later is required; prepare the environment with `scripts/bootstrap.sh`.

## Protocol

Each request and response occupies one JSON line. Request: `{"id":"identifier","method":"health","params":{}}`. Response: `{"id":"identifier","result":{...}}` or `{"id":"identifier","error":{"code":"code","message":"safe message"}}`.

The channel contains sensitive data and must not be logged. The bridge discards stdout/stderr from external libraries and silences their logs, which may contain authenticated URLs or identifiers. It does not accept download paths or URLs supplied by the client: `download_clip` only accepts the ID of a clip previously discovered in the account.

| Method | Parameters | Result |
| --- | --- | --- |
| `health` | None | Bridge/Python/blinkpy versions and bridge availability; works without blinkpy installed. |
| `login` | `username`, `password` | `status`, `message`, and `session`; may return `requires_2fa`. |
| `verify` | `code` | Completes the verification started by `login`. |
| `restore` | `session` | Restores a session using its refresh token, without a password. |
| `session` | None | Current session, including tokens refreshed during an operation. |
| `logout` | None | Discards the process session and closes local connections. |
| `snapshot` | Optional `refresh_recordings` boolean | `systems`, `cameras`, `clips`, `refreshedAt`. An explicit refresh can check for newly published clips before the normal cache expires. |
| `set_system_armed` | `system_id`, `armed` (boolean) | Requests arming or disarming the system. |
| `set_motion_enabled` | `camera_id`, `enabled` (boolean) | Requests enabling or disabling motion detection. |
| `refresh_thumbnail` | `camera_id` | Requests a new image from the camera. |
| `record` | `camera_id` | Requests a clip; does not start continuous recording. |
| `download_clip` | `clip_id` | `dataBase64`, `mimeType`, `filename` for export by the app. |
| `live_start` | `camera_id` | Private loopback HLS `url`, opaque `sessionID`, and ISO 8601 `expiresAt`. |
| `live_status` | None | `active` and optional session metadata and terminal reason. |
| `live_stop` | None | Closes the relay, FFmpeg process, HTTP server, and temporary media. |

`session` is a string containing a JSON object, suitable for storage in macOS Keychain. The bridge does not create credential or session files. Live playback uses HLS segments in a private temporary directory; the directory is removed when the view ends. The password and PIN are removed when authentication completes and are excluded from the serialized session. The client must store the session returned by `login`/`verify`/`restore` and query `session` again after operations to persist token rotation. A revoked refresh token requires signing in again; there is no automatic retry with a password.

Camera IDs are opaque: `<network_id>:<camera_type>:<camera_id>`. The `armed` and `motionEnabled` states may be `null` when Blink does not report them; they must not be interpreted as disabled. `updatedAt` is only provided when the image timestamp can be extracted from Blink's image URL; `refreshedAt` indicates when the state was refreshed, not when every image was captured.

## Initial version limits

- State and clip lists normally use a 60-second cache. An explicit refresh can query recordings earlier, and a change can trigger a refresh to check the result. New image or clip requests have a minimum interval of 60 seconds per camera. Detection does not provide real-time notifications.
- Clips: up to eight cloud pages and four USB systems per refresh. Cloud queries start at page zero, encode UTC timestamps, and preserve usable clips if pagination stalls. USB manifests are bounded to 500 entries per source; the combined list is bounded to 500. Catalog discovery has a 75-second budget, or 50 seconds for an explicit refresh. `recordingWarnings`, `recordingsStatus`, and `recordingsTruncated` distinguish an empty list from failed or partial discovery. Each clip includes a `source` of `cloud` or `local`.
- Each download is limited to 20 MB, and HTTP responses are read in chunks. Continuous recording, two-way audio, and automatic downloads are not provided.
- Actions use the verified Blink endpoints and poll command completion. System and motion changes additionally refresh and compare server state. Recording returns `completed: true` only when a new clip is discovered; an accepted command with no visible clip returns `completed: false` and a user-facing message. No action automatically arms unrelated cameras.
- Live playback selects IMMIS or legacy RTSPS from the session returned by Blink. Both transports use verified TLS and feed MPEG-TS to FFmpeg through a pipe. Blink credentials stay in memory and do not appear in FFmpeg arguments. HLS is served on `127.0.0.1` under a random capability path, with no directory listing or persistent archive. Sessions expire within 90 seconds and close when idle. EOF, logout, stop, and termination clean up owned resources.
- For public IP relays, the bridge requires the bundled Blink certificate and verifies its exact fingerprint before sending stream credentials. IMMIS uses port 443 and legacy RTSPS can use ports 332 or 443. DNS relays use TLS hostname verification. Legacy RTSPS DNS relays are limited to Blink relay hosts, and the bundled certificate is also available to their verifier. See the [certificate provenance and licence](../Sources/Blinky/Bridge/Certificates/NOTICE.txt).
- `logout` disconnects locally; it does not guarantee token revocation on Blink's servers. The app must also remove the session from Keychain.
- The library uses private Blink interfaces, which may change. Automated tests use substitutes for the account and cameras; real compatibility requires testing with the user's account.

## Verified sources

- [blinkpy v0.25.9 release](https://github.com/fronzbot/blinkpy/releases/tag/v0.25.9).
- [Authentication and session attributes in this release](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/auth.py).
- [Startup, verification, and clip metadata](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/blinkpy.py).
- [Camera methods](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/camera.py).
- [Cloud pagination and command status reference](https://github.com/madmachinations/blink/blob/main/custom_components/blink_live/blink_live_client/client.py).
- [Blink live stream protocol implementation](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/livestream.py).
- [Legacy RTSPS protocol and supported camera families](https://github.com/Teethree89/ha-blink-live-view-proxy/blob/main/docs/CONFIGURATION.md#live-view-transports).
- [Community live integration](https://github.com/Derinbay-Labs/ha-blink-live).
